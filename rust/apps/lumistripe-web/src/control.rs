use crate::audio;
use crate::bluetooth::BluetoothService;
use crate::bluetooth::SPOTIFY_MONITOR_SOURCE;
use crate::bluetooth_process::BluetoothError;
use crate::config::EngineOutputOptions;
use crate::error::ApiError;
use crate::settings::{
    AudioTuningProfile, ColorCorrection, DashboardSettings, MAX_SAVED_ANIMATION_SEQUENCES,
    MusicIdleMode, MusicIdleSettings, PersistedPlaybackMode, SavedAnimationSequence, SettingsStore,
    StripeTopologySettings, SwitchQuantization, TransitionSettings,
};
use crate::spotify::SpotifyProvider;
use crate::state::{
    AUDIO_CALIBRATION_TIMEOUT, ActiveAudioCalibration, ActiveColorCalibration,
    AudioCalibrationResult, COLOR_CALIBRATION_TIMEOUT, ControlModel, EngineDesiredState,
    OutputControl, PlaybackMode,
};
use lumistripe_engine_audio::{InputAudioDevice, recommend_audio_calibration};
use lumistripe_engine_client::{EngineClient, EngineClientError};
use lumistripe_engine_protocol::{EngineAudioStatus, RequestCommand, ResponseResult};
use serde_json::{Map, Value, json};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tokio::process::Command;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::task::JoinHandle;
use tokio::time::{Instant, interval, timeout_at};
use tokio_util::sync::CancellationToken;

const COMMAND_CAPACITY: usize = 64;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(5);
const IDENTITY_COMMAND_TIMEOUT: Duration = Duration::from_secs(35);
const TOPOLOGY_COMMAND_TIMEOUT: Duration = Duration::from_secs(30);
const AUTOMATIC_SPOTIFY_RETRY: Duration = Duration::from_secs(5);

#[derive(Clone, Debug)]
struct SpotifyDeviceNameHelper {
    sudo_path: PathBuf,
    helper_path: PathBuf,
    timeout: Duration,
}

impl Default for SpotifyDeviceNameHelper {
    fn default() -> Self {
        Self {
            sudo_path: PathBuf::from("/usr/bin/sudo"),
            helper_path: std::env::var_os("LUMI_DEPLOY_HELPER")
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    PathBuf::from("/usr/local/libexec/lumistripe/deploy/lumistripe-deploy-helper")
                }),
            timeout: Duration::from_secs(30),
        }
    }
}

impl SpotifyDeviceNameHelper {
    async fn apply(&self, spotify_device_name: &str) -> Result<bool, ApiError> {
        if !self.helper_path.is_file() {
            return Ok(false);
        }

        let mut update_name = Command::new(&self.helper_path);
        update_name
            .arg("spotify-device-name")
            .arg(spotify_device_name)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let status = tokio::time::timeout(self.timeout, update_name.status())
            .await
            .map_err(|_| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "Could not apply Spotify player name: configuration helper timed out",
                )
            })?
            .map_err(|error| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    format!("Could not apply Spotify player name: {error}"),
                )
            })?;
        if !status.success() {
            return Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                format!(
                    "Could not apply Spotify player name: configuration helper exited with {status}"
                ),
            ));
        }

        let mut restart = Command::new(&self.sudo_path);
        restart
            .arg("-n")
            .arg("/usr/bin/systemctl")
            .arg("restart")
            .arg("--no-block")
            .arg("lumistripe-spotify.service")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let status = tokio::time::timeout(self.timeout, restart.status())
            .await
            .map_err(|_| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "Could not apply Spotify player name: service restart timed out",
                )
            })?
            .map_err(|error| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    format!("Could not apply Spotify player name: {error}"),
                )
            })?;
        if !status.success() {
            return Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                format!(
                    "Could not apply Spotify player name: service restart exited with {status}"
                ),
            ));
        }
        Ok(true)
    }
}

#[derive(Default)]
struct AudioSourceRouting {
    original_default_source: Option<Option<String>>,
    selected_pipewire_source: Option<String>,
    bluetooth_address: Option<String>,
    spotify_active: Option<bool>,
    spotify_retry_at: Option<Instant>,
}

#[derive(Clone)]
struct ApplicationSettingsSnapshot {
    settings: DashboardSettings,
    output_ids: Vec<String>,
    audio_status: Option<lumistripe_engine_protocol::EngineAudioStatus>,
    audio_source: String,
    active_audio_source: String,
    audio_calibration: Option<ActiveAudioCalibration>,
}

#[derive(Clone)]
pub struct ControlPlane {
    sender: mpsc::Sender<Envelope>,
    state: watch::Receiver<Arc<Value>>,
    startup_settings: watch::Receiver<Arc<Value>>,
    application_settings: watch::Receiver<Arc<ApplicationSettingsSnapshot>>,
    client: EngineClient,
}

enum ControlCommand {
    SetMode {
        mode: PlaybackMode,
        color: Option<String>,
        stripe_id: Option<String>,
        music_recognition_enabled: Option<bool>,
    },
    SetBrightness {
        brightness: f64,
        stripe_id: Option<String>,
    },
    SelectAnimation {
        name: String,
        stripe_id: Option<String>,
    },
    SetBlackout {
        enabled: bool,
        stripe_id: Option<String>,
    },
    SetStripeTopology {
        topology: StripeTopologySettings,
    },
    TestStripe {
        stripe_id: String,
        pattern: String,
        topology: Option<StripeTopologySettings>,
    },
    StartColorCalibration {
        session_id: String,
        output_index: usize,
    },
    UpdateColorCalibration {
        session_id: String,
        correction: ColorCorrection,
        pattern: String,
    },
    FinishColorCalibration {
        session_id: String,
        save: bool,
    },
    SetMusicIdle {
        settings: MusicIdleSettings,
        stripe_id: Option<String>,
    },
    SetStartupRestore {
        enabled: bool,
    },
    SetAnimationSettings {
        animation_name: String,
        stripe_id: Option<String>,
        values: Option<Map<String, Value>>,
    },
    SetTransitionSettings {
        settings: TransitionSettings,
    },
    SetSequencerSettings {
        switch_quantization: SwitchQuantization,
        bars_per_phrase: u8,
    },
    CreateSequence {
        sequence: SavedAnimationSequence,
    },
    UpdateSequence {
        sequence_id: String,
        sequence: SavedAnimationSequence,
    },
    DeleteSequence {
        sequence_id: String,
    },
    ActivateSequence {
        sequence_id: Option<String>,
    },
    SelectAudioDevice {
        device: String,
    },
    ApplyAudioSettings {
        device: String,
        profile: AudioTuningProfile,
    },
    SetAudioSyncOffset {
        offset_ms: f64,
    },
    SetAudioSource {
        source: String,
    },
    StartAudioCalibration {
        session_id: String,
        device: String,
        duration_seconds: f64,
        two_step: bool,
    },
    BeginAudioCalibrationMusic {
        session_id: String,
    },
    FinishAudioCalibration {
        session_id: String,
        apply: bool,
        target_level: Option<f64>,
        noise_floor: Option<f64>,
    },
    SetIdentity {
        application_name: String,
        spotify_device_name: String,
    },
}

struct Envelope {
    command: ControlCommand,
    reply: oneshot::Sender<Result<Value, ApiError>>,
    deadline: Instant,
}

impl ControlPlane {
    pub(crate) fn start(
        mut model: ControlModel,
        settings_store: SettingsStore,
        desired_state: watch::Sender<EngineDesiredState>,
        client: EngineClient,
        mut health: watch::Receiver<Option<Result<Arc<ResponseResult>, String>>>,
        mut audio: watch::Receiver<Option<Result<EngineAudioStatus, String>>>,
        bluetooth: Option<BluetoothService>,
        spotify: Option<Arc<dyn SpotifyProvider>>,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        let (sender, mut receiver) = mpsc::channel::<Envelope>(COMMAND_CAPACITY);
        let (state_sender, state) = watch::channel(Arc::new(model.snapshot(0)));
        let (startup_sender, startup_settings) =
            watch::channel(Arc::new(startup_settings_snapshot(&model)));
        let (application_settings_sender, application_settings) =
            watch::channel(Arc::new(application_settings_snapshot(&model)));
        let control_client = client.clone();
        let spotify_device_name_helper = SpotifyDeviceNameHelper::default();
        let task = tokio::spawn(async move {
            // The worker is the sole owner of persisted application settings.
            let mut settings_store = settings_store;
            let mut audio_routing = AudioSourceRouting::default();
            let mut state_tick = interval(Duration::from_secs(1));
            loop {
                tokio::select! {
                    _ = cancellation.cancelled() => break,
                    envelope = receiver.recv() => {
                        let Some(envelope) = envelope else { break; };
                        if envelope.reply.is_closed() {
                            continue;
                        }
                        let Envelope { command, mut reply, deadline } = envelope;
                        let finish_transaction = matches!(
                            &command,
                            ControlCommand::SetStripeTopology { .. }
                                | ControlCommand::TestStripe { .. }
                                | ControlCommand::StartColorCalibration { .. }
                                | ControlCommand::UpdateColorCalibration { .. }
                                | ControlCommand::FinishColorCalibration { .. }
                                | ControlCommand::SelectAudioDevice { .. }
                                | ControlCommand::ApplyAudioSettings { .. }
                                | ControlCommand::SetAudioSource { .. }
                                | ControlCommand::SetAudioSyncOffset { .. }
                                | ControlCommand::StartAudioCalibration { .. }
                                | ControlCommand::BeginAudioCalibrationMusic { .. }
                                | ControlCommand::FinishAudioCalibration { .. }
                        );
                        let identity_update = matches!(&command, ControlCommand::SetIdentity { .. });
                        let apply = apply_command(
                                &mut model,
                                &mut settings_store,
                                &desired_state,
                                &client,
                                &mut audio_routing,
                                bluetooth.as_ref(),
                                spotify.as_deref(),
                                &spotify_device_name_helper,
                                command,
                            );
                        let result = if finish_transaction {
                            match timeout_at(deadline, apply).await {
                                Ok(result) => result,
                                Err(_) => Err(ApiError::new(
                                    axum::http::StatusCode::GATEWAY_TIMEOUT,
                                    "runtime command timed out",
                                )),
                            }
                        } else {
                            tokio::select! {
                                _ = reply.closed() => continue,
                                result = timeout_at(deadline, apply) => match result {
                                    Ok(result) => result,
                                    Err(_) => Err(ApiError::new(
                                        axum::http::StatusCode::GATEWAY_TIMEOUT,
                                        "runtime command timed out",
                                    )),
                                }
                            }
                        };
                        let result = match result {
                            Ok(()) => {
                                let snapshot = model.snapshot(receiver.max_capacity() - receiver.capacity());
                                let _ = state_sender.send(Arc::new(snapshot.clone()));
                                startup_sender.send_replace(Arc::new(startup_settings_snapshot(&model)));
                                application_settings_sender.send_replace(Arc::new(application_settings_snapshot(&model)));
                                if identity_update {
                                    Ok(identity_settings_value(
                                        &model,
                                        model.spotify_restart_requested,
                                    ))
                                } else {
                                    Ok(snapshot)
                                }
                            }
                            Err(error) => {
                                let snapshot = model.snapshot(
                                    receiver.max_capacity() - receiver.capacity(),
                                );
                                let _ = state_sender.send(Arc::new(snapshot));
                                startup_sender.send_replace(Arc::new(startup_settings_snapshot(&model)));
                                Err(error)
                            }
                        };
                        let _ = reply.send(result);
                    }
                    changed = health.changed() => {
                        if changed.is_err() { break; }
                        if let Some(Err(error)) = health.borrow_and_update().as_ref() {
                            if model.engine_error.as_ref() != Some(error) {
                                model.set_engine_health(Err(error.clone()));
                                model.revision = model.revision.saturating_add(1);
                                let _ = state_sender.send(Arc::new(model.snapshot(receiver.max_capacity() - receiver.capacity())));
                            }
                        }
                    }
                    changed = audio.changed() => {
                        if changed.is_err() { break; }
                        let snapshot = { audio.borrow_and_update().as_ref().cloned() };
                        if let Some(snapshot) = snapshot {
                            let snapshot = reconcile_demo_audio_status(
                                &model.audio_source,
                                snapshot,
                                &client,
                            ).await;
                            model.set_audio_status(snapshot.clone());
                            match &snapshot {
                                Ok(status) => advance_audio_calibration(&mut model, status),
                                Err(error) => fail_audio_calibration(&mut model, error),
                            }
                            application_settings_sender.send_replace(Arc::new(application_settings_snapshot(&model)));
                        }
                    }
                    _ = state_tick.tick() => {
                        if should_poll_automatic_audio_source(
                            model.config.runtime_kind,
                            model.config.spotify_provider_enabled,
                        )
                            && matches!(model.audio_source.as_str(), "auto" | "bluetooth" | "spotify")
                        {
                            let _ = maintain_configured_audio_source(
                                &mut model,
                                &desired_state,
                                &client,
                                &mut audio_routing,
                                bluetooth.as_ref(),
                                spotify.as_deref(),
                            ).await;
                        }
                        expire_color_calibration(&mut model, &client, &desired_state).await;
                        if let Some(snapshot) = health.borrow().as_ref() {
                            model.set_engine_health(snapshot.as_ref().map(|value| (**value).clone()).map_err(|error| error.clone()));
                        }
                        let snapshot = { audio.borrow().as_ref().cloned() };
                        if let Some(snapshot) = snapshot {
                            let snapshot = reconcile_demo_audio_status(
                                &model.audio_source,
                                snapshot,
                                &client,
                            ).await;
                            model.set_audio_status(snapshot.clone());
                            match &snapshot {
                                Ok(status) => advance_audio_calibration(&mut model, status),
                                Err(error) => fail_audio_calibration(&mut model, error),
                            }
                        }
                        model.revision = model.revision.saturating_add(1);
                        let _ = state_sender.send(Arc::new(model.snapshot(receiver.max_capacity() - receiver.capacity())));
                        application_settings_sender.send_replace(Arc::new(application_settings_snapshot(&model)));
                    }
                }
            }
            if let (Some(bluetooth), Some(Some(previous_source))) = (
                bluetooth.as_ref(),
                audio_routing.original_default_source.take(),
            ) {
                bluetooth
                    .restore_default_source(Some(previous_source))
                    .await;
            }
        });
        (
            Self {
                sender,
                state,
                startup_settings,
                application_settings,
                client: control_client,
            },
            task,
        )
    }

    pub fn state(&self) -> watch::Receiver<Arc<Value>> {
        self.state.clone()
    }

    pub fn startup_settings(&self) -> Value {
        self.startup_settings.borrow().as_ref().clone()
    }

    pub fn stripe_topology(&self) -> Value {
        self.state
            .borrow()
            .get("stripe_topology")
            .cloned()
            .unwrap_or(Value::Null)
    }

    pub async fn audio_settings(&self) -> Result<Value, ApiError> {
        let snapshot = self.application_settings.borrow().as_ref().clone();
        let (devices, enumeration_error) =
            match self.client.request(RequestCommand::AudioDevices).await {
                Ok(ResponseResult::AudioDevices { devices }) => (devices, None),
                Ok(ResponseResult::Error { message, .. }) => (Vec::new(), Some(message)),
                Ok(other) => (
                    Vec::new(),
                    Some(format!("unexpected audio-device response: {other:?}")),
                ),
                Err(error) => (Vec::new(), Some(error.to_string())),
            };
        Ok(audio::response(
            &snapshot.settings,
            &snapshot.audio_source,
            &snapshot.active_audio_source,
            snapshot.audio_status.as_ref(),
            &devices,
            enumeration_error.as_deref(),
        ))
    }

    pub(crate) fn audio_stream_context(&self) -> audio::AudioStreamContext {
        let snapshot = self.application_settings.borrow();
        audio::stream_context(
            &snapshot.settings,
            &snapshot.audio_source,
            &snapshot.active_audio_source,
            snapshot.audio_status.as_ref(),
        )
    }

