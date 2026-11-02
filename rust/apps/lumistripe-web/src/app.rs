use crate::auth::{PairingAuth, PairingError, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS};
use crate::config::AppConfig;
use crate::control::ControlPlane;
use crate::error::ApiError;
use crate::settings::{
    AudioTuningProfile, ColorCorrection, MusicIdleMode, MusicIdleSettings, SavedAnimationSequence,
    SavedAnimationSequenceStep, SettingsStore, StripeBackend, StripeLayout, StripeOutputSettings,
    StripeTopologySettings, SwitchQuantization, TransitionEffect, TransitionSettings, ZonePattern,
};
use crate::state::{ControlModel, EngineDesiredState, PlaybackMode, animation_list};
use crate::streams::{
    AudioTelemetryReceiver, PreviewReceiver, spawn_audio_telemetry_publisher,
    spawn_health_publisher, spawn_preview_publisher, websocket_audio, websocket_preview,
    websocket_state,
};
use axum::Json;
use axum::body::Body;
use axum::extract::{Json as JsonExtractor, Path, Query, Request, State};
use axum::http::header::{COOKIE, HOST, ORIGIN, RETRY_AFTER, SET_COOKIE};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post, put};
use axum::{Router, middleware as axum_middleware};
use lumistripe_engine_client::EngineClient;
use lumistripe_engine_protocol::{RequestCommand, ResponseResult};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tempfile::TempDir;
use tokio::net::TcpListener;
use tokio::process::{Child, Command};
use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;
use tokio::time::{sleep, timeout};
use tokio_util::sync::CancellationToken;
use tower_http::services::{ServeDir, ServeFile};
use tower_http::trace::TraceLayer;
use url::Url;

const ENGINE_STARTUP_TIMEOUT: Duration = Duration::from_secs(10);

struct EngineSupervisor {
    binary: PathBuf,
    socket_path: PathBuf,
    client: EngineClient,
    health: watch::Sender<Option<Result<Arc<ResponseResult>, String>>>,
    cancellation: CancellationToken,
}

#[derive(Clone)]
pub(crate) struct AppState {
    pub(crate) control: ControlPlane,
    pub(crate) access: PairingAuth,
    pub(crate) audio_telemetry: AudioTelemetryReceiver,
    pub(crate) preview: PreviewReceiver,
    secure_cookie: bool,
}

pub struct WebApplication {
    router: Router,
    cancellation: CancellationToken,
    engine_manager: Option<JoinHandle<()>>,
    tasks: Vec<JoinHandle<()>>,
    _engine_directory: TempDir,
}

impl WebApplication {
    pub async fn start(
        mut config: AppConfig,
        engine_binary: PathBuf,
        pairing_code: Option<String>,
    ) -> Result<Self, String> {
        let settings_store =
            SettingsStore::open(&config.settings_file).map_err(|error| error.to_string())?;
        let settings = settings_store.settings().clone();
        if let Some(topology) = &settings.stripe_topology {
            config.apply_topology(topology)?;
        }
        if settings.selected_audio_device.is_some() {
            config.audio_device = settings.selected_audio_device.clone();
        }
        let access = PairingAuth::new(pairing_code)?;
        let engine_directory = TempDir::new()
            .map_err(|error| format!("create private engine socket directory: {error}"))?;
        let socket_path = engine_directory.path().join("engine.sock");
        let client = EngineClient::new(&socket_path, Duration::from_secs(2));
        let cancellation = CancellationToken::new();
        let model = ControlModel::new(config.clone(), settings);
        let initial_scene = model.initial_scene();
        let (desired_state, desired_state_receiver) = watch::channel(EngineDesiredState {
            config: config.engine_config(),
            scene: initial_scene,
            audio: None,
        });
        let (health_sender, health) = watch::channel(None);
        let (audio_sender, audio) = watch::channel(None);
        let (preview_sender, preview) = watch::channel(None);
        let (ready_sender, ready_receiver) = oneshot::channel();
        let engine_manager = tokio::spawn(supervise_engine(
            EngineSupervisor {
                binary: engine_binary,
                socket_path,
                client: client.clone(),
                health: health_sender.clone(),
                cancellation: cancellation.clone(),
            },
            desired_state_receiver,
            ready_sender,
        ));

        match timeout(
            ENGINE_STARTUP_TIMEOUT + Duration::from_secs(6),
            ready_receiver,
        )
        .await
        {
            Ok(Ok(Ok(()))) => {}
            Ok(Ok(Err(error))) => {
                cancellation.cancel();
                let _ = engine_manager.await;
                return Err(error);
            }
            Ok(Err(_)) => return Err("Rust engine supervisor stopped during startup".to_owned()),
            Err(_) => {
                cancellation.cancel();
                engine_manager.abort();
                return Err(
                    "Rust engine did not become ready before the startup deadline".to_owned(),
                );
            }
        }

        let mut model = model;
        if let Some(Ok(health_result)) = health.borrow().as_ref() {
            model.set_engine_health(Ok((**health_result).clone()));
        }
        if model.settings.selected_audio_device.is_some()
            || model
                .outputs
                .iter()
                .any(|output| output.mode == crate::state::PlaybackMode::Dynamic)
        {
            let profile = model
                .settings
                .selected_audio_device
                .as_deref()
                .and_then(|device| model.settings.audio_profiles.get(device))
                .cloned()
                .unwrap_or_default();
            let audio_result = client
                .request(RequestCommand::AudioStart {
                    selector: model.config.audio_device.clone(),
                    config: crate::audio::engine_config(&profile, true),
                })
                .await;
            match audio_result {
                Ok(ResponseResult::AudioConfigured { active: true }) => {
                    model.audio_started = true;
                    let audio = model.desired_audio();
                    desired_state.send_modify(|desired| desired.audio = audio);
                    match client.audio_status().await {
                        Ok(ResponseResult::AudioStatus { status }) => {
                            model.set_audio_status(Ok(status));
                        }
                        Ok(other) => model.set_audio_status(Err(format!(
                            "engine returned unexpected restored audio status: {other:?}"
                        ))),
                        Err(error) => model.set_audio_status(Err(error.to_string())),
                    }
                }
                Ok(ResponseResult::AudioConfigured { active: false }) => {
                    model.set_audio_status(Err(
                        "saved microphone did not become active during startup".to_owned(),
                    ));
                }
                Ok(ResponseResult::Error { message, .. }) => {
                    model.set_audio_status(Err(message));
                }
                Ok(other) => model.set_audio_status(Err(format!(
                    "engine returned unexpected restored audio response: {other:?}"
                ))),
                Err(error) => model.set_audio_status(Err(error.to_string())),
            }
        }
        let (control, control_task) = ControlPlane::start(
            model,
            settings_store,
            desired_state,
            client.clone(),
            health.clone(),
            audio.clone(),
            cancellation.clone(),
        );
        let (telemetry_sender, telemetry_receiver) = watch::channel(None);
        let telemetry_task = spawn_audio_telemetry_publisher(
            client.clone(),
            health.clone(),
            control.clone(),
            cancellation.clone(),
            telemetry_sender,
        );
        let app_state = AppState {
            control,
            access: access.clone(),
            audio_telemetry: telemetry_receiver,
            preview: preview.clone(),
            secure_cookie: config.secure_cookie,
        };
        let router = build_router(app_state, config.static_dir.clone());
        let tasks = vec![
            control_task,
            spawn_health_publisher(client, cancellation.clone(), health_sender),
            spawn_audio_status_publisher(
                EngineClient::new(
                    engine_directory.path().join("engine.sock"),
                    Duration::from_secs(2),
                ),
                cancellation.clone(),
                audio_sender,
            ),
            spawn_preview_publisher(health, cancellation.clone(), preview_sender),
            telemetry_task,
        ];
        Ok(Self {
            router,
            cancellation,
            engine_manager: Some(engine_manager),
            tasks,
            _engine_directory: engine_directory,
        })
    }

