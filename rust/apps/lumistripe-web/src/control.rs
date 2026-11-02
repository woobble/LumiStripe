use crate::error::ApiError;
use crate::state::{ControlModel, PlaybackMode};
use lumistripe_engine_audio::AudioConfig;
use lumistripe_engine_client::{EngineClient, EngineClientError};
use lumistripe_engine_protocol::{
    EngineAudioStatus, RenderSceneUpdate, RequestCommand, ResponseResult,
};
use serde_json::{Value, json};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::task::JoinHandle;
use tokio::time::{interval, timeout};
use tokio_util::sync::CancellationToken;

const COMMAND_CAPACITY: usize = 64;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct ControlPlane {
    sender: mpsc::Sender<Envelope>,
    state: watch::Receiver<Arc<Value>>,
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
}

struct Envelope {
    command: ControlCommand,
    reply: oneshot::Sender<Result<Value, ApiError>>,
}

impl ControlPlane {
    pub fn start(
        mut model: ControlModel,
        client: EngineClient,
        mut health: watch::Receiver<Option<Result<Arc<ResponseResult>, String>>>,
        mut audio: watch::Receiver<Option<Result<EngineAudioStatus, String>>>,
        desired_scene: watch::Sender<RenderSceneUpdate>,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        let (sender, mut receiver) = mpsc::channel::<Envelope>(COMMAND_CAPACITY);
        let (state_sender, state) = watch::channel(Arc::new(model.snapshot(0)));
        let task = tokio::spawn(async move {
            let mut state_tick = interval(Duration::from_secs(1));
            loop {
                tokio::select! {
                    _ = cancellation.cancelled() => break,
                    envelope = receiver.recv() => {
                        let Some(envelope) = envelope else { break; };
                        if envelope.reply.is_closed() {
                            continue;
                        }
                        let result = apply_command(
                            &mut model,
                            &client,
                            &desired_scene,
                            envelope.command,
                        ).await;
                        let result = match result {
                            Ok(()) => {
                                let snapshot = model.snapshot(receiver.max_capacity() - receiver.capacity());
                                let _ = state_sender.send(Arc::new(snapshot.clone()));
                                Ok(snapshot)
                            }
                            Err(error) => Err(error),
                        };
                        let _ = envelope.reply.send(result);
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
                        if let Some(snapshot) = audio.borrow_and_update().as_ref() {
                            model.set_audio_status(snapshot.clone());
                        }
                    }
                    _ = state_tick.tick() => {
                        if let Some(snapshot) = health.borrow().as_ref() {
                            model.set_engine_health(snapshot.as_ref().map(|value| (**value).clone()).map_err(|error| error.clone()));
                        }
                        if let Some(snapshot) = audio.borrow().as_ref() {
                            model.set_audio_status(snapshot.clone());
                        }
                        model.revision = model.revision.saturating_add(1);
                        let _ = state_sender.send(Arc::new(model.snapshot(receiver.max_capacity() - receiver.capacity())));
                    }
                }
            }
        });
        (Self { sender, state }, task)
    }

    pub fn state(&self) -> watch::Receiver<Arc<Value>> {
        self.state.clone()
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

    async fn execute(&self, command: ControlCommand) -> Result<Value, ApiError> {
        let (reply, response) = oneshot::channel();
        self.sender
            .try_send(Envelope { command, reply })
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
        timeout(COMMAND_TIMEOUT, response)
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
    client: &EngineClient,
    desired_scene: &watch::Sender<RenderSceneUpdate>,
    command: ControlCommand,
) -> Result<(), ApiError> {
    let mut candidate = model.clone();
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
        }
        ControlCommand::SetBlackout { enabled, stripe_id } => {
            for output in candidate.targets_mut(stripe_id.as_deref())? {
                output.blackout = enabled;
            }
        }
    }

    let revision = model.revision.saturating_add(1);
    candidate.revision = revision;
    let update = candidate.render_scene(revision);
    let response = client
        .update_scene(update.clone())
        .await
        .map_err(engine_api_error)?;
    match response {
        ResponseResult::SceneUpdated {
            revision: received, ..
        } if received == revision => {
            desired_scene.send_replace(update);
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

async fn ensure_audio_active(model: &ControlModel, client: &EngineClient) -> Result<(), ApiError> {
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
    match client
        .request(RequestCommand::AudioStart {
            selector: model.config.audio_device.clone(),
            config: AudioConfig::default(),
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
    use crate::state::PlaybackMode;
    use lumistripe_engine_client::EngineClient;
    use lumistripe_engine_protocol::{RenderSceneUpdate, ResponseResult};
    use serde_json::json;
    use std::time::Duration;
    use tempfile::tempdir;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixListener;
    use tokio::sync::watch;

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
            }],
            runtime_kind: "simulation",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            secure_cookie: false,
            static_dir: None,
        };
        let (scene_tx, _) = watch::channel(RenderSceneUpdate {
            revision: 1,
            timestamp_ns: 1,
            brightness: 1.0,
            power: Default::default(),
            outputs: Vec::new(),
            scheduler: lumistripe_engine_protocol::RenderSchedulerConfig { max_fps: 60 },
        });
        let model = crate::state::ControlModel::new(config);
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
            client,
            snapshot,
            audio,
            scene_tx,
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
}