    pub async fn select_audio_device(&self, device: String) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SelectAudioDevice { device })
            .await?;
        self.audio_settings().await
    }

    pub async fn apply_audio_settings(
        &self,
        device: String,
        profile: AudioTuningProfile,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::ApplyAudioSettings { device, profile })
            .await?;
        self.audio_settings().await
    }

    pub async fn reset_audio_settings(&self, device: String) -> Result<Value, ApiError> {
        self.apply_audio_settings(device, AudioTuningProfile::default())
            .await
    }

    pub async fn set_audio_sync_offset(&self, offset_ms: f64) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetAudioSyncOffset { offset_ms })
            .await?;
        self.audio_settings().await
    }

    pub async fn set_audio_source(&self, source: String) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetAudioSource { source })
            .await?;
        self.audio_settings().await
    }

    pub async fn start_audio_calibration(
        &self,
        device: String,
        duration_seconds: f64,
        two_step: bool,
    ) -> Result<Value, ApiError> {
        let session_id = crate::auth::random_session_token();
        self.execute(ControlCommand::StartAudioCalibration {
            session_id: session_id.clone(),
            device,
            duration_seconds,
            two_step,
        })
        .await?;
        self.audio_calibration_status(&session_id)
    }

    pub fn audio_calibration_status(&self, session_id: &str) -> Result<Value, ApiError> {
        let snapshot = self.application_settings.borrow();
        let Some(session) = snapshot.audio_calibration.as_ref() else {
            return Err(ApiError::new(
                axum::http::StatusCode::NOT_FOUND,
                "audio calibration session not found",
            ));
        };
        if session.session_id != session_id {
            return Err(ApiError::new(
                axum::http::StatusCode::NOT_FOUND,
                "audio calibration session not found",
            ));
        }
        Ok(audio_calibration_value(session))
    }

    pub async fn begin_audio_calibration_music(
        &self,
        session_id: String,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::BeginAudioCalibrationMusic { session_id })
            .await?;
        let snapshot = self.application_settings.borrow();
        snapshot
            .audio_calibration
            .as_ref()
            .map(audio_calibration_value)
            .ok_or_else(|| {
                ApiError::new(
                    axum::http::StatusCode::NOT_FOUND,
                    "audio calibration session not found",
                )
            })
    }

    pub async fn finish_audio_calibration(
        &self,
        session_id: String,
        apply: bool,
        target_level: Option<f64>,
        noise_floor: Option<f64>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::FinishAudioCalibration {
            session_id,
            apply,
            target_level,
            noise_floor,
        })
        .await?;
        self.audio_settings().await
    }

    pub fn identity_settings(&self) -> Value {
        let settings = &self.application_settings.borrow().settings;
        json!({
            "application_name": settings.application_name,
            "spotify_device_name": settings.spotify_device_name,
            "spotify_restart_requested": false,
        })
    }

    pub async fn set_identity_settings(
        &self,
        application_name: String,
        spotify_device_name: String,
    ) -> Result<Value, ApiError> {
        self.execute_with_timeout(
            ControlCommand::SetIdentity {
                application_name,
                spotify_device_name,
            },
            IDENTITY_COMMAND_TIMEOUT,
        )
        .await
    }

    pub fn animation_settings(
        &self,
        animation_name: &str,
        stripe_id: Option<&str>,
    ) -> Result<Value, ApiError> {
        let snapshot = self.application_settings.borrow();
        let definition = lumistripe_engine_animation_definitions::catalog()
            .animation(animation_name)
            .ok_or_else(|| {
                ApiError::new(
                    axum::http::StatusCode::NOT_FOUND,
                    format!("unknown animation: {animation_name}"),
                )
            })?;
        if definition.settings.is_empty() {
            return Err(ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                format!("animation {animation_name} has no configurable settings"),
            ));
        }
        if let Some(stripe_id) = stripe_id
            && !snapshot.output_ids.iter().any(|id| id == stripe_id)
        {
            return Err(ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                format!("unknown stripe: {stripe_id}"),
            ));
        }
        let shared = snapshot
            .settings
            .animation_settings
            .get("shared")
            .and_then(|items| items.get(animation_name));
        let values = stripe_id
            .and_then(|stripe_id| {
                snapshot
                    .settings
                    .animation_settings
                    .get(stripe_id)
                    .and_then(|items| items.get(animation_name))
            })
            .or(shared);
        let empty = Map::new();
        let normalized = definition
            .normalize_settings(values.unwrap_or(&empty), true)
            .map_err(|error| {
                ApiError::new(
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    error.to_string(),
                )
            })?;
        let options = serde_json::to_value(&definition.settings).map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("could not encode animation settings options: {error}"),
            )
        })?;
        Ok(json!({
            "animation": animation_name,
            "stripe_id": stripe_id,
            "options": options,
            "values": normalized,
        }))
    }

    pub async fn set_mode(
        &self,
        mode: PlaybackMode,
        color: Option<String>,
        stripe_id: Option<String>,
        music_recognition_enabled: Option<bool>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetMode {
            mode,
            color,
            stripe_id,
            music_recognition_enabled,
        })
        .await
    }

    pub async fn set_brightness(
        &self,
        brightness: f64,
        stripe_id: Option<String>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetBrightness {
            brightness,
            stripe_id,
        })
        .await
    }

    pub async fn select_animation(
        &self,
        name: String,
        stripe_id: Option<String>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SelectAnimation { name, stripe_id })
            .await
    }

    pub async fn set_blackout(
        &self,
        enabled: bool,
        stripe_id: Option<String>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetBlackout { enabled, stripe_id })
            .await
    }

    pub async fn set_music_idle(
        &self,
        settings: MusicIdleSettings,
        stripe_id: Option<String>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetMusicIdle {
            settings,
            stripe_id,
        })
        .await
    }

    pub async fn set_stripe_topology(
        &self,
        topology: StripeTopologySettings,
    ) -> Result<Value, ApiError> {
        self.execute_with_timeout(
            ControlCommand::SetStripeTopology { topology },
            TOPOLOGY_COMMAND_TIMEOUT,
        )
        .await
    }

    pub async fn test_stripe(
        &self,
        stripe_id: String,
        pattern: String,
        topology: Option<StripeTopologySettings>,
    ) -> Result<Value, ApiError> {
        self.execute_with_timeout(
            ControlCommand::TestStripe {
                stripe_id,
                pattern,
                topology,
            },
            TOPOLOGY_COMMAND_TIMEOUT,
        )
        .await
    }

    pub async fn start_color_calibration(&self, output_index: usize) -> Result<Value, ApiError> {
        let session_id = crate::auth::random_session_token();
        let state = self
            .execute(ControlCommand::StartColorCalibration {
                session_id: session_id.clone(),
                output_index,
            })
            .await?;
        Ok(json!({"session_id": session_id, "state": state}))
    }

    pub async fn update_color_calibration(
        &self,
        session_id: String,
        correction: ColorCorrection,
        pattern: String,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::UpdateColorCalibration {
            session_id,
            correction,
            pattern,
        })
        .await
    }

    pub async fn finish_color_calibration(
        &self,
        session_id: String,
        save: bool,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::FinishColorCalibration { session_id, save })
            .await
    }

    pub async fn set_startup_restore(&self, enabled: bool) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetStartupRestore { enabled })
            .await
    }

    pub async fn set_animation_settings(
        &self,
        animation_name: String,
        stripe_id: Option<String>,
        values: Option<Map<String, Value>>,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetAnimationSettings {
            animation_name,
            stripe_id,
            values,
        })
        .await
    }

    pub async fn set_transition_settings(
        &self,
        settings: TransitionSettings,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetTransitionSettings { settings })
            .await
    }

    pub async fn set_sequencer_settings(
        &self,
        switch_quantization: SwitchQuantization,
        bars_per_phrase: u8,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::SetSequencerSettings {
            switch_quantization,
            bars_per_phrase,
        })
        .await
    }

    pub async fn create_sequence(
        &self,
        sequence: SavedAnimationSequence,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::CreateSequence { sequence })
            .await
    }

    pub async fn update_sequence(
        &self,
        sequence_id: String,
        sequence: SavedAnimationSequence,
    ) -> Result<Value, ApiError> {
        self.execute(ControlCommand::UpdateSequence {
            sequence_id,
            sequence,
        })
        .await
    }

    pub async fn delete_sequence(&self, sequence_id: String) -> Result<Value, ApiError> {
        self.execute(ControlCommand::DeleteSequence { sequence_id })
            .await
    }

    pub async fn activate_sequence(&self, sequence_id: Option<String>) -> Result<Value, ApiError> {
        self.execute(ControlCommand::ActivateSequence { sequence_id })
            .await
    }

    async fn execute(&self, command: ControlCommand) -> Result<Value, ApiError> {
        self.execute_with_timeout(command, COMMAND_TIMEOUT).await
    }

    async fn execute_with_timeout(
        &self,
        command: ControlCommand,
        command_timeout: Duration,
    ) -> Result<Value, ApiError> {
        let (reply, response) = oneshot::channel();
        let deadline = Instant::now() + command_timeout;
        self.sender
            .try_send(Envelope {
                command,
                reply,
                deadline,
            })
            .map_err(|error| match error {
                mpsc::error::TrySendError::Full(_) => ApiError::new(
                    axum::http::StatusCode::SERVICE_UNAVAILABLE,
                    "runtime command queue is full",
                ),
                mpsc::error::TrySendError::Closed(_) => ApiError::new(
                    axum::http::StatusCode::SERVICE_UNAVAILABLE,
                    "runtime is not running",
                ),
            })?;
        timeout_at(deadline, response)
            .await
            .map_err(|_| {
                ApiError::new(
                    axum::http::StatusCode::GATEWAY_TIMEOUT,
                    "runtime command timed out",
                )
            })?
            .map_err(|_| {
                ApiError::new(
                    axum::http::StatusCode::SERVICE_UNAVAILABLE,
                    "runtime stopped before the command completed",
                )
            })?
    }
}

async fn apply_command(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    spotify: Option<&dyn SpotifyProvider>,
    spotify_device_name_helper: &SpotifyDeviceNameHelper,
    command: ControlCommand,
) -> Result<(), ApiError> {
    if model.audio_calibration.is_some()
        && !matches!(
            &command,
            ControlCommand::BeginAudioCalibrationMusic { .. }
                | ControlCommand::FinishAudioCalibration { .. }
        )
    {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "finish or discard microphone calibration before changing audio settings",
        ));
    }
    let command = match command {
        ControlCommand::SetStripeTopology { topology } => {
            return apply_stripe_topology(model, settings_store, desired_state, client, topology)
                .await;
        }
        ControlCommand::TestStripe {
            stripe_id,
            pattern,
            topology,
        } => {
            return run_stripe_test(model, desired_state, client, stripe_id, pattern, topology)
                .await;
        }
        ControlCommand::SelectAudioDevice { device } => {
            if model.calibration.is_some() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "finish or cancel color calibration before changing the lights",
                ));
            }
            return select_audio_device(
                model,
                settings_store,
                desired_state,
                client,
                audio_routing,
                bluetooth,
                &device,
            )
            .await;
        }
        ControlCommand::ApplyAudioSettings { device, profile } => {
            if model.calibration.is_some() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "finish or cancel color calibration before changing the lights",
                ));
            }
            return apply_audio_settings(
                model,
                settings_store,
                desired_state,
                client,
                &device,
                profile,
            )
            .await;
        }
        ControlCommand::SetAudioSource { source } => {
            if model.calibration.is_some() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "finish or cancel color calibration before changing the lights",
                ));
            }
            return set_audio_source(
                model,
                settings_store,
                desired_state,
                client,
                audio_routing,
                bluetooth,
                spotify,
                &source,
            )
            .await;
        }
        ControlCommand::StartAudioCalibration {
            session_id,
            device,
            duration_seconds,
            two_step,
        } => {
            if model.calibration.is_some() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "finish or cancel color calibration before starting microphone calibration",
                ));
            }
            return start_audio_calibration(
                model,
                client,
                session_id,
                &device,
                duration_seconds,
                two_step,
            )
            .await;
        }
        ControlCommand::BeginAudioCalibrationMusic { session_id } => {
            return begin_audio_calibration_music(model, &session_id);
        }
        ControlCommand::FinishAudioCalibration {
            session_id,
            apply,
            target_level,
            noise_floor,
        } => {
            return finish_audio_calibration(
                model,
                settings_store,
                desired_state,
                client,
                AudioCalibrationFinish {
                    session_id: &session_id,
                    apply,
                    target_level,
                    noise_floor,
                },
            )
            .await;
        }
        ControlCommand::SetIdentity {
            application_name,
            spotify_device_name,
        } => {
            if model.calibration.is_some() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "finish or cancel color calibration before changing application settings",
                ));
            }
            return set_identity_settings(
                model,
                settings_store,
                &application_name,
                &spotify_device_name,
                spotify_device_name_helper,
            )
            .await;
        }
        command => command,
    };
    if model.calibration.is_some()
        && !matches!(
            &command,
            ControlCommand::UpdateColorCalibration { .. }
                | ControlCommand::FinishColorCalibration { .. }
        )
    {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "finish or cancel color calibration before changing the lights",
        ));
    }
    let mut candidate = model.clone();
    let mut persisted_categories = None;
    let mut remember_shared_playback = false;
    let transition_requested = matches!(&command, ControlCommand::SelectAnimation { .. });
    match command {
        ControlCommand::SetMode {
            mode,
            color,
            stripe_id,
            music_recognition_enabled,
        } => {
            if let Some(color) = color {
                if parse_color(&color).is_none() {
                    return Err(validation_error(
                        "string_pattern_mismatch",
                        "color",
                        "String should match pattern '^#[0-9A-Fa-f]{6}$'",
                        json!(color),
                    )
                    .with_context(json!({"pattern": "^#[0-9A-Fa-f]{6}$"})));
                }
                for output in candidate.targets_mut(stripe_id.as_deref())? {
                    output.solid_color = color.clone();
                }
            }
            for output in candidate.targets_mut(stripe_id.as_deref())? {
                output.mode = mode;
                if let Some(enabled) = music_recognition_enabled {
                    output.music_recognition_enabled = enabled;
                }
            }
            if mode == PlaybackMode::Dynamic {
                if !model
                    .audio_status
                    .as_ref()
                    .is_some_and(|status| status.active)
                {
                    match model.audio_source.as_str() {
                        "auto" | "bluetooth" | "spotify" => {
                            maintain_configured_audio_source(
                                model,
                                desired_state,
                                client,
                                audio_routing,
                                bluetooth,
                                spotify,
                            )
                            .await?;
                        }
                        "demo" => {
                            set_audio_source(
                                model,
                                settings_store,
                                desired_state,
                                client,
                                audio_routing,
                                bluetooth,
                                spotify,
                                "demo",
                            )
                            .await?;
                        }
                        _ => ensure_audio_active(model, client).await?,
                    }
                }
                if !model
                    .audio_status
                    .as_ref()
                    .is_some_and(|status| status.active)
                {
                    return Err(ApiError::new(
                        axum::http::StatusCode::CONFLICT,
                        "dynamic mode requires an active audio source",
                    ));
                }
                candidate.audio_started = model.audio_started;
                candidate.audio_status = model.audio_status.clone();
                candidate.active_audio_source = model.active_audio_source.clone();
            }
            remember_shared_playback = stripe_id.is_none();
        }
        ControlCommand::SetBrightness {
            brightness,
            stripe_id,
        } => {
            if !brightness.is_finite() || !(0.0..=1.0).contains(&brightness) {
                let (kind, message, context) = if brightness < 0.0 {
                    (
                        "greater_than_equal",
                        "Input should be greater than or equal to 0",
                        json!({"ge": 0.0}),
                    )
                } else {
                    (
                        "less_than_equal",
                        "Input should be less than or equal to 1",
                        json!({"le": 1.0}),
                    )
                };
                let input = serde_json::Number::from_f64(brightness)
                    .map(Value::Number)
                    .unwrap_or(Value::Null);
                return Err(
                    validation_error(kind, "brightness", message, input).with_context(context)
                );
            }
            for output in candidate.targets_mut(stripe_id.as_deref())? {
                output.brightness = brightness;
            }
            remember_shared_playback = stripe_id.is_none();
        }
        ControlCommand::SelectAnimation { name, stripe_id } => {
            if lumistripe_engine_animation_definitions::catalog()
                .animation(&name)
                .is_none()
            {
                return Err(ApiError::new(
                    axum::http::StatusCode::NOT_FOUND,
                    format!("unknown animation: {name}"),
                ));
            }
            for output in candidate.targets_mut(stripe_id.as_deref())? {
                output.animation.clone_from(&name);
                output.mode = PlaybackMode::Static;
            }
            remember_shared_playback = stripe_id.is_none();
        }
        ControlCommand::SetBlackout { enabled, stripe_id } => {
            for output in candidate.targets_mut(stripe_id.as_deref())? {
                output.blackout = enabled;
            }
            remember_shared_playback = stripe_id.is_none();
        }
        ControlCommand::SetMusicIdle {
            settings,
            stripe_id,
        } => {
            if !settings.animation.is_empty()
                && lumistripe_engine_animation_definitions::catalog()
                    .animation(&settings.animation)
                    .is_none()
            {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "unknown music idle animation",
                ));
            }
            if settings.mode == MusicIdleMode::Animation && settings.animation.is_empty() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "music idle animation is required",
                ));
            }
            let independent = candidate
                .settings
                .stripe_topology
                .as_ref()
                .is_some_and(|topology| {
                    topology.layout == crate::settings::StripeLayout::Independent
                });
            let scopes = if let Some(stripe_id) = stripe_id {
                if !independent {
                    return Err(ApiError::new(
                        axum::http::StatusCode::CONFLICT,
                        "a stripe target is only available in independent layout",
                    ));
                }
                if !candidate
                    .outputs
                    .iter()
                    .any(|item| item.stripe_id == stripe_id)
                {
                    return Err(ApiError::new(
                        axum::http::StatusCode::CONFLICT,
                        format!("unknown stripe: {stripe_id}"),
                    ));
                }
                vec![stripe_id]
            } else if independent {
                candidate
                    .outputs
                    .iter()
                    .map(|output| output.stripe_id.clone())
                    .collect()
            } else {
                vec!["shared".to_owned()]
            };
            for scope in scopes {
                let mut scoped_settings = settings.clone();
                if let Some(previous) = candidate.settings.music_idle.get(&scope) {
                    scoped_settings.extra.clone_from(&previous.extra);
                }
                candidate.settings.music_idle.insert(scope, scoped_settings);
            }
            persisted_categories = Some(vec![(
                "music_idle",
                serde_json::to_value(&candidate.settings.music_idle).map_err(|error| {
                    ApiError::new(
                        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                        format!("could not encode music-idle settings: {error}"),
                    )
                })?,
            )]);
        }
        ControlCommand::SetStartupRestore { enabled } => {
            candidate.settings.restore_last_state = enabled;
            if enabled {
                remember_playback_from_current_output(&mut candidate);
            }
            let remembered = serde_json::to_value(&candidate.settings.remembered_playback)
                .map_err(|error| {
                    ApiError::new(
                        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                        format!("could not encode remembered playback: {error}"),
                    )
                })?;
            persisted_categories = Some(vec![
                ("restore_last_state", json!(enabled)),
                ("remembered_playback", remembered),
            ]);
        }
        ControlCommand::StartColorCalibration {
            session_id,
            output_index,
        } => {
            if candidate.calibration.is_some() {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "a color calibration session is already active",
                ));
            }
            if output_index >= candidate.outputs.len() {
                return Err(ApiError::new(
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    format!("unknown output index: {output_index}"),
                ));
            }
            candidate.calibration = Some(ActiveColorCalibration {
                session_id,
                output_index,
                pattern: "white".to_owned(),
                last_activity: std::time::Instant::now(),
                original_corrections: candidate.settings.color_corrections.clone(),
            });
        }
        ControlCommand::UpdateColorCalibration {
            session_id,
            mut correction,
            pattern,
        } => {
            if !["white", "red", "green", "blue"].contains(&pattern.as_str()) {
                return Err(ApiError::new(
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    format!("unknown calibration pattern: {pattern}"),
                ));
            }
            let session = candidate.calibration.as_ref().ok_or_else(|| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "no color calibration session is active",
                )
            })?;
            if session.session_id != session_id {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "color calibration session is stale",
                ));
            }
            if session.last_activity.elapsed() >= COLOR_CALIBRATION_TIMEOUT {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "color calibration session has expired",
                ));
            }
            let stripe_id = candidate.outputs[session.output_index].stripe_id.clone();
            if let Some(previous) = candidate.settings.color_corrections.get(&stripe_id) {
                correction.extra.clone_from(&previous.extra);
            }
            candidate
                .settings
                .color_corrections
                .insert(stripe_id, correction);
            let active = candidate.calibration.as_mut().expect("session was checked");
            active.pattern = pattern;
            active.last_activity = std::time::Instant::now();
        }
        ControlCommand::FinishColorCalibration { session_id, save } => {
            let session = candidate.calibration.as_ref().ok_or_else(|| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "no color calibration session is active",
                )
            })?;
            if session.session_id != session_id {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "color calibration session is stale",
                ));
            }
            if session.last_activity.elapsed() >= COLOR_CALIBRATION_TIMEOUT {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "color calibration session has expired",
                ));
            }
            if !save {
                candidate.settings.color_corrections = session.original_corrections.clone();
            } else {
                persisted_categories = Some(vec![(
                    "color_correction",
                    serde_json::to_value(&candidate.settings.color_corrections).map_err(
                        |error| {
                            ApiError::new(
                                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                                format!("could not encode color-correction profiles: {error}"),
                            )
                        },
                    )?,
                )]);
            }
            candidate.calibration = None;
        }
        ControlCommand::SetAnimationSettings {
            animation_name,
            stripe_id,
            values,
        } => {
            let definition = lumistripe_engine_animation_definitions::catalog()
                .animation(&animation_name)
                .ok_or_else(|| {
                    ApiError::new(
                        axum::http::StatusCode::NOT_FOUND,
                        format!("unknown animation: {animation_name}"),
                    )
                })?;
            if definition.settings.is_empty() {
                return Err(ApiError::new(
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    format!("animation {animation_name} has no configurable settings"),
                ));
            }
            if let Some(stripe_id) = stripe_id.as_deref() {
                let independent =
                    candidate
                        .settings
                        .stripe_topology
                        .as_ref()
                        .is_some_and(|topology| {
                            topology.layout == crate::settings::StripeLayout::Independent
                        });
                if !independent {
                    return Err(ApiError::new(
                        axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                        "a stripe target is only available in independent layout",
                    ));
                }
                if !candidate
                    .outputs
                    .iter()
                    .any(|output| output.stripe_id == stripe_id)
                {
                    return Err(ApiError::new(
                        axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                        format!("unknown stripe: {stripe_id}"),
                    ));
                }
            }
            let allow_partial = values.is_none();
            let provided = values.unwrap_or_default();
            let normalized = definition
                .normalize_settings(&provided, allow_partial)
                .map_err(|error| {
                    ApiError::new(
                        axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                        error.to_string(),
                    )
                })?;
            let normalized: Map<String, Value> = normalized.into_iter().collect();
            if let Some(stripe_id) = stripe_id {
                candidate
                    .settings
                    .animation_settings
                    .entry(stripe_id)
                    .or_default()
                    .insert(animation_name, normalized);
            } else {
                candidate
                    .settings
                    .animation_settings
                    .entry("shared".to_owned())
                    .or_default()
                    .insert(animation_name.clone(), normalized);
                for (scope, animations) in &mut candidate.settings.animation_settings {
                    if scope != "shared" {
                        animations.remove(&animation_name);
                    }
                }
                candidate
                    .settings
                    .animation_settings
                    .retain(|scope, animations| !animations.is_empty() || scope == "shared");
            }
            persisted_categories = Some(vec![(
                "animation_settings",
                serde_json::to_value(&candidate.settings.animation_settings).map_err(|error| {
                    ApiError::new(
                        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                        format!("could not encode animation settings: {error}"),
                    )
                })?,
            )]);
        }
        ControlCommand::SetTransitionSettings { mut settings } => {
            settings
                .extra
                .clone_from(&candidate.settings.transition_settings.extra);
            candidate.settings.transition_settings = settings.clone();
            persisted_categories = Some(vec![(
                "transition_settings",
                serde_json::to_value(&settings).map_err(|error| {
                    ApiError::new(
                        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                        format!("could not encode transition settings: {error}"),
                    )
                })?,
            )]);
        }
        ControlCommand::SetSequencerSettings {
            switch_quantization,
            bars_per_phrase,
        } => {
            candidate.settings.switch_quantization = switch_quantization;
            candidate.settings.bars_per_phrase = bars_per_phrase;
            persisted_categories = Some(vec![
                ("switch_quantization", json!(switch_quantization)),
                ("bars_per_phrase", json!(bars_per_phrase)),
            ]);
        }
        ControlCommand::SetAudioSyncOffset { offset_ms } => {
            if !offset_ms.is_finite() || !(-500.0..=500.0).contains(&offset_ms) {
                return Err(ApiError::new(
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    "audio sync offset must be between -500 and 500 ms",
                ));
            }
            let route = audio::sync_route(
                model.audio_status.as_ref(),
                &model.active_audio_source,
                &model.settings,
            );
            candidate
                .settings
                .audio_sync_offsets_ms
                .insert(route, offset_ms);
            persisted_categories = Some(vec![(
                "audio_sync_offsets_ms",
                serde_json::to_value(&candidate.settings.audio_sync_offsets_ms).map_err(
                    |error| {
                        ApiError::new(
                            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                            format!("could not encode audio synchronization offsets: {error}"),
                        )
                    },
                )?,
            )]);
        }
        ControlCommand::CreateSequence { sequence } => {
            let sequences = &mut candidate.settings.animation_sequences;
            if sequences.iter().any(|item| item.id == sequence.id) {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    format!("sequence already exists: {}", sequence.id),
                ));
            }
            if sequences.len() >= MAX_SAVED_ANIMATION_SEQUENCES {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    format!("at most {MAX_SAVED_ANIMATION_SEQUENCES} sequences may be saved"),
                ));
            }
            sequences.push(sequence);
            persisted_categories = Some(sequence_categories(&candidate)?);
        }
        ControlCommand::UpdateSequence {
            sequence_id,
            sequence,
        } => {
            let Some(item) = candidate
                .settings
                .animation_sequences
                .iter_mut()
                .find(|item| item.id == sequence_id)
            else {
                return Err(ApiError::new(
                    axum::http::StatusCode::NOT_FOUND,
                    format!("unknown sequence: {sequence_id}"),
                ));
            };
            *item = sequence;
            persisted_categories = Some(sequence_categories(&candidate)?);
        }
        ControlCommand::DeleteSequence { sequence_id } => {
            let sequences = &mut candidate.settings.animation_sequences;
            let original_len = sequences.len();
            sequences.retain(|item| item.id != sequence_id);
            if sequences.len() == original_len {
                return Err(ApiError::new(
                    axum::http::StatusCode::NOT_FOUND,
                    format!("unknown sequence: {sequence_id}"),
                ));
            }
            if candidate.settings.active_sequence_id.as_deref() == Some(sequence_id.as_str()) {
                candidate.settings.active_sequence_id = None;
            }
            persisted_categories = Some(sequence_categories(&candidate)?);
        }
        ControlCommand::ActivateSequence { sequence_id } => {
            if sequence_id.as_ref().is_some_and(|id| {
                !candidate
                    .settings
                    .animation_sequences
                    .iter()
                    .any(|item| &item.id == id)
            }) {
                return Err(ApiError::new(
                    axum::http::StatusCode::NOT_FOUND,
                    format!(
                        "unknown sequence: {}",
                        sequence_id.as_deref().unwrap_or_default()
                    ),
                ));
            }
            candidate.settings.active_sequence_id = sequence_id;
            persisted_categories = Some(sequence_categories(&candidate)?);
        }
        ControlCommand::SetStripeTopology { .. } => {
            unreachable!("topology commands are applied transactionally before scene commands")
        }
        ControlCommand::TestStripe { .. } => {
            unreachable!("stripe test commands are handled transactionally before scene commands")
        }
        ControlCommand::SelectAudioDevice { .. }
        | ControlCommand::ApplyAudioSettings { .. }
        | ControlCommand::SetAudioSource { .. }
        | ControlCommand::StartAudioCalibration { .. }
        | ControlCommand::BeginAudioCalibrationMusic { .. }
        | ControlCommand::FinishAudioCalibration { .. }
        | ControlCommand::SetIdentity { .. } => {
            unreachable!("settings commands are handled before scene commands")
        }
    }

    if remember_shared_playback && candidate.settings.restore_last_state {
        remember_playback_from_current_output(&mut candidate);
        let remembered =
            serde_json::to_value(&candidate.settings.remembered_playback).map_err(|error| {
                ApiError::new(
                    axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                    format!("could not encode remembered playback: {error}"),
                )
            })?;
        match &mut persisted_categories {
            Some(categories) => categories.push(("remembered_playback", remembered)),
            None => persisted_categories = Some(vec![("remembered_playback", remembered)]),
        }
    }

    if let Some(categories) = &persisted_categories {
        settings_store
            .validate_categories(
                categories
                    .iter()
                    .map(|(category, value)| (category.to_string(), value.clone())),
            )
            .map_err(|error| {
                ApiError::new(
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    error.to_string(),
                )
            })?;
    }

    let revision = model.revision.saturating_add(1);
    candidate.revision = revision;
    let update = candidate.render_scene_with_transition(revision, transition_requested);
    let response = client
        .update_scene(update.clone())
        .await
        .map_err(engine_api_error)?;
    match response {
        ResponseResult::SceneUpdated {
            revision: received, ..
        } if received == revision => {
            if let Some(categories) = persisted_categories {
                if let Err(error) = settings_store.replace_categories(
                    categories
                        .into_iter()
                        .map(|(category, value)| (category.to_owned(), value)),
                ) {
                    let rollback_revision = revision.saturating_add(1);
                    let rollback = model.render_scene(rollback_revision);
                    let rollback_result = client.update_scene(rollback.clone()).await;
                    model.revision = rollback_revision;
                    return match rollback_result {
                        Ok(ResponseResult::SceneUpdated {
                            revision: received, ..
                        }) if received == rollback_revision => {
                            desired_state.send_replace(EngineDesiredState::for_model(
                                model,
                                model.config.engine_config(),
                                rollback,
                            ));
                            Err(ApiError::new(
                                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                                format!(
                                    "settings were not saved; the previous engine state was restored: {error}"
                                ),
                            ))
                        }
                        rollback_result => {
                            model.engine_error = Some(format!(
                                "settings persistence failed ({error}) and engine rollback was not acknowledged ({rollback_result:?})"
                            ));
                            Err(ApiError::new(
                                axum::http::StatusCode::SERVICE_UNAVAILABLE,
                                "settings could not be saved and the engine state could not be restored",
                            ))
                        }
                    };
                }
            }
            desired_state.send_replace(EngineDesiredState::for_model(
                &candidate,
                candidate.config.engine_config(),
                update,
            ));
            *model = candidate;
            Ok(())
        }
        ResponseResult::SceneUpdated { .. } => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            "engine scene acknowledgement revision did not match",
        )),
        _ => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            "engine returned an unexpected scene acknowledgement",
        )),
    }
}