    pub fn router(&self) -> Router {
        self.router.clone()
    }

    pub async fn serve(mut self, host: &str, port: u16) -> Result<(), String> {
        let listener = TcpListener::bind((host, port))
            .await
            .map_err(|error| format!("bind LumiStripe web listener: {error}"))?;
        let local_address = listener
            .local_addr()
            .map_err(|error| format!("read LumiStripe web listener address: {error}"))?;
        tracing::info!(address = %local_address, "LumiStripe Rust web server is listening");
        let cancellation = self.cancellation.clone();
        let serve_result = axum::serve(listener, self.router.clone())
            .with_graceful_shutdown(async move {
                shutdown_signal().await;
                cancellation.cancel();
            })
            .await;
        self.shutdown().await;
        serve_result.map_err(|error| format!("LumiStripe web server failed: {error}"))
    }

    pub async fn shutdown(&mut self) {
        self.cancellation.cancel();
        if let Some(manager) = self.engine_manager.take() {
            if timeout(Duration::from_secs(7), manager).await.is_err() {
                tracing::warn!("Rust engine supervisor did not stop before the shutdown deadline");
            }
        }
        for task in self.tasks.drain(..) {
            if timeout(Duration::from_secs(3), task).await.is_err() {
                tracing::warn!(
                    "Rust web background task did not stop before the shutdown deadline"
                );
            }
        }
    }
}

impl Drop for WebApplication {
    fn drop(&mut self) {
        self.cancellation.cancel();
    }
}

pub(crate) fn build_router(state: AppState, static_dir: Option<PathBuf>) -> Router {
    let routes = Router::new()
        .route("/api/health", get(health))
        .route("/api/state", get(runtime_state))
        .route("/api/animations", get(animations))
        .route(
            "/api/animations/{animation_name}/settings",
            get(get_animation_settings).put(put_animation_settings),
        )
        .route(
            "/api/animations/{animation_name}/settings/reset",
            post(reset_animation_settings),
        )
        .route("/api/mode", put(set_mode))
        .route("/api/brightness", put(set_brightness))
        .route("/api/animation", put(select_animation))
        .route("/api/blackout", post(set_blackout))
        .route(
            "/api/identity",
            get(get_identity_settings).put(put_identity_settings),
        )
        .route(
            "/api/stripes",
            get(get_stripe_topology).put(put_stripe_topology),
        )
        .route("/api/stripes/test", post(test_stripe))
        .route(
            "/api/audio/settings",
            get(get_audio_settings).put(put_audio_settings),
        )
        .route("/api/audio/settings/reset", post(reset_audio_settings))
        .route("/api/audio/device", put(select_audio_device))
        .route("/api/audio/source", put(select_audio_source))
        .route("/api/audio/sync-offset", put(set_audio_sync_offset))
        .route(
            "/api/audio/sync-calibration",
            post(set_audio_sync_calibration),
        )
        .route(
            "/api/audio/calibration/session",
            post(start_audio_calibration),
        )
        .route(
            "/api/audio/calibration/session/{session_id}",
            get(get_audio_calibration),
        )
        .route(
            "/api/audio/calibration/session/{session_id}/music",
            post(begin_audio_calibration_music),
        )
        .route(
            "/api/audio/calibration/session/{session_id}/finish",
            post(finish_audio_calibration),
        )
        .route("/api/calibration/session", post(start_color_calibration))
        .route(
            "/api/calibration/session/{session_id}",
            put(update_color_calibration),
        )
        .route(
            "/api/calibration/session/{session_id}/finish",
            post(finish_color_calibration),
        )
        .route("/api/music-idle", put(set_music_idle))
        .route("/api/startup", get(get_startup_settings))
        .route("/api/startup", put(put_startup_settings))
        .route("/api/transitions/settings", get(get_transition_settings))
        .route("/api/transitions/settings", put(put_transition_settings))
        .route("/api/sequencer/settings", get(get_sequencer_settings))
        .route("/api/sequencer/settings", put(put_sequencer_settings))
        .route(
            "/api/sequencer/sequences",
            get(get_sequence_library).post(create_sequence),
        )
        .route(
            "/api/sequencer/sequences/{sequence_id}",
            put(update_sequence).delete(delete_sequence),
        )
        .route("/api/sequencer/active", put(set_active_sequence))
        .route("/api/auth/status", get(auth_status))
        .route("/api/auth/pair", post(pair))
        .route("/api/auth/logout", post(logout))
        .route("/ws/state", get(websocket_state))
        .route("/ws/audio", get(websocket_audio))
        .route("/ws/preview", get(websocket_preview))
        .route("/api", any(api_not_found))
        .route("/api/{*path}", any(api_not_found))
        .layer(axum_middleware::from_fn_with_state(
            state.access.clone(),
            access_middleware,
        ))
        .with_state(state)
        .layer(TraceLayer::new_for_http());
    let static_dir = static_dir.unwrap_or_else(default_static_dir);
    let index = static_dir.join("index.html");
    if index.is_file() {
        routes.fallback_service(ServeDir::new(static_dir).not_found_service(ServeFile::new(index)))
    } else {
        routes
    }
}

