use crate::audio;
use crate::config::EngineOutputOptions;
use crate::error::ApiError;
use crate::settings::{
    AudioTuningProfile, ColorCorrection, DashboardSettings, MAX_SAVED_ANIMATION_SEQUENCES,
    MusicIdleSettings, PersistedPlaybackMode, SavedAnimationSequence, SettingsStore,
    StripeTopologySettings, SwitchQuantization, TransitionSettings,
};
use crate::state::{
    AUDIO_CALIBRATION_TIMEOUT, ActiveAudioCalibration, ActiveColorCalibration,
    AudioCalibrationResult, COLOR_CALIBRATION_TIMEOUT, ControlModel, EngineDesiredState,
    OutputControl, PlaybackMode,
};
use lumistripe_engine_audio::InputAudioDevice;
use lumistripe_engine_client::{EngineClient, EngineClientError};
use lumistripe_engine_protocol::{EngineAudioStatus, RequestCommand, ResponseResult};
use serde_json::{Map, Value, json};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::task::JoinHandle;
use tokio::time::{Instant, interval, timeout_at};
use tokio_util::sync::CancellationToken;

const COMMAND_CAPACITY: usize = 64;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(5);
const TOPOLOGY_COMMAND_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone)]
struct ApplicationSettingsSnapshot {
    settings: DashboardSettings,
    output_ids: Vec<String>,
    audio_status: Option<lumistripe_engine_protocol::EngineAudioStatus>,
    audio_source: String,
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
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        let (sender, mut receiver) = mpsc::channel::<Envelope>(COMMAND_CAPACITY);
        let (state_sender, state) = watch::channel(Arc::new(model.snapshot(0)));
        let (startup_sender, startup_settings) =
            watch::channel(Arc::new(startup_settings_snapshot(&model)));
        let (application_settings_sender, application_settings) =
            watch::channel(Arc::new(application_settings_snapshot(&model)));
        let control_client = client.clone();
        let task = tokio::spawn(async move {
            // The worker is the sole owner of persisted application settings.
            let mut settings_store = settings_store;
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
                        let apply = apply_command(
                                &mut model,
                                &mut settings_store,
                                &desired_state,
                                &client,
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
                                Ok(snapshot)
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
        self.execute(ControlCommand::SetIdentity {
            application_name,
            spotify_device_name,
        })
        .await?;
        Ok(self.identity_settings())
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
            return select_audio_device(model, settings_store, desired_state, client, &device)
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
            return set_audio_source(model, settings_store, desired_state, client, &source).await;
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
            );
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
                ensure_audio_active(model, client).await?;
                candidate.audio_started = true;
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
            let independent = candidate
                .settings
                .stripe_topology
                .as_ref()
                .is_some_and(|topology| {
                    topology.layout == crate::settings::StripeLayout::Independent
                });
            let scopes = if let Some(stripe_id) = stripe_id {
                if !independent
                    || !candidate
                        .outputs
                        .iter()
                        .any(|item| item.stripe_id == stripe_id)
                {
                    return Err(ApiError::new(
                        axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                        format!("unknown or unsupported stripe: {stripe_id}"),
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
                &model.audio_source,
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
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
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
            "microphone input device is unavailable",
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
            axum::http::StatusCode::NOT_FOUND,
            "audio calibration session not found",
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
                axum::http::StatusCode::NOT_FOUND,
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
    if session.frame_rms.is_empty() {
        return Err("audio calibration requires at least one frame".to_owned());
    }
    let rms = session
        .frame_rms
        .iter()
        .map(|value| value.clamp(0.0, 1.0))
        .collect::<Vec<_>>();
    let energy = session
        .energy
        .iter()
        .map(|value| value.clamp(0.0, 1.0))
        .collect::<Vec<_>>();
    let floor = percentile(&rms, 20.0);
    let peak = rms.iter().chain(energy.iter()).copied().fold(0.0, f64::max);
    let active = energy
        .iter()
        .copied()
        .filter(|value| *value >= (floor * 1.8).max(0.01))
        .collect::<Vec<_>>();
    let active_levels = if active.is_empty() { &energy } else { &active };
    let active_level = percentile(active_levels, 80.0);
    let noise_floor = (floor * 1.6 + 0.004).clamp(0.003, 0.12);
    let target_level = (active_level * 1.25).max(0.24).clamp(0.24, 0.62);
    Ok(AudioCalibrationResult {
        duration_seconds: session.duration_seconds,
        samples: rms.len(),
        measured_floor: floor,
        measured_peak: peak,
        recommended_noise_floor: noise_floor,
        recommended_target_level: target_level,
        recommended_hardware_gain: None,
        recommended_idle_threshold_scale: (noise_floor / 0.015).max(0.5).clamp(0.5, 4.0),
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

fn set_identity_settings(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    application_name: &str,
    spotify_device_name: &str,
) -> Result<(), ApiError> {
    let application_name = application_name.trim();
    let spotify_device_name = spotify_device_name.trim();
    for (field, value) in [
        ("application_name", application_name),
        ("spotify_device_name", spotify_device_name),
    ] {
        if value.is_empty() || value.chars().count() > 64 {
            return Err(ApiError::new(
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                format!("{field} must contain 1 to 64 characters"),
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
    settings_store
        .replace_categories(categories)
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("could not save identity settings: {error}"),
            )
        })?;
    model.settings = candidate;
    Ok(())
}

async fn select_audio_device(
    model: &mut ControlModel,
    settings_store: &mut SettingsStore,
    desired_state: &watch::Sender<EngineDesiredState>,
    client: &EngineClient,
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
    source: &str,
) -> Result<(), ApiError> {
    match source {
        "auto" => {
            let was_demo = model.audio_source == "demo";
            if was_demo {
                stop_audio_engine(model, client).await?;
            }
            if model
                .outputs
                .iter()
                .any(|output| output.mode == PlaybackMode::Dynamic)
                && !model
                    .audio_status
                    .as_ref()
                    .is_some_and(|status| status.active)
            {
                ensure_audio_active(model, client).await?;
                model.audio_started = true;
            }
            model.audio_source = "auto".to_owned();
            if was_demo {
                model.revision = model.revision.saturating_add(1);
                let scene = model.render_scene(model.revision);
                desired_state.send_replace(EngineDesiredState::for_model(
                    model,
                    model.config.engine_config(),
                    scene,
                ));
            } else {
                let audio = model.desired_audio();
                desired_state.send_modify(|desired| desired.audio = audio);
            }
            Ok(())
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
            stop_audio_engine(model, client).await?;
            model.audio_source = "off".to_owned();
            model.revision = model.revision.saturating_add(1);
            let scene = model.render_scene(model.revision);
            desired_state.send_replace(EngineDesiredState::for_model(
                model,
                model.config.engine_config(),
                scene,
            ));
            Ok(())
        }
        "mic" => {
            let selected = model
                .settings
                .selected_audio_device
                .clone()
                .or_else(|| model.config.audio_device.clone())
                .or_else(|| {
                    model
                        .audio_status
                        .as_ref()
                        .and_then(|status| status.device.as_ref())
                        .map(|device| device.selector.clone())
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
            select_audio_device(model, settings_store, desired_state, client, &selected).await?;
            model.audio_source = "mic".to_owned();
            Ok(())
        }
        "demo" => {
            let profile = model
                .settings
                .selected_audio_device
                .as_deref()
                .and_then(|device| model.settings.audio_profiles.get(device))
                .cloned()
                .unwrap_or_default();
            match client
                .request(RequestCommand::AudioStart {
                    selector: Some("demo".to_owned()),
                    config: audio::engine_config(&profile, false),
                })
                .await
            {
                Ok(ResponseResult::AudioConfigured { active: true }) => {
                    model.audio_source = "demo".to_owned();
                    model.audio_started = true;
                    model.audio_status = match client.audio_status().await {
                        Ok(ResponseResult::AudioStatus { status }) => Some(status),
                        Ok(other) => {
                            return Err(ApiError::new(
                                axum::http::StatusCode::BAD_GATEWAY,
                                format!(
                                    "engine returned an unexpected demo audio status: {other:?}"
                                ),
                            ));
                        }
                        Err(error) => return Err(engine_api_error(error)),
                    };
                    model.revision = model.revision.saturating_add(1);
                    let scene = model.render_scene(model.revision);
                    desired_state.send_replace(EngineDesiredState::for_model(
                        model,
                        model.config.engine_config(),
                        scene,
                    ));
                    Ok(())
                }
                Ok(ResponseResult::AudioConfigured { active: false }) => Err(ApiError::new(
                    axum::http::StatusCode::CONFLICT,
                    "Rust demo audio source did not become active",
                )),
                Ok(ResponseResult::Error { code, message }) => {
                    Err(engine_api_error(EngineClientError::Engine {
                        code,
                        message,
                    }))
                }
                Ok(other) => Err(ApiError::new(
                    axum::http::StatusCode::BAD_GATEWAY,
                    format!("engine returned an unexpected demo audio response: {other:?}"),
                )),
                Err(error) => Err(engine_api_error(error)),
            }
        }
        "bluetooth" | "spotify" => Err(ApiError::new(
            axum::http::StatusCode::CONFLICT,
            format!("audio source {source:?} is not available in the Rust web runtime"),
        )),
        _ => Err(ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            format!("invalid audio source: {source}"),
        )),
    }
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

async fn restore_audio_capture(model: &ControlModel, client: &EngineClient) -> Result<(), String> {
    let Some(previous) = model
        .audio_status
        .as_ref()
        .filter(|status| status.active)
        .and_then(|status| status.device.as_ref())
    else {
        return match client.request(RequestCommand::AudioStop).await {
            Ok(ResponseResult::AudioStopped) => Ok(()),
            result => Err(format!("engine did not acknowledge audio stop: {result:?}")),
        };
    };
    let profile = model
        .settings
        .audio_profiles
        .get(&previous.name)
        .cloned()
        .unwrap_or_default();
    let config = audio::engine_config(&profile, true);
    match client
        .request(RequestCommand::AudioStart {
            selector: Some(previous.selector.clone()),
            config,
        })
        .await
    {
        Ok(ResponseResult::AudioConfigured { active: true }) => Ok(()),
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

async fn ensure_audio_active(model: &ControlModel, client: &EngineClient) -> Result<(), ApiError> {
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
        Ok(ResponseResult::AudioConfigured { active: true }) => Ok(()),
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
                    axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                    "a stripe target is only available in independent layout",
                ));
            }
            let index = self
                .outputs
                .iter()
                .position(|output| output.stripe_id == stripe_id)
                .ok_or_else(|| {
                    ApiError::new(
                        axum::http::StatusCode::NOT_FOUND,
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
        AudioCalibrationFinish, ControlPlane, advance_audio_calibration, audio_calibration_value,
        expire_color_calibration, finish_audio_calibration, microphone_calibration_result,
    };
    use crate::settings::{ColorCorrection, DashboardSettings, SettingsStore};
    use crate::state::{
        ActiveAudioCalibration, ActiveColorCalibration, AudioCalibrationResult,
        COLOR_CALIBRATION_TIMEOUT, ControlModel, EngineDesiredState, PlaybackMode,
    };
    use lumistripe_engine_audio::InputAudioDevice;
    use lumistripe_engine_client::EngineClient;
    use lumistripe_engine_protocol::{
        EngineAudioStatus, EngineErrorCode, Request, RequestCommand, Response, ResponseResult,
    };
    use serde_json::{Value, json};
    use std::time::Duration;
    use tempfile::tempdir;
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixListener;
    use tokio::sync::{oneshot, watch};

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