async fn apply_stripe_topology(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    topology: StripeTopologySettings,
) -> Result<(), ApiError> {
    topology
        .validate()
        .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;
    if topology.outputs.is_empty() {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            "at least one physical LED output must be configured",
        ));
    }

    let mut candidate = model.clone();
    let mut candidate_config = model.config.clone();
    candidate_config
        .apply_topology(&topology)
        .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;
    candidate.outputs = remap_output_controls(&model.outputs, &candidate_config.outputs);
    candidate.settings.stripe_topology = Some(topology.clone());
    candidate.config = candidate_config;
    let persisted_topology = serde_json::to_value(&topology).map_err(|error| {
        ApiError::new(
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("could not encode stripe topology: {error}"),
        )
    })?;
    settings_store
        .validate_categories([("stripes".to_owned(), persisted_topology.clone())])
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                error.to_string(),
            )
        })?;

    let revision = model.revision.saturating_add(1);
    candidate.revision = revision;
    let proposed_config = candidate.config.engine_config();
    let engine_config_changed = model.config.engine_config() != proposed_config;
    if engine_config_changed {
        let configure_result = client.configure(proposed_config.clone()).await;
        match configure_result {
            Ok(ResponseResult::Configured {
                output_count,
                total_pixels,
            }) if output_count == proposed_config.outputs.len()
                && total_pixels == proposed_config.total_pixels() => {}
            result => {
                let reason = match result {
                    Ok(ResponseResult::Configured { .. }) => {
                        "engine configure acknowledgement did not match the requested topology"
                            .to_owned()
                    }
                    Ok(other) => format!(
                        "engine returned an unexpected configure acknowledgement: {other:?}"
                    ),
                    Err(error) => format!("engine rejected the requested topology: {error}"),
                };
                return rollback_topology(
                    model,
                    client,
                    desired_state,
                    revision,
                    reason,
                    axum::http::StatusCode::BAD_GATEWAY,
                    true,
                )
                .await;
            }
        }
    }

    let update = candidate.render_scene(revision);
    match client.update_scene(update.clone()).await {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == revision => {}
        result => {
            let reason = match result {
                Ok(ResponseResult::SceneUpdated {
                    revision: received, ..
                }) => format!("engine acknowledged scene revision {received}, expected {revision}"),
                Ok(other) => {
                    format!("engine returned an unexpected scene acknowledgement: {other:?}")
                }
                Err(error) => format!("engine rejected the topology scene: {error}"),
            };
            return rollback_topology(
                model,
                client,
                desired_state,
                revision,
                reason,
                axum::http::StatusCode::BAD_GATEWAY,
                engine_config_changed,
            )
            .await;
        }
    }

    if let Err(error) = settings_store.replace_category("stripes", persisted_topology) {
        return rollback_topology(
            model,
            client,
            desired_state,
            revision,
            format!("stripe topology was not saved: {error}"),
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            engine_config_changed,
        )
        .await;
    }

    desired_state.send_replace(EngineDesiredState::for_model(
        &candidate,
        proposed_config,
        update,
    ));
    *model = candidate;
    Ok(())
}

async fn rollback_topology(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
    revision: u64,
    reason: String,
    status: axum::http::StatusCode,
    restore_engine_config: bool,
) -> Result<(), ApiError> {
    let rollback_revision = revision.saturating_add(1);
    if let Err(rollback_error) = restore_engine_topology(
        model,
        client,
        desired_state,
        rollback_revision,
        restore_engine_config,
    )
    .await
    {
        let message =
            format!("{reason}; previous topology rollback was not acknowledged: {rollback_error}");
        model.engine_error = Some(message.clone());
        return Err(ApiError::new(
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            message,
        ));
    }
    Err(ApiError::new(
        status,
        format!("{reason}; the previous topology was restored"),
    ))
}

async fn run_stripe_test(
    model: &mut ControlModel,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    stripe_id: String,
    pattern: String,
    topology: Option<StripeTopologySettings>,
) -> Result<(), ApiError> {
    let color = match pattern.as_str() {
        "identify" | "white" => "#FFFFFF",
        "red" => "#FF0000",
        "green" => "#00FF00",
        "blue" => "#0000FF",
        _ => {
            return Err(ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                format!("unknown test pattern: {pattern}"),
            ));
        }
    };

    let mut candidate = model.clone();
    if let Some(topology) = topology {
        topology
            .validate()
            .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;
        if topology.outputs.is_empty() {
            return Err(ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                "at least one physical LED output must be configured",
            ));
        }
        let mut config = candidate.config.clone();
        config
            .apply_topology(&topology)
            .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;
        candidate.outputs = remap_output_controls(&candidate.outputs, &config.outputs);
        candidate.config = config;
        candidate.settings.stripe_topology = Some(topology);
    }

    if !candidate
        .config
        .outputs
        .iter()
        .any(|output| output.id == stripe_id)
    {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            format!("unknown stripe: {stripe_id}"),
        ));
    }

    for output in &mut candidate.outputs {
        output.mode = PlaybackMode::Solid;
        output.solid_color = if output.stripe_id == stripe_id {
            color.to_owned()
        } else {
            "#000000".to_owned()
        };
        output.brightness = 1.0;
        output.blackout = false;
    }
    for correction in candidate.settings.color_corrections.values_mut() {
        *correction = ColorCorrection::default();
    }

    let test_revision = model.revision.saturating_add(1);
    candidate.revision = test_revision;
    let previous_engine_config = model.config.engine_config();
    let test_engine_config = candidate.config.engine_config();
    let engine_config_changed = previous_engine_config != test_engine_config;
    if engine_config_changed {
        let result = client.configure(test_engine_config.clone()).await;
        match result {
            Ok(ResponseResult::Configured {
                output_count,
                total_pixels,
            }) if output_count == test_engine_config.outputs.len()
                && total_pixels == test_engine_config.total_pixels() => {}
            result => {
                let reason = match result {
                    Ok(other) => {
                        format!("unexpected stripe-test configure acknowledgement: {other:?}")
                    }
                    Err(error) => format!("could not configure stripe-test topology: {error}"),
                };
                return rollback_topology(
                    model,
                    client,
                    desired_state,
                    test_revision,
                    reason,
                    axum::http::StatusCode::BAD_GATEWAY,
                    true,
                )
                .await;
            }
        }
    }

    let test_scene = candidate.render_scene(test_revision);
    match client.update_scene(test_scene.clone()).await {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == test_revision => {}
        result => {
            let reason = match result {
                Ok(other) => format!("unexpected stripe-test scene acknowledgement: {other:?}"),
                Err(error) => format!("could not start stripe test: {error}"),
            };
            return rollback_topology(
                model,
                client,
                desired_state,
                test_revision,
                reason,
                axum::http::StatusCode::BAD_GATEWAY,
                engine_config_changed,
            )
            .await;
        }
    }
    desired_state.send_replace(EngineDesiredState::for_model(
        model,
        test_engine_config,
        test_scene,
    ));

    tokio::time::sleep(Duration::from_secs(3)).await;
    let restore_revision = test_revision.saturating_add(1);
    if let Err(error) = restore_engine_topology(
        model,
        client,
        desired_state,
        restore_revision,
        engine_config_changed,
    )
    .await
    {
        let message = format!(
            "stripe test ended, but the previous output state could not be restored: {error}"
        );
        model.engine_error = Some(message.clone());
        return Err(ApiError::new(
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            message,
        ));
    }
    Ok(())
}

async fn restore_engine_topology(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
    revision: u64,
    restore_engine_config: bool,
) -> Result<(), String> {
    if restore_engine_config {
        let previous_config = model.config.engine_config();
        let configure = client.configure(previous_config.clone()).await;
        match configure {
            Ok(ResponseResult::Configured {
                output_count,
                total_pixels,
            }) if output_count == previous_config.outputs.len()
                && total_pixels == previous_config.total_pixels() => {}
            result => {
                let detail = match result {
                    Ok(other) => format!("unexpected configure acknowledgement: {other:?}"),
                    Err(error) => error.to_string(),
                };
                return Err(format!(
                    "previous topology configure rollback was not acknowledged: {detail}"
                ));
            }
        }
    }
    let scene = model.render_scene(revision);
    let scene_result = client.update_scene(scene.clone()).await;
    match scene_result {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == revision => {
            model.revision = revision;
            model.engine_error = None;
            desired_state.send_replace(EngineDesiredState::for_model(
                model,
                model.config.engine_config(),
                scene,
            ));
            Ok(())
        }
        result => {
            let rollback_error = match result {
                Ok(other) => format!("unexpected rollback acknowledgement: {other:?}"),
                Err(error) => error.to_string(),
            };
            Err(format!(
                "previous scene rollback was not acknowledged: {rollback_error}"
            ))
        }
    }
}

fn remap_output_controls(
    previous: &[OutputControl],
    outputs: &[EngineOutputOptions],
) -> Vec<OutputControl> {
    outputs
        .iter()
        .map(|output| {
            previous
                .iter()
                .find(|state| state.stripe_id == output.id)
                .cloned()
                .or_else(|| previous.first().cloned())
                .unwrap_or_else(|| OutputControl {
                    stripe_id: output.id.clone(),
                    mode: PlaybackMode::Static,
                    solid_color: "#7C3AED".to_owned(),
                    animation: "rainbow_cycle".to_owned(),
                    brightness: 1.0,
                    blackout: false,
                    music_recognition_enabled: true,
                })
                .with_stripe_id(&output.id)
        })
        .collect()
}

trait WithStripeId {
    fn with_stripe_id(self, stripe_id: &str) -> Self;
}

impl WithStripeId for OutputControl {
    fn with_stripe_id(mut self, stripe_id: &str) -> Self {
        self.stripe_id = stripe_id.to_owned();
        self
    }
}

async fn expire_color_calibration(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
) {
    let expired = model
        .calibration
        .as_ref()
        .is_some_and(|session| session.last_activity.elapsed() >= COLOR_CALIBRATION_TIMEOUT);
    if !expired {
        return;
    }

    let mut candidate = model.clone();
    if let Some(session) = candidate.calibration.take() {
        candidate.settings.color_corrections = session.original_corrections;
    }
    let revision = model.revision.saturating_add(1);
    candidate.revision = revision;
    let update = candidate.render_scene(revision);
    desired_state.send_replace(EngineDesiredState::for_model(
        &candidate,
        model.config.engine_config(),
        update.clone(),
    ));

    match client.update_scene(update).await {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == revision => {
            candidate.engine_error = None;
            *model = candidate;
        }
        result => {
            let detail = match result {
                Ok(other) => format!("unexpected scene acknowledgement: {other:?}"),
                Err(error) => error.to_string(),
            };
            model.engine_error = Some(format!(
                "color calibration expired but the prior scene was not restored: {detail}"
            ));
        }
    }
}

fn sequence_categories(model: &ControlModel) -> Result<Vec<(&'static str, Value)>, ApiError> {
    let sequences = serde_json::to_value(&model.settings.animation_sequences).map_err(|error| {
        ApiError::new(
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("could not encode saved animation sequences: {error}"),
        )
    })?;
    Ok(vec![
        ("animation_sequences", sequences),
        (
            "active_sequence_id",
            model
                .settings
                .active_sequence_id
                .as_ref()
                .map_or(Value::Null, |id| json!(id)),
        ),
    ])
}

fn remember_playback_from_current_output(model: &mut ControlModel) {
    let Some(output) = model.outputs.first() else {
        return;
    };
    model.settings.remembered_playback.mode = match output.mode {
        PlaybackMode::Solid => PersistedPlaybackMode::Solid,
        PlaybackMode::Static => PersistedPlaybackMode::Static,
        PlaybackMode::Cycling => PersistedPlaybackMode::Cycling,
        PlaybackMode::Dynamic => PersistedPlaybackMode::Dynamic,
    };
    model
        .settings
        .remembered_playback
        .solid_color
        .clone_from(&output.solid_color);
    model
        .settings
        .remembered_playback
        .animation
        .clone_from(&output.animation);
    model.settings.remembered_playback.brightness = output.brightness;
    model.settings.remembered_playback.blackout = output.blackout;
}

fn startup_settings_snapshot(model: &ControlModel) -> Value {
    let remembered = &model.settings.remembered_playback;
    json!({
        "restore_last_state": model.settings.restore_last_state,
        "remembered": {
            "mode": remembered.mode,
            "solid_color": remembered.solid_color,
            "animation": remembered.animation,
            "brightness": remembered.brightness,
            "blackout": remembered.blackout,
        }
    })
}

fn application_settings_snapshot(model: &ControlModel) -> ApplicationSettingsSnapshot {
    ApplicationSettingsSnapshot {
        settings: model.settings.clone(),
        output_ids: model
            .config
            .outputs
            .iter()
            .map(|output| output.id.clone())
            .collect(),
        audio_status: model.audio_status.clone(),
        audio_source: model.audio_source.clone(),
        active_audio_source: model.active_audio_source.clone(),
        audio_calibration: model.audio_calibration.clone(),
    }
}

fn audio_calibration_value(session: &ActiveAudioCalibration) -> Value {
    let elapsed = if session.phase == "waiting_music" {
        session.duration_seconds
    } else {
        session
            .started_at
            .elapsed()
            .as_secs_f64()
            .min(session.duration_seconds)
    };
    let status = if session.error.is_some() {
        "failed"
    } else if session.result.is_some() {
        "complete"
    } else if session.phase == "waiting_music" {
        "waiting"
    } else {
        "capturing"
    };
    let result = session.result.as_ref().map(|result| {
        json!({
            "duration_seconds": result.duration_seconds,
            "samples": result.samples,
            "measured_floor": result.measured_floor,
            "measured_peak": result.measured_peak,
            "recommended_noise_floor": result.recommended_noise_floor,
            "recommended_target_level": result.recommended_target_level,
            "recommended_hardware_gain": result.recommended_hardware_gain,
            "recommended_idle_threshold_scale": result.recommended_idle_threshold_scale,
            "recommended_input_noise_floor": result.recommended_input_noise_floor,
            "measured_music_level": result.measured_music_level,
            "clipping_fraction": result.clipping_fraction,
        })
    });
    json!({
        "session_id": session.session_id,
        "phase": session.phase,
        "status": status,
        "elapsed_seconds": elapsed,
        "remaining_seconds": (session.duration_seconds - elapsed).max(0.0),
        "result": result,
        "error": session.error,
    })
}