async fn health(State(state): State<AppState>) -> Response {
    let mut snapshot = state.control.state();
    let current = snapshot.borrow_and_update().clone();
    if current.get("running").and_then(Value::as_bool) == Some(true) {
        Json(json!({"status": "ok"})).into_response()
    } else {
        let message = current
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("runtime is not running");
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"status": "unavailable", "error": message})),
        )
            .into_response()
    }
}

async fn runtime_state(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow().as_ref().clone())
}

async fn animations() -> Json<Value> {
    Json(animation_list())
}

async fn get_stripe_topology(State(state): State<AppState>) -> Json<Value> {
    Json(state.control.stripe_topology())
}

#[derive(Deserialize)]
struct StripeTopologyRequest {
    layout: StripeLayout,
    outputs: Vec<StripeOutputRequest>,
    #[serde(default)]
    power_budget_enabled: bool,
    #[serde(default)]
    power_budget_watts: Option<f64>,
    #[serde(default)]
    shared_supply_enabled: bool,
    #[serde(default = "default_supply_capacity")]
    supply_capacity_watts: f64,
    #[serde(default = "default_pi_reserve")]
    pi_reserve_watts: f64,
}

#[derive(Deserialize)]
struct StripeOutputRequest {
    id: String,
    name: String,
    pixels: usize,
    #[serde(default)]
    backend: StripeBackend,
    #[serde(default)]
    reversed: bool,
    #[serde(default = "default_zone_count")]
    zone_count: usize,
    #[serde(default)]
    zone_pattern: ZonePattern,
    #[serde(default = "default_spi_device")]
    spi_device: String,
    #[serde(default = "default_spi_speed")]
    spi_speed_hz: u32,
    #[serde(default = "default_gpio_chip")]
    chip: String,
    #[serde(default = "default_data_pin")]
    data_pin: u32,
    #[serde(default = "default_clock_pin")]
    clock_pin: u32,
    #[serde(default = "default_voltage")]
    voltage_v: f64,
    #[serde(default = "default_full_white_current")]
    full_white_current_a: f64,
    #[serde(default)]
    power_limit_watts: Option<f64>,
}

impl From<StripeOutputRequest> for StripeOutputSettings {
    fn from(output: StripeOutputRequest) -> Self {
        Self {
            id: output.id,
            name: output.name,
            pixels: output.pixels,
            backend: output.backend,
            reversed: output.reversed,
            zone_count: output.zone_count,
            zone_pattern: output.zone_pattern,
            spi_device: output.spi_device,
            spi_speed_hz: output.spi_speed_hz,
            chip: output.chip,
            data_pin: output.data_pin,
            clock_pin: output.clock_pin,
            voltage_v: output.voltage_v,
            full_white_current_a: output.full_white_current_a,
            power_limit_watts: output.power_limit_watts,
            extra: Default::default(),
        }
    }
}

impl From<StripeTopologyRequest> for StripeTopologySettings {
    fn from(topology: StripeTopologyRequest) -> Self {
        Self {
            layout: topology.layout,
            outputs: topology.outputs.into_iter().map(Into::into).collect(),
            power_budget_enabled: topology.power_budget_enabled,
            power_budget_watts: topology.power_budget_watts,
            shared_supply_enabled: topology.shared_supply_enabled,
            supply_capacity_watts: topology.supply_capacity_watts,
            pi_reserve_watts: topology.pi_reserve_watts,
            extra: Default::default(),
        }
    }
}

#[derive(Deserialize)]
struct StripeTestRequest {
    stripe_id: String,
    #[serde(default = "default_identify_pattern")]
    pattern: String,
    #[serde(default)]
    topology: Option<StripeTopologyRequest>,
}

async fn put_stripe_topology(
    State(state): State<AppState>,
    body: Result<JsonExtractor<StripeTopologyRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let topology = StripeTopologySettings::from(body.map_err(json_rejection)?.0);
    state.control.set_stripe_topology(topology).await.map(Json)
}

async fn test_stripe(
    State(state): State<AppState>,
    body: Result<JsonExtractor<StripeTestRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if !["identify", "red", "green", "blue", "white"].contains(&body.pattern.as_str()) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            format!("unknown test pattern: {}", body.pattern),
        ));
    }
    let topology = body.topology.map(StripeTopologySettings::from);
    state
        .control
        .test_stripe(body.stripe_id, body.pattern, topology)
        .await
        .map(Json)
}

#[derive(Deserialize)]
struct CalibrationStartRequest {
    output_index: usize,
}

#[derive(Deserialize)]
struct CalibrationUpdateRequest {
    red: u8,
    green: u8,
    blue: u8,
    pattern: String,
}

#[derive(Deserialize)]
struct CalibrationFinishRequest {
    save: bool,
}

async fn start_color_calibration(
    State(state): State<AppState>,
    body: Result<JsonExtractor<CalibrationStartRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .start_color_calibration(body.output_index)
        .await
        .map(Json)
}

async fn update_color_calibration(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
    body: Result<JsonExtractor<CalibrationUpdateRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if !["white", "red", "green", "blue"].contains(&body.pattern.as_str()) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            format!("unknown calibration pattern: {}", body.pattern),
        ));
    }
    state
        .control
        .update_color_calibration(
            session_id,
            ColorCorrection {
                red: body.red,
                green: body.green,
                blue: body.blue,
                extra: Default::default(),
            },
            body.pattern,
        )
        .await
        .map(Json)
}

async fn finish_color_calibration(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
    body: Result<JsonExtractor<CalibrationFinishRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .finish_color_calibration(session_id, body.save)
        .await
        .map(Json)
}

#[derive(Deserialize)]
struct AudioDeviceRequest {
    device: String,
}

#[derive(Deserialize)]
struct AudioSettingsRequest {
    device: String,
    settings: AudioTuningProfile,
}

#[derive(Deserialize)]
struct AudioSyncOffsetRequest {
    offset_ms: f64,
}

#[derive(Deserialize)]
struct AudioSourceRequest {
    source: String,
}

#[derive(Deserialize)]
struct AudioSyncCalibrationRequest {
    active: bool,
}

fn default_audio_calibration_duration() -> f64 {
    8.0
}

#[derive(Deserialize)]
struct AudioCalibrationStartRequest {
    device: String,
    #[serde(default = "default_audio_calibration_duration")]
    duration_seconds: f64,
    #[serde(default)]
    two_step: bool,
}

#[derive(Deserialize)]
struct AudioCalibrationFinishRequest {
    apply: bool,
    target_level: Option<f64>,
    noise_floor: Option<f64>,
}

#[derive(Deserialize)]
struct IdentitySettingsRequest {
    application_name: String,
    spotify_device_name: String,
}

async fn get_identity_settings(State(state): State<AppState>) -> Json<Value> {
    Json(state.control.identity_settings())
}

async fn put_identity_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<IdentitySettingsRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_identity_settings(body.application_name, body.spotify_device_name)
        .await
        .map(Json)
}

async fn get_audio_settings(State(state): State<AppState>) -> Result<Json<Value>, ApiError> {
    state.control.audio_settings().await.map(Json)
}

async fn select_audio_device(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.device.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "device must contain at least one character",
        ));
    }
    state
        .control
        .select_audio_device(body.device)
        .await
        .map(Json)
}

async fn put_audio_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioSettingsRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.device.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "device must contain at least one character",
        ));
    }
    state
        .control
        .apply_audio_settings(body.device, body.settings)
        .await
        .map(Json)
}

async fn reset_audio_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.device.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "device must contain at least one character",
        ));
    }
    state
        .control
        .reset_audio_settings(body.device)
        .await
        .map(Json)
}

async fn set_audio_sync_offset(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioSyncOffsetRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_audio_sync_offset(body.offset_ms)
        .await
        .map(Json)
}

async fn select_audio_source(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioSourceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state.control.set_audio_source(body.source).await.map(Json)
}

async fn set_audio_sync_calibration(
    State(state): State<AppState>,
    body: Result<
        JsonExtractor<AudioSyncCalibrationRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    let settings = state.control.audio_settings().await?;
    if body.active {
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            "audio sync playback calibration requires an audio output provider",
        ));
    }
    Ok(Json(json!({
        "start_at_ms": null,
        "sync_route": settings["sync_route"],
        "sync_offset_ms": settings["sync_offset_ms"],
    })))
}

async fn start_audio_calibration(
    State(state): State<AppState>,
    body: Result<
        JsonExtractor<AudioCalibrationStartRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.device.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "device must contain at least one character",
        ));
    }
    state
        .control
        .start_audio_calibration(body.device, body.duration_seconds, body.two_step)
        .await
        .map(Json)
}

async fn get_audio_calibration(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    state
        .control
        .audio_calibration_status(&session_id)
        .map(Json)
}

async fn begin_audio_calibration_music(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    state
        .control
        .begin_audio_calibration_music(session_id)
        .await
        .map(Json)
}

async fn finish_audio_calibration(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
    body: Result<
        JsonExtractor<AudioCalibrationFinishRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .finish_audio_calibration(session_id, body.apply, body.target_level, body.noise_floor)
        .await
        .map(Json)
}

fn default_supply_capacity() -> f64 {
    200.0
}
fn default_pi_reserve() -> f64 {
    15.0
}
fn default_zone_count() -> usize {
    1
}
fn default_spi_device() -> String {
    "/dev/spidev0.0".to_owned()
}
fn default_spi_speed() -> u32 {
    1_000_000
}
fn default_gpio_chip() -> String {
    "/dev/gpiochip0".to_owned()
}
fn default_data_pin() -> u32 {
    10
}
fn default_clock_pin() -> u32 {
    11
}
fn default_voltage() -> f64 {
    5.0
}
fn default_full_white_current() -> f64 {
    0.06
}
fn default_identify_pattern() -> String {
    "identify".to_owned()
}