fn fail_audio_calibration(model: &mut ControlModel, reason: &str) {
    if let Some(session) = &mut model.audio_calibration {
        if session.error.is_none() {
            session.error = Some(reason.to_owned());
            session.result = None;
        }
    }
}

fn advance_audio_calibration(model: &mut ControlModel, status: &EngineAudioStatus) {
    let Some(session) = &mut model.audio_calibration else {
        return;
    };
    if session.last_activity.elapsed() >= AUDIO_CALIBRATION_TIMEOUT {
        session.error = Some("audio calibration session expired".to_owned());
        session.result = None;
        return;
    }
    let input_matches = status.active
        && status.device.as_ref().is_some_and(|device| {
            device.name == session.device_name || device.selector == session.selector
        });
    if !input_matches {
        session.error =
            Some("Audio device changed or disconnected. Discard and measure again.".to_owned());
        session.result = None;
        return;
    }
    if session.error.is_some() || session.result.is_some() || session.phase == "waiting_music" {
        return;
    }
    if status.frame.fresh && status.frame.sequence > session.last_sequence {
        session.last_sequence = status.frame.sequence;
        session.last_activity = std::time::Instant::now();
        if session.two_step {
            let (rms, peaks, clipping) = if session.phase == "quiet" {
                (
                    &mut session.quiet_input_rms,
                    &mut session.quiet_input_peaks,
                    &mut session.quiet_clipping,
                )
            } else {
                (
                    &mut session.music_input_rms,
                    &mut session.music_input_peaks,
                    &mut session.music_clipping,
                )
            };
            rms.push(f64::from(status.processor.input_rms));
            peaks.push(f64::from(status.processor.input_peak));
            clipping.push(f64::from(status.processor.clipping_fraction));
        } else {
            session.frame_rms.push(f64::from(status.frame.rms));
            session.energy.push(f64::from(status.features.energy));
        }
    }
    if session.started_at.elapsed().as_secs_f64() < session.duration_seconds {
        return;
    }
    if session.two_step {
        if session.phase == "quiet" {
            if session.quiet_input_rms.len() < 10 || !status.frame.fresh {
                session.error =
                    Some("Not enough fresh input; check the microphone and retry.".to_owned());
                return;
            }
            session.phase = "waiting_music".to_owned();
            session.last_activity = std::time::Instant::now();
        } else {
            match microphone_calibration_result(session) {
                Ok(result) => session.result = Some(result),
                Err(error) => session.error = Some(error),
            }
        }
    } else {
        match audio_calibration_result(session) {
            Ok(result) => session.result = Some(result),
            Err(error) => session.error = Some(error),
        }
    }
}

async fn start_audio_calibration(
    model: &mut ControlModel,
    client: &EngineClient,
    session_id: String,
    requested_device: &str,
    duration_seconds: f64,
    two_step: bool,
) -> Result<(), ApiError> {
    if model.audio_calibration.is_some() {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "an audio calibration session is already active",
        ));
    }
    if !duration_seconds.is_finite() || !(3.0..=30.0).contains(&duration_seconds) {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            "duration_seconds must be between 3 and 30 seconds",
        ));
    }
    let status = model
        .audio_status
        .as_ref()
        .filter(|status| status.active)
        .ok_or_else(|| {
            ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "microphone input is unavailable",
            )
        })?;
    let current_device = status.device.as_ref().ok_or_else(|| {
        ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "microphone input is unavailable",
        )
    })?;
    let resolved =
        if current_device.selector == requested_device || current_device.name == requested_device {
            Some(current_device.clone())
        } else {
            audio_devices(client).await?.into_iter().find(|candidate| {
                candidate.selector == requested_device || candidate.name == requested_device
            })
        };
    let Some(device) = resolved.filter(|candidate| {
        candidate.name == current_device.name || candidate.selector == current_device.selector
    }) else {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "select the active microphone before calibrating",
        ));
    };
    if two_step && model.audio_source == "spotify" {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "two-step calibration requires microphone input",
        ));
    }
    let device_name = device.name;
    let selector = device.selector;
    let last_sequence = status.frame.sequence;
    let now = std::time::Instant::now();
    model.audio_calibration = Some(ActiveAudioCalibration {
        session_id,
        device_name,
        selector,
        started_at: now,
        last_activity: now,
        duration_seconds,
        two_step,
        phase: "quiet".to_owned(),
        last_sequence,
        frame_rms: Vec::new(),
        energy: Vec::new(),
        quiet_input_rms: Vec::new(),
        quiet_input_peaks: Vec::new(),
        quiet_clipping: Vec::new(),
        music_input_rms: Vec::new(),
        music_input_peaks: Vec::new(),
        music_clipping: Vec::new(),
        result: None,
        error: None,
    });
    Ok(())
}

fn begin_audio_calibration_music(
    model: &mut ControlModel,
    session_id: &str,
) -> Result<(), ApiError> {
    let status = model
        .audio_status
        .as_ref()
        .filter(|status| status.active)
        .cloned();
    let Some(session) = &mut model.audio_calibration else {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "calibration is not waiting for music",
        ));
    };
    if session.session_id != session_id
        || session.phase != "waiting_music"
        || session.error.is_some()
    {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "calibration is not waiting for music",
        ));
    }
    let Some(status) = status else {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "microphone input is unavailable; discard and measure again",
        ));
    };
    if !status.frame.fresh
        || !status.device.as_ref().is_some_and(|device| {
            device.name == session.device_name || device.selector == session.selector
        })
    {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "microphone input is unavailable; discard and measure again",
        ));
    }
    session.phase = "music".to_owned();
    session.started_at = std::time::Instant::now();
    session.last_activity = session.started_at;
    session.last_sequence = status.frame.sequence;
    Ok(())
}

struct AudioCalibrationFinish<'a> {
    session_id: &'a str,
    apply: bool,
    target_level: Option<f64>,
    noise_floor: Option<f64>,
}

async fn finish_audio_calibration(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    finish: AudioCalibrationFinish<'_>,
) -> Result<(), ApiError> {
    let AudioCalibrationFinish {
        session_id,
        apply,
        target_level,
        noise_floor,
    } = finish;
    let session = model
        .audio_calibration
        .as_ref()
        .filter(|session| session.session_id == session_id)
        .cloned()
        .ok_or_else(|| {
            ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "audio calibration session not found",
            )
        })?;
    if !apply {
        model.audio_calibration = None;
        return Ok(());
    }
    if session.error.is_some() || session.result.is_none() {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            session
                .error
                .unwrap_or_else(|| "audio calibration has no valid recommendation".to_owned()),
        ));
    }
    let status = model.audio_status.as_ref().filter(|status| {
        status.active
            && (!session.two_step || status.frame.fresh)
            && status.device.as_ref().is_some_and(|device| {
                device.name == session.device_name || device.selector == session.selector
            })
    });
    if status.is_none() {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "microphone input changed; discard and measure again",
        ));
    }
    if target_level.is_some_and(|value| !value.is_finite() || !(0.1..=0.8).contains(&value)) {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            "target_level must be between 0.1 and 0.8",
        ));
    }
    if noise_floor.is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value)) {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            "noise_floor must be between 0 and 1",
        ));
    }
    let result = session.result.expect("result checked above");
    let mut profile = model
        .settings
        .audio_profiles
        .get(&session.device_name)
        .cloned()
        .unwrap_or_default();
    profile.target_level = target_level.unwrap_or(result.recommended_target_level);
    profile.noise_floor = noise_floor.unwrap_or(result.recommended_noise_floor);
    if session.two_step {
        profile.input_noise_floor = result.recommended_input_noise_floor;
    }
    apply_audio_settings(
        model,
        settings_store,
        desired_state,
        client,
        &session.device_name,
        profile,
    )
    .await?;
    model.audio_calibration = None;
    Ok(())
}

fn audio_calibration_result(
    session: &ActiveAudioCalibration,
) -> Result<AudioCalibrationResult, String> {
    let recommendation = recommend_audio_calibration(
        &session.frame_rms,
        &session.energy,
        session.duration_seconds,
    )?;
    Ok(AudioCalibrationResult {
        duration_seconds: recommendation.duration_seconds,
        samples: recommendation.samples,
        measured_floor: recommendation.measured_floor,
        measured_peak: recommendation.measured_peak,
        recommended_noise_floor: recommendation.recommended_noise_floor,
        recommended_target_level: recommendation.recommended_target_level,
        recommended_hardware_gain: None,
        recommended_idle_threshold_scale: recommendation.recommended_idle_threshold_scale,
        recommended_input_noise_floor: 0.003,
        measured_music_level: 0.0,
        clipping_fraction: 0.0,
    })
}

fn microphone_calibration_result(
    session: &ActiveAudioCalibration,
) -> Result<AudioCalibrationResult, String> {
    let quiet = &session.quiet_input_rms;
    let music = &session.music_input_rms;
    if quiet.len() < 10 || music.len() < 10 {
        return Err("Not enough fresh input; check the microphone and retry.".to_owned());
    }
    if !quiet
        .iter()
        .chain(music.iter())
        .all(|value| value.is_finite() && (0.0..=1.0).contains(value))
    {
        return Err("Invalid microphone levels; retry calibration.".to_owned());
    }
    let floor = percentile(quiet, 95.0);
    let level = percentile(music, 70.0);
    let clipping = session
        .quiet_clipping
        .iter()
        .chain(session.music_clipping.iter())
        .copied()
        .fold(0.0, f64::max);
    if clipping > 0.0 {
        return Err(
            "Input is clipping. Lower microphone capture gain and measure again.".to_owned(),
        );
    }
    if level < 0.003 {
        return Err("Music input is too weak. Increase microphone capture gain or move the mic closer, then repeat both measurements.".to_owned());
    }
    if level < floor * 3.0 {
        return Err("Music is too close to room noise. Move the mic closer or increase music volume and retry.".to_owned());
    }
    let peak = session
        .music_input_peaks
        .iter()
        .copied()
        .fold(0.0, f64::max);
    Ok(AudioCalibrationResult {
        duration_seconds: session.duration_seconds * 2.0,
        samples: quiet.len() + music.len(),
        measured_floor: floor,
        measured_peak: peak,
        recommended_noise_floor: 0.015,
        recommended_target_level: (level * 4.0).clamp(0.24, 0.62),
        recommended_hardware_gain: None,
        recommended_idle_threshold_scale: 1.0,
        recommended_input_noise_floor: (floor * 1.1).clamp(0.00005, 0.5),
        measured_music_level: level,
        clipping_fraction: clipping,
    })
}

fn percentile(values: &[f64], percentile_value: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    if ordered.len() == 1 {
        return ordered[0];
    }
    let position = percentile_value.clamp(0.0, 100.0) / 100.0 * (ordered.len() - 1) as f64;
    let lower = position as usize;
    let upper = (lower + 1).min(ordered.len() - 1);
    ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower as f64)
}

async fn audio_devices(client: &EngineClient) -> Result<Vec<InputAudioDevice>, ApiError> {
    match client.request(RequestCommand::AudioDevices).await {
        Ok(ResponseResult::AudioDevices { devices }) => Ok(devices),
        Ok(ResponseResult::Error { code, message }) => {
            Err(engine_api_error(EngineClientError::Engine {
                code,
                message,
            }))
        }
        Ok(other) => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            format!("engine returned an unexpected audio-device response: {other:?}"),
        )),
        Err(error) => Err(engine_api_error(error)),
    }
}

fn find_audio_device(devices: &[InputAudioDevice], selector: &str) -> Option<InputAudioDevice> {
    let lowered = selector.to_lowercase();
    devices
        .iter()
        .find(|device| device.selector == selector || device.name == selector)
        .or_else(|| {
            devices
                .iter()
                .find(|device| device.name.to_lowercase().contains(&lowered))
        })
        .cloned()
}

fn encode_audio_profiles(settings: &DashboardSettings) -> Result<Value, ApiError> {
    serde_json::to_value(&settings.audio_profiles).map_err(|error| {
        ApiError::new(
            axum::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("could not encode audio tuning profiles: {error}"),
        )
    })
}

async fn set_identity_settings(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    application_name: &str,
    spotify_device_name: &str,
    spotify_device_name_helper: &SpotifyDeviceNameHelper,
) -> Result<(), ApiError> {
    for (field, value) in [
        ("application_name", application_name),
        ("spotify_device_name", spotify_device_name),
    ] {
        let length = value.chars().count();
        if length == 0 || length > 64 {
            return Err(ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                format!("{field} must contain 1 to 64 characters"),
            ));
        }
    }
    let application_name = application_name.trim();
    let spotify_device_name = spotify_device_name.trim();
    for (label, value) in [
        ("application name", application_name),
        ("Spotify player name", spotify_device_name),
    ] {
        if value.is_empty() || value.chars().any(char::is_control) {
            return Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                format!("{label} must be 1 to 64 printable characters"),
            ));
        }
    }
    let mut candidate = model.settings.clone();
    candidate.application_name = application_name.to_owned();
    candidate.spotify_device_name = spotify_device_name.to_owned();
    let categories = [
        (
            "application_name".to_owned(),
            json!(candidate.application_name),
        ),
        (
            "spotify_device_name".to_owned(),
            json!(candidate.spotify_device_name),
        ),
    ];
    settings_store
        .validate_categories(categories.clone())
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                error.to_string(),
            )
        })?;
    let spotify_restart_requested = if model.config.runtime_kind == "hardware"
        && model.settings.spotify_device_name != candidate.spotify_device_name
    {
        spotify_device_name_helper
            .apply(&candidate.spotify_device_name)
            .await?
    } else {
        false
    };
    settings_store
        .replace_categories(categories)
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("could not save identity settings: {error}"),
            )
        })?;
    model.settings = candidate;
    model.spotify_restart_requested = spotify_restart_requested;
    Ok(())
}

fn identity_settings_value(model: &ControlModel, spotify_restart_requested: bool) -> Value {
    json!({
        "application_name": model.settings.application_name,
        "spotify_device_name": model.settings.spotify_device_name,
        "spotify_restart_requested": spotify_restart_requested,
    })
}

async fn select_audio_device(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    requested_device: &str,
) -> Result<(), ApiError> {
    let devices = audio_devices(client).await?;
    let Some(device) = find_audio_device(&devices, requested_device) else {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            format!("no Rust audio input matches {requested_device:?}"),
        ));
    };
    let profile = model
        .settings
        .audio_profiles
        .get(&device.name)
        .cloned()
        .unwrap_or_default();
    let config = audio::engine_config(&profile, true);
    config
        .validate()
        .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;

    let mut candidate = model.clone();
    candidate.settings.selected_audio_device = Some(device.name.clone());
    candidate.config.audio_device = Some(device.selector.clone());
    candidate.audio_started = true;
    candidate.active_audio_source = "mic".to_owned();
    let selected = json!(device.name);
    let profiles = encode_audio_profiles(&candidate.settings)?;
    settings_store
        .validate_categories([
            ("selected_audio_device".to_owned(), selected.clone()),
            ("audio_profiles".to_owned(), profiles.clone()),
        ])
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                error.to_string(),
            )
        })?;

    restore_audio_source(audio_routing, bluetooth).await;

    match client
        .request(RequestCommand::AudioStart {
            selector: Some(device.selector.clone()),
            config,
        })
        .await
    {
        Ok(ResponseResult::AudioConfigured { active: true }) => {}
        Ok(ResponseResult::AudioConfigured { active: false }) => {
            return Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "Rust audio capture did not become active",
            ));
        }
        Ok(ResponseResult::Error { code, message }) => {
            return Err(engine_api_error(EngineClientError::Engine {
                code,
                message,
            }));
        }
        Ok(other) => {
            return Err(ApiError::new(
                axum::http::StatusCode::BAD_GATEWAY,
                format!("engine returned an unexpected audio-start acknowledgement: {other:?}"),
            ));
        }
        Err(error) => return Err(engine_api_error(error)),
    }

    let status = match client.audio_status().await {
        Ok(ResponseResult::AudioStatus { status }) if status.active => status,
        Ok(ResponseResult::AudioStatus { .. }) => {
            return rollback_audio_capture(
                model,
                client,
                "audio capture stopped before its active status could be confirmed".to_owned(),
            )
            .await;
        }
        Ok(other) => {
            return rollback_audio_capture(
                model,
                client,
                format!("engine returned an unexpected audio status: {other:?}"),
            )
            .await;
        }
        Err(error) => {
            return rollback_audio_capture(
                model,
                client,
                format!("could not confirm audio capture status: {error}"),
            )
            .await;
        }
    };
    candidate.audio_status = Some(status);

    let revision = model.revision.saturating_add(1);
    candidate.revision = revision;
    let update = candidate.render_scene(revision);
    match client.update_scene(update.clone()).await {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == revision => {}
        result => {
            let reason = format!("engine did not acknowledge the audio-device scene: {result:?}");
            return rollback_audio_scene_and_capture(
                model,
                client,
                desired_state,
                revision,
                Some(reason),
            )
            .await;
        }
    }

    if let Err(error) = settings_store.replace_categories([
        ("selected_audio_device".to_owned(), selected),
        ("audio_profiles".to_owned(), profiles),
    ]) {
        let reason = format!("audio device selection was not saved: {error}");
        return rollback_audio_scene_and_capture(
            model,
            client,
            desired_state,
            revision,
            Some(reason),
        )
        .await;
    }

    desired_state.send_replace(EngineDesiredState::for_model(
        &candidate,
        candidate.config.engine_config(),
        update,
    ));
    *model = candidate;
    Ok(())
}

async fn apply_audio_settings(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    requested_device: &str,
    mut profile: AudioTuningProfile,
) -> Result<(), ApiError> {
    let devices = audio_devices(client).await.unwrap_or_default();
    let resolved = find_audio_device(&devices, requested_device);
    let active_device = model
        .audio_status
        .as_ref()
        .filter(|status| status.active)
        .and_then(|status| status.device.as_ref());
    let device_name = resolved
        .as_ref()
        .map(|device| device.name.clone())
        .or_else(|| {
            active_device
                .filter(|device| {
                    device.selector == requested_device || device.name == requested_device
                })
                .map(|device| device.name.clone())
        })
        .unwrap_or_else(|| requested_device.to_owned());
    if let Some(previous) = model.settings.audio_profiles.get(&device_name) {
        for (key, value) in &previous.extra {
            profile
                .extra
                .entry(key.clone())
                .or_insert_with(|| value.clone());
        }
    }

    let mut candidate = model.clone();
    candidate
        .settings
        .audio_profiles
        .insert(device_name.clone(), profile.clone());
    if device_name != "bluetooth" && device_name != "spotify" {
        candidate.settings.selected_audio_device = Some(device_name.clone());
        if let Some(device) = &resolved {
            candidate.config.audio_device = Some(device.selector.clone());
        }
    }
    let profiles = encode_audio_profiles(&candidate.settings)?;
    let mut categories = vec![("audio_profiles".to_owned(), profiles.clone())];
    if let Some(selected) = &candidate.settings.selected_audio_device {
        categories.push(("selected_audio_device".to_owned(), json!(selected)));
    }
    settings_store
        .validate_categories(categories.clone())
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                error.to_string(),
            )
        })?;

    let active_matches = active_device.is_some_and(|device| device.name == device_name);
    let previous_config = model
        .settings
        .audio_profiles
        .get(&device_name)
        .cloned()
        .unwrap_or_default();
    let mut audio_reconfigured = false;
    if active_matches {
        let config = audio::engine_config(&profile, true);
        match configure_audio(client, config).await {
            Ok(()) => audio_reconfigured = true,
            Err(error) => {
                if let Err(rollback) =
                    configure_audio(client, audio::engine_config(&previous_config, true)).await
                {
                    model.engine_error = Some(format!(
                        "audio profile update failed ({}); previous audio configuration could not be restored ({})",
                        error.detail(),
                        rollback.detail(),
                    ));
                    return Err(ApiError::new(
                        axum::http::StatusCode::SERVICE_UNAVAILABLE,
                        model.engine_error.clone().unwrap_or_default(),
                    ));
                }
                return Err(error);
            }
        }
    }

    let revision = model.revision.saturating_add(1);
    candidate.revision = revision;
    let update = candidate.render_scene(revision);
    match client.update_scene(update.clone()).await {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == revision => {}
        result => {
            let reason = format!("engine did not acknowledge the audio-profile scene: {result:?}");
            if audio_reconfigured {
                return rollback_audio_profile_and_scene(
                    model,
                    client,
                    desired_state,
                    revision,
                    &device_name,
                    &previous_config,
                    reason,
                )
                .await;
            }
            return rollback_scene_only(model, client, desired_state, revision, reason).await;
        }
    }

    if let Err(error) = settings_store.replace_categories(categories) {
        let reason = format!("audio tuning profile was not saved: {error}");
        if audio_reconfigured {
            return rollback_audio_profile_and_scene(
                model,
                client,
                desired_state,
                revision,
                &device_name,
                &previous_config,
                reason,
            )
            .await;
        }
        return rollback_scene_only(model, client, desired_state, revision, reason).await;
    }

    desired_state.send_replace(EngineDesiredState::for_model(
        &candidate,
        candidate.config.engine_config(),
        update,
    ));
    *model = candidate;
    Ok(())
}