#[derive(Deserialize)]
struct AnimationSettingsQuery {
    stripe_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AnimationSettingsUpdateRequest {
    values: Map<String, Value>,
    stripe_id: Option<String>,
}

async fn get_animation_settings(
    State(state): State<AppState>,
    Path(animation_name): Path<String>,
    Query(query): Query<AnimationSettingsQuery>,
) -> Result<Json<Value>, ApiError> {
    let animation_name = validate_animation_path(animation_name)?;
    if let Some(stripe_id) = query.stripe_id.as_deref() {
        validate_stripe_id(stripe_id, "query")?;
    }
    state
        .control
        .animation_settings(&animation_name, query.stripe_id.as_deref())
        .map(Json)
}

async fn put_animation_settings(
    State(state): State<AppState>,
    Path(animation_name): Path<String>,
    body: Result<
        JsonExtractor<AnimationSettingsUpdateRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<Value>, ApiError> {
    let animation_name = validate_animation_path(animation_name)?;
    let body = body.map_err(json_rejection)?.0;
    if let Some(stripe_id) = body.stripe_id.as_deref() {
        validate_stripe_id(stripe_id, "body")?;
    }
    state
        .control
        .set_animation_settings(
            animation_name.clone(),
            body.stripe_id.clone(),
            Some(body.values),
        )
        .await?;
    state
        .control
        .animation_settings(&animation_name, body.stripe_id.as_deref())
        .map(Json)
}

async fn reset_animation_settings(
    State(state): State<AppState>,
    Path(animation_name): Path<String>,
    Query(query): Query<AnimationSettingsQuery>,
) -> Result<Json<Value>, ApiError> {
    let animation_name = validate_animation_path(animation_name)?;
    if let Some(stripe_id) = query.stripe_id.as_deref() {
        validate_stripe_id(stripe_id, "query")?;
    }
    state
        .control
        .set_animation_settings(animation_name.clone(), query.stripe_id.clone(), None)
        .await?;
    state
        .control
        .animation_settings(&animation_name, query.stripe_id.as_deref())
        .map(Json)
}

fn validate_animation_path(value: String) -> Result<String, ApiError> {
    validate_path_text(value, "animation_name", 100)
}

fn validate_stripe_id(value: &str, location: &str) -> Result<(), ApiError> {
    if value.is_empty() || value.chars().count() > 64 {
        return Err(ApiError::validation(json!([{
            "type": if value.is_empty() { "string_too_short" } else { "string_too_long" },
            "loc": [location, "stripe_id"],
            "msg": if value.is_empty() { "String should have at least 1 character" } else { "String should have at most 64 characters" },
            "input": value,
            "ctx": if value.is_empty() { json!({"min_length": 1}) } else { json!({"max_length": 64}) },
        }])));
    }
    Ok(())
}

fn validate_path_text(
    value: String,
    field: &str,
    max_characters: usize,
) -> Result<String, ApiError> {
    let length = value.chars().count();
    if length == 0 || length > max_characters {
        let error = if length == 0 {
            json!([{
                "type": "string_too_short",
                "loc": ["path", field],
                "msg": "String should have at least 1 character",
                "input": value,
                "ctx": {"min_length": 1},
            }])
        } else {
            json!([{
                "type": "string_too_long",
                "loc": ["path", field],
                "msg": format!("String should have at most {max_characters} characters"),
                "input": value,
                "ctx": {"max_length": max_characters},
            }])
        };
        return Err(ApiError::validation(error));
    }
    Ok(value)
}

#[derive(Deserialize)]
struct ModeRequest {
    mode: PlaybackMode,
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    stripe_id: Option<String>,
    #[serde(default)]
    music_recognition_enabled: Option<bool>,
}

#[derive(Deserialize)]
struct BrightnessRequest {
    #[serde(deserialize_with = "deserialize_float")]
    brightness: f64,
    #[serde(default)]
    stripe_id: Option<String>,
}

fn deserialize_float<'de, D>(deserializer: D) -> Result<f64, D::Error>
where
    D: Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum FloatInput {
        Number(f64),
        String(String),
    }

    match FloatInput::deserialize(deserializer)? {
        FloatInput::Number(value) => Ok(value),
        FloatInput::String(value) => value.parse().map_err(serde::de::Error::custom),
    }
}

#[derive(Deserialize)]
struct AnimationRequest {
    name: String,
    #[serde(default)]
    stripe_id: Option<String>,
}

#[derive(Deserialize)]
struct BlackoutRequest {
    enabled: bool,
    #[serde(default)]
    stripe_id: Option<String>,
}

async fn set_mode(
    State(state): State<AppState>,
    body: Result<JsonExtractor<ModeRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_mode(
            body.mode,
            body.color,
            body.stripe_id,
            body.music_recognition_enabled,
        )
        .await
        .map(Json)
}

async fn set_brightness(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BrightnessRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_brightness(body.brightness, body.stripe_id)
        .await
        .map(Json)
}

async fn select_animation(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AnimationRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.name.is_empty() {
        return Err(ApiError::validation(json!([{
            "type": "string_too_short",
            "loc": ["body", "name"],
            "msg": "String should have at least 1 character",
            "input": body.name,
            "ctx": {"min_length": 1},
        }])));
    }
    state
        .control
        .select_animation(body.name, body.stripe_id)
        .await
        .map(Json)
}

async fn set_blackout(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BlackoutRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_blackout(body.enabled, body.stripe_id)
        .await
        .map(Json)
}

#[derive(Deserialize)]
#[serde(default)]
struct MusicIdleRequest {
    mode: MusicIdleMode,
    color: String,
    animation: String,
    brightness: f64,
    stripe_id: Option<String>,
}

impl Default for MusicIdleRequest {
    fn default() -> Self {
        let defaults = MusicIdleSettings::default();
        Self {
            mode: defaults.mode,
            color: defaults.color,
            animation: defaults.animation,
            brightness: defaults.brightness,
            stripe_id: None,
        }
    }
}

async fn set_music_idle(
    State(state): State<AppState>,
    body: Result<JsonExtractor<MusicIdleRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_music_idle(
            MusicIdleSettings {
                mode: body.mode,
                color: body.color,
                animation: body.animation,
                brightness: body.brightness,
                extra: Default::default(),
            },
            body.stripe_id,
        )
        .await
        .map(Json)
}

#[derive(Deserialize)]
struct StartupSettingsRequest {
    restore_last_state: bool,
}

async fn get_startup_settings(State(state): State<AppState>) -> Json<Value> {
    Json(state.control.startup_settings())
}

async fn put_startup_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<StartupSettingsRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .control
        .set_startup_restore(body.restore_last_state)
        .await?;
    Ok(Json(state.control.startup_settings()))
}

async fn get_transition_settings(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow()["transition_settings"].clone())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TransitionSettingsRequest {
    enabled: bool,
    duration_ms: u16,
    #[serde(default)]
    effect: TransitionEffect,
}

async fn put_transition_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<TransitionSettingsRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if !(100..=1500).contains(&body.duration_ms) {
        let (kind, message, context) = if body.duration_ms < 100 {
            (
                "greater_than_equal",
                "Input should be greater than or equal to 100",
                json!({"ge": 100}),
            )
        } else {
            (
                "less_than_equal",
                "Input should be less than or equal to 1500",
                json!({"le": 1500}),
            )
        };
        return Err(ApiError::validation(json!([{
            "type": kind,
            "loc": ["body", "duration_ms"],
            "msg": message,
            "input": body.duration_ms,
            "ctx": context,
        }])));
    }
    state
        .control
        .set_transition_settings(TransitionSettings {
            enabled: body.enabled,
            duration_ms: body.duration_ms,
            effect: body.effect,
            extra: Default::default(),
        })
        .await
        .map(Json)
}

async fn get_sequencer_settings(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow()["sequencer_settings"].clone())
}

#[derive(Deserialize)]
struct SequencerSettingsRequest {
    switch_quantization: SwitchQuantization,
    bars_per_phrase: u8,
}

async fn put_sequencer_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<SequencerSettingsRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if !matches!(body.bars_per_phrase, 4 | 8 | 16) {
        return Err(ApiError::validation(json!([{
            "type": "literal_error",
            "loc": ["body", "bars_per_phrase"],
            "msg": "Input should be 4, 8 or 16",
            "input": body.bars_per_phrase,
            "ctx": {"expected": "4, 8 or 16"},
        }])));
    }
    state
        .control
        .set_sequencer_settings(body.switch_quantization, body.bars_per_phrase)
        .await
        .map(Json)
}

async fn get_sequence_library(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow()["sequence_library"].clone())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SequenceStepRequest {
    animation_name: String,
    duration_bars: u8,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SequenceDefinitionRequest {
    name: String,
    repeat: bool,
    steps: Vec<SequenceStepRequest>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SequenceCreateRequest {
    id: String,
    name: String,
    repeat: bool,
    steps: Vec<SequenceStepRequest>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ActiveSequenceRequest {
    sequence_id: Value,
}

async fn create_sequence(
    State(state): State<AppState>,
    body: Result<JsonExtractor<SequenceCreateRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    let sequence = make_sequence(body.id, body.name, body.repeat, body.steps)?;
    state.control.create_sequence(sequence).await.map(Json)
}

async fn update_sequence(
    State(state): State<AppState>,
    Path(sequence_id): Path<String>,
    body: Result<JsonExtractor<SequenceDefinitionRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    let sequence_id = validate_sequence_path_id(sequence_id)?;
    let sequence = make_sequence_with_id(sequence_id.clone(), body.name, body.repeat, body.steps)?;
    state
        .control
        .update_sequence(sequence_id, sequence)
        .await
        .map(Json)
}

async fn delete_sequence(
    State(state): State<AppState>,
    Path(sequence_id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let sequence_id = validate_sequence_path_id(sequence_id)?;
    state.control.delete_sequence(sequence_id).await.map(Json)
}

async fn set_active_sequence(
    State(state): State<AppState>,
    body: Result<JsonExtractor<ActiveSequenceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    let sequence_id = match body.sequence_id {
        Value::Null => None,
        Value::String(id) => Some(validate_sequence_text(
            id,
            json!(["body", "sequence_id"]),
            64,
        )?),
        input => {
            return Err(ApiError::validation(json!([{
                "type": "string_type",
                "loc": ["body", "sequence_id"],
                "msg": "Input should be a valid string",
                "input": input,
            }])));
        }
    };
    state.control.activate_sequence(sequence_id).await.map(Json)
}

fn make_sequence(
    id: String,
    name: String,
    repeat: bool,
    steps: Vec<SequenceStepRequest>,
) -> Result<SavedAnimationSequence, ApiError> {
    let id = validate_sequence_text(id, json!(["body", "id"]), 64)?;
    make_sequence_with_id(id, name, repeat, steps)
}

fn make_sequence_with_id(
    id: String,
    name: String,
    repeat: bool,
    steps: Vec<SequenceStepRequest>,
) -> Result<SavedAnimationSequence, ApiError> {
    let name = validate_sequence_text(name, json!(["body", "name"]), 80)?;
    if steps.is_empty() {
        return Err(ApiError::validation(json!([{
            "type": "too_short",
            "loc": ["body", "steps"],
            "msg": "List should have at least 1 item after validation, not 0",
            "input": steps.len(),
            "ctx": {"min_length": 1},
        }])));
    }
    if steps.len() > 32 {
        return Err(ApiError::validation(json!([{
            "type": "too_long",
            "loc": ["body", "steps"],
            "msg": format!("List should have at most 32 items after validation, not {}", steps.len()),
            "input": steps.len(),
            "ctx": {"max_length": 32},
        }])));
    }
    let steps = steps
        .into_iter()
        .enumerate()
        .map(|(index, step)| {
            let animation_name = validate_sequence_text(
                step.animation_name,
                json!(["body", "steps", index, "animation_name"]),
                100,
            )?;
            if !matches!(step.duration_bars, 1 | 2 | 4 | 8) {
                return Err(ApiError::validation(json!([{
                    "type": "literal_error",
                    "loc": ["body", "steps", index, "duration_bars"],
                    "msg": "Input should be 1, 2, 4 or 8",
                    "input": step.duration_bars,
                    "ctx": {"expected": "1, 2, 4 or 8"},
                }])));
            }
            Ok(SavedAnimationSequenceStep {
                animation_name,
                duration_bars: step.duration_bars,
            })
        })
        .collect::<Result<Vec<_>, ApiError>>()?;
    Ok(SavedAnimationSequence {
        id,
        name,
        repeat,
        steps,
    })
}

fn validate_sequence_path_id(value: String) -> Result<String, ApiError> {
    let length = value.chars().count();
    if length == 0 || length > 64 {
        let error = if length == 0 {
            json!([{
                "type": "string_too_short",
                "loc": ["path", "sequence_id"],
                "msg": "String should have at least 1 character",
                "input": value,
                "ctx": {"min_length": 1},
            }])
        } else {
            json!([{
                "type": "string_too_long",
                "loc": ["path", "sequence_id"],
                "msg": "String should have at most 64 characters",
                "input": value,
                "ctx": {"max_length": 64},
            }])
        };
        return Err(ApiError::validation(error));
    }
    Ok(value)
}

fn validate_sequence_text(
    value: String,
    location: Value,
    max_characters: usize,
) -> Result<String, ApiError> {
    let value = value.trim().to_owned();
    let length = value.chars().count();
    if length == 0 || length > max_characters {
        let error = if length == 0 {
            json!([{
                "type": "string_too_short",
                "loc": location,
                "msg": "String should have at least 1 character",
                "input": value,
                "ctx": {"min_length": 1},
            }])
        } else {
            json!([{
                "type": "string_too_long",
                "loc": location,
                "msg": format!("String should have at most {max_characters} characters"),
                "input": value,
                "ctx": {"max_length": max_characters},
            }])
        };
        return Err(ApiError::validation(error));
    }
    Ok(value)
}

async fn auth_status(State(state): State<AppState>, headers: HeaderMap) -> Json<Value> {
    Json(json!({
        "required": state.access.required(),
        "authenticated": state.access.authenticated(session_from_headers(&headers).as_deref()),
    }))
}

#[derive(Deserialize)]
struct PairRequest {
    code: String,
}

async fn pair(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Result<JsonExtractor<PairRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Response, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.code.len() != 4
        || !body.code.is_ascii()
        || !body.code.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(ApiError::validation(json!([{
            "type": "string_pattern_mismatch",
            "loc": ["body", "code"],
            "msg": "String should match pattern '^[0-9]{4}$'",
            "input": body.code,
            "ctx": {"pattern": "^[0-9]{4}$"},
        }])));
    }
    let session = match state.access.pair(&body.code) {
        Ok(session) => session,
        Err(PairingError::InvalidCode) => {
            return Err(ApiError::new(
                StatusCode::UNAUTHORIZED,
                "The pairing code is incorrect.",
            ));
        }
        Err(PairingError::RateLimited { retry_after }) => {
            let mut response = ApiError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "Too many pairing attempts. Please try again later.",
            )
            .into_response();
            response.headers_mut().insert(
                RETRY_AFTER,
                HeaderValue::from_str(&retry_after.to_string())
                    .unwrap_or(HeaderValue::from_static("1")),
            );
            return Ok(response);
        }
    };
    let mut response = Json(json!({
        "required": state.access.required(),
        "authenticated": true,
    }))
    .into_response();
    let secure = state.secure_cookie || effective_scheme(&headers) == "https";
    let mut cookie = format!(
        "{SESSION_COOKIE}={session}; Path=/; Max-Age={SESSION_MAX_AGE_SECONDS}; HttpOnly; SameSite=Strict"
    );
    if secure {
        cookie.push_str("; Secure");
    }
    if let Ok(value) = HeaderValue::from_str(&cookie) {
        response.headers_mut().insert(SET_COOKIE, value);
    }
    Ok(response)
}

async fn logout(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let token = session_from_headers(&headers);
    state.access.revoke(token.as_deref());
    let mut response = Json(json!({
        "required": state.access.required(),
        "authenticated": !state.access.required(),
    }))
    .into_response();
    let secure = state.secure_cookie || effective_scheme(&headers) == "https";
    let mut cookie = format!("{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict");
    if secure {
        cookie.push_str("; Secure");
    }
    if let Ok(value) = HeaderValue::from_str(&cookie) {
        response.headers_mut().insert(SET_COOKIE, value);
    }
    response
}

async fn access_middleware(
    State(access): State<PairingAuth>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let path = request.uri().path();
    if matches!(path, "/ws/state" | "/ws/audio" | "/ws/preview") {
        if !websocket_origin_allowed(request.headers()) {
            return ApiError::new(StatusCode::FORBIDDEN, "invalid origin").into_response();
        }
        let session = session_from_headers(request.headers());
        if !access.authenticated(session.as_deref()) {
            return ApiError::new(StatusCode::FORBIDDEN, "pairing required").into_response();
        }
    }
    if path.starts_with("/api/")
        && matches!(
            request.method(),
            &Method::POST | &Method::PUT | &Method::PATCH | &Method::DELETE
        )
        && request.headers().contains_key(ORIGIN)
        && !websocket_origin_allowed(request.headers())
    {
        return ApiError::new(
            StatusCode::FORBIDDEN,
            "Cross-origin API requests are not allowed.",
        )
        .into_response();
    }
    let public = matches!(path, "/api/health" | "/api/auth/status" | "/api/auth/pair");
    if path.starts_with("/api/") && !public {
        let session = session_from_headers(request.headers());
        if !access.authenticated(session.as_deref()) {
            return ApiError::new(StatusCode::UNAUTHORIZED, "Pairing required.").into_response();
        }
    }
    next.run(request).await
}

async fn api_not_found() -> Response {
    (StatusCode::NOT_FOUND, Json(json!({"detail": "Not Found"}))).into_response()
}

fn json_rejection(rejection: axum::extract::rejection::JsonRejection) -> ApiError {
    ApiError::validation(json!([{
        "type": "json_invalid",
        "loc": ["body"],
        "msg": rejection.body_text(),
        "input": Value::Null,
    }]))
}

fn session_from_headers(headers: &HeaderMap) -> Option<String> {
    headers
        .get(COOKIE)?
        .to_str()
        .ok()?
        .split(';')
        .filter_map(|part| part.trim().split_once('='))
        .find_map(|(name, value)| (name == SESSION_COOKIE).then(|| value.to_owned()))
}

pub(crate) fn websocket_origin_allowed(headers: &HeaderMap) -> bool {
    let Some(origin) = headers.get(ORIGIN).and_then(|value| value.to_str().ok()) else {
        return true;
    };
    let Some(host) = headers.get(HOST).and_then(|value| value.to_str().ok()) else {
        return false;
    };
    let scheme = effective_scheme(headers);
    let Ok(origin_url) = Url::parse(origin) else {
        return false;
    };
    let Ok(request_url) = Url::parse(&format!("{scheme}://{host}")) else {
        return false;
    };
    origin_url.scheme() == scheme
        && origin_url.username().is_empty()
        && origin_url.password().is_none()
        && origin_url.path() == "/"
        && origin_url.query().is_none()
        && origin_url.fragment().is_none()
        && origin_url
            .host_str()
            .zip(request_url.host_str())
            .is_some_and(|(left, right)| left.eq_ignore_ascii_case(right))
        && origin_url.port_or_known_default() == request_url.port_or_known_default()
}

fn effective_scheme(headers: &HeaderMap) -> &str {
    headers
        .get("x-forwarded-proto")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(',').next())
        .map(str::trim)
        .filter(|scheme| *scheme == "http" || *scheme == "https")
        .unwrap_or("http")
}

fn default_static_dir() -> PathBuf {
    std::env::var_os("LUMISTRIPE_WEB_STATIC_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../../apps/lumistripe-web/src/lumistripe_web/static")
        })
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("install SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {},
            _ = terminate.recv() => {},
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

async fn supervise_engine(
    supervisor: EngineSupervisor,
    mut desired_state: watch::Receiver<EngineDesiredState>,
    ready: oneshot::Sender<Result<(), String>>,
) {
    let EngineSupervisor {
        binary,
        socket_path,
        client,
        health,
        cancellation,
    } = supervisor;
    let mut ready = Some(ready);
    let mut backoff = Duration::from_millis(250);
    loop {
        if cancellation.is_cancelled() {
            break;
        }
        let desired = desired_state.borrow_and_update().clone();
        let mut child = match Command::new(&binary)
            .arg("--socket")
            .arg(&socket_path)
            .arg("--owner-pid")
            .arg(std::process::id().to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
        {
            Ok(child) => child,
            Err(error) => {
                let message = format!("could not start Rust engine {}: {error}", binary.display());
                if let Some(ready) = ready.take() {
                    let _ = ready.send(Err(message));
                    return;
                }
                health.send_replace(Some(Err(message.clone())));
                tracing::error!(error = %message, "Rust engine restart failed");
                sleep(backoff).await;
                backoff = (backoff * 2).min(Duration::from_secs(5));
                continue;
            }
        };
        let startup = async {
            wait_for_engine_ready(&client, &mut child).await?;
            match client
                .configure(desired.config.clone())
                .await
                .map_err(|error| error.to_string())?
            {
                ResponseResult::Configured { .. } => {}
                _ => {
                    return Err(
                        "Rust engine returned an unexpected configure acknowledgement".to_owned(),
                    );
                }
            }
            match client
                .update_scene(desired.scene)
                .await
                .map_err(|error| error.to_string())?
            {
                ResponseResult::SceneUpdated { .. } => {}
                _ => {
                    return Err(
                        "Rust engine returned an unexpected scene acknowledgement".to_owned()
                    );
                }
            }
            if let Some(audio) = desired.audio {
                let restore = client
                    .request(RequestCommand::AudioStart {
                        selector: audio.selector,
                        config: audio.config,
                    })
                    .await;
                match restore {
                    Ok(ResponseResult::AudioConfigured { active: true }) => {
                        tracing::info!("Rust engine restored acknowledged audio configuration");
                    }
                    Ok(other) => {
                        tracing::warn!(
                            response = ?other,
                            "Rust engine restarted without restoring acknowledged audio configuration"
                        );
                    }
                    Err(error) => {
                        tracing::warn!(
                            error = %error,
                            "Rust engine restarted without restoring acknowledged audio configuration"
                        );
                    }
                }
            }
            Ok::<(), String>(())
        };
        let startup_result = timeout(ENGINE_STARTUP_TIMEOUT, startup).await;
        let setup_error = match startup_result {
            Ok(Ok(())) => None,
            Ok(Err(error)) => Some(error),
            Err(_) => Some("Rust engine startup exceeded its deadline".to_owned()),
        };
        if let Some(error) = setup_error {
            let _ = child.kill().await;
            let _ = child.wait().await;
            if let Some(ready) = ready.take() {
                let _ = ready.send(Err(error));
                return;
            }
            health.send_replace(Some(Err(error.clone())));
            tracing::error!(error = %error, "Rust engine failed to restart");
            tokio::select! {
                _ = cancellation.cancelled() => break,
                _ = sleep(backoff) => {}
            }
            backoff = (backoff * 2).min(Duration::from_secs(5));
            continue;
        }

        backoff = Duration::from_millis(250);
        if let Ok(result @ ResponseResult::Health { .. }) = client.health().await {
            health.send_replace(Some(Ok(Arc::new(result))));
        }
        if let Some(ready) = ready.take() {
            let _ = ready.send(Ok(()));
            tracing::info!("Rust engine started and owns configured outputs");
        } else {
            tracing::info!("Rust engine restarted and reapplied the latest desired state");
        }

        tokio::select! {
            _ = cancellation.cancelled() => {
                match client.shutdown().await {
                    Ok(ResponseResult::Shutdown) => {}
                    Ok(_) => tracing::warn!("Rust engine returned an unexpected shutdown response"),
                    Err(error) => tracing::debug!(error = %error, "Rust engine was already unavailable during shutdown"),
                }
                if timeout(Duration::from_secs(5), child.wait()).await.is_err() {
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                }
                break;
            }
            result = child.wait() => {
                let message = match result {
                    Ok(status) => format!("Rust engine exited with {status}"),
                    Err(error) => format!("Rust engine wait failed: {error}"),
                };
                health.send_replace(Some(Err(message.clone())));
                tracing::error!(error = %message, "Rust engine stopped; restarting it");
                tokio::select! {
                    _ = cancellation.cancelled() => break,
                    _ = sleep(backoff) => {}
                }
                backoff = (backoff * 2).min(Duration::from_secs(5));
            }
        }
    }
}

async fn wait_for_engine_ready(client: &EngineClient, child: &mut Child) -> Result<(), String> {
    let deadline = tokio::time::Instant::now() + ENGINE_STARTUP_TIMEOUT;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("check Rust engine process: {error}"))?
        {
            return Err(format!("Rust engine exited during startup with {status}"));
        }
        match client.health().await {
            Ok(ResponseResult::Health {
                status: lumistripe_engine_protocol::EngineStatus::Ready,
                ..
            }) => return Ok(()),
            Ok(_) => {}
            Err(_) => {}
        }
        if tokio::time::Instant::now() >= deadline {
            return Err("Rust engine did not become ready before the startup deadline".to_owned());
        }
        sleep(Duration::from_millis(50)).await;
    }
}

fn spawn_audio_status_publisher(
    client: EngineClient,
    cancellation: CancellationToken,
    publisher: watch::Sender<Option<Result<lumistripe_engine_protocol::EngineAudioStatus, String>>>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut poll = tokio::time::interval(Duration::from_secs_f64(1.0 / 15.0));
        poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = cancellation.cancelled() => break,
                _ = poll.tick() => {
                    let result = match client.audio_status().await {
                        Ok(ResponseResult::AudioStatus { status }) => Ok(status),
                        Ok(_) => Err("Rust engine returned an unexpected audio-status response".to_owned()),
                        Err(error) => Err(error.to_string()),
                    };
                    publisher.send_replace(Some(result));
                }
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::websocket_origin_allowed;
    use axum::http::{HeaderMap, HeaderValue};

    #[test]
    fn origin_check_accepts_same_origin_and_rejects_cross_origin() {
        let mut headers = HeaderMap::new();
        headers.insert("host", HeaderValue::from_static("localhost:8000"));
        headers.insert("origin", HeaderValue::from_static("http://localhost:8000"));
        assert!(websocket_origin_allowed(&headers));
        headers.insert("origin", HeaderValue::from_static("https://evil.example"));
        assert!(!websocket_origin_allowed(&headers));
    }
}