async fn set_audio_source(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    spotify: Option<&dyn SpotifyProvider>,
    source: &str,
) -> Result<(), ApiError> {
    if !matches!(
        source,
        "auto" | "off" | "demo" | "mic" | "bluetooth" | "spotify"
    ) {
        return Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            format!("invalid audio source: {source}"),
        ));
    }
    match source {
        "auto" => {
            model.audio_source = "auto".to_owned();
            audio_routing.spotify_retry_at = None;
            maintain_configured_audio_source(
                model,
                desired_state,
                client,
                audio_routing,
                bluetooth,
                spotify,
            )
            .await
        }
        "off" => {
            if model
                .outputs
                .iter()
                .any(|output| output.mode == PlaybackMode::Dynamic)
            {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "dynamic mode requires microphone input; turn dynamic mode off first",
                ));
            }
            restore_audio_source(audio_routing, bluetooth).await;
            stop_audio_engine(model, client).await?;
            model.audio_source = "off".to_owned();
            model.active_audio_source = "off".to_owned();
            model.revision = model.revision.saturating_add(1);
            desired_state.send_modify(|desired| desired.audio = model.desired_audio());
            Ok(())
        }
        "mic" => {
            restore_audio_source(audio_routing, bluetooth).await;
            let selected = model
                .settings
                .selected_audio_device
                .clone()
                .or_else(|| model.config.audio_device.clone())
                .or_else(|| {
                    (model.active_audio_source == "mic")
                        .then(|| {
                            model
                                .audio_status
                                .as_ref()
                                .and_then(|status| status.device.as_ref())
                                .map(|device| device.selector.clone())
                        })
                        .flatten()
                });
            let selected = if let Some(selected) = selected {
                selected
            } else {
                let devices = audio_devices(client).await?;
                devices
                    .iter()
                    .find(|device| device.is_default)
                    .or_else(|| devices.first())
                    .cloned()
                    .map(|device| device.selector)
                    .ok_or_else(|| {
                        ApiError::new(
                            axum::http::StatusCode::CONFLICT,
                            "no Rust microphone input is available",
                        )
                    })?
            };
            select_audio_device(
                model,
                settings_store,
                desired_state,
                client,
                audio_routing,
                bluetooth,
                &selected,
            )
            .await?;
            model.audio_source = "mic".to_owned();
            model.active_audio_source = "mic".to_owned();
            model.revision = model.revision.saturating_add(1);
            desired_state.send_modify(|desired| desired.audio = model.desired_audio());
            Ok(())
        }
        "demo" => {
            restore_audio_source(audio_routing, bluetooth).await;
            let profile = model
                .settings
                .selected_audio_device
                .as_deref()
                .and_then(|device| model.settings.audio_profiles.get(device))
                .cloned()
                .unwrap_or_default();
            if let Err(error) = start_audio_capture(
                model,
                client,
                "demo",
                Some("demo".to_owned()),
                &profile,
                false,
            )
            .await
            {
                if let Err(rollback) = restore_audio_capture(model, client).await {
                    model.engine_error = Some(format!(
                        "demo source switch failed; audio capture rollback failed: {rollback}"
                    ));
                    return Err(ApiError::new(
                        axum::http::StatusCode::SERVICE_UNAVAILABLE,
                        model.engine_error.clone().unwrap_or_default(),
                    ));
                }
                return Err(error);
            }
            commit_audio_source(model, desired_state, "demo".to_owned());
            Ok(())
        }
        "bluetooth" | "spotify" => {
            let result = activate_monitor_source(
                model,
                desired_state,
                client,
                audio_routing,
                bluetooth,
                spotify,
                source,
                source,
            )
            .await;
            if result.is_err() {
                restore_audio_source(audio_routing, bluetooth).await;
            }
            result
        }
        _ => unreachable!("source variants were validated above"),
    }
}

async fn maintain_configured_audio_source(
    model: &mut ControlModel,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    spotify: Option<&dyn SpotifyProvider>,
) -> Result<(), ApiError> {
    let automatic = model.audio_source == "auto"
        && should_poll_automatic_audio_source(
            model.config.runtime_kind,
            model.config.spotify_provider_enabled,
        );
    if automatic {
        let spotify_status = spotify.map(SpotifyProvider::status);
        let spotify_ready = spotify_status.as_ref().is_some_and(|status| {
            status.configured && status.connected && status.logged_in && status.is_active
        });
        if !spotify_ready {
            audio_routing.spotify_retry_at = None;
        }
        let spotify_retry_ready =
            automatic_spotify_retry_ready(Instant::now(), audio_routing.spotify_retry_at);
        let bluetooth_status = bluetooth.map(BluetoothService::status);
        let candidates = automatic_audio_source_candidates(
            spotify_ready,
            spotify_retry_ready,
            bluetooth.is_some_and(BluetoothService::enabled)
                && bluetooth_status
                    .as_ref()
                    .is_some_and(|status| status.streaming() && status.input_source.is_some()),
        );
        let mut last_error = None;
        for source in candidates {
            let result = if source == "mic" {
                activate_microphone_capture(
                    model,
                    desired_state,
                    client,
                    audio_routing,
                    bluetooth,
                    None,
                    "auto",
                )
                .await
            } else {
                activate_monitor_source(
                    model,
                    desired_state,
                    client,
                    audio_routing,
                    bluetooth,
                    spotify,
                    source,
                    "auto",
                )
                .await
            };
            match result {
                Ok(()) => {
                    if source == "spotify" {
                        audio_routing.spotify_retry_at = None;
                    }
                    return Ok(());
                }
                Err(error) => {
                    if source == "spotify" {
                        audio_routing.spotify_retry_at =
                            Some(Instant::now() + AUTOMATIC_SPOTIFY_RETRY);
                    }
                    last_error = Some(error);
                }
            }
        }
        restore_audio_source(audio_routing, bluetooth).await;
        return Err(last_error.unwrap_or_else(|| {
            ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "no audio source is available",
            )
        }));
    }

    match model.audio_source.as_str() {
        "auto" => {
            activate_microphone_capture(
                model,
                desired_state,
                client,
                audio_routing,
                bluetooth,
                None,
                "auto",
            )
            .await
        }
        "bluetooth" => match bluetooth.map(BluetoothService::status) {
            Some(status)
                if bluetooth.is_some_and(BluetoothService::enabled)
                    && status.streaming()
                    && status.input_source.is_some() =>
            {
                activate_monitor_source(
                    model,
                    desired_state,
                    client,
                    audio_routing,
                    bluetooth,
                    spotify,
                    "bluetooth",
                    "bluetooth",
                )
                .await
            }
            _ => {
                deactivate_unavailable_source(
                    model,
                    desired_state,
                    client,
                    audio_routing,
                    bluetooth,
                    "bluetooth",
                    "Bluetooth is connected but its PipeWire input is not ready.",
                )
                .await
            }
        },
        "spotify" => {
            let status = spotify.map(SpotifyProvider::status);
            if status
                .as_ref()
                .is_some_and(|status| status.configured && status.connected && status.logged_in)
            {
                activate_monitor_source(
                    model,
                    desired_state,
                    client,
                    audio_routing,
                    bluetooth,
                    spotify,
                    "spotify",
                    "spotify",
                )
                .await
            } else {
                deactivate_unavailable_source(
                    model,
                    desired_state,
                    client,
                    audio_routing,
                    bluetooth,
                    "spotify",
                    "Spotify Soloist is not connected and paired.",
                )
                .await
            }
        }
        _ => Ok(()),
    }
}

fn automatic_audio_source_candidates(
    spotify_ready: bool,
    spotify_retry_ready: bool,
    bluetooth_ready: bool,
) -> Vec<&'static str> {
    let mut candidates = Vec::with_capacity(3);
    if spotify_ready && spotify_retry_ready {
        candidates.push("spotify");
    }
    if bluetooth_ready {
        candidates.push("bluetooth");
    }
    candidates.push("mic");
    candidates
}

fn should_poll_automatic_audio_source(runtime_kind: &str, spotify_provider_enabled: bool) -> bool {
    runtime_kind == "hardware" || spotify_provider_enabled
}

fn automatic_spotify_retry_ready(now: Instant, retry_at: Option<Instant>) -> bool {
    retry_at.is_none_or(|retry_at| now >= retry_at)
}

async fn activate_monitor_source(
    model: &mut ControlModel,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    spotify: Option<&dyn SpotifyProvider>,
    source: &str,
    configured_source: &str,
) -> Result<(), ApiError> {
    let connected_bluetooth_address = bluetooth
        .map(|service| service.status())
        .and_then(|status| status.connected_device.map(|device| device.address));
    let spotify_active = spotify.map(|provider| provider.status().is_active);
    if source == model.active_audio_source
        && model
            .audio_status
            .as_ref()
            .is_some_and(|status| status.active)
    {
        let same_bluetooth = source != "bluetooth"
            || audio_routing.bluetooth_address.as_deref() == connected_bluetooth_address.as_deref();
        let same_spotify_activity = source != "spotify"
            || spotify_active.is_none_or(|active| audio_routing.spotify_active == Some(active));
        if same_bluetooth && same_spotify_activity {
            return Ok(());
        }
    }
    let bluetooth = bluetooth.ok_or_else(|| {
        ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "Bluetooth and PipeWire audio routing are unavailable",
        )
    })?;
    if source == "bluetooth" && !bluetooth.enabled() {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "Bluetooth audio input is only available in hardware mode.",
        ));
    }
    let (selector, pipewire_source) = if source == "bluetooth" {
        let status = bluetooth.status();
        let device = status
            .connected_inputs
            .first()
            .or(status.connected_device.as_ref())
            .filter(|_| status.streaming())
            .ok_or_else(|| {
                ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "Connect a Bluetooth audio source to LumiStripe first.",
                )
            })?;
        let pipewire_source = status.input_source.ok_or_else(|| {
            ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "the connected Bluetooth input has no PipeWire source",
            )
        })?;
        audio_routing.bluetooth_address = Some(device.address.clone());
        (None, Some(pipewire_source))
    } else {
        let spotify_status = spotify.map(SpotifyProvider::status).ok_or_else(|| {
            ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "Spotify Soloist is unavailable",
            )
        })?;
        if !spotify_status.configured || !spotify_status.connected || !spotify_status.logged_in {
            return Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "Spotify Soloist is not connected and paired.",
            ));
        }
        let route_result = if audio_routing
            .spotify_active
            .is_some_and(|previous| previous != spotify_status.is_active)
        {
            bluetooth.refresh_spotify_route().await
        } else {
            bluetooth.ensure_spotify_route().await
        };
        route_result.map_err(bluetooth_control_error)?;
        audio_routing.spotify_active = Some(spotify_status.is_active);
        let devices = audio_devices(client).await?;
        let dedicated = devices.iter().find(|device| {
            let name = device.name.to_ascii_lowercase();
            name.contains("lumistripe spotify") || name.contains("lumistripe_spotify")
        });
        if let Some(device) = dedicated {
            restore_audio_source(audio_routing, Some(bluetooth)).await;
            (Some(device.selector.clone()), None)
        } else {
            (None, Some(SPOTIFY_MONITOR_SOURCE.to_owned()))
        }
    };
    if let Some(source) = pipewire_source {
        override_audio_source(audio_routing, bluetooth, &source).await?;
    }
    let profile = model
        .settings
        .audio_profiles
        .get(source)
        .cloned()
        .unwrap_or_default();
    let result = start_audio_capture(model, client, source, selector, &profile, false).await;
    if result.is_ok() {
        model.audio_started = true;
        commit_audio_source(model, desired_state, configured_source.to_owned());
    } else {
        restore_audio_source(audio_routing, Some(bluetooth)).await;
        if let Err(error) = restore_audio_capture(model, client).await {
            model.engine_error = Some(format!(
                "audio source switch failed; capture rollback failed: {error}"
            ));
            return Err(ApiError::new(
                axum::http::StatusCode::SERVICE_UNAVAILABLE,
                model.engine_error.clone().unwrap_or_default(),
            ));
        }
    }
    result
}

async fn activate_microphone_capture(
    model: &mut ControlModel,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    requested_selector: Option<String>,
    configured_source: &str,
) -> Result<(), ApiError> {
    restore_audio_source(audio_routing, bluetooth).await;
    if model.active_audio_source == "mic"
        && model
            .audio_status
            .as_ref()
            .is_some_and(|status| status.active)
        && requested_selector.as_ref().is_none_or(|selector| {
            model
                .audio_status
                .as_ref()
                .and_then(|status| status.device.as_ref())
                .is_some_and(|device| device.selector == *selector)
        })
    {
        return Ok(());
    }
    let devices = audio_devices(client).await?;
    let requested = requested_selector
        .or_else(|| model.settings.selected_audio_device.clone())
        .or_else(|| model.config.audio_device.clone());
    let selected = requested
        .as_deref()
        .and_then(|selector| find_audio_device(&devices, selector))
        .or_else(|| {
            requested
                .is_none()
                .then(|| {
                    devices
                        .iter()
                        .find(|device| device.is_default)
                        .or(devices.first())
                })
                .flatten()
                .cloned()
        })
        .ok_or_else(|| {
            ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "no Rust microphone input is available",
            )
        })?;
    let profile = model
        .settings
        .audio_profiles
        .get(&selected.name)
        .cloned()
        .unwrap_or_default();
    if let Err(error) = start_audio_capture(
        model,
        client,
        "mic",
        Some(selected.selector),
        &profile,
        true,
    )
    .await
    {
        if let Err(rollback) = restore_audio_capture(model, client).await {
            model.engine_error = Some(format!(
                "microphone switch failed; capture rollback failed: {rollback}"
            ));
            return Err(ApiError::new(
                axum::http::StatusCode::SERVICE_UNAVAILABLE,
                model.engine_error.clone().unwrap_or_default(),
            ));
        }
        return Err(error);
    }
    model.audio_started = true;
    commit_audio_source(model, desired_state, configured_source.to_owned());
    Ok(())
}

async fn start_audio_capture(
    model: &mut ControlModel,
    client: &EngineClient,
    source: &str,
    selector: Option<String>,
    profile: &AudioTuningProfile,
    microphone: bool,
) -> Result<(), ApiError> {
    let config = audio::engine_config(profile, microphone);
    config
        .validate()
        .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;
    match client
        .request(RequestCommand::AudioStart { selector, config })
        .await
    {
        Ok(ResponseResult::AudioConfigured { active: true }) => {}
        Ok(ResponseResult::AudioConfigured { active: false }) => {
            return Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                format!("Rust {source} audio capture did not become active"),
            ));
        }
        Ok(ResponseResult::Error { code, message }) => {
            return Err(engine_api_error(EngineClientError::Engine {
                code,
                message,
            }));
        }
        Ok(other) => {
            return Err(ApiError::new(
                axum::http::StatusCode::BAD_GATEWAY,
                format!("engine returned an unexpected audio-start acknowledgement: {other:?}"),
            ));
        }
        Err(error) => return Err(engine_api_error(error)),
    }
    match client.audio_status().await {
        Ok(ResponseResult::AudioStatus { status }) if status.active => {
            model.audio_status = Some(status);
            model.active_audio_source = source.to_owned();
            model.audio_started = true;
            Ok(())
        }
        Ok(ResponseResult::AudioStatus { .. }) => Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            format!("Rust {source} audio capture stopped before activation completed"),
        )),
        Ok(other) => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            format!("engine returned an unexpected audio status: {other:?}"),
        )),
        Err(error) => Err(engine_api_error(error)),
    }
}

fn commit_audio_source(
    model: &mut ControlModel,
    desired_state: &watch::Sender<EngineDesiredState>,
    configured_source: String,
) {
    model.audio_source = configured_source;
    model.revision = model.revision.saturating_add(1);
    let audio = model.desired_audio();
    desired_state.send_modify(|desired| desired.audio = audio);
}

async fn deactivate_unavailable_source(
    model: &mut ControlModel,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
    audio_routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
    source: &str,
    error: &str,
) -> Result<(), ApiError> {
    if model
        .audio_status
        .as_ref()
        .is_some_and(|status| status.active)
    {
        stop_audio_engine(model, client).await?;
    }
    restore_audio_source(audio_routing, bluetooth).await;
    model.active_audio_source = source.to_owned();
    model.audio_started = false;
    model.set_audio_status(Err(error.to_owned()));
    desired_state.send_modify(|desired| desired.audio = model.desired_audio());
    Ok(())
}

async fn override_audio_source(
    routing: &mut AudioSourceRouting,
    bluetooth: &BluetoothService,
    source: &str,
) -> Result<(), ApiError> {
    let captured_source = routing.original_default_source.is_none();
    if captured_source {
        routing.original_default_source = Some(bluetooth.default_source().await);
    }
    if let Err(error) = bluetooth.set_default_source(source.to_owned()).await {
        if captured_source {
            routing.original_default_source = None;
        }
        return Err(bluetooth_control_error(error));
    }
    routing.selected_pipewire_source = Some(source.to_owned());
    Ok(())
}

async fn restore_audio_source(
    routing: &mut AudioSourceRouting,
    bluetooth: Option<&BluetoothService>,
) {
    if let (Some(bluetooth), Some(previous_source)) =
        (bluetooth, routing.original_default_source.take())
    {
        bluetooth.restore_default_source(previous_source).await;
    }
    routing.selected_pipewire_source = None;
    routing.bluetooth_address = None;
}

fn bluetooth_control_error(error: BluetoothError) -> ApiError {
    ApiError::new(axum::http::StatusCode::CONFLICT, error.message)
}

async fn stop_audio_engine(
    model: &mut ControlModel,
    client: &EngineClient,
) -> Result<(), ApiError> {
    match client.request(RequestCommand::AudioStop).await {
        Ok(ResponseResult::AudioStopped) => {
            model.audio_started = false;
            model.audio_status = match client.audio_status().await {
                Ok(ResponseResult::AudioStatus { status }) => Some(status),
                _ => Some(EngineAudioStatus::default()),
            };
            Ok(())
        }
        Ok(ResponseResult::Error { code, message }) => {
            Err(engine_api_error(EngineClientError::Engine {
                code,
                message,
            }))
        }
        Ok(other) => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            format!("engine returned an unexpected audio-stop acknowledgement: {other:?}"),
        )),
        Err(error) => Err(engine_api_error(error)),
    }
}

async fn reconcile_demo_audio_status(
    source: &str,
    snapshot: Result<EngineAudioStatus, String>,
    client: &EngineClient,
) -> Result<EngineAudioStatus, String> {
    if source != "demo" || snapshot.as_ref().is_ok_and(|status| status.active) {
        return snapshot;
    }
    if let Ok(ResponseResult::AudioStatus { status }) = client.audio_status().await {
        if status.active {
            return Ok(status);
        }
    }
    snapshot
}

async fn rollback_audio_capture(
    model: &mut ControlModel,
    client: &EngineClient,
    reason: String,
) -> Result<(), ApiError> {
    if let Err(error) = restore_audio_capture(model, client).await {
        model.engine_error = Some(format!("{reason}; audio rollback failed: {error}"));
        return Err(ApiError::new(
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            model.engine_error.clone().unwrap_or_default(),
        ));
    }
    Err(ApiError::new(axum::http::StatusCode::BAD_GATEWAY, reason))
}

async fn rollback_audio_scene_and_capture(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
    revision: u64,
    reason: Option<String>,
) -> Result<(), ApiError> {
    let mut failures = Vec::new();
    if let Err(error) = restore_audio_capture(model, client).await {
        failures.push(format!("audio capture rollback failed: {error}"));
    }
    if let Err(error) = restore_scene(model, client, desired_state, revision).await {
        failures.push(format!("scene rollback failed: {error}"));
    }
    if !failures.is_empty() {
        model.engine_error = Some(failures.join("; "));
        return Err(ApiError::new(
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            format!("{}; {}", reason.unwrap_or_default(), failures.join("; ")),
        ));
    }
    Err(ApiError::new(
        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
        reason.unwrap_or_else(|| "audio device selection was rolled back".to_owned()),
    ))
}

async fn rollback_audio_profile_and_scene(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
    revision: u64,
    device_name: &str,
    previous_profile: &AudioTuningProfile,
    reason: String,
) -> Result<(), ApiError> {
    let config = audio::engine_config(previous_profile, true);
    let audio_result = client
        .request(RequestCommand::AudioConfigure { config })
        .await;
    let audio_rollback = matches!(
        audio_result,
        Ok(ResponseResult::AudioConfigured { active: true })
    );
    let scene_rollback = restore_scene(model, client, desired_state, revision).await;
    if !audio_rollback || scene_rollback.is_err() {
        let audio_error = if audio_rollback {
            String::new()
        } else {
            format!("; audio profile rollback failed: {audio_result:?}")
        };
        let scene_error = scene_rollback
            .err()
            .map_or_else(String::new, |error| format!("; {error}"));
        model.engine_error = Some(format!("{reason}{audio_error}{scene_error}"));
        return Err(ApiError::new(
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            model.engine_error.clone().unwrap_or_default(),
        ));
    }
    Err(ApiError::new(
        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
        format!("{reason}; previous profile for {device_name:?} was restored"),
    ))
}

async fn rollback_scene_only(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
    revision: u64,
    reason: String,
) -> Result<(), ApiError> {
    restore_scene(model, client, desired_state, revision)
        .await
        .map_err(|error| {
            model.engine_error = Some(format!("{reason}; {error}"));
            ApiError::new(
                axum::http::StatusCode::SERVICE_UNAVAILABLE,
                model.engine_error.clone().unwrap_or_default(),
            )
        })?;
    Err(ApiError::new(
        axum::http::StatusCode::INTERNAL_SERVER_ERROR,
        reason,
    ))
}

async fn restore_scene(
    model: &mut ControlModel,
    client: &EngineClient,
    desired_state: &watch::Sender<EngineDesiredState>,
    revision: u64,
) -> Result<(), String> {
    let rollback_revision = revision.saturating_add(1);
    let rollback = model.render_scene(rollback_revision);
    match client.update_scene(rollback.clone()).await {
        Ok(ResponseResult::SceneUpdated {
            revision: received, ..
        }) if received == rollback_revision => {
            model.revision = rollback_revision;
            desired_state.send_replace(EngineDesiredState::for_model(
                model,
                model.config.engine_config(),
                rollback,
            ));
            Ok(())
        }
        result => Err(format!("previous scene was not restored: {result:?}")),
    }
}

async fn restore_audio_capture(
    model: &mut ControlModel,
    client: &EngineClient,
) -> Result<(), String> {
    let previous = model
        .audio_status
        .as_ref()
        .filter(|status| status.active)
        .and_then(|status| status.device.as_ref());
    let source = model.active_audio_source.as_str();
    if source == "off" || (previous.is_none() && !model.audio_started) {
        return match client.request(RequestCommand::AudioStop).await {
            Ok(ResponseResult::AudioStopped) => {
                model.audio_started = false;
                model.audio_status = Some(EngineAudioStatus::default());
                Ok(())
            }
            result => Err(format!("engine did not acknowledge audio stop: {result:?}")),
        };
    }
    let profile_name = match source {
        "bluetooth" | "spotify" => Some(source),
        _ => previous
            .map(|device| device.name.as_str())
            .or(model.settings.selected_audio_device.as_deref()),
    };
    let profile = profile_name
        .and_then(|name| model.settings.audio_profiles.get(name))
        .cloned()
        .unwrap_or_default();
    let demo = source == "demo";
    let microphone = matches!(source, "mic" | "auto");
    let selector = if demo {
        Some("demo".to_owned())
    } else {
        previous
            .map(|device| device.selector.clone())
            .or_else(|| model.config.audio_device.clone())
    };
    let config = audio::engine_config(&profile, microphone);
    match client
        .request(RequestCommand::AudioStart { selector, config })
        .await
    {
        Ok(ResponseResult::AudioConfigured { active: true }) => match client.audio_status().await {
            Ok(ResponseResult::AudioStatus { status }) if status.active => {
                model.audio_status = Some(status);
                model.audio_started = true;
                Ok(())
            }
            other => Err(format!(
                "engine did not restore the previous audio status: {other:?}"
            )),
        },
        result => Err(format!(
            "engine did not acknowledge previous audio input: {result:?}"
        )),
    }
}

async fn configure_audio(
    client: &EngineClient,
    config: lumistripe_engine_audio::AudioConfig,
) -> Result<(), ApiError> {
    config
        .validate()
        .map_err(|error| ApiError::new(axum::http::StatusCode::UNPROCESSABLE_ENTITY, error))?;
    match client
        .request(RequestCommand::AudioConfigure { config })
        .await
    {
        Ok(ResponseResult::AudioConfigured { active: true }) => Ok(()),
        Ok(ResponseResult::AudioConfigured { active: false }) => Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "Rust audio capture stopped while applying its configuration",
        )),
        Ok(ResponseResult::Error { code, message }) => {
            Err(engine_api_error(EngineClientError::Engine {
                code,
                message,
            }))
        }
        Ok(other) => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            format!("engine returned an unexpected audio configuration acknowledgement: {other:?}"),
        )),
        Err(error) => Err(engine_api_error(error)),
    }
}

async fn ensure_audio_active(
    model: &mut ControlModel,
    client: &EngineClient,
) -> Result<(), ApiError> {
    if model.audio_source == "off" {
        return Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "dynamic mode requires microphone input; enable microphone or automatic audio first",
        ));
    }
    if model.audio_started
        || model
            .audio_status
            .as_ref()
            .is_some_and(|status| status.active)
    {
        return Ok(());
    }
    if let Ok(ResponseResult::AudioStatus { status }) = client.audio_status().await {
        if status.active {
            model.audio_status = Some(status);
            model.active_audio_source = if model.audio_source == "demo" {
                "demo"
            } else {
                "mic"
            }
            .to_owned();
            model.audio_started = true;
            return Ok(());
        }
    }
    let profile = model
        .settings
        .selected_audio_device
        .as_deref()
        .and_then(|device| model.settings.audio_profiles.get(device))
        .cloned()
        .unwrap_or_default();
    match client
        .request(RequestCommand::AudioStart {
            selector: model.config.audio_device.clone(),
            config: audio::engine_config(&profile, true),
        })
        .await
    {
        Ok(ResponseResult::AudioConfigured { active: true }) => match client.audio_status().await {
            Ok(ResponseResult::AudioStatus { status }) if status.active => {
                model.audio_status = Some(status);
                model.active_audio_source = "mic".to_owned();
                model.audio_started = true;
                Ok(())
            }
            _ => Err(ApiError::new(
                axum::http::StatusCode::CONFLICT,
                "Rust microphone audio did not become active",
            )),
        },
        Ok(ResponseResult::AudioConfigured { active: false }) => Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            "dynamic mode requires an available microphone input",
        )),
        Err(EngineClientError::Engine { message, .. }) => Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            format!("dynamic mode requires an available microphone input: {message}"),
        )),
        Err(error) => Err(engine_api_error(error)),
        _ => Err(ApiError::new(
            axum::http::StatusCode::BAD_GATEWAY,
            "engine returned an unexpected audio-start acknowledgement",
        )),
    }
}

fn engine_api_error(error: EngineClientError) -> ApiError {
    match error {
        EngineClientError::Engine { code, message } => {
            let status = match code {
                lumistripe_engine_protocol::EngineErrorCode::InvalidConfig
                | lumistripe_engine_protocol::EngineErrorCode::InvalidFrame
                | lumistripe_engine_protocol::EngineErrorCode::InvalidRender => {
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY
                }
                lumistripe_engine_protocol::EngineErrorCode::NotConfigured
                | lumistripe_engine_protocol::EngineErrorCode::ShuttingDown
                | lumistripe_engine_protocol::EngineErrorCode::OutputFailure
                | lumistripe_engine_protocol::EngineErrorCode::AudioFailure => {
                    axum::http::StatusCode::SERVICE_UNAVAILABLE
                }
                lumistripe_engine_protocol::EngineErrorCode::UnsupportedVersion => {
                    axum::http::StatusCode::BAD_GATEWAY
                }
            };
            ApiError::new(status, message)
        }
        EngineClientError::DeadlineExceeded => ApiError::new(
            axum::http::StatusCode::GATEWAY_TIMEOUT,
            "runtime command timed out",
        ),
        error => ApiError::new(
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            format!("Rust engine is unavailable: {error}"),
        ),
    }
}

fn parse_color(value: &str) -> Option<[u8; 3]> {
    if value.len() != 7 || !value.starts_with('#') {
        return None;
    }
    Some([
        u8::from_str_radix(&value[1..3], 16).ok()?,
        u8::from_str_radix(&value[3..5], 16).ok()?,
        u8::from_str_radix(&value[5..7], 16).ok()?,
    ])
}

fn validation_error(kind: &str, field: &str, message: &str, input: Value) -> ApiError {
    ApiError::validation(json!([{
        "type": kind,
        "loc": ["body", field],
        "msg": message,
        "input": input,
    }]))
}

trait ErrorContext {
    fn with_context(self, context: Value) -> Self;
}

impl ErrorContext for ApiError {
    fn with_context(self, context: Value) -> Self {
        let mut value = self.detail().clone();
        if let Some(item) = value.as_array_mut().and_then(|array| array.first_mut()) {
            item["ctx"] = context;
        }
        ApiError::validation(value)
    }
}

trait TargetsMut {
    fn targets_mut(
        &mut self,
        stripe_id: Option<&str>,
    ) -> Result<Vec<&mut crate::state::OutputControl>, ApiError>;
}

impl TargetsMut for ControlModel {
    fn targets_mut(
        &mut self,
        stripe_id: Option<&str>,
    ) -> Result<Vec<&mut crate::state::OutputControl>, ApiError> {
        if let Some(stripe_id) = stripe_id {
            if !self
                .settings
                .stripe_topology
                .as_ref()
                .is_some_and(|topology| {
                    topology.layout == crate::settings::StripeLayout::Independent
                })
            {
                return Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "a stripe target is only available in independent layout",
                ));
            }
            let index = self
                .outputs
                .iter()
                .position(|output| output.stripe_id == stripe_id)
                .ok_or_else(|| {
                    ApiError::new(
                        axum::http::StatusCode::CONFLICT,
                        format!("unknown stripe: {stripe_id}"),
                    )
                })?;
            Ok(vec![&mut self.outputs[index]])
        } else {
            Ok(self.outputs.iter_mut().collect())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        AudioCalibrationFinish, AudioSourceRouting, ControlPlane, SpotifyDeviceNameHelper,
        advance_audio_calibration, audio_calibration_value, automatic_audio_source_candidates,
        automatic_spotify_retry_ready, begin_audio_calibration_music, expire_color_calibration,
        finish_audio_calibration, maintain_configured_audio_source, microphone_calibration_result,
        restore_audio_source, set_audio_source, set_identity_settings,
        should_poll_automatic_audio_source,
    };
    use crate::bluetooth::BluetoothService;
    use crate::bluetooth_models::BluetoothDevice;
    use crate::bluetooth_process::BluetoothError;
    use crate::bluetooth_providers::{
        BlueZCommand, BlueZProvider, BlueZSnapshot, PipeWireCommand, PipeWireProvider,
    };
    use crate::settings::{AudioTuningProfile, ColorCorrection, DashboardSettings, SettingsStore};
    use crate::spotify::{
        SpotifyControlAction, SpotifyError, SpotifyPlaybackStatus, SpotifyProvider, SpotifyRepeat,
        SpotifyStatus,
    };
    use crate::state::{
        ActiveAudioCalibration, ActiveColorCalibration, AudioCalibrationResult,
        COLOR_CALIBRATION_TIMEOUT, ControlModel, EngineDesiredState, PlaybackMode,
    };
    use futures_util::future::BoxFuture;
    use lumistripe_engine_audio::InputAudioDevice;
    use lumistripe_engine_client::EngineClient;
    use lumistripe_engine_protocol::{
        EngineAudioStatus, EngineErrorCode, Request, RequestCommand, Response, ResponseResult,
    };
    use serde_json::{Value, json};
    use std::fs;
    use std::future::Future;
    use std::os::unix::fs::PermissionsExt;
    use std::pin::Pin;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::Duration;
    use tempfile::tempdir;
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixListener;
    use tokio::sync::{oneshot, watch};
    use tokio::time::{Instant, sleep, timeout};
    use tokio_util::sync::CancellationToken;

    fn spotify_name_helper_fixture(
        directory: &std::path::Path,
        helper_script: &str,
        sudo_script: &str,
        timeout: Duration,
    ) -> SpotifyDeviceNameHelper {
        let sudo_path = directory.join("fake-sudo");
        fs::write(&sudo_path, format!("#!/bin/sh\n{sudo_script}\n")).unwrap();
        fs::set_permissions(&sudo_path, fs::Permissions::from_mode(0o755)).unwrap();

        let helper_path = directory.join("installed-helper");
        fs::write(&helper_path, format!("#!/bin/sh\n{helper_script}\n")).unwrap();
        let mut permissions = fs::metadata(&helper_path).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&helper_path, permissions).unwrap();

        SpotifyDeviceNameHelper {
            sudo_path,
            helper_path,
            timeout,
        }
    }

    #[tokio::test]
    async fn hardware_identity_change_calls_restricted_spotify_name_helper_and_persists() {
        let directory = tempdir().unwrap();
        let helper = spotify_name_helper_fixture(
            directory.path(),
            "[ \"$1\" = \"spotify-device-name\" ] || exit 91\n[ \"$2\" = \"Studio player\" ] || exit 92\nexit 0",
            "[ \"$1\" = \"-n\" ] || exit 93\n[ \"$2\" = \"/usr/bin/systemctl\" ] || exit 94\n[ \"$3\" = \"restart\" ] || exit 95\n[ \"$4\" = \"--no-block\" ] || exit 96\n[ \"$5\" = \"lumistripe-spotify.service\" ] || exit 97\nexit 0",
            Duration::from_secs(1),
        );
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config, DashboardSettings::default());

        set_identity_settings(
            &mut model,
            &mut settings_store,
            "Studio lights",
            "Studio player",
            &helper,
        )
        .await
        .unwrap();

        assert!(model.spotify_restart_requested);
        assert_eq!(model.settings.spotify_device_name, "Studio player");
        assert_eq!(
            settings_store.settings().spotify_device_name,
            "Studio player"
        );
    }

    #[tokio::test]
    async fn failed_spotify_name_helper_keeps_identity_settings_unchanged() {
        let directory = tempdir().unwrap();
        let helper = spotify_name_helper_fixture(
            directory.path(),
            "exit 17",
            "exit 0",
            Duration::from_secs(1),
        );
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config, DashboardSettings::default());

        let error = set_identity_settings(
            &mut model,
            &mut settings_store,
            "Studio lights",
            "Studio player",
            &helper,
        )
        .await
        .unwrap_err();

        assert_eq!(error.status(), axum::http::StatusCode::CONFLICT);
        assert_eq!(model.settings.application_name, "LumiStripe");
        assert_eq!(model.settings.spotify_device_name, "LumiStripe");
        assert_eq!(settings_store.settings().spotify_device_name, "LumiStripe");
    }

    #[tokio::test]
    async fn software_identity_update_skips_spotify_name_helper() {
        let directory = tempdir().unwrap();
        let helper = spotify_name_helper_fixture(
            directory.path(),
            "exit 17",
            "exit 0",
            Duration::from_secs(1),
        );
        let config = test_config(directory.path().join("settings.json"));
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config, DashboardSettings::default());

        set_identity_settings(
            &mut model,
            &mut settings_store,
            "Studio lights",
            "Studio player",
            &helper,
        )
        .await
        .unwrap();

        assert!(!model.spotify_restart_requested);
        assert_eq!(model.settings.spotify_device_name, "Studio player");
    }

    #[tokio::test]
    async fn missing_spotify_name_helper_saves_identity_without_restart_request() {
        let directory = tempdir().unwrap();
        let helper = SpotifyDeviceNameHelper {
            sudo_path: directory.path().join("missing-sudo"),
            helper_path: directory.path().join("missing-helper"),
            timeout: Duration::from_secs(1),
        };
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config, DashboardSettings::default());

        set_identity_settings(
            &mut model,
            &mut settings_store,
            "Studio lights",
            "Studio player",
            &helper,
        )
        .await
        .unwrap();

        assert!(!model.spotify_restart_requested);
        assert_eq!(model.settings.spotify_device_name, "Studio player");
    }

    #[tokio::test]
    async fn unchanged_hardware_spotify_name_does_not_call_the_helper() {
        let directory = tempdir().unwrap();
        let helper = spotify_name_helper_fixture(
            directory.path(),
            "exit 17",
            "exit 0",
            Duration::from_secs(1),
        );
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config, DashboardSettings::default());

        set_identity_settings(
            &mut model,
            &mut settings_store,
            "Studio lights",
            "LumiStripe",
            &helper,
        )
        .await
        .unwrap();

        assert!(!model.spotify_restart_requested);
        assert_eq!(model.settings.spotify_device_name, "LumiStripe");
    }

    struct AudioRouteBlueZ;

    impl BlueZProvider for AudioRouteBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            Box::pin(async {
                Ok(BlueZSnapshot {
                    powered: true,
                    alias: Some("LumiStripe".to_owned()),
                    scanning: false,
                    devices: vec![BluetoothDevice {
                        address: "AA:BB:CC:DD:EE:01".to_owned(),
                        name: "Headset".to_owned(),
                        paired: true,
                        connected: true,
                        roles: vec!["input".to_owned()],
                    }],
                })
            })
        }

        fn execute(
            &self,
            _command: BlueZCommand,
        ) -> BoxFuture<'static, Result<(), BluetoothError>> {
            Box::pin(async { Ok(()) })
        }
    }

    struct HandoffBlueZ {
        connected: Arc<AtomicBool>,
    }

    impl BlueZProvider for HandoffBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            let connected = self.connected.load(Ordering::Acquire);
            Box::pin(async move {
                Ok(BlueZSnapshot {
                    powered: true,
                    alias: Some("LumiStripe".to_owned()),
                    scanning: false,
                    devices: if connected {
                        vec![BluetoothDevice {
                            address: "AA:BB:CC:DD:EE:01".to_owned(),
                            name: "Headset".to_owned(),
                            paired: true,
                            connected: true,
                            roles: vec!["input".to_owned()],
                        }]
                    } else {
                        Vec::new()
                    },
                })
            })
        }

        fn execute(
            &self,
            _command: BlueZCommand,
        ) -> BoxFuture<'static, Result<(), BluetoothError>> {
            Box::pin(async { Ok(()) })
        }
    }

    struct AudioRoutePipeWire {
        commands: Arc<Mutex<Vec<String>>>,
        fail_loopback: Arc<AtomicBool>,
    }

    impl PipeWireProvider for AudioRoutePipeWire {
        fn available(&self) -> bool {
            true
        }

        fn has_wpctl(&self) -> bool {
            false
        }

        fn execute(
            &self,
            command: PipeWireCommand,
        ) -> BoxFuture<'static, Result<String, BluetoothError>> {
            let commands = self.commands.clone();
            let fail_loopback = self.fail_loopback.clone();
            Box::pin(async move {
                let (name, output) = match command {
                    PipeWireCommand::Sources => (
                        "sources",
                        "3 bluez_input.AA_BB_CC_DD_EE_01.0 PipeWire s16le 2ch 48000Hz RUNNING"
                            .to_owned(),
                    ),
                    PipeWireCommand::Sinks => ("sinks", String::new()),
                    PipeWireCommand::SinkDetails => ("sink-details", String::new()),
                    PipeWireCommand::Info => (
                        "info",
                        "Default Sink: alsa_output.test\nDefault Source: alsa_input.usb".to_owned(),
                    ),
                    PipeWireCommand::StreamStatus => ("stream-status", String::new()),
                    PipeWireCommand::SetDefaultSource(source) => ("set-source", source),
                    PipeWireCommand::SinkInputs => ("sink-inputs", String::new()),
                    PipeWireCommand::Modules => ("modules", String::new()),
                    PipeWireCommand::SetDefaultSink(_) => ("set-sink", String::new()),
                    PipeWireCommand::MoveSinkInput { .. } => ("move-input", String::new()),
                    PipeWireCommand::LoadModule { .. } if fail_loopback.load(Ordering::Acquire) => {
                        commands
                            .lock()
                            .unwrap()
                            .push("load-module:failed".to_owned());
                        return Err(BluetoothError::new(
                            "routing_failed",
                            "simulated Spotify loopback failure",
                        ));
                    }
                    PipeWireCommand::LoadModule { .. } => ("load-module", "57".to_owned()),
                    PipeWireCommand::UnloadModule(_) => ("unload-module", String::new()),
                    PipeWireCommand::SetVolume { .. } => ("set-volume", String::new()),
                    PipeWireCommand::SetMute { .. } => ("set-mute", String::new()),
                };
                commands.lock().unwrap().push(format!("{name}:{output}"));
                Ok(output)
            })
        }
    }

    async fn mock_bluetooth_capture_engine(listener: UnixListener) {
        for _ in 0..2 {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let result = match request.command {
                RequestCommand::AudioStart { .. } => {
                    ResponseResult::AudioConfigured { active: true }
                }
                RequestCommand::AudioStatus => ResponseResult::AudioStatus {
                    status: EngineAudioStatus {
                        active: true,
                        device: Some(InputAudioDevice {
                            selector: "default".to_owned(),
                            host: "PipeWire".to_owned(),
                            name: "PipeWire default".to_owned(),
                            is_default: true,
                            sample_rate: 48_000,
                            channels: 2,
                        }),
                        ..Default::default()
                    },
                },
                other => panic!("unexpected Bluetooth capture engine request: {other:?}"),
            };
            let response = Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result,
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        }
    }

    async fn mock_automatic_microphone_engine(listener: UnixListener) {
        let microphone = InputAudioDevice {
            selector: "alsa:usb:1".to_owned(),
            host: "ALSA".to_owned(),
            name: "USB microphone".to_owned(),
            is_default: true,
            sample_rate: 48_000,
            channels: 1,
        };
        let mut request_count = 0;
        loop {
            let (stream, _) = match timeout(Duration::from_millis(500), listener.accept()).await {
                Ok(Ok(connection)) => connection,
                Ok(Err(error)) => panic!("microphone engine listener failed: {error}"),
                Err(_) => break,
            };
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let result = match request.command {
                RequestCommand::AudioDevices => ResponseResult::AudioDevices {
                    devices: vec![microphone.clone()],
                },
                RequestCommand::AudioStart { .. } => {
                    ResponseResult::AudioConfigured { active: true }
                }
                RequestCommand::AudioStatus => ResponseResult::AudioStatus {
                    status: EngineAudioStatus {
                        active: true,
                        device: Some(microphone.clone()),
                        ..Default::default()
                    },
                },
                RequestCommand::SceneUpdate { update } => ResponseResult::SceneUpdated {
                    revision: update.revision,
                    replaced: false,
                },
                other => panic!("unexpected automatic microphone request: {other:?}"),
            };
            let response = Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result,
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
            request_count += 1;
        }
        assert_eq!(
            request_count, 3,
            "unexpected microphone engine request count"
        );
    }

    async fn mock_automatic_handoff_engine(
        listener: UnixListener,
        captures: Arc<Mutex<Vec<(Option<String>, bool, f32)>>>,
    ) {
        let spotify = InputAudioDevice {
            selector: "pulse:lumistripe_spotify".to_owned(),
            host: "PulseAudio".to_owned(),
            name: "LumiStripe Spotify".to_owned(),
            is_default: false,
            sample_rate: 48_000,
            channels: 2,
        };
        let bluetooth = InputAudioDevice {
            selector: "default".to_owned(),
            host: "PipeWire".to_owned(),
            name: "PipeWire default".to_owned(),
            is_default: true,
            sample_rate: 48_000,
            channels: 2,
        };
        let microphone = InputAudioDevice {
            selector: "alsa:usb:1".to_owned(),
            host: "ALSA".to_owned(),
            name: "USB microphone".to_owned(),
            is_default: true,
            sample_rate: 48_000,
            channels: 1,
        };
        let mut active_device = None;
        for _ in 0..11 {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let result = match request.command {
                RequestCommand::AudioDevices => ResponseResult::AudioDevices {
                    devices: vec![spotify.clone(), microphone.clone()],
                },
                RequestCommand::AudioStart { selector, config } => {
                    captures.lock().unwrap().push((
                        selector.clone(),
                        config.microphone_enabled,
                        config.noise_floor,
                    ));
                    active_device = Some(match selector.as_deref() {
                        Some("pulse:lumistripe_spotify") => spotify.clone(),
                        Some("alsa:usb:1") => microphone.clone(),
                        None => bluetooth.clone(),
                        Some(other) => panic!("unexpected automatic source selector: {other}"),
                    });
                    ResponseResult::AudioConfigured { active: true }
                }
                RequestCommand::AudioStatus => ResponseResult::AudioStatus {
                    status: EngineAudioStatus {
                        active: active_device.is_some(),
                        device: active_device.clone(),
                        ..Default::default()
                    },
                },
                other => panic!("unexpected automatic handoff engine request: {other:?}"),
            };
            let response = Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result,
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        }
    }

    async fn mock_spotify_capture_engine(listener: UnixListener) {
        for _ in 0..3 {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let result = match request.command {
                RequestCommand::AudioDevices => ResponseResult::AudioDevices {
                    devices: vec![InputAudioDevice {
                        selector: "pulse:lumistripe_spotify".to_owned(),
                        host: "PulseAudio".to_owned(),
                        name: "LumiStripe Spotify".to_owned(),
                        is_default: false,
                        sample_rate: 48_000,
                        channels: 2,
                    }],
                },
                RequestCommand::AudioStart { .. } => {
                    ResponseResult::AudioConfigured { active: true }
                }
                RequestCommand::AudioStatus => ResponseResult::AudioStatus {
                    status: EngineAudioStatus {
                        active: true,
                        device: Some(InputAudioDevice {
                            selector: "pulse:lumistripe_spotify".to_owned(),
                            host: "PulseAudio".to_owned(),
                            name: "LumiStripe Spotify".to_owned(),
                            is_default: false,
                            sample_rate: 48_000,
                            channels: 2,
                        }),
                        ..Default::default()
                    },
                },
                other => panic!("unexpected Spotify capture engine request: {other:?}"),
            };
            let response = Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result,
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        }
    }

    struct ReadySpotify;

    impl SpotifyProvider for ReadySpotify {
        fn start(&self) {}

        fn stop(&self) {}

        fn status(&self) -> SpotifyStatus {
            SpotifyStatus {
                configured: true,
                connected: true,
                logged_in: true,
                is_active: true,
                device_name: Some("LumiStripe".to_owned()),
                status: SpotifyPlaybackStatus::Playing,
                track: None,
                position_ms: 0,
                duration_ms: None,
                volume: 60,
                shuffle: false,
                repeat: SpotifyRepeat::Off,
                error: None,
            }
        }

        fn control<'a>(
            &'a self,
            _action: SpotifyControlAction,
            _value: Option<Value>,
        ) -> Pin<Box<dyn Future<Output = Result<SpotifyStatus, SpotifyError>> + Send + 'a>>
        {
            let status = self.status();
            Box::pin(async move { Ok(status) })
        }
    }

    struct MutableSpotify {
        status: Arc<Mutex<SpotifyStatus>>,
    }

    impl SpotifyProvider for MutableSpotify {
        fn start(&self) {}

        fn stop(&self) {}

        fn status(&self) -> SpotifyStatus {
            self.status.lock().unwrap().clone()
        }

        fn control<'a>(
            &'a self,
            _action: SpotifyControlAction,
            _value: Option<Value>,
        ) -> Pin<Box<dyn Future<Output = Result<SpotifyStatus, SpotifyError>> + Send + 'a>>
        {
            let status = self.status();
            Box::pin(async move { Ok(status) })
        }
    }

    fn spotify_status_for_handoff(active: bool) -> SpotifyStatus {
        SpotifyStatus {
            configured: true,
            connected: true,
            logged_in: true,
            is_active: active,
            device_name: Some("LumiStripe".to_owned()),
            status: if active {
                SpotifyPlaybackStatus::Playing
            } else {
                SpotifyPlaybackStatus::Paused
            },
            track: None,
            position_ms: 0,
            duration_ms: None,
            volume: 60,
            shuffle: false,
            repeat: SpotifyRepeat::Off,
            error: None,
        }
    }

    async fn mock_topology_engine(
        listener: UnixListener,
        request_count: usize,
        fail_first_scene: bool,
    ) {
        let mut failed_scene = false;
        for _ in 0..request_count {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let result = match request.command {
                RequestCommand::Configure { config } => ResponseResult::Configured {
                    output_count: config.outputs.len(),
                    total_pixels: config.total_pixels(),
                },
                RequestCommand::SceneUpdate { .. } if fail_first_scene && !failed_scene => {
                    failed_scene = true;
                    ResponseResult::Error {
                        code: EngineErrorCode::InvalidRender,
                        message: "simulated scene rejection".to_owned(),
                    }
                }
                RequestCommand::SceneUpdate { update } => ResponseResult::SceneUpdated {
                    revision: update.revision,
                    replaced: false,
                },
                other => panic!("unexpected engine request: {other:?}"),
            };
            let response = Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result,
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        }
    }

    async fn mock_audio_settings_engine(listener: UnixListener, device: InputAudioDevice) {
        for _ in 0..3 {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let result = match request.command {
                RequestCommand::AudioDevices => ResponseResult::AudioDevices {
                    devices: vec![device.clone()],
                },
                RequestCommand::AudioConfigure { .. } => {
                    ResponseResult::AudioConfigured { active: true }
                }
                RequestCommand::SceneUpdate { update } => ResponseResult::SceneUpdated {
                    revision: update.revision,
                    replaced: false,
                },
                other => panic!("unexpected audio settings engine request: {other:?}"),
            };
            let response = Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result,
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        }
    }

    #[tokio::test]
    async fn bluetooth_audio_selection_routes_pipewire_and_restores_the_previous_source() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let engine = tokio::spawn(mock_bluetooth_capture_engine(listener));
        let commands = Arc::new(Mutex::new(Vec::new()));
        let cancellation = CancellationToken::new();
        let (bluetooth, poller) = BluetoothService::with_providers(
            true,
            false,
            Arc::new(AudioRouteBlueZ),
            Arc::new(AudioRoutePipeWire {
                commands: commands.clone(),
                fail_loopback: Arc::new(AtomicBool::new(false)),
            }),
            cancellation.clone(),
        );
        let status = bluetooth.refresh().await;
        assert!(status.streaming);

        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let client = EngineClient::new(socket, Duration::from_secs(1));
        let mut routing = AudioSourceRouting::default();

        set_audio_source(
            &mut model,
            &mut settings_store,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            None,
            "bluetooth",
        )
        .await
        .unwrap();

        assert_eq!(model.audio_source, "bluetooth");
        assert_eq!(model.active_audio_source, "bluetooth");
        assert!(model.audio_status.as_ref().unwrap().active);
        assert!(
            commands
                .lock()
                .unwrap()
                .iter()
                .any(|command| command == "set-source:bluez_input.AA_BB_CC_DD_EE_01.0")
        );

        restore_audio_source(&mut routing, Some(&bluetooth)).await;
        assert!(
            commands
                .lock()
                .unwrap()
                .iter()
                .any(|command| command == "set-source:alsa_input.usb")
        );

        engine.await.unwrap();
        cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn spotify_audio_selection_uses_the_soloist_capture_and_its_virtual_route() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let engine = tokio::spawn(mock_spotify_capture_engine(listener));
        let commands = Arc::new(Mutex::new(Vec::new()));
        let cancellation = CancellationToken::new();
        let (bluetooth, poller) = BluetoothService::with_providers(
            false,
            true,
            Arc::new(AudioRouteBlueZ),
            Arc::new(AudioRoutePipeWire {
                commands: commands.clone(),
                fail_loopback: Arc::new(AtomicBool::new(false)),
            }),
            cancellation.clone(),
        );
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let client = EngineClient::new(socket, Duration::from_secs(1));
        let mut routing = AudioSourceRouting::default();

        set_audio_source(
            &mut model,
            &mut settings_store,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&ReadySpotify),
            "spotify",
        )
        .await
        .unwrap();

        assert_eq!(model.audio_source, "spotify");
        assert_eq!(model.active_audio_source, "spotify");
        assert!(model.audio_status.as_ref().unwrap().active);
        assert!(
            commands
                .lock()
                .unwrap()
                .iter()
                .any(|command| command == "load-module:57")
        );
        assert!(
            !commands
                .lock()
                .unwrap()
                .iter()
                .any(|command| command.starts_with("set-source:"))
        );

        engine.await.unwrap();
        cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), poller)
            .await
            .expect("disabled Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn automatic_source_falls_back_to_bluetooth_after_spotify_routing_fails() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let engine = tokio::spawn(mock_bluetooth_capture_engine(listener));
        let commands = Arc::new(Mutex::new(Vec::new()));
        let fail_loopback = Arc::new(AtomicBool::new(false));
        let cancellation = CancellationToken::new();
        let (bluetooth, poller) = BluetoothService::with_providers(
            true,
            true,
            Arc::new(AudioRouteBlueZ),
            Arc::new(AudioRoutePipeWire {
                commands: commands.clone(),
                fail_loopback: fail_loopback.clone(),
            }),
            cancellation.clone(),
        );
        assert!(bluetooth.refresh().await.streaming);
        sleep(Duration::from_millis(50)).await;
        commands.lock().unwrap().clear();
        fail_loopback.store(true, Ordering::Release);

        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        assert_eq!(model.audio_source, "auto");
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let client = EngineClient::new(socket, Duration::from_secs(1));
        let mut routing = AudioSourceRouting::default();

        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&ReadySpotify),
        )
        .await
        .unwrap();

        assert_eq!(model.audio_source, "auto");
        assert_eq!(model.active_audio_source, "bluetooth");
        assert!(
            model
                .audio_status
                .as_ref()
                .is_some_and(|status| status.active)
        );
        assert!(
            routing
                .spotify_retry_at
                .is_some_and(|retry_at| retry_at > Instant::now())
        );
        assert!(
            commands
                .lock()
                .unwrap()
                .iter()
                .any(|command| command == "load-module:failed")
        );
        assert!(
            commands
                .lock()
                .unwrap()
                .iter()
                .any(|command| command == "set-source:bluez_input.AA_BB_CC_DD_EE_01.0")
        );

        let command_count = commands.lock().unwrap().len();
        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&ReadySpotify),
        )
        .await
        .unwrap();
        assert_eq!(commands.lock().unwrap().len(), command_count);

        engine.await.unwrap();
        cancellation.cancel();
        tokio::time::timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[tokio::test]
    async fn automatic_source_selects_microphone_when_spotify_and_bluetooth_are_unavailable() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let engine = tokio::spawn(mock_automatic_microphone_engine(listener));
        let cancellation = CancellationToken::new();
        let (bluetooth, poller) = BluetoothService::with_providers(
            false,
            false,
            Arc::new(AudioRouteBlueZ),
            Arc::new(AudioRoutePipeWire {
                commands: Arc::new(Mutex::new(Vec::new())),
                fail_loopback: Arc::new(AtomicBool::new(false)),
            }),
            cancellation.clone(),
        );
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let client = EngineClient::new(socket, Duration::from_secs(1));
        let mut routing = AudioSourceRouting::default();

        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            None,
        )
        .await
        .unwrap();

        assert_eq!(model.audio_source, "auto");
        assert_eq!(model.active_audio_source, "mic");
        assert_eq!(
            model
                .audio_status
                .as_ref()
                .and_then(|status| status.device.as_ref())
                .map(|device| device.name.as_str()),
            Some("USB microphone")
        );
        assert!(
            model
                .audio_status
                .as_ref()
                .is_some_and(|status| status.active)
        );

        timeout(Duration::from_secs(1), engine)
            .await
            .expect("microphone engine fixture should finish after audio setup")
            .unwrap();
        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("disabled Bluetooth poller should already be stopped")
            .unwrap();
    }

    #[tokio::test]
    async fn automatic_source_hands_off_spotify_bluetooth_microphone_and_recovers() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let captures = Arc::new(Mutex::new(Vec::new()));
        let engine = tokio::spawn(mock_automatic_handoff_engine(listener, captures.clone()));

        let connected = Arc::new(AtomicBool::new(true));
        let pipewire_commands = Arc::new(Mutex::new(Vec::new()));
        let cancellation = CancellationToken::new();
        let (bluetooth, poller) = BluetoothService::with_providers(
            true,
            true,
            Arc::new(HandoffBlueZ {
                connected: connected.clone(),
            }),
            Arc::new(AudioRoutePipeWire {
                commands: pipewire_commands.clone(),
                fail_loopback: Arc::new(AtomicBool::new(false)),
            }),
            cancellation.clone(),
        );
        assert!(bluetooth.refresh().await.streaming);
        pipewire_commands.lock().unwrap().clear();

        let spotify_status = Arc::new(Mutex::new(spotify_status_for_handoff(true)));
        let spotify = MutableSpotify {
            status: spotify_status.clone(),
        };
        let mut config = test_config(directory.path().join("settings.json"));
        config.runtime_kind = "hardware";
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        for (name, noise_floor) in [
            ("spotify", 0.11),
            ("bluetooth", 0.22),
            ("USB microphone", 0.33),
        ] {
            model.settings.audio_profiles.insert(
                name.to_owned(),
                AudioTuningProfile {
                    noise_floor,
                    ..AudioTuningProfile::default()
                },
            );
        }
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let client = EngineClient::new(socket, Duration::from_secs(1));
        let mut routing = AudioSourceRouting::default();

        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&spotify),
        )
        .await
        .unwrap();
        assert_eq!(model.audio_source, "auto");
        assert_eq!(model.active_audio_source, "spotify");
        assert_eq!(
            model
                .audio_status
                .as_ref()
                .and_then(|status| status.device.as_ref())
                .map(|device| device.name.as_str()),
            Some("LumiStripe Spotify")
        );

        *spotify_status.lock().unwrap() = spotify_status_for_handoff(false);
        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&spotify),
        )
        .await
        .unwrap();
        assert_eq!(model.active_audio_source, "bluetooth");
        assert_eq!(
            model
                .audio_status
                .as_ref()
                .and_then(|status| status.device.as_ref())
                .map(|device| device.name.as_str()),
            Some("PipeWire default")
        );

        connected.store(false, Ordering::Release);
        assert!(!bluetooth.refresh().await.streaming);
        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&spotify),
        )
        .await
        .unwrap();
        assert_eq!(model.active_audio_source, "mic");
        assert_eq!(
            model
                .audio_status
                .as_ref()
                .and_then(|status| status.device.as_ref())
                .map(|device| device.name.as_str()),
            Some("USB microphone")
        );

        *spotify_status.lock().unwrap() = spotify_status_for_handoff(true);
        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&spotify),
        )
        .await
        .unwrap();
        assert_eq!(model.active_audio_source, "spotify");

        maintain_configured_audio_source(
            &mut model,
            &desired_state,
            &client,
            &mut routing,
            Some(&bluetooth),
            Some(&spotify),
        )
        .await
        .unwrap();
        assert_eq!(
            *captures.lock().unwrap(),
            [
                (Some("pulse:lumistripe_spotify".to_owned()), false, 0.11),
                (None, false, 0.22),
                (Some("alsa:usb:1".to_owned()), true, 0.33),
                (Some("pulse:lumistripe_spotify".to_owned()), false, 0.11),
            ]
        );
        let routing_commands = pipewire_commands.lock().unwrap();
        assert!(
            routing_commands
                .iter()
                .any(|command| command == "set-source:bluez_input.AA_BB_CC_DD_EE_01.0")
        );
        assert!(
            routing_commands
                .iter()
                .any(|command| command == "set-source:alsa_input.usb")
        );
        drop(routing_commands);

        engine.await.unwrap();
        cancellation.cancel();
        timeout(Duration::from_secs(1), poller)
            .await
            .expect("Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[test]
    fn automatic_audio_source_policy_keeps_spotify_then_bluetooth_then_mic_priority() {
        assert_eq!(
            automatic_audio_source_candidates(true, true, true),
            ["spotify", "bluetooth", "mic"]
        );
        assert_eq!(
            automatic_audio_source_candidates(false, true, true),
            ["bluetooth", "mic"]
        );
        assert_eq!(
            automatic_audio_source_candidates(false, true, false),
            ["mic"]
        );
        assert_eq!(
            automatic_audio_source_candidates(true, false, true),
            ["bluetooth", "mic"]
        );
        assert_eq!(
            automatic_audio_source_candidates(true, false, false),
            ["mic"]
        );
    }

    #[test]
    fn simulation_polls_automatic_audio_only_when_spotify_is_enabled() {
        assert!(!should_poll_automatic_audio_source("simulation", false));
        assert!(should_poll_automatic_audio_source("simulation", true));
        assert!(should_poll_automatic_audio_source("hardware", false));
    }

    #[test]
    fn automatic_spotify_retry_waits_five_seconds_after_a_route_failure() {
        let now = Instant::now();
        let retry_at = now + super::AUTOMATIC_SPOTIFY_RETRY;

        assert!(automatic_spotify_retry_ready(now, None));
        assert!(!automatic_spotify_retry_ready(
            retry_at - Duration::from_millis(1),
            Some(retry_at),
        ));
        assert!(automatic_spotify_retry_ready(retry_at, Some(retry_at)));
    }

    fn active_audio_status() -> EngineAudioStatus {
        EngineAudioStatus {
            active: true,
            device: Some(InputAudioDevice {
                selector: "alsa:usb:1".to_owned(),
                host: "alsa".to_owned(),
                name: "USB mic".to_owned(),
                is_default: true,
                sample_rate: 48_000,
                channels: 1,
            }),
            ..Default::default()
        }
    }

    fn audio_calibration_session(two_step: bool) -> ActiveAudioCalibration {
        let now = std::time::Instant::now();
        ActiveAudioCalibration {
            session_id: "audio-session".to_owned(),
            device_name: "USB mic".to_owned(),
            selector: "alsa:usb:1".to_owned(),
            started_at: now - Duration::from_secs(5),
            last_activity: now,
            duration_seconds: 1.0,
            two_step,
            phase: "quiet".to_owned(),
            last_sequence: 0,
            frame_rms: Vec::new(),
            energy: Vec::new(),
            quiet_input_rms: Vec::new(),
            quiet_input_peaks: Vec::new(),
            quiet_clipping: Vec::new(),
            music_input_rms: Vec::new(),
            music_input_peaks: Vec::new(),
            music_clipping: Vec::new(),
            result: None,
            error: None,
        }
    }

    fn test_config(settings_file: std::path::PathBuf) -> crate::config::AppConfig {
        crate::config::AppConfig {
            outputs: vec![crate::config::EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: crate::settings::ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "simulation",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_provider_enabled: false,
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file,
        }
    }

    fn test_topology() -> crate::settings::StripeTopologySettings {
        let output = crate::settings::StripeOutputSettings {
            id: "main".to_owned(),
            name: "Main".to_owned(),
            pixels: 16,
            ..Default::default()
        };
        crate::settings::StripeTopologySettings {
            layout: crate::settings::StripeLayout::Continuous,
            outputs: vec![output],
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn commits_control_state_only_after_engine_acknowledges_the_scene() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: lumistripe_engine_protocol::Request =
                serde_json::from_str(line.trim()).unwrap();
            let response = lumistripe_engine_protocol::Response {
                protocol_version: lumistripe_engine_protocol::PROTOCOL_VERSION,
                request_id: request.request_id,
                result: ResponseResult::SceneUpdated {
                    revision: 2,
                    replaced: false,
                },
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        });
        let client = EngineClient::new(socket, Duration::from_secs(1));
        let config = crate::config::AppConfig {
            outputs: vec![crate::config::EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: crate::settings::ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "simulation",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_provider_enabled: false,
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let model = crate::state::ControlModel::new(
            config.clone(),
            crate::settings::DashboardSettings::default(),
        );
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let (snapshot_tx, snapshot) =
            watch::channel(Some(Ok(std::sync::Arc::new(ResponseResult::Health {
                status: lumistripe_engine_protocol::EngineStatus::Ready,
                configured: true,
                output_count: 1,
                pixel_counts: vec![8],
                frames_rendered: 0,
                last_sequence: None,
                dropped_frames: 0,
                failed_frames: 0,
                scheduler_missed_deadlines: 0,
                selected_animations: vec!["rainbow_cycle".into()],
                scene_revisions: vec![1],
                structure_event_sequences: vec![0],
                cycling_active: vec![false],
                dynamic_gate_states: vec!["calm".into()],
                selector_diagnostics: vec![],
                beat_clocks: vec![],
                sequence_playback: vec![],
                effect_schedulers: vec![],
                last_error: None,
                output_rate: 60.0,
                frame_latency_ms: None,
                queue_latency_ms: None,
                transmission_ms: None,
                frame_latency_p50_ms: None,
                frame_latency_p95_ms: None,
                frame_latency_p99_ms: None,
                audio_feature_to_led_latency_ms: None,
                audio_feature_to_led_p50_ms: None,
                audio_feature_to_led_p95_ms: None,
                audio_feature_to_led_p99_ms: None,
                sequential_skew_ms: None,
                last_successful_update_epoch_ms: None,
                render_time_ms: None,
                scene_control_revision: Some(1),
                audio_update_sequence: None,
                estimated_power_watts: None,
                applied_power_scale: None,
                output_power_watts: None,
                limiting_output_index: None,
                preview_outputs: None,
            }))));
        let (audio_tx, audio) = watch::channel(None);
        let (plane, task) = crate::control::ControlPlane::start(
            model,
            settings_store,
            desired_state,
            client,
            snapshot,
            audio,
            None,
            None,
            tokio_util::sync::CancellationToken::new(),
        );
        let state = plane
            .select_animation("comet".to_owned(), None)
            .await
            .unwrap();
        assert_eq!(state["animation"], "comet");
        assert_eq!(state["mode"], "static");
        server.await.unwrap();
        task.abort();
        drop(snapshot_tx);
        drop(audio_tx);
        let _ = json!(PlaybackMode::Dynamic);
    }

    #[tokio::test]
    async fn stripe_topology_waits_for_engine_acknowledgements_before_persisting() {
        let directory = tempdir().unwrap();
        let settings_path = directory.path().join("settings.json");
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let server = tokio::spawn(mock_topology_engine(listener, 2, false));
        let config = test_config(settings_path.clone());
        let settings_store = SettingsStore::open(&settings_path).unwrap();
        let model = ControlModel::new(config.clone(), DashboardSettings::default());
        let (desired_state, desired_receiver) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let (health_tx, health) = watch::channel(None);
        let (audio_tx, audio) = watch::channel(None);
        let cancellation = tokio_util::sync::CancellationToken::new();
        let (plane, worker) = ControlPlane::start(
            model,
            settings_store,
            desired_state,
            EngineClient::new(socket, Duration::from_secs(1)),
            health,
            audio,
            None,
            None,
            cancellation.clone(),
        );

        let state = plane.set_stripe_topology(test_topology()).await.unwrap();
        assert_eq!(state["stripe_topology"]["layout"], "continuous");
        assert_eq!(state["stripe_topology"]["outputs"][0]["pixels"], 16);
        let desired = desired_receiver.borrow();
        assert_eq!(desired.config.outputs[0].id, "main");
        assert_eq!(desired.config.outputs[0].pixel_count, 16);
        assert_eq!(desired.scene.outputs.len(), 1);
        let persisted: Value =
            serde_json::from_slice(&std::fs::read(settings_path).unwrap()).unwrap();
        assert_eq!(persisted["stripes"]["layout"], "continuous");
        assert_eq!(persisted["stripes"]["outputs"][0]["pixels"], 16);

        cancellation.cancel();
        worker.await.unwrap();
        server.await.unwrap();
        drop(health_tx);
        drop(audio_tx);
    }

    #[tokio::test]
    async fn topology_persistence_failure_restores_the_previous_engine_configuration() {
        let directory = tempdir().unwrap();
        let settings_path = directory.path().join("settings.json");
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let server = tokio::spawn(mock_topology_engine(listener, 4, false));
        let config = test_config(settings_path.clone());
        let settings_store = SettingsStore::open(&settings_path).unwrap();
        std::fs::create_dir(&settings_path).unwrap();
        let model = ControlModel::new(config.clone(), DashboardSettings::default());
        let (desired_state, desired_receiver) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let (health_tx, health) = watch::channel(None);
        let (audio_tx, audio) = watch::channel(None);
        let cancellation = tokio_util::sync::CancellationToken::new();
        let (plane, worker) = ControlPlane::start(
            model,
            settings_store,
            desired_state,
            EngineClient::new(socket, Duration::from_secs(1)),
            health,
            audio,
            None,
            None,
            cancellation.clone(),
        );

        let error = plane
            .set_stripe_topology(test_topology())
            .await
            .unwrap_err();
        assert_eq!(
            error.status(),
            axum::http::StatusCode::INTERNAL_SERVER_ERROR
        );
        assert!(
            error
                .detail()
                .as_str()
                .unwrap()
                .contains("previous topology was restored")
        );
        let desired = desired_receiver.borrow();
        assert_eq!(desired.config.outputs[0].pixel_count, 8);
        assert_eq!(desired.scene.revision, 3);
        let state = plane.state();
        assert_eq!(
            state.borrow()["stripe_topology"]["outputs"][0]["id"],
            "main"
        );

        cancellation.cancel();
        worker.await.unwrap();
        server.await.unwrap();
        drop(health_tx);
        drop(audio_tx);
    }

    #[tokio::test]
    async fn expired_color_calibration_restores_the_saved_correction_and_scene() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let server = tokio::spawn(mock_topology_engine(listener, 1, false));
        let config = test_config(directory.path().join("settings.json"));
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        let original_corrections = model.settings.color_corrections.clone();
        model.settings.color_corrections.insert(
            "main".to_owned(),
            ColorCorrection {
                red: 32,
                green: 64,
                blue: 128,
                extra: Default::default(),
            },
        );
        model.calibration = Some(ActiveColorCalibration {
            session_id: "expired-session".to_owned(),
            output_index: 0,
            pattern: "red".to_owned(),
            last_activity: std::time::Instant::now()
                - COLOR_CALIBRATION_TIMEOUT
                - Duration::from_secs(1),
            original_corrections,
        });
        let (desired_state, desired_receiver) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.render_scene(1),
            audio: None,
        });

        expire_color_calibration(
            &mut model,
            &EngineClient::new(socket, Duration::from_secs(1)),
            &desired_state,
        )
        .await;

        assert!(model.calibration.is_none());
        assert!(!model.settings.color_corrections.contains_key("main"));
        assert_eq!(model.snapshot(0)["calibration"]["active"], false);
        let desired = desired_receiver.borrow();
        assert_eq!(desired.scene.outputs[0].color_correction, [255, 255, 255]);
        drop(desired);
        server.await.unwrap();
    }

    #[test]
    fn audio_calibration_uses_engine_frames_and_reaches_a_complete_result() {
        let config = test_config(std::path::PathBuf::from("/tmp/lumistripe-audio-test.json"));
        let mut model = ControlModel::new(config, DashboardSettings::default());
        let mut status = active_audio_status();
        status.frame.fresh = true;
        status.frame.sequence = 1;
        status.frame.rms = 0.12;
        status.features.energy = 0.3;
        model.audio_status = Some(status.clone());
        let mut session = audio_calibration_session(false);
        session.started_at = std::time::Instant::now() - Duration::from_secs(2);
        model.audio_calibration = Some(session);

        advance_audio_calibration(&mut model, &status);

        let session = model.audio_calibration.as_ref().unwrap();
        assert_eq!(audio_calibration_value(session)["status"], "complete");
        let result = session.result.as_ref().unwrap();
        assert_eq!(result.samples, 1);
        assert!((result.measured_floor - 0.12).abs() < 0.000_001);
        assert!((result.recommended_noise_floor - 0.12).abs() < 0.000_001);
        assert!(session.error.is_none());
    }

    #[test]
    fn beginning_music_without_a_calibration_session_returns_python_compatible_conflict() {
        let config = test_config(std::path::PathBuf::from("/tmp/lumistripe-audio-test.json"));
        let mut model = ControlModel::new(config, DashboardSettings::default());

        let error = begin_audio_calibration_music(&mut model, "missing-session").unwrap_err();

        assert_eq!(error.status(), axum::http::StatusCode::CONFLICT);
        assert_eq!(
            error.detail(),
            &Value::String("calibration is not waiting for music".to_owned())
        );
    }

    #[test]
    fn two_step_audio_calibration_requires_usable_quiet_and_music_levels() {
        let mut session = audio_calibration_session(true);
        session.phase = "music".to_owned();
        session.quiet_input_rms = vec![0.01; 10];
        session.quiet_input_peaks = vec![0.02; 10];
        session.quiet_clipping = vec![0.0; 10];
        session.music_input_rms = vec![0.1; 10];
        session.music_input_peaks = vec![0.2; 10];
        session.music_clipping = vec![0.0; 10];

        let result = microphone_calibration_result(&session).unwrap();
        assert_eq!(result.samples, 20);
        assert_eq!(result.measured_floor, 0.01);
        assert_eq!(result.measured_music_level, 0.1);
        assert!((result.recommended_input_noise_floor - 0.011).abs() < 0.000_001);
        assert_eq!(result.recommended_target_level, 0.4);

        session.music_clipping[0] = 0.01;
        assert!(
            microphone_calibration_result(&session)
                .unwrap_err()
                .contains("clipping")
        );
    }

    #[tokio::test]
    async fn audio_calibration_save_applies_and_persists_recommendations() {
        let directory = tempdir().unwrap();
        let settings_path = directory.path().join("settings.json");
        std::fs::write(
            &settings_path,
            r#"{"version":12,"unrelated":{"keep":true}}"#,
        )
        .unwrap();
        let store = SettingsStore::open(&settings_path).unwrap();
        let device = active_audio_status().device.unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let server = tokio::spawn(mock_audio_settings_engine(listener, device));
        let config = test_config(settings_path.clone());
        let mut model = ControlModel::new(config.clone(), store.settings().clone());
        model.audio_status = Some(active_audio_status());
        model.audio_calibration = Some(ActiveAudioCalibration {
            result: Some(AudioCalibrationResult {
                duration_seconds: 6.0,
                samples: 50,
                measured_floor: 0.01,
                measured_peak: 0.4,
                recommended_noise_floor: 0.02,
                recommended_target_level: 0.5,
                recommended_hardware_gain: None,
                recommended_idle_threshold_scale: 1.3,
                recommended_input_noise_floor: 0.011,
                measured_music_level: 0.1,
                clipping_fraction: 0.0,
            }),
            ..audio_calibration_session(true)
        });
        let mut status = active_audio_status();
        status.frame.fresh = true;
        status.frame.sequence = 1;
        model.audio_status = Some(status);
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let mut settings_store = store;
        finish_audio_calibration(
            &mut model,
            &mut settings_store,
            &desired_state,
            &EngineClient::new(socket, Duration::from_secs(1)),
            AudioCalibrationFinish {
                session_id: "audio-session",
                apply: true,
                target_level: None,
                noise_floor: None,
            },
        )
        .await
        .unwrap();

        assert!(model.audio_calibration.is_none());
        assert_eq!(model.settings.audio_profiles["USB mic"].target_level, 0.5);
        assert_eq!(
            model.settings.audio_profiles["USB mic"].input_noise_floor,
            0.011
        );
        let saved: Value = serde_json::from_slice(&std::fs::read(settings_path).unwrap()).unwrap();
        assert_eq!(saved["unrelated"]["keep"], true);
        assert_eq!(saved["audio_profiles"]["USB mic"]["target_level"], 0.5);
        server.await.unwrap();
    }

    #[tokio::test]
    async fn audio_calibration_cancel_discards_the_session_without_engine_changes() {
        let directory = tempdir().unwrap();
        let settings_path = directory.path().join("settings.json");
        let mut settings_store = SettingsStore::open(&settings_path).unwrap();
        let config = test_config(settings_path);
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        model.audio_calibration = Some(audio_calibration_session(true));
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        finish_audio_calibration(
            &mut model,
            &mut settings_store,
            &desired_state,
            &EngineClient::new(
                directory.path().join("unused.sock"),
                Duration::from_millis(50),
            ),
            AudioCalibrationFinish {
                session_id: "audio-session",
                apply: false,
                target_level: None,
                noise_floor: None,
            },
        )
        .await
        .unwrap();
        assert!(model.audio_calibration.is_none());
        assert!(settings_store.settings().audio_profiles.is_empty());
    }

    #[tokio::test]
    async fn finishing_a_missing_audio_calibration_session_returns_python_compatible_conflict() {
        let directory = tempdir().unwrap();
        let settings_path = directory.path().join("settings.json");
        let mut settings_store = SettingsStore::open(&settings_path).unwrap();
        let config = test_config(settings_path);
        let mut model = ControlModel::new(config.clone(), DashboardSettings::default());
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });

        let error = finish_audio_calibration(
            &mut model,
            &mut settings_store,
            &desired_state,
            &EngineClient::new(
                directory.path().join("unused.sock"),
                Duration::from_millis(50),
            ),
            AudioCalibrationFinish {
                session_id: "missing-session",
                apply: false,
                target_level: None,
                noise_floor: None,
            },
        )
        .await
        .unwrap_err();

        assert_eq!(error.status(), axum::http::StatusCode::CONFLICT);
        assert_eq!(
            error.detail(),
            &Value::String("audio calibration session not found".to_owned())
        );
    }

    #[tokio::test]
    async fn dropping_a_waiting_command_cancels_its_engine_request() {
        let directory = tempdir().unwrap();
        let socket = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let (request_seen, request_received) = oneshot::channel();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, _write) = stream.into_split();
            let mut reader = BufReader::new(read);
            let mut line = String::new();
            reader.read_line(&mut line).await.unwrap();
            request_seen.send(()).unwrap();
            let mut trailing = [0; 1];
            let read = tokio::time::timeout(Duration::from_secs(1), reader.read(&mut trailing))
                .await
                .expect("cancelled IPC request should close its socket")
                .unwrap();
            assert_eq!(read, 0);
        });
        let client = EngineClient::new(&socket, Duration::from_secs(5));
        let config = crate::config::AppConfig {
            outputs: vec![crate::config::EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: crate::settings::ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "simulation",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_provider_enabled: false,
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let model = crate::state::ControlModel::new(
            config.clone(),
            crate::settings::DashboardSettings::default(),
        );
        let (desired_state, _) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: model.initial_scene(),
            audio: None,
        });
        let (health_tx, health) =
            watch::channel::<Option<Result<std::sync::Arc<ResponseResult>, String>>>(None);
        let (audio_tx, audio) = watch::channel::<
            Option<Result<lumistripe_engine_protocol::EngineAudioStatus, String>>,
        >(None);
        let settings_store = SettingsStore::open(&config.settings_file).unwrap();
        let cancellation = tokio_util::sync::CancellationToken::new();
        let (plane, worker) = crate::control::ControlPlane::start(
            model,
            settings_store,
            desired_state,
            client,
            health,
            audio,
            None,
            None,
            cancellation.clone(),
        );
        let caller = tokio::spawn(async move { plane.set_brightness(0.3, None).await });
        tokio::time::timeout(Duration::from_secs(1), request_received)
            .await
            .expect("worker should send the scene command")
            .unwrap();
        caller.abort();
        let _ = caller.await;
        tokio::time::timeout(Duration::from_secs(1), server)
            .await
            .expect("engine request should be cancelled promptly")
            .unwrap();
        drop(health_tx);
        drop(audio_tx);
        cancellation.cancel();
        worker.await.unwrap();
    }
}
