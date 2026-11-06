use crate::api_schema::{
    AccessStatus, AnimationList, AnimationSettingsResponse, AudioCalibrationSessionResponse,
    AudioOutputDeviceInfoSchema, AudioResetRequest, AudioSettingsResponse,
    AudioSyncCalibrationResponse, AudioTuningValuesSchema, BluetoothStatusResponseSchema,
    CalibrationSessionResponse, DashboardStateSchema, IdentitySettingsResponse,
    SequenceLibraryResponse, SequencerSettingsResponse, StartupSettingsResponse,
    StripeTopologyResponse, ValidationLocation,
};
use crate::auth::{PairingAuth, PairingError, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS};
use crate::bluetooth::BluetoothService;
use crate::bluetooth_models::{
    AudioOutputMuteRequest, AudioOutputSelectionRequest, AudioOutputVolumeRequest,
    BluetoothAliasRequest, BluetoothDeviceRequest, BluetoothPowerRequest, BluetoothRole,
    BluetoothStatus,
};
use crate::bluetooth_process::BluetoothError;
use crate::bluetooth_providers::{BlueZProvider, PipeWireProvider};
use crate::config::{AppConfig, SpotifyBackend};
use crate::control::{ControlPlane, EngineAccess};
use crate::error::ApiError;
use crate::network::{
    NetworkError, NetworkManagerService, NetworkStatusResponse, WifiConnectRequest,
    WifiConnectResponse, WifiScanRequest, WifiScanResponse, remove_secret_from_message,
};
use crate::request_validation::{
    deserialize_optional_pydantic_bool, deserialize_pydantic_bool, pydantic_bool_error,
};
use crate::settings::{
    AudioTuningProfile, ColorCorrection, MusicIdleMode, MusicIdleSettings, SavedAnimationSequence,
    SavedAnimationSequenceStep, SettingsStore, StripeBackend, StripeLayout, StripeOutputSettings,
    StripeTopologySettings, SwitchQuantization, TransitionEffect, TransitionSettings, ZonePattern,
};
use crate::spotify::{
    DisabledSpotifyBackend, LibrespotBackend, SoloistBackend, SpotifyControlAction,
    SpotifyProvider, SpotifyStatus,
};
use crate::state::{ControlModel, PlaybackMode, animation_list};
use crate::streams::{
    AudioTelemetryReceiver, PreviewReceiver, spawn_audio_telemetry_publisher,
    spawn_engine_failure_monitor, spawn_health_publisher, spawn_preview_publisher, websocket_audio,
    websocket_preview, websocket_state,
};
use crate::system_stats::{SystemStats, spawn_system_stats_publisher};
use crate::updates::{SystemUpdateService, UpdateError, UpdateStatus};
use axum::Json;
use axum::body::{Body, to_bytes};
use axum::extract::{Json as JsonExtractor, MatchedPath, Path, Query, Request, State};
use axum::http::header::{CONTENT_TYPE, COOKIE, HOST, ORIGIN, RETRY_AFTER, SET_COOKIE};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post, put};
use axum::{Router, middleware as axum_middleware};
use lumistripe_engine_animation_definitions::catalog;
use lumistripe_engine_audio::{PcmIngressSender, PcmPlaybackState, monotonic_time_ns};
use lumistripe_engine_protocol::{RequestCommand, ResponseResult};
use lumistripe_engine_runtime::{EngineHandle, EngineRuntime};
use lumistripe_spotify::{DecodedPcmPlaybackState, DecodedPcmTap, SpotifyConnectConfig};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value, json};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tokio::time::{Instant as TokioInstant, timeout, timeout_at};
use tokio_util::sync::CancellationToken;
use tower_http::services::{ServeDir, ServeFile};
use tower_http::trace::TraceLayer;
use url::Url;

#[derive(Clone)]
pub(crate) struct AppState {
    pub(crate) control: ControlPlane,
    pub(crate) access: PairingAuth,
    pub(crate) audio_telemetry: AudioTelemetryReceiver,
    pub(crate) preview: PreviewReceiver,
    pub(crate) dashboard_state: watch::Receiver<Arc<Value>>,
    pub(crate) system_stats: watch::Receiver<SystemStats>,
    pub(crate) updates: SystemUpdateService,
    pub(crate) network: NetworkManagerService,
    pub(crate) bluetooth: BluetoothService,
    pub(crate) spotify: Arc<dyn SpotifyProvider>,
    secure_cookie: bool,
}

#[derive(utoipa::OpenApi)]
#[openapi(
    info(title = "LumiStripe", version = "0.1.0"),
    paths(
        health,
        runtime_state,
        system_stats,
        update_status,
        check_for_updates,
        install_update,
        network_status,
        network_scan,
        network_connect,
        animations,
        get_animation_settings,
        put_animation_settings,
        reset_animation_settings,
        set_mode,
        set_brightness,
        select_animation,
        set_blackout,
        get_stripe_topology,
        put_stripe_topology,
        test_stripe,
        start_color_calibration,
        update_color_calibration,
        finish_color_calibration,
        get_audio_settings,
        select_audio_device,
        put_audio_settings,
        reset_audio_settings,
        select_audio_source,
        set_audio_sync_offset,
        set_audio_sync_calibration,
        start_audio_calibration,
        get_audio_calibration,
        begin_audio_calibration_music,
        finish_audio_calibration,
        get_spotify_status,
        control_spotify,
        bluetooth_status,
        bluetooth_power,
        bluetooth_alias,
        bluetooth_scan,
        bluetooth_pair,
        bluetooth_connect,
        bluetooth_forget,
        bluetooth_disconnect,
        select_audio_output,
        set_audio_output_volume,
        set_audio_output_mute,
        get_identity_settings,
        put_identity_settings,
        set_music_idle,
        get_startup_settings,
        put_startup_settings,
        get_transition_settings,
        put_transition_settings,
        get_sequencer_settings,
        put_sequencer_settings,
        get_sequence_library,
        create_sequence,
        update_sequence,
        delete_sequence,
        set_active_sequence,
        auth_status,
        pair,
        logout
    ),
    components(schemas(
        SpotifyControlRequest,
        AccessStatus,
        AnimationList,
        AnimationSettingsResponse,
        AudioCalibrationSessionResponse,
        CalibrationSessionResponse,
        AudioResetRequest,
        AudioSettingsResponse,
        AudioSyncCalibrationResponse,
        DashboardStateSchema,
        IdentitySettingsResponse,
        SequenceLibraryResponse,
        SequencerSettingsResponse,
        StartupSettingsResponse,
        StripeTopologyResponse,
        BluetoothStatusResponseSchema,
        AudioOutputDeviceInfoSchema,
        AudioTuningValuesSchema,
        MusicIdleRequestSchema,
        BluetoothPowerRequest,
        BluetoothAliasRequest,
        BluetoothDeviceRequest,
        AudioOutputSelectionRequest,
        AudioOutputVolumeRequest,
        AudioOutputMuteRequest,
        NetworkStatusResponse,
        WifiScanRequest,
        WifiScanResponse,
        WifiConnectRequest,
        WifiConnectResponse,
        SystemStats,
        UpdateStatus,
        TransitionEffect,
        ErrorResponseSchema,
        ValidationErrorSchema,
        HttpValidationErrorSchema
    )),
    tags(
        (name = "system", description = "System status and update operations"),
        (name = "network", description = "Wi-Fi network operations"),
        (name = "audio", description = "Spotify, Bluetooth, and audio output operations")
    )
)]
pub struct ApiDoc;

pub struct WebApplication {
    router: Router,
    cancellation: CancellationToken,
    spotify: Arc<dyn SpotifyProvider>,
    engine: Option<EngineRuntime>,
    engine_failure: watch::Receiver<Option<String>>,
    tasks: Vec<JoinHandle<()>>,
}

const APPLICATION_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(10);

impl WebApplication {
    pub async fn start(config: AppConfig, pairing_code: Option<String>) -> Result<Self, String> {
        Self::start_inner(config, pairing_code, None, None).await
    }

    #[cfg(test)]
    async fn start_with_bluetooth_providers(
        config: AppConfig,
        pairing_code: Option<String>,
        bluez: Arc<dyn BlueZProvider>,
        pipewire: Arc<dyn PipeWireProvider>,
        network: Option<NetworkManagerService>,
    ) -> Result<Self, String> {
        Self::start_inner(config, pairing_code, Some((bluez, pipewire)), network).await
    }

    async fn start_inner(
        mut config: AppConfig,
        pairing_code: Option<String>,
        bluetooth_providers: Option<(Arc<dyn BlueZProvider>, Arc<dyn PipeWireProvider>)>,
        network_service: Option<NetworkManagerService>,
    ) -> Result<Self, String> {
        let settings_store =
            SettingsStore::open(&config.settings_file).map_err(|error| error.to_string())?;
        let settings = settings_store.settings().clone();
        let spotify_device_name = settings.spotify_device_name.clone();
        if let Some(topology) = &settings.stripe_topology {
            config.apply_topology(topology)?;
        }
        if settings.selected_audio_device.is_some() {
            config.audio_device = settings.selected_audio_device.clone();
        }
        let access = PairingAuth::new(pairing_code)?;
        let engine = EngineRuntime::start().map_err(|error| error.to_string())?;
        let client = engine.handle();
        let pcm_ingress = client
            .audio_pcm_ingress()
            .map_err(|error| error.to_string())?;
        let control_client = EngineAccess::direct(client.clone());
        let cancellation = CancellationToken::new();
        let model = ControlModel::new(config.clone(), settings);
        let initial_scene = model.initial_scene();
        match client
            .configure(config.engine_config())
            .await
            .map_err(|error| error.to_string())?
        {
            ResponseResult::Configured { .. } => {}
            other => {
                return Err(format!(
                    "Rust engine returned an unexpected configure acknowledgement: {other:?}"
                ));
            }
        }
        match client
            .update_scene(initial_scene)
            .await
            .map_err(|error| error.to_string())?
        {
            ResponseResult::SceneUpdated { .. } => {}
            other => {
                return Err(format!(
                    "Rust engine returned an unexpected initial scene acknowledgement: {other:?}"
                ));
            }
        }
        let health_response = client.health().await.map_err(|error| error.to_string())?;
        let (health_sender, health) = watch::channel(Some(Ok(Arc::new(health_response))));
        let (engine_failure_sender, engine_failure) = watch::channel(None);
        let (audio_sender, audio) = watch::channel(None);
        let (preview_sender, preview) = watch::channel(None);

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
                    model.active_audio_source = "mic".to_owned();
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
        let (spotify, spotify_task) = create_spotify_provider(
            &config,
            spotify_device_name,
            cancellation.clone(),
            pcm_ingress,
        );
        spotify.start();
        let bluetooth_enabled = config.runtime_kind == "hardware";
        let spotify_audio_routing_enabled = bluetooth_enabled || spotify_enabled(&config);
        let (bluetooth, bluetooth_task) = match bluetooth_providers {
            Some((bluez, pipewire)) => BluetoothService::with_limits_and_providers(
                bluetooth_enabled,
                spotify_audio_routing_enabled,
                bluez,
                pipewire,
                config.bluetooth_max_inputs,
                config.bluetooth_max_outputs,
                cancellation.clone(),
            ),
            None => BluetoothService::new(
                bluetooth_enabled,
                spotify_audio_routing_enabled,
                config.bluetooth_max_inputs,
                config.bluetooth_max_outputs,
                cancellation.clone(),
            ),
        };
        let (control, control_task) = ControlPlane::start(
            model,
            settings_store,
            control_client,
            health.clone(),
            audio.clone(),
            Some(bluetooth.clone()),
            Some(spotify.clone()),
            cancellation.clone(),
        );
        let (dashboard_state, dashboard_state_task) = spawn_dashboard_state_publisher(
            control.state(),
            spotify.clone(),
            bluetooth.clone(),
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
        let (system_stats, system_stats_task) = spawn_system_stats_publisher(cancellation.clone());
        let app_state = AppState {
            control,
            access: access.clone(),
            audio_telemetry: telemetry_receiver,
            preview: preview.clone(),
            dashboard_state,
            system_stats,
            updates: SystemUpdateService::with_cancellation(cancellation.clone()),
            network: network_service.unwrap_or_default(),
            bluetooth,
            spotify: spotify.clone(),
            secure_cookie: config.secure_cookie,
        };
        let router = build_router(app_state, config.static_dir.clone());
        let mut tasks = vec![
            control_task,
            dashboard_state_task,
            spawn_health_publisher(client.clone(), cancellation.clone(), health_sender),
            spawn_engine_failure_monitor(
                health.clone(),
                cancellation.clone(),
                engine_failure_sender,
            ),
            spawn_audio_status_publisher(client.clone(), cancellation.clone(), audio_sender),
            spawn_preview_publisher(health, cancellation.clone(), preview_sender),
            telemetry_task,
            system_stats_task,
        ];
        if let Some(spotify_task) = spotify_task {
            tasks.push(spotify_task);
        }
        tasks.push(bluetooth_task);
        Ok(Self {
            router,
            cancellation,
            spotify,
            engine: Some(engine),
            engine_failure,
            tasks,
        })
    }

    pub fn router(&self) -> Router {
        self.router.clone()
    }

    pub async fn serve(mut self, host: &str, port: u16) -> Result<(), String> {
        let startup_failure = { self.engine_failure.borrow().clone() };
        if let Some(error) = startup_failure {
            self.shutdown().await;
            return Err(format!("in-process Rust engine failed: {error}"));
        }
        let listener = TcpListener::bind((host, port))
            .await
            .map_err(|error| format!("bind LumiStripe web listener: {error}"))?;
        let local_address = listener
            .local_addr()
            .map_err(|error| format!("read LumiStripe web listener address: {error}"))?;
        tracing::info!(address = %local_address, "LumiStripe Rust web server is listening");
        let cancellation = self.cancellation.clone();
        let mut engine_failure_signal = self.engine_failure.clone();
        let serve_result = axum::serve(listener, self.router.clone())
            .with_graceful_shutdown(async move {
                tokio::select! {
                    _ = shutdown_signal() => {}
                    _ = engine_failure_signal.changed() => {}
                }
                cancellation.cancel();
            })
            .await;
        let engine_failure = self.engine_failure.borrow().clone();
        self.shutdown().await;
        if let Some(error) = engine_failure {
            return Err(format!("in-process Rust engine failed: {error}"));
        }
        serve_result.map_err(|error| format!("LumiStripe web server failed: {error}"))
    }

    pub async fn shutdown(&mut self) {
        let deadline = TokioInstant::now() + APPLICATION_SHUTDOWN_TIMEOUT;
        self.cancellation.cancel();
        shutdown_background_tasks(&mut self.tasks, deadline).await;
        if let Some(mut engine) = self.engine.take() {
            if TokioInstant::now() >= deadline {
                tracing::warn!(
                    "Rust web application shutdown deadline elapsed before engine shutdown"
                );
                drop(engine);
            } else {
                // EngineRuntime::shutdown joins engine threads; a timed-out
                // spawn_blocking task would still hold up Tokio runtime teardown.
                let (shutdown_sender, shutdown_receiver) = tokio::sync::oneshot::channel();
                match std::thread::Builder::new()
                    .name("lumistripe-web-engine-shutdown".to_owned())
                    .spawn(move || {
                        let _ = shutdown_sender.send(engine.shutdown());
                    }) {
                    Ok(_shutdown_thread) => match timeout_at(deadline, shutdown_receiver).await {
                        Ok(Ok(Ok(()))) => {}
                        Ok(Ok(Err(error))) => {
                            tracing::error!(error = %error, "Rust engine shutdown failed")
                        }
                        Ok(Err(_)) => {
                            tracing::error!("Rust engine shutdown thread stopped without a result")
                        }
                        Err(_) => tracing::warn!(
                            "Rust engine shutdown did not finish before the application shutdown deadline"
                        ),
                    },
                    Err(error) => {
                        tracing::error!(error = %error, "Rust engine shutdown thread failed to start")
                    }
                }
            }
        }
    }
}

async fn shutdown_background_tasks(tasks: &mut Vec<JoinHandle<()>>, deadline: TokioInstant) {
    for (index, mut task) in tasks.drain(..).enumerate() {
        match timeout_at(deadline, &mut task).await {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                tracing::warn!(task_index = index, error = %error, "Rust web background task failed during shutdown");
            }
            Err(_) => {
                task.abort();
                tracing::warn!(
                    task_index = index,
                    "Rust web background task did not stop before the shutdown deadline; aborting it"
                );
            }
        }
    }
}

impl Drop for WebApplication {
    fn drop(&mut self) {
        self.spotify.stop();
        self.cancellation.cancel();
    }
}

pub(crate) fn build_router(state: AppState, static_dir: Option<PathBuf>) -> Router {
    let routes = Router::new()
        .route("/api/health", get(health))
        .route("/api/state", get(runtime_state))
        .route("/api/system/stats", get(system_stats))
        .route("/api/system/update", get(update_status))
        .route("/api/system/update/check", post(check_for_updates))
        .route("/api/system/update/install", post(install_update))
        .route("/api/network/status", get(network_status))
        .route("/api/network/scan", post(network_scan))
        .route("/api/network/connect", post(network_connect))
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
        .route("/api/audio/spotify", get(get_spotify_status))
        .route("/api/audio/spotify/control", post(control_spotify))
        .route("/api/audio/bluetooth", get(bluetooth_status))
        .route("/api/audio/bluetooth/power", put(bluetooth_power))
        .route("/api/audio/bluetooth/alias", put(bluetooth_alias))
        .route("/api/audio/bluetooth/scan", post(bluetooth_scan))
        .route("/api/audio/bluetooth/pair", post(bluetooth_pair))
        .route("/api/audio/bluetooth/connect", post(bluetooth_connect))
        .route("/api/audio/bluetooth/forget", post(bluetooth_forget))
        .route(
            "/api/audio/bluetooth/disconnect",
            post(bluetooth_disconnect),
        )
        .route("/api/audio/output", put(select_audio_output))
        .route("/api/audio/output/volume", put(set_audio_output_volume))
        .route("/api/audio/output/mute", put(set_audio_output_mute))
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
        .route_layer(axum_middleware::from_fn(validate_json_object))
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

#[utoipa::path(
    get,
    path = "/api/health",
    tag = "system",
    responses((status = 200, body = Value))
)]
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

#[utoipa::path(
    get,
    path = "/api/system/stats",
    tag = "system",
    responses((status = 200, body = SystemStats))
)]
async fn system_stats(State(state): State<AppState>) -> Json<SystemStats> {
    Json(state.system_stats.borrow().clone())
}

#[utoipa::path(
    get,
    path = "/api/system/update",
    tag = "system",
    responses((status = 200, body = UpdateStatus))
)]
async fn update_status(State(state): State<AppState>) -> Json<UpdateStatus> {
    Json(state.updates.status())
}

#[utoipa::path(
    post,
    path = "/api/system/update/check",
    tag = "system",
    responses((status = 202, body = UpdateStatus))
)]
async fn check_for_updates(
    State(state): State<AppState>,
) -> Result<(StatusCode, Json<UpdateStatus>), ApiError> {
    state
        .updates
        .check()
        .await
        .map(|status| (StatusCode::ACCEPTED, Json(status)))
        .map_err(update_api_error)
}

#[utoipa::path(
    post,
    path = "/api/system/update/install",
    tag = "system",
    responses((status = 202, body = UpdateStatus))
)]
async fn install_update(
    State(state): State<AppState>,
) -> Result<(StatusCode, Json<UpdateStatus>), ApiError> {
    state
        .updates
        .install()
        .await
        .map(|status| (StatusCode::ACCEPTED, Json(status)))
        .map_err(update_api_error)
}

fn update_api_error(error: UpdateError) -> ApiError {
    let status = match error.code {
        "unsupported" | "already_running" | "dirty_checkout" | "no_update" => StatusCode::CONFLICT,
        _ => StatusCode::SERVICE_UNAVAILABLE,
    };
    ApiError::new(status, error.message)
}

#[utoipa::path(
    get,
    path = "/api/audio/spotify",
    tag = "audio",
    responses((status = 200, body = SpotifyStatus))
)]
async fn get_spotify_status(State(state): State<AppState>) -> Json<Value> {
    Json(spotify_status_value(&state.spotify.status()))
}

#[derive(Deserialize, utoipa::ToSchema)]
struct SpotifyControlRequest {
    action: SpotifyControlAction,
    #[serde(default)]
    #[schema(schema_with = crate::api_schema::spotify_control_value_schema)]
    value: Option<Value>,
}

#[derive(utoipa::ToSchema)]
#[schema(as = ValidationError)]
#[allow(dead_code)]
struct ValidationErrorSchema {
    loc: Vec<ValidationLocation>,
    msg: String,
    #[schema(rename = "type")]
    error_type: String,
    #[schema(nullable = true)]
    input: Option<Value>,
    #[schema(nullable = true)]
    ctx: Option<Map<String, Value>>,
}

#[derive(utoipa::ToSchema)]
#[schema(as = HTTPValidationError)]
#[allow(dead_code)]
struct HttpValidationErrorSchema {
    #[schema(required = false)]
    detail: Vec<ValidationErrorSchema>,
}

#[derive(utoipa::ToSchema)]
#[schema(as = ErrorResponse)]
#[allow(dead_code)]
struct ErrorResponseSchema {
    detail: String,
}

#[utoipa::path(
    post,
    path = "/api/audio/spotify/control",
    tag = "audio",
    request_body = SpotifyControlRequest,
    responses(
        (status = 200, body = SpotifyStatus),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn control_spotify(
    State(state): State<AppState>,
    body: Result<JsonExtractor<SpotifyControlRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    state
        .spotify
        .control(body.action, body.value)
        .await
        .map(|status| Json(spotify_status_value(&status)))
        .map_err(|error| ApiError::new(StatusCode::CONFLICT, error.message))
}

fn spotify_status_value(status: &SpotifyStatus) -> Value {
    serde_json::to_value(status).expect("Spotify status contains only JSON-compatible fields")
}

fn spotify_enabled(config: &AppConfig) -> bool {
    config.spotify_backend.is_enabled()
}

fn create_spotify_provider(
    config: &AppConfig,
    device_name: String,
    cancellation: CancellationToken,
    pcm_ingress: PcmIngressSender,
) -> (Arc<dyn SpotifyProvider>, Option<JoinHandle<()>>) {
    match config.spotify_backend {
        SpotifyBackend::Librespot => {
            let native_config =
                SpotifyConnectConfig::new(config.spotify_data_dir.clone(), device_name)
                    .with_pcm_tap(Arc::new(EngineSpotifyPcmTap { pcm_ingress }));
            let (provider, task) = LibrespotBackend::new(native_config, cancellation);
            (Arc::new(provider), Some(task))
        }
        SpotifyBackend::Soloist => {
            let (provider, task) =
                SoloistBackend::new(config.spotify_ws_uri.clone(), true, cancellation);
            (Arc::new(provider), Some(task))
        }
        SpotifyBackend::Disabled => (Arc::new(DisabledSpotifyBackend), None),
    }
}

#[derive(Debug)]
struct EngineSpotifyPcmTap {
    pcm_ingress: PcmIngressSender,
}

impl DecodedPcmTap for EngineSpotifyPcmTap {
    fn stream_generation(&self) -> u64 {
        self.pcm_ingress.generation()
    }

    fn begin_generation(&self) {
        self.pcm_ingress.begin_generation();
    }

    fn set_playback_state(&self, state: DecodedPcmPlaybackState) {
        let state = match state {
            DecodedPcmPlaybackState::Idle => PcmPlaybackState::Idle,
            DecodedPcmPlaybackState::Loading => PcmPlaybackState::Loading,
            DecodedPcmPlaybackState::Playing => PcmPlaybackState::Playing,
            DecodedPcmPlaybackState::Paused => PcmPlaybackState::Paused,
            DecodedPcmPlaybackState::Failed => PcmPlaybackState::Failed,
        };
        self.pcm_ingress.set_playback_state(state);
    }

    fn submit_interleaved_f64(
        &self,
        samples: &[f64],
        sample_rate: u32,
        channels: u16,
        stream_generation: u64,
        sample_position: u64,
    ) {
        self.pcm_ingress.submit_interleaved_f64_at(
            samples,
            sample_rate,
            channels,
            stream_generation,
            sample_position,
            monotonic_time_ns(),
        );
    }

    fn record_unsupported_packet(&self) {
        self.pcm_ingress.record_unsupported_packet();
    }
}

fn with_spotify_status(response: Value, state: &AppState) -> Value {
    with_provider_status(response, state.spotify.as_ref(), &state.bluetooth)
}

fn with_provider_status(
    mut response: Value,
    spotify: &dyn SpotifyProvider,
    bluetooth: &BluetoothService,
) -> Value {
    if let Some(object) = response.as_object_mut() {
        object.insert(
            "spotify".to_owned(),
            spotify_status_value(&spotify.status()),
        );
        object.insert(
            "bluetooth".to_owned(),
            serde_json::to_value(bluetooth.status())
                .expect("Bluetooth status contains only JSON-compatible fields"),
        );
    }
    response
}

fn spawn_dashboard_state_publisher(
    mut control_state: watch::Receiver<Arc<Value>>,
    spotify: Arc<dyn SpotifyProvider>,
    bluetooth: BluetoothService,
    cancellation: CancellationToken,
) -> (watch::Receiver<Arc<Value>>, JoinHandle<()>) {
    let initial_control_state = control_state.borrow_and_update().as_ref().clone();
    let initial_state =
        with_provider_status(initial_control_state.clone(), spotify.as_ref(), &bluetooth);
    let (publisher, receiver) = watch::channel(Arc::new(initial_state.clone()));
    let task = tokio::spawn(async move {
        let mut latest_control_state = initial_control_state;
        let mut latest_published_state = initial_state;
        let mut cadence = tokio::time::interval(Duration::from_millis(250));
        cadence.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = cancellation.cancelled() => break,
                changed = control_state.changed() => {
                    if changed.is_err() {
                        break;
                    }
                    latest_control_state = control_state.borrow_and_update().as_ref().clone();
                }
                _ = cadence.tick() => {}
            }
            let next =
                with_provider_status(latest_control_state.clone(), spotify.as_ref(), &bluetooth);
            if next != latest_published_state {
                publisher.send_replace(Arc::new(next.clone()));
                latest_published_state = next;
            }
        }
    });
    (receiver, task)
}

#[utoipa::path(
    get,
    path = "/api/audio/bluetooth",
    tag = "audio",
    responses((status = 200, body = BluetoothStatusResponseSchema))
)]
async fn bluetooth_status(State(state): State<AppState>) -> Json<BluetoothStatus> {
    Json(state.bluetooth.status())
}

#[utoipa::path(
    put,
    path = "/api/audio/bluetooth/power",
    tag = "audio",
    request_body = BluetoothPowerRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn bluetooth_power(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BluetoothPowerRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    state
        .bluetooth
        .set_power(body.map_err(json_rejection)?.0.powered)
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    put,
    path = "/api/audio/bluetooth/alias",
    tag = "audio",
    request_body = BluetoothAliasRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn bluetooth_alias(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BluetoothAliasRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let alias = body.map_err(json_rejection)?.0.alias;
    validate_body_string_length(&alias, "alias", 1, 64)?;
    state
        .bluetooth
        .set_alias(alias)
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    post,
    path = "/api/audio/bluetooth/scan",
    tag = "audio",
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema)
    )
)]
async fn bluetooth_scan(State(state): State<AppState>) -> Result<Json<BluetoothStatus>, ApiError> {
    state
        .bluetooth
        .start_scan()
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    post,
    path = "/api/audio/bluetooth/pair",
    tag = "audio",
    request_body = BluetoothDeviceRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn bluetooth_pair(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BluetoothDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let address = body.map_err(json_rejection)?.0.address;
    validate_body_string_length(&address, "address", 17, 17)?;
    state
        .bluetooth
        .pair(address)
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    post,
    path = "/api/audio/bluetooth/connect",
    tag = "audio",
    request_body = BluetoothDeviceRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn bluetooth_connect(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BluetoothDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.address, "address", 17, 17)?;
    state
        .bluetooth
        .connect(
            body.address,
            body.role
                .map(|role: BluetoothRole| role.as_str().to_owned()),
        )
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    post,
    path = "/api/audio/bluetooth/forget",
    tag = "audio",
    request_body = BluetoothDeviceRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn bluetooth_forget(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BluetoothDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let address = body.map_err(json_rejection)?.0.address;
    validate_body_string_length(&address, "address", 17, 17)?;
    state
        .bluetooth
        .forget(address)
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    post,
    path = "/api/audio/bluetooth/disconnect",
    tag = "audio",
    request_body = BluetoothDeviceRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn bluetooth_disconnect(
    State(state): State<AppState>,
    body: Result<JsonExtractor<BluetoothDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let address = body.map_err(json_rejection)?.0.address;
    validate_body_string_length(&address, "address", 17, 17)?;
    state
        .bluetooth
        .disconnect(address)
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    put,
    path = "/api/audio/output",
    tag = "audio",
    request_body = AudioOutputSelectionRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn select_audio_output(
    State(state): State<AppState>,
    body: Result<
        JsonExtractor<AudioOutputSelectionRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let selector = body.map_err(json_rejection)?.0.selector;
    validate_body_string_length(&selector, "selector", 1, 256)?;
    state
        .bluetooth
        .set_default_sink(selector)
        .await
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    put,
    path = "/api/audio/output/volume",
    tag = "audio",
    request_body = AudioOutputVolumeRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn set_audio_output_volume(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioOutputVolumeRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.selector, "selector", 1, 256)?;
    if !(0.0..=1.0).contains(&body.volume) {
        let (error_type, message, context) = if body.volume < 0.0 {
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
        return Err(ApiError::validation(json!([{
            "type": error_type,
            "loc": ["body", "volume"],
            "msg": message,
            "input": body.volume,
            "ctx": context,
        }])));
    }
    state
        .bluetooth
        .set_output_volume(body.selector, body.volume)
        .await
        .map(Json)
        .map_err(bluetooth_api_error)
}

#[utoipa::path(
    put,
    path = "/api/audio/output/mute",
    tag = "audio",
    request_body = AudioOutputMuteRequest,
    responses(
        (status = 200, body = BluetoothStatusResponseSchema),
        (status = 409, body = ErrorResponseSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn set_audio_output_mute(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioOutputMuteRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<BluetoothStatus>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.selector, "selector", 1, 256)?;
    state
        .bluetooth
        .set_output_mute(body.selector, body.muted)
        .await
        .map(Json)
        .map_err(bluetooth_api_error)
}

fn validate_body_string_length(
    value: &str,
    field: &str,
    minimum: usize,
    maximum: usize,
) -> Result<(), ApiError> {
    validate_body_string_length_with_input(value, field, minimum, maximum, true)
}

fn validate_body_string_length_without_input(
    value: &str,
    field: &str,
    minimum: usize,
    maximum: usize,
) -> Result<(), ApiError> {
    validate_body_string_length_with_input(value, field, minimum, maximum, false)
}

fn validate_body_string_length_with_input(
    value: &str,
    field: &str,
    minimum: usize,
    maximum: usize,
    include_input: bool,
) -> Result<(), ApiError> {
    let length = value.chars().count();
    if length >= minimum && length <= maximum {
        return Ok(());
    }
    let too_short = length < minimum;
    let (error_type, message, context) = if too_short {
        (
            "string_too_short",
            format!(
                "String should have at least {minimum} character{}",
                if minimum == 1 { "" } else { "s" }
            ),
            json!({"min_length": minimum}),
        )
    } else {
        (
            "string_too_long",
            format!("String should have at most {maximum} characters"),
            json!({"max_length": maximum}),
        )
    };
    let mut detail = json!({
        "type": error_type,
        "loc": ["body", field],
        "msg": message,
        "ctx": context,
    });
    if include_input {
        detail["input"] = json!(value);
    }
    Err(ApiError::validation(json!([detail])))
}

fn bluetooth_api_error(error: BluetoothError) -> ApiError {
    ApiError::new(StatusCode::CONFLICT, error.message)
}

#[utoipa::path(
    get,
    path = "/api/network/status",
    tag = "network",
    responses((status = 200, body = NetworkStatusResponse))
)]
async fn network_status(
    State(state): State<AppState>,
) -> Result<Json<NetworkStatusResponse>, ApiError> {
    state
        .network
        .status()
        .await
        .map(Json)
        .map_err(network_api_error)
}

#[utoipa::path(
    post,
    path = "/api/network/scan",
    tag = "network",
    request_body = WifiScanRequest,
    responses(
        (status = 200, body = WifiScanResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn network_scan(
    State(state): State<AppState>,
    body: Result<JsonExtractor<WifiScanRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<WifiScanResponse>, ApiError> {
    let request = body.map_err(json_rejection)?.0;
    validate_body_string_length(&request.interface, "interface", 1, 15)?;
    state
        .network
        .scan(request)
        .await
        .map(Json)
        .map_err(network_api_error)
}

#[utoipa::path(
    post,
    path = "/api/network/connect",
    tag = "network",
    request_body = WifiConnectRequest,
    responses(
        (status = 200, body = WifiConnectResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn network_connect(
    State(state): State<AppState>,
    body: Result<JsonExtractor<WifiConnectRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<WifiConnectResponse>, ApiError> {
    let request = body.map_err(json_rejection)?.0;
    validate_body_string_length_without_input(&request.interface, "interface", 1, 15)?;
    validate_body_string_length_without_input(&request.ssid, "ssid", 1, 32)?;
    let password_length = request.password.chars().count();
    if password_length > 63 {
        return Err(ApiError::validation(json!([{
            "type": "too_long",
            "loc": ["body", "password"],
            "msg": format!(
                "Value should have at most 63 items after validation, not {password_length}"
            ),
            // Do not echo a credential in a validation response.
            "ctx": {
                "field_type": "Value",
                "max_length": 63,
                "actual_length": password_length,
            },
        }])));
    }
    let password = request.password.clone();
    state
        .network
        .connect(request)
        .await
        .map(Json)
        .map_err(|error| network_api_error_with_secret(error, &password))
}

fn network_api_error(error: NetworkError) -> ApiError {
    network_api_error_with_secret(error, "")
}

fn network_api_error_with_secret(error: NetworkError, secret: &str) -> ApiError {
    let status = match error.code {
        "invalid_contract" | "unsupported" => StatusCode::UNPROCESSABLE_ENTITY,
        "invalid" | "connection_failed" | "busy" => StatusCode::CONFLICT,
        _ => StatusCode::SERVICE_UNAVAILABLE,
    };
    ApiError::new(status, remove_secret_from_message(error.message, secret))
}

#[utoipa::path(
    get,
    path = "/api/state",
    tag = "control",
    responses((status = 200, body = DashboardStateSchema))
)]
async fn runtime_state(State(state): State<AppState>) -> Json<Value> {
    Json(state.dashboard_state.borrow().as_ref().clone())
}

#[utoipa::path(
    get,
    path = "/api/animations",
    tag = "animations",
    responses((status = 200, body = AnimationList))
)]
async fn animations() -> Json<Value> {
    Json(animation_list())
}

#[utoipa::path(
    get,
    path = "/api/stripes",
    tag = "stripes",
    responses((status = 200, body = StripeTopologyResponse))
)]
async fn get_stripe_topology(State(state): State<AppState>) -> Json<Value> {
    Json(state.control.stripe_topology())
}

#[derive(Deserialize, utoipa::ToSchema)]
struct StripeTopologyRequest {
    layout: StripeLayout,
    #[schema(max_items = 2)]
    outputs: Vec<StripeOutputRequest>,
    #[schema(default = false)]
    #[serde(default, deserialize_with = "deserialize_pydantic_bool")]
    power_budget_enabled: bool,
    #[schema(exclusive_minimum = 0.0)]
    #[serde(default)]
    power_budget_watts: Option<f64>,
    #[schema(default = false)]
    #[serde(default, deserialize_with = "deserialize_pydantic_bool")]
    shared_supply_enabled: bool,
    #[schema(exclusive_minimum = 0.0, default = 200.0)]
    #[serde(default = "default_supply_capacity")]
    supply_capacity_watts: f64,
    #[schema(minimum = 0.0, default = 15.0)]
    #[serde(default = "default_pi_reserve")]
    pi_reserve_watts: f64,
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
#[schema(as = StripeOutputConfig)]
pub(crate) struct StripeOutputRequest {
    #[schema(min_length = 1, max_length = 64)]
    id: String,
    #[schema(min_length = 1, max_length = 40)]
    name: String,
    #[schema(minimum = 1, maximum = 4096)]
    pixels: usize,
    #[serde(default)]
    #[schema(schema_with = crate::api_schema::stripe_backend_schema)]
    backend: StripeBackend,
    #[schema(default = false)]
    #[serde(default, deserialize_with = "deserialize_pydantic_bool")]
    reversed: bool,
    #[serde(default = "default_zone_count")]
    #[schema(minimum = 1, maximum = 4096, default = 1)]
    zone_count: usize,
    #[serde(default)]
    #[schema(schema_with = crate::api_schema::zone_pattern_schema)]
    zone_pattern: ZonePattern,
    #[serde(default = "default_spi_device")]
    #[schema(min_length = 1, default = "/dev/spidev0.0")]
    spi_device: String,
    #[serde(default = "default_spi_speed")]
    #[schema(exclusive_minimum = 0.0, maximum = 32000000, default = 1000000)]
    spi_speed_hz: u32,
    #[serde(default = "default_gpio_chip")]
    #[schema(min_length = 1, default = "/dev/gpiochip0")]
    chip: String,
    #[serde(default = "default_data_pin")]
    #[schema(minimum = 0, default = 10)]
    data_pin: u32,
    #[serde(default = "default_clock_pin")]
    #[schema(minimum = 0, default = 11)]
    clock_pin: u32,
    #[serde(default = "default_voltage")]
    #[schema(exclusive_minimum = 0.0, default = 5.0)]
    voltage_v: f64,
    #[serde(default = "default_full_white_current")]
    #[schema(exclusive_minimum = 0.0, default = 0.06)]
    full_white_current_a: f64,
    #[serde(default)]
    #[schema(exclusive_minimum = 0.0)]
    power_limit_watts: Option<f64>,
    #[serde(default)]
    #[schema(format = DateTime)]
    #[allow(dead_code)]
    last_output_at: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    error: Option<String>,
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

#[derive(Deserialize, utoipa::ToSchema)]
struct StripeTestRequest {
    stripe_id: String,
    #[schema(required = false, schema_with = crate::api_schema::stripe_test_pattern_schema)]
    #[serde(default = "default_identify_pattern")]
    pattern: String,
    #[serde(default)]
    topology: Option<StripeTopologyRequest>,
}

#[utoipa::path(
    put,
    path = "/api/stripes",
    tag = "stripes",
    request_body = StripeTopologyRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn put_stripe_topology(
    State(state): State<AppState>,
    body: Result<JsonExtractor<StripeTopologyRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let topology = StripeTopologySettings::from(body.map_err(json_rejection)?.0);
    state.control.set_stripe_topology(topology).await.map(Json)
}

#[utoipa::path(
    post,
    path = "/api/stripes/test",
    tag = "stripes",
    request_body = StripeTestRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn test_stripe(
    State(state): State<AppState>,
    body: Result<JsonExtractor<StripeTestRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if !["identify", "red", "green", "blue", "white"].contains(&body.pattern.as_str()) {
        return Err(literal_validation_error(
            "pattern",
            "'identify', 'red', 'green', 'blue' or 'white'",
            json!(body.pattern),
        ));
    }
    let topology = body.topology.map(StripeTopologySettings::from);
    state
        .control
        .test_stripe(body.stripe_id, body.pattern, topology)
        .await
        .map(Json)
}

#[derive(Deserialize, utoipa::ToSchema)]
struct CalibrationStartRequest {
    #[schema(minimum = 0)]
    output_index: usize,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct CalibrationUpdateRequest {
    #[schema(maximum = 255)]
    red: u8,
    #[schema(maximum = 255)]
    green: u8,
    #[schema(maximum = 255)]
    blue: u8,
    #[schema(value_type = crate::api_schema::CalibrationPattern)]
    pattern: String,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct CalibrationFinishRequest {
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    save: bool,
}

#[utoipa::path(
    post,
    path = "/api/calibration/session",
    tag = "calibration",
    request_body = CalibrationStartRequest,
    responses(
        (status = 200, body = CalibrationSessionResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    put,
    path = "/api/calibration/session/{session_id}",
    tag = "calibration",
    params(("session_id" = String, Path)),
    request_body = CalibrationUpdateRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    post,
    path = "/api/calibration/session/{session_id}/finish",
    tag = "calibration",
    params(("session_id" = String, Path)),
    request_body = CalibrationFinishRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioDeviceRequest {
    #[schema(min_length = 1)]
    device: String,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioSettingsRequest {
    #[schema(min_length = 1)]
    device: String,
    #[schema(value_type = AudioTuningValuesSchema)]
    settings: AudioTuningProfile,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioSyncOffsetRequest {
    #[schema(minimum = -500.0, maximum = 500.0)]
    offset_ms: f64,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioSourceRequest {
    #[schema(value_type = crate::api_schema::AudioSource)]
    source: String,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioSyncCalibrationRequest {
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    active: bool,
}

fn default_audio_calibration_duration() -> f64 {
    8.0
}

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioCalibrationStartRequest {
    #[schema(min_length = 1)]
    device: String,
    #[schema(minimum = 3.0, maximum = 30.0, default = 8.0)]
    #[serde(default = "default_audio_calibration_duration")]
    duration_seconds: f64,
    #[schema(default = false)]
    #[serde(default, deserialize_with = "deserialize_pydantic_bool")]
    two_step: bool,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct AudioCalibrationFinishRequest {
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    apply: bool,
    #[schema(minimum = 0.1, maximum = 0.8)]
    target_level: Option<f64>,
    #[schema(minimum = 0.0, maximum = 1.0)]
    noise_floor: Option<f64>,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct IdentitySettingsRequest {
    #[schema(min_length = 1, max_length = 64)]
    application_name: String,
    #[schema(min_length = 1, max_length = 64)]
    spotify_device_name: String,
}

#[utoipa::path(
    get,
    path = "/api/identity",
    tag = "identity",
    responses((status = 200, body = IdentitySettingsResponse))
)]
async fn get_identity_settings(State(state): State<AppState>) -> Json<Value> {
    Json(state.control.identity_settings())
}

#[utoipa::path(
    put,
    path = "/api/identity",
    tag = "identity",
    request_body = IdentitySettingsRequest,
    responses(
        (status = 200, body = IdentitySettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    get,
    path = "/api/audio/settings",
    tag = "audio",
    responses((status = 200, body = AudioSettingsResponse))
)]
async fn get_audio_settings(State(state): State<AppState>) -> Result<Json<Value>, ApiError> {
    let response = state.control.audio_settings().await?;
    Ok(Json(with_spotify_status(response, &state)))
}

#[utoipa::path(
    put,
    path = "/api/audio/device",
    tag = "audio",
    request_body = AudioDeviceRequest,
    responses(
        (status = 200, body = AudioSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn select_audio_device(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.device, "device", 1, usize::MAX)?;
    state
        .control
        .select_audio_device(body.device)
        .await
        .map(|response| Json(with_spotify_status(response, &state)))
}

#[utoipa::path(
    put,
    path = "/api/audio/settings",
    tag = "audio",
    request_body = AudioSettingsRequest,
    responses(
        (status = 200, body = AudioSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn put_audio_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioSettingsRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.device, "device", 1, usize::MAX)?;
    state
        .control
        .apply_audio_settings(body.device, body.settings)
        .await
        .map(|response| Json(with_spotify_status(response, &state)))
}

#[utoipa::path(
    post,
    path = "/api/audio/settings/reset",
    tag = "audio",
    request_body = AudioResetRequest,
    responses(
        (status = 200, body = AudioSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn reset_audio_settings(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioDeviceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.device, "device", 1, usize::MAX)?;
    state
        .control
        .reset_audio_settings(body.device)
        .await
        .map(|response| Json(with_spotify_status(response, &state)))
}

#[utoipa::path(
    put,
    path = "/api/audio/sync-offset",
    tag = "audio",
    request_body = AudioSyncOffsetRequest,
    responses(
        (status = 200, body = AudioSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn set_audio_sync_offset(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioSyncOffsetRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if body.offset_ms > 500.0 {
        return Err(ApiError::validation(json!([{
            "type": "less_than_equal",
            "loc": ["body", "offset_ms"],
            "msg": "Input should be less than or equal to 500",
            "input": body.offset_ms,
            "ctx": {"le": 500.0},
        }])));
    }
    if body.offset_ms < -500.0 {
        return Err(ApiError::validation(json!([{
            "type": "greater_than_equal",
            "loc": ["body", "offset_ms"],
            "msg": "Input should be greater than or equal to -500",
            "input": body.offset_ms,
            "ctx": {"ge": -500.0},
        }])));
    }
    state
        .control
        .set_audio_sync_offset(body.offset_ms)
        .await
        .map(|response| Json(with_spotify_status(response, &state)))
}

#[utoipa::path(
    put,
    path = "/api/audio/source",
    tag = "audio",
    request_body = AudioSourceRequest,
    responses(
        (status = 200, body = AudioSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn select_audio_source(
    State(state): State<AppState>,
    body: Result<JsonExtractor<AudioSourceRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    if !["auto", "off", "demo", "mic", "bluetooth", "spotify"].contains(&body.source.as_str()) {
        return Err(literal_validation_error(
            "source",
            "'auto', 'off', 'demo', 'mic', 'bluetooth' or 'spotify'",
            json!(body.source),
        ));
    }
    state
        .control
        .set_audio_source(body.source)
        .await
        .map(|response| Json(with_spotify_status(response, &state)))
}

#[utoipa::path(
    post,
    path = "/api/audio/sync-calibration",
    tag = "audio",
    request_body = AudioSyncCalibrationRequest,
    responses(
        (status = 200, body = AudioSyncCalibrationResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn set_audio_sync_calibration(
    State(state): State<AppState>,
    body: Result<
        JsonExtractor<AudioSyncCalibrationRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    let settings = with_spotify_status(state.control.audio_settings().await?, &state);
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

#[utoipa::path(
    post,
    path = "/api/audio/calibration/session",
    tag = "audio",
    request_body = AudioCalibrationStartRequest,
    responses(
        (status = 200, body = AudioCalibrationSessionResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn start_audio_calibration(
    State(state): State<AppState>,
    body: Result<
        JsonExtractor<AudioCalibrationStartRequest>,
        axum::extract::rejection::JsonRejection,
    >,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    validate_body_string_length(&body.device, "device", 1, usize::MAX)?;
    state
        .control
        .start_audio_calibration(body.device, body.duration_seconds, body.two_step)
        .await
        .map(Json)
}

#[utoipa::path(
    get,
    path = "/api/audio/calibration/session/{session_id}",
    tag = "audio",
    params(("session_id" = String, Path)),
    responses(
        (status = 200, body = AudioCalibrationSessionResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn get_audio_calibration(
    State(state): State<AppState>,
    Path(session_id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    state
        .control
        .audio_calibration_status(&session_id)
        .map(Json)
}

#[utoipa::path(
    post,
    path = "/api/audio/calibration/session/{session_id}/music",
    tag = "audio",
    params(("session_id" = String, Path)),
    responses(
        (status = 200, body = AudioCalibrationSessionResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    post,
    path = "/api/audio/calibration/session/{session_id}/finish",
    tag = "audio",
    params(("session_id" = String, Path)),
    request_body = AudioCalibrationFinishRequest,
    responses(
        (status = 200, body = AudioSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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
        .map(|response| Json(with_spotify_status(response, &state)))
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

#[derive(Deserialize, utoipa::ToSchema)]
struct AnimationSettingsQuery {
    stripe_id: Option<String>,
}

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
struct AnimationSettingsUpdateRequest {
    #[schema(schema_with = crate::api_schema::arbitrary_object_schema)]
    values: Map<String, Value>,
    #[schema(min_length = 1, max_length = 64)]
    stripe_id: Option<String>,
}

#[utoipa::path(
    get,
    path = "/api/animations/{animation_name}/settings",
    tag = "animations",
    params(
        ("animation_name" = String, Path, min_length = 1, max_length = 100),
        ("stripe_id" = Option<String>, Query, min_length = 1, max_length = 64)
    ),
    responses(
        (status = 200, body = AnimationSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    put,
    path = "/api/animations/{animation_name}/settings",
    tag = "animations",
    params(("animation_name" = String, Path, min_length = 1, max_length = 100)),
    request_body = AnimationSettingsUpdateRequest,
    responses(
        (status = 200, body = AnimationSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    post,
    path = "/api/animations/{animation_name}/settings/reset",
    tag = "animations",
    params(
        ("animation_name" = String, Path, min_length = 1, max_length = 100),
        ("stripe_id" = Option<String>, Query, min_length = 1, max_length = 64)
    ),
    responses(
        (status = 200, body = AnimationSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[derive(Deserialize, utoipa::ToSchema)]
struct ModeRequest {
    mode: PlaybackMode,
    #[serde(default)]
    #[schema(pattern = "^#[0-9A-Fa-f]{6}$")]
    color: Option<String>,
    #[serde(default)]
    stripe_id: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_pydantic_bool")]
    music_recognition_enabled: Option<bool>,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct BrightnessRequest {
    #[schema(minimum = 0.0, maximum = 1.0)]
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

#[derive(Deserialize, utoipa::ToSchema)]
struct AnimationRequest {
    #[schema(min_length = 1)]
    name: String,
    #[serde(default)]
    stripe_id: Option<String>,
}

#[derive(Deserialize, utoipa::ToSchema)]
struct BlackoutRequest {
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    enabled: bool,
    #[serde(default)]
    stripe_id: Option<String>,
}

#[utoipa::path(
    put,
    path = "/api/mode",
    tag = "control",
    request_body = ModeRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    put,
    path = "/api/brightness",
    tag = "control",
    request_body = BrightnessRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    put,
    path = "/api/animation",
    tag = "control",
    request_body = AnimationRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    post,
    path = "/api/blackout",
    tag = "control",
    request_body = BlackoutRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(default)]
struct MusicIdleRequest {
    mode: MusicIdleMode,
    color: String,
    animation: String,
    brightness: f64,
    stripe_id: Option<String>,
}

#[derive(utoipa::ToSchema)]
#[schema(as = MusicIdleRequest)]
#[allow(dead_code)]
struct MusicIdleRequestSchema {
    #[schema(required = false, schema_with = crate::api_schema::music_idle_mode_schema)]
    mode: MusicIdleMode,
    #[schema(required = false, default = "#2060FF", pattern = "^#[0-9A-Fa-f]{6}$")]
    color: String,
    #[schema(required = false, default = "")]
    animation: String,
    #[schema(required = false, minimum = 0.0, maximum = 1.0, default = 1.0)]
    brightness: f64,
    #[schema(required = false)]
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

#[utoipa::path(
    put,
    path = "/api/music-idle",
    tag = "control",
    request_body = MusicIdleRequestSchema,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[derive(Deserialize, utoipa::ToSchema)]
struct StartupSettingsRequest {
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    restore_last_state: bool,
}

#[utoipa::path(
    get,
    path = "/api/startup",
    tag = "startup",
    responses((status = 200, body = StartupSettingsResponse))
)]
async fn get_startup_settings(State(state): State<AppState>) -> Json<Value> {
    Json(state.control.startup_settings())
}

#[utoipa::path(
    put,
    path = "/api/startup",
    tag = "startup",
    request_body = StartupSettingsRequest,
    responses(
        (status = 200, body = StartupSettingsResponse),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    get,
    path = "/api/transitions/settings",
    tag = "transitions",
    responses((status = 200, body = TransitionSettings))
)]
async fn get_transition_settings(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow()["transition_settings"].clone())
}

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
struct TransitionSettingsRequest {
    enabled: bool,
    #[schema(minimum = 100, maximum = 1500)]
    duration_ms: u16,
    #[schema(required = false, schema_with = crate::api_schema::transition_effect_schema)]
    #[serde(default)]
    effect: TransitionEffect,
}

#[utoipa::path(
    put,
    path = "/api/transitions/settings",
    tag = "transitions",
    request_body = TransitionSettingsRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    get,
    path = "/api/sequencer/settings",
    tag = "sequencer",
    responses((status = 200, body = SequencerSettingsResponse))
)]
async fn get_sequencer_settings(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow()["sequencer_settings"].clone())
}

#[derive(Deserialize, utoipa::ToSchema)]
struct SequencerSettingsRequest {
    switch_quantization: SwitchQuantization,
    #[schema(schema_with = crate::api_schema::duration_phrase_bars_schema)]
    bars_per_phrase: u8,
}

#[utoipa::path(
    put,
    path = "/api/sequencer/settings",
    tag = "sequencer",
    request_body = SequencerSettingsRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    get,
    path = "/api/sequencer/sequences",
    tag = "sequencer",
    responses((status = 200, body = SequenceLibraryResponse))
)]
async fn get_sequence_library(State(state): State<AppState>) -> Json<Value> {
    let snapshot = state.control.state();
    Json(snapshot.borrow()["sequence_library"].clone())
}

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
struct SequenceStepRequest {
    #[schema(min_length = 1, max_length = 100)]
    animation_name: String,
    #[schema(schema_with = crate::api_schema::duration_sequence_bars_schema)]
    duration_bars: u8,
}

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
struct SequenceDefinitionRequest {
    #[schema(min_length = 1, max_length = 80)]
    name: String,
    repeat: bool,
    #[schema(min_items = 1, max_items = 32, value_type = Vec<crate::settings::SavedAnimationSequenceStep>)]
    steps: Vec<SequenceStepRequest>,
}

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
struct SequenceCreateRequest {
    #[schema(min_length = 1, max_length = 64)]
    id: String,
    #[schema(min_length = 1, max_length = 80)]
    name: String,
    repeat: bool,
    #[schema(min_items = 1, max_items = 32, value_type = Vec<crate::settings::SavedAnimationSequenceStep>)]
    steps: Vec<SequenceStepRequest>,
}

#[derive(Deserialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
#[schema(as = SequenceActiveRequest)]
struct ActiveSequenceRequest {
    #[schema(value_type = Option<String>, required = true, min_length = 1, max_length = 64)]
    sequence_id: Value,
}

#[utoipa::path(
    post,
    path = "/api/sequencer/sequences",
    tag = "sequencer",
    request_body = SequenceCreateRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn create_sequence(
    State(state): State<AppState>,
    body: Result<JsonExtractor<SequenceCreateRequest>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    let body = body.map_err(json_rejection)?.0;
    let sequence = make_sequence(body.id, body.name, body.repeat, body.steps)?;
    state.control.create_sequence(sequence).await.map(Json)
}

#[utoipa::path(
    put,
    path = "/api/sequencer/sequences/{sequence_id}",
    tag = "sequencer",
    params(("sequence_id" = String, Path, min_length = 1, max_length = 64)),
    request_body = SequenceDefinitionRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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

#[utoipa::path(
    delete,
    path = "/api/sequencer/sequences/{sequence_id}",
    tag = "sequencer",
    params(("sequence_id" = String, Path, min_length = 1, max_length = 64)),
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
async fn delete_sequence(
    State(state): State<AppState>,
    Path(sequence_id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let sequence_id = validate_sequence_path_id(sequence_id)?;
    state.control.delete_sequence(sequence_id).await.map(Json)
}

#[utoipa::path(
    put,
    path = "/api/sequencer/active",
    tag = "sequencer",
    request_body = ActiveSequenceRequest,
    responses(
        (status = 200, body = DashboardStateSchema),
        (status = 422, body = HttpValidationErrorSchema)
    )
)]
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
    for step in &steps {
        let definition = catalog().animation(&step.animation_name).ok_or_else(|| {
            ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                format!("unknown animation in sequence: {}", step.animation_name),
            )
        })?;
        let dynamic = &definition.dynamic_metadata;
        let eligible = definition.automatic
            && definition.role == "base"
            && dynamic.get("role").and_then(Value::as_str) == Some("base")
            && dynamic.get("dynamic_safe").and_then(Value::as_bool) == Some(true)
            && definition.effect_metadata.is_none();
        if !eligible {
            return Err(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                format!(
                    "animation is not eligible for Dynamic sequences: {}",
                    step.animation_name
                ),
            ));
        }
    }
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

#[utoipa::path(
    get,
    path = "/api/auth/status",
    tag = "auth",
    responses((status = 200, body = AccessStatus))
)]
async fn auth_status(State(state): State<AppState>, headers: HeaderMap) -> Json<Value> {
    Json(json!({
        "required": state.access.required(),
        "authenticated": state.access.authenticated(session_from_headers(&headers).as_deref()),
    }))
}

#[derive(Deserialize, utoipa::ToSchema)]
#[schema(as = PairingRequest)]
struct PairRequest {
    #[schema(pattern = "^[0-9]{4}$")]
    code: String,
}

#[utoipa::path(
    post,
    path = "/api/auth/pair",
    tag = "auth",
    request_body = PairRequest,
    responses(
        (status = 200, body = AccessStatus),
        (status = 422, body = HttpValidationErrorSchema),
        (status = 429, description = "Too many pairing attempts; retry after the indicated delay.")
    )
)]
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

#[utoipa::path(
    post,
    path = "/api/auth/logout",
    tag = "auth",
    responses((status = 200, body = AccessStatus))
)]
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

async fn validate_json_object(request: Request<Body>, next: Next) -> Response {
    if !matches!(
        request.method(),
        &Method::POST | &Method::PUT | &Method::PATCH | &Method::DELETE
    ) {
        return next.run(request).await;
    }
    let matched_path = request
        .extensions()
        .get::<MatchedPath>()
        .map(|path| path.as_str().to_owned())
        .unwrap_or_else(|| request.uri().path().to_owned());
    let request_method = request.method().clone();
    if !has_json_object_body(request.method(), &matched_path) {
        return next.run(request).await;
    }
    let is_json = request
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .is_some_and(|media_type| {
            media_type.trim().eq_ignore_ascii_case("application/json")
                || media_type.trim().to_ascii_lowercase().ends_with("+json")
        });
    if !is_json {
        return next.run(request).await;
    }

    const MAX_JSON_REQUEST_BYTES: usize = 2 * 1024 * 1024;
    let (parts, body) = request.into_parts();
    let bytes = match to_bytes(body, MAX_JSON_REQUEST_BYTES).await {
        Ok(bytes) => bytes,
        Err(_) => {
            return ApiError::new(
                StatusCode::PAYLOAD_TOO_LARGE,
                "The JSON request body is too large.",
            )
            .into_response();
        }
    };
    if let Ok(value) = serde_json::from_slice::<Value>(&bytes) {
        if !value.is_object() {
            let mut detail = json!({
                "type": "model_attributes_type",
                "loc": ["body"],
                "msg": "Input should be a valid dictionary or object to extract fields from",
                "input": value,
            });
            if matched_path == "/api/network/connect"
                && let Some(detail_object) = detail.as_object_mut()
            {
                detail_object.remove("input");
            }
            return ApiError::validation(json!([detail])).into_response();
        }

        if let Some(errors) =
            pydantic_body_validation_errors(&request_method, &matched_path, &value)
        {
            return ApiError::validation(json!(errors)).into_response();
        }
    }
    next.run(Request::from_parts(parts, Body::from(bytes)))
        .await
}

fn has_json_object_body(method: &Method, path: &str) -> bool {
    matches!(
        (method, path),
        (&Method::PUT, "/api/animation")
            | (&Method::PUT, "/api/animations/{animation_name}/settings")
            | (&Method::PUT, "/api/audio/bluetooth/alias")
            | (&Method::POST, "/api/audio/bluetooth/connect")
            | (&Method::POST, "/api/audio/bluetooth/disconnect")
            | (&Method::POST, "/api/audio/bluetooth/forget")
            | (&Method::POST, "/api/audio/bluetooth/pair")
            | (&Method::PUT, "/api/audio/bluetooth/power")
            | (&Method::POST, "/api/audio/calibration/session")
            | (
                &Method::POST,
                "/api/audio/calibration/session/{session_id}/finish"
            )
            | (&Method::PUT, "/api/audio/device")
            | (&Method::PUT, "/api/audio/output")
            | (&Method::PUT, "/api/audio/output/mute")
            | (&Method::PUT, "/api/audio/output/volume")
            | (&Method::PUT, "/api/audio/settings")
            | (&Method::POST, "/api/audio/settings/reset")
            | (&Method::PUT, "/api/audio/source")
            | (&Method::POST, "/api/audio/spotify/control")
            | (&Method::POST, "/api/audio/sync-calibration")
            | (&Method::PUT, "/api/audio/sync-offset")
            | (&Method::POST, "/api/auth/pair")
            | (&Method::POST, "/api/blackout")
            | (&Method::PUT, "/api/brightness")
            | (&Method::POST, "/api/calibration/session")
            | (&Method::PUT, "/api/calibration/session/{session_id}")
            | (
                &Method::POST,
                "/api/calibration/session/{session_id}/finish"
            )
            | (&Method::PUT, "/api/identity")
            | (&Method::PUT, "/api/mode")
            | (&Method::PUT, "/api/music-idle")
            | (&Method::POST, "/api/network/connect")
            | (&Method::POST, "/api/network/scan")
            | (&Method::PUT, "/api/sequencer/active")
            | (&Method::POST, "/api/sequencer/sequences")
            | (&Method::PUT, "/api/sequencer/sequences/{sequence_id}")
            | (&Method::PUT, "/api/sequencer/settings")
            | (&Method::PUT, "/api/startup")
            | (&Method::PUT, "/api/stripes")
            | (&Method::POST, "/api/stripes/test")
            | (&Method::PUT, "/api/transitions/settings")
    )
}

fn pydantic_body_validation_errors(
    method: &Method,
    path: &str,
    body: &Value,
) -> Option<Vec<Value>> {
    let object = body.as_object()?;
    let mut errors = Vec::new();

    match (method, path) {
        (&Method::PUT, "/api/mode") => {
            if let Some(mode) = object.get("mode")
                && !matches!(
                    mode.as_str(),
                    Some("solid" | "static" | "cycling" | "dynamic")
                )
            {
                errors.push(enum_validation_error(
                    "mode",
                    "'solid', 'static', 'cycling' or 'dynamic'",
                    mode.clone(),
                ));
            }
        }
        (&Method::POST, "/api/auth/pair") => {
            if let Some(code) = object.get("code")
                && !code.is_string()
            {
                errors.push(pydantic_validation_error(
                    "string_type",
                    "code",
                    "Input should be a valid string",
                    code.clone(),
                    None,
                ));
            }
        }
        (&Method::PUT, "/api/sequencer/settings") => {
            if let Some(quantization) = object.get("switch_quantization")
                && !matches!(
                    quantization.as_str(),
                    Some("off" | "beat" | "bar" | "phrase")
                )
            {
                errors.push(enum_validation_error(
                    "switch_quantization",
                    "'off', 'beat', 'bar' or 'phrase'",
                    quantization.clone(),
                ));
            }
        }
        (&Method::PUT, "/api/stripes") => {
            if let Some(layout) = object.get("layout")
                && !matches!(
                    layout.as_str(),
                    Some("mirrored" | "continuous" | "independent")
                )
            {
                errors.push(pydantic_literal_validation_error(
                    "layout",
                    "'mirrored', 'continuous' or 'independent'",
                    layout.clone(),
                ));
            }
            if let Some(outputs) = object.get("outputs").and_then(Value::as_array) {
                for (index, output) in outputs.iter().enumerate() {
                    let Some(output_fields) = output.as_object() else {
                        continue;
                    };
                    if let Some(pattern) = output_fields.get("zone_pattern")
                        && !matches!(pattern.as_str(), Some("repeat" | "alternate"))
                    {
                        let expected = "'repeat' or 'alternate'";
                        errors.push(json!({
                            "type": "literal_error",
                            "loc": ["body", "outputs", index, "zone_pattern"],
                            "msg": format!("Input should be {expected}"),
                            "input": pattern,
                            "ctx": {"expected": expected},
                        }));
                        continue;
                    }
                    let pixels = output_fields.get("pixels").and_then(Value::as_u64);
                    let zone_count = output_fields.get("zone_count").and_then(Value::as_u64);
                    if let (Some(pixels), Some(zone_count)) = (pixels, zone_count)
                        && zone_count > pixels
                    {
                        errors.push(json!({
                            "type": "value_error",
                            "loc": ["body", "outputs", index],
                            "msg": "Value error, zone count cannot exceed the stripe pixel count",
                            "input": output,
                            "ctx": {"error": {}},
                        }));
                    }
                }
            }
            if errors.is_empty()
                && object.get("power_budget_enabled") == Some(&Value::Bool(true))
                && object.get("power_budget_watts").is_none_or(Value::is_null)
            {
                errors.push(json!({
                    "type": "value_error",
                    "loc": ["body"],
                    "msg": "Value error, an enabled power budget requires a watt limit",
                    "input": body,
                    "ctx": {"error": {}},
                }));
            }
            if object
                .get("supply_capacity_watts")
                .and_then(Value::as_f64)
                .is_some_and(|capacity| capacity <= 0.0)
            {
                let input = object
                    .get("supply_capacity_watts")
                    .cloned()
                    .unwrap_or(Value::Null);
                errors.push(pydantic_validation_error(
                    "greater_than",
                    "supply_capacity_watts",
                    "Input should be greater than 0",
                    input,
                    Some(json!({"gt": 0.0})),
                ));
            }
        }
        (&Method::POST, "/api/calibration/session") => {
            if let Some(output_index) = object.get("output_index")
                && output_index.as_i64().is_some_and(|value| value < 0)
            {
                errors.push(pydantic_validation_error(
                    "greater_than_equal",
                    "output_index",
                    "Input should be greater than or equal to 0",
                    output_index.clone(),
                    Some(json!({"ge": 0})),
                ));
            }
        }
        (&Method::PUT, "/api/calibration/session/{session_id}") => {
            for field in ["red", "green", "blue"] {
                if let Some(input) = object.get(field)
                    && let Some(value) = input.as_i64()
                    && value < 0
                {
                    errors.push(pydantic_validation_error(
                        "greater_than_equal",
                        field,
                        "Input should be greater than or equal to 0",
                        input.clone(),
                        Some(json!({"ge": 0})),
                    ));
                }
            }
            if let Some(pattern) = object.get("pattern")
                && !matches!(pattern.as_str(), Some("white" | "red" | "green" | "blue"))
            {
                errors.push(pydantic_literal_validation_error(
                    "pattern",
                    "'white', 'red', 'green' or 'blue'",
                    pattern.clone(),
                ));
            }
        }
        (&Method::PUT, "/api/animations/{animation_name}/settings") => {
            if let Some(values) = object.get("values")
                && !values.is_object()
            {
                errors.push(pydantic_validation_error(
                    "dict_type",
                    "values",
                    "Input should be a valid dictionary",
                    values.clone(),
                    None,
                ));
            }
        }
        (
            &Method::POST,
            "/api/audio/bluetooth/pair"
            | "/api/audio/bluetooth/connect"
            | "/api/audio/bluetooth/disconnect"
            | "/api/audio/bluetooth/forget",
        ) => {
            if let Some(address) = object.get("address")
                && !address.is_string()
            {
                errors.push(pydantic_validation_error(
                    "string_type",
                    "address",
                    "Input should be a valid string",
                    address.clone(),
                    None,
                ));
            }
        }
        (&Method::POST, "/api/audio/spotify/control") => {
            if let Some(action) = object.get("action")
                && !matches!(
                    action.as_str(),
                    Some(
                        "play"
                            | "pause"
                            | "skip_next"
                            | "skip_prev"
                            | "seek"
                            | "set_volume"
                            | "set_shuffle"
                            | "set_repeat"
                    )
                )
            {
                errors.push(pydantic_literal_validation_error(
                    "action",
                    "'play', 'pause', 'skip_next', 'skip_prev', 'seek', 'set_volume', 'set_shuffle' or 'set_repeat'",
                    action.clone(),
                ));
            }
        }
        (&Method::POST, "/api/sequencer/sequences") => {
            if let Some(steps) = object.get("steps")
                && steps.as_array().is_some_and(Vec::is_empty)
            {
                errors.push(pydantic_validation_error(
                    "too_short",
                    "steps",
                    "List should have at least 1 item after validation, not 0",
                    steps.clone(),
                    Some(json!({
                        "field_type": "List",
                        "min_length": 1,
                        "actual_length": 0,
                    })),
                ));
            }
        }
        (&Method::PUT, "/api/sequencer/sequences/{sequence_id}") => {
            for field in ["name", "repeat", "steps"] {
                if !object.contains_key(field) {
                    errors.push(pydantic_validation_error(
                        "missing",
                        field,
                        "Field required",
                        body.clone(),
                        None,
                    ));
                }
            }
        }
        _ => {}
    }

    (!errors.is_empty()).then_some(errors)
}

fn pydantic_validation_error(
    error_type: &str,
    field: &str,
    message: &str,
    input: Value,
    context: Option<Value>,
) -> Value {
    let mut error = json!({
        "type": error_type,
        "loc": ["body", field],
        "msg": message,
        "input": input,
    });
    if let Some(context) = context {
        error["ctx"] = context;
    }
    error
}

fn enum_validation_error(field: &str, expected: &str, input: Value) -> Value {
    pydantic_validation_error(
        "enum",
        field,
        &format!("Input should be {expected}"),
        input,
        Some(json!({"expected": expected})),
    )
}

fn pydantic_literal_validation_error(field: &str, expected: &str, input: Value) -> Value {
    pydantic_validation_error(
        "literal_error",
        field,
        &format!("Input should be {expected}"),
        input,
        Some(json!({"expected": expected})),
    )
}

async fn api_not_found() -> Response {
    (StatusCode::NOT_FOUND, Json(json!({"detail": "Not Found"}))).into_response()
}

fn json_rejection(rejection: axum::extract::rejection::JsonRejection) -> ApiError {
    let message = rejection.body_text();
    if let Some((field_path, input)) = pydantic_bool_error(&message) {
        let mut location = vec![json!("body")];
        location.extend(field_path);
        let (error_type, detail) = match input {
            Value::String(_) | Value::Number(_) => (
                "bool_parsing",
                "Input should be a valid boolean, unable to interpret input",
            ),
            Value::Null | Value::Array(_) | Value::Object(_) | Value::Bool(_) => {
                ("bool_type", "Input should be a valid boolean")
            }
        };
        return ApiError::validation(json!([{
            "type": error_type,
            "loc": location,
            "msg": detail,
            "input": input,
        }]));
    }
    ApiError::validation(json!([{
        "type": "json_invalid",
        "loc": ["body"],
        "msg": message,
        "input": Value::Null,
    }]))
}

fn literal_validation_error(field: &str, expected: &str, input: Value) -> ApiError {
    ApiError::validation(json!([{
        "type": "literal_error",
        "loc": ["body", field],
        "msg": format!("Input should be {expected}"),
        "input": input,
        "ctx": {"expected": expected},
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
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("static"))
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

fn spawn_audio_status_publisher(
    client: EngineHandle,
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
    use super::{
        NetworkManagerService, WebApplication, create_spotify_provider, shutdown_background_tasks,
        spawn_dashboard_state_publisher, websocket_origin_allowed,
    };
    use crate::bluetooth::BluetoothService;
    use crate::bluetooth_process::BluetoothError;
    use crate::bluetooth_providers::{
        BlueZCommand, BlueZProvider, BlueZSnapshot, PipeWireCommand, PipeWireProvider,
    };
    use crate::config::{AppConfig, EngineOutputOptions};
    use crate::settings::ZonePattern;
    use crate::spotify::{
        ProviderFuture, SpotifyControlAction, SpotifyError, SpotifyPlaybackStatus, SpotifyProvider,
        SpotifyRepeat, SpotifyStatus,
    };
    use crate::updates::UpdateError;
    use axum::body::Body;
    use axum::http::{HeaderMap, HeaderValue, Request, StatusCode};
    use clap::Parser;
    use futures_util::future::BoxFuture;
    use lumistripe_engine_audio::bounded_pcm_ingress;
    use serde_json::{Value, json};
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::time::Duration;
    use tempfile::tempdir;
    use tokio::sync::{mpsc, watch};
    use tokio::time::{Instant as TokioInstant, sleep, timeout};
    use tokio_util::sync::CancellationToken;
    use tower::ServiceExt;

    #[tokio::test]
    async fn shutdown_aborts_all_background_tasks_at_one_shared_deadline() {
        struct DropCounter(Arc<AtomicUsize>);

        impl Drop for DropCounter {
            fn drop(&mut self) {
                self.0.fetch_add(1, Ordering::AcqRel);
            }
        }

        let dropped = Arc::new(AtomicUsize::new(0));
        let mut tasks = (0..4)
            .map(|_| {
                let dropped = dropped.clone();
                tokio::spawn(async move {
                    let _drop_counter = DropCounter(dropped);
                    std::future::pending::<()>().await;
                })
            })
            .collect();
        let deadline = TokioInstant::now() + Duration::from_millis(50);
        let started = TokioInstant::now();

        shutdown_background_tasks(&mut tasks, deadline).await;

        assert!(started.elapsed() < Duration::from_millis(250));
        timeout(Duration::from_secs(1), async {
            while dropped.load(Ordering::Acquire) != 4 {
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("background tasks should be aborted after the shared deadline");
        assert!(tasks.is_empty());
    }

    #[test]
    fn update_install_launch_failure_maps_to_service_unavailable() {
        let error = super::update_api_error(UpdateError {
            code: "start_install_failed",
            message: "Could not start the system updater. Check the Pi installation.",
        });

        assert_eq!(error.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            error.detail(),
            &json!("Could not start the system updater. Check the Pi installation.")
        );
    }

    #[tokio::test]
    async fn spotify_provider_factory_starts_exactly_the_selected_backend() {
        let directory = tempdir().unwrap();
        for (backend, expected_task, expected_configured) in [
            ("librespot", true, true),
            ("soloist", true, true),
            ("disabled", false, false),
        ] {
            let settings_path = directory.path().join(format!("{backend}.json"));
            let data_path = directory.path().join(format!("{backend}-spotify"));
            let options = crate::config::CliOptions::try_parse_from([
                "lumistripe-web",
                "--spotify-backend",
                backend,
                "--settings-file",
                settings_path.to_str().unwrap(),
                "--spotify-data-dir",
                data_path.to_str().unwrap(),
            ])
            .unwrap();
            let (config, _, _) = options.into_config().unwrap();
            let cancellation = CancellationToken::new();
            let (pcm_ingress, _pcm_receiver) = bounded_pcm_ingress();
            let (provider, task) = create_spotify_provider(
                &config,
                "Test LumiStripe".to_owned(),
                cancellation.clone(),
                pcm_ingress,
            );

            assert_eq!(task.is_some(), expected_task, "backend: {backend}");
            assert_eq!(
                provider.status().configured,
                expected_configured,
                "backend: {backend}"
            );
            if backend == "librespot" {
                assert_eq!(
                    provider.status().device_name.as_deref(),
                    Some("Test LumiStripe")
                );
            }
            provider.stop();
            if let Some(task) = task {
                timeout(Duration::from_secs(1), task)
                    .await
                    .expect("selected Spotify provider should stop promptly")
                    .unwrap();
            }
            cancellation.cancel();
        }
    }

    struct PendingBlueZDisconnect {
        commands: mpsc::UnboundedSender<BlueZCommand>,
        operation_dropped: Arc<AtomicBool>,
    }

    impl BlueZProvider for PendingBlueZDisconnect {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            Box::pin(async { Ok(BlueZSnapshot::default()) })
        }

        fn execute(&self, command: BlueZCommand) -> BoxFuture<'static, Result<(), BluetoothError>> {
            let commands = self.commands.clone();
            let operation_dropped = self.operation_dropped.clone();
            Box::pin(async move {
                struct DropSignal(Arc<AtomicBool>);

                impl Drop for DropSignal {
                    fn drop(&mut self) {
                        self.0.store(true, Ordering::Release);
                    }
                }

                let _drop_signal = DropSignal(operation_dropped);
                commands
                    .send(command)
                    .map_err(|_| BluetoothError::new("test", "command receiver stopped"))?;
                std::future::pending::<Result<(), BluetoothError>>().await
            })
        }
    }

    struct EmptyPipeWire;

    impl PipeWireProvider for EmptyPipeWire {
        fn available(&self) -> bool {
            true
        }

        fn has_wpctl(&self) -> bool {
            false
        }

        fn execute(
            &self,
            _command: PipeWireCommand,
        ) -> BoxFuture<'static, Result<String, BluetoothError>> {
            Box::pin(async { Ok(String::new()) })
        }
    }

    struct EmptyBlueZ;

    impl BlueZProvider for EmptyBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            Box::pin(async { Ok(BlueZSnapshot::default()) })
        }

        fn execute(
            &self,
            _command: BlueZCommand,
        ) -> BoxFuture<'static, Result<(), BluetoothError>> {
            Box::pin(async { Ok(()) })
        }
    }

    struct ApiFixtureBlueZ {
        commands: mpsc::UnboundedSender<BlueZCommand>,
        fail_next_pair: Arc<AtomicBool>,
        fail_next_operation: Arc<std::sync::Mutex<Option<(String, String)>>>,
        block_next_pair: Arc<AtomicBool>,
        pair_gate: Arc<tokio::sync::Semaphore>,
    }

    impl BlueZProvider for ApiFixtureBlueZ {
        fn snapshot(&self) -> BoxFuture<'static, Result<BlueZSnapshot, BluetoothError>> {
            Box::pin(async {
                Ok(BlueZSnapshot {
                    powered: true,
                    alias: Some("LumiStripe".to_owned()),
                    scanning: false,
                    devices: vec![
                        crate::bluetooth_models::BluetoothDevice {
                            address: "AA:BB:CC:DD:EE:01".to_owned(),
                            name: "Test headset".to_owned(),
                            paired: true,
                            connected: false,
                            roles: vec!["input".to_owned()],
                        },
                        crate::bluetooth_models::BluetoothDevice {
                            address: "AA:BB:CC:DD:EE:02".to_owned(),
                            name: "Test speaker".to_owned(),
                            paired: true,
                            connected: false,
                            roles: vec!["output".to_owned()],
                        },
                    ],
                })
            })
        }

        fn execute(&self, command: BlueZCommand) -> BoxFuture<'static, Result<(), BluetoothError>> {
            let commands = self.commands.clone();
            let fail_next_pair = self.fail_next_pair.clone();
            let fail_next_operation = self.fail_next_operation.clone();
            let block_next_pair = self.block_next_pair.clone();
            let pair_gate = self.pair_gate.clone();
            Box::pin(async move {
                if matches!(&command, BlueZCommand::Pair(_))
                    && block_next_pair.swap(false, Ordering::AcqRel)
                {
                    commands
                        .send(command)
                        .map_err(|_| BluetoothError::new("test", "command receiver stopped"))?;
                    let _permit = pair_gate
                        .acquire_owned()
                        .await
                        .map_err(|_| BluetoothError::new("test", "pair gate closed"))?;
                    return Ok(());
                }
                if matches!(&command, BlueZCommand::Pair(_))
                    && fail_next_pair.swap(false, Ordering::AcqRel)
                {
                    return Err(BluetoothError::new("fixture", "pairing was rejected"));
                }
                let failure = fail_next_operation.lock().ok().and_then(|mut pending| {
                    if pending.as_ref().is_some_and(|(operation, _)| {
                        operation == bluez_command_operation(&command)
                    }) {
                        pending.take()
                    } else {
                        None
                    }
                });
                commands
                    .send(command)
                    .map_err(|_| BluetoothError::new("test", "command receiver stopped"))?;
                if let Some((_, message)) = failure {
                    return Err(BluetoothError::new("fixture", message));
                }
                Ok(())
            })
        }
    }

    #[derive(Debug, PartialEq)]
    enum ApiFixturePipeWireCommand {
        SetDefaultSink(String),
        SetVolume { sink: String, volume: f64 },
        SetMute { sink: String, muted: bool },
    }

    struct ApiFixturePipeWire {
        commands: mpsc::UnboundedSender<ApiFixturePipeWireCommand>,
        fail_next_default_sink: Arc<AtomicBool>,
        fail_next_operation: Arc<std::sync::Mutex<Option<(String, String)>>>,
    }

    impl PipeWireProvider for ApiFixturePipeWire {
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
            let fail_next_default_sink = self.fail_next_default_sink.clone();
            let fail_next_operation = self.fail_next_operation.clone();
            Box::pin(async move {
                let output = match command {
                    PipeWireCommand::Sources => {
                        "1 alsa_input.usb_test PipeWire s16le 2ch 48000Hz RUNNING".to_owned()
                    }
                    PipeWireCommand::Sinks => {
                        "1 speaker_main module-test s16le 2ch 48000Hz RUNNING".to_owned()
                    }
                    PipeWireCommand::SinkDetails => "Sink #1\n Name: speaker_main\n Description: Test speaker\n Mute: no\n Volume: front-left: 65536 / 75%".to_owned(),
                    PipeWireCommand::Info => {
                        "Default Sink: speaker_main\nDefault Source: alsa_input.usb_test".to_owned()
                    }
                    PipeWireCommand::Modules => {
                        "1 module-loopback source=lumistripe_spotify.monitor sink=speaker_main".to_owned()
                    }
                    PipeWireCommand::SetDefaultSink(sink) => {
                        if fail_next_default_sink.swap(false, Ordering::AcqRel) {
                            return Err(BluetoothError::new(
                                "fixture",
                                "PipeWire speaker switch was rejected",
                            ));
                        }
                        commands
                            .send(ApiFixturePipeWireCommand::SetDefaultSink(sink))
                            .map_err(|_| {
                                BluetoothError::new("test", "command receiver stopped")
                            })?;
                        String::new()
                    }
                    PipeWireCommand::SetVolume { sink, volume } => {
                        let failure = take_fixture_operation_failure(
                            &fail_next_operation,
                            "set_volume",
                        );
                        if let Some(message) = failure {
                            return Err(BluetoothError::new("fixture", message));
                        }
                        commands
                            .send(ApiFixturePipeWireCommand::SetVolume { sink, volume })
                            .map_err(|_| {
                                BluetoothError::new("test", "command receiver stopped")
                            })?;
                        String::new()
                    }
                    PipeWireCommand::SetMute { sink, muted } => {
                        let failure = take_fixture_operation_failure(
                            &fail_next_operation,
                            "set_mute",
                        );
                        if let Some(message) = failure {
                            return Err(BluetoothError::new("fixture", message));
                        }
                        commands
                            .send(ApiFixturePipeWireCommand::SetMute { sink, muted })
                            .map_err(|_| {
                                BluetoothError::new("test", "command receiver stopped")
                            })?;
                        String::new()
                    }
                    PipeWireCommand::SinkInputs
                    | PipeWireCommand::StreamStatus
                    | PipeWireCommand::SetDefaultSource(_)
                    | PipeWireCommand::MoveSinkInput { .. }
                    | PipeWireCommand::LoadModule { .. }
                    | PipeWireCommand::UnloadModule(_) => String::new(),
                };
                Ok(output)
            })
        }
    }

    const BLUETOOTH_WIFI_API_REQUEST_TIMEOUT: Duration = Duration::from_secs(5);
    const BLUETOOTH_WIFI_TEST_TIMEOUT: Duration = Duration::from_secs(120);
    const BLUETOOTH_WIFI_TEST_TRACE_ENV: &str = "LUMISTRIPE_TRACE_BLUETOOTH_WIFI_TEST";

    fn trace_bluetooth_wifi_test(message: impl std::fmt::Display) {
        if std::env::var_os(BLUETOOTH_WIFI_TEST_TRACE_ENV).is_some() {
            eprintln!("[bluetooth-wifi-test] {message}");
        }
    }

    async fn api_request(
        app: &WebApplication,
        method: &str,
        uri: &str,
        body: Option<Value>,
    ) -> (StatusCode, Value) {
        let mut builder = Request::builder().method(method).uri(uri);
        let body = match body {
            Some(value) => {
                builder = builder.header("content-type", "application/json");
                Body::from(serde_json::to_vec(&value).unwrap())
            }
            None => Body::empty(),
        };
        let response = app
            .router()
            .oneshot(builder.body(body).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let body = axum::body::to_bytes(response.into_body(), 64 * 1024)
            .await
            .unwrap();
        (status, serde_json::from_slice(&body).unwrap())
    }

    async fn bounded_bluetooth_wifi_api_request(
        app: &WebApplication,
        method: &str,
        uri: &str,
        body: Option<Value>,
    ) -> (StatusCode, Value) {
        trace_bluetooth_wifi_test(format_args!("request started: {method} {uri}"));
        let response = timeout(
            BLUETOOTH_WIFI_API_REQUEST_TIMEOUT,
            api_request(app, method, uri, body),
        )
        .await
        .unwrap_or_else(|_| {
            panic!(
                "API request exceeded the {:?} test deadline: {method} {uri}",
                BLUETOOTH_WIFI_API_REQUEST_TIMEOUT
            )
        });
        trace_bluetooth_wifi_test(format_args!("request completed: {method} {uri}"));
        response
    }

    async fn wait_for_completed_bluetooth_operation(app: &WebApplication) -> Value {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        while tokio::time::Instant::now() < deadline {
            let (status, body) =
                bounded_bluetooth_wifi_api_request(app, "GET", "/api/audio/bluetooth", None).await;
            assert_eq!(status, StatusCode::OK);
            match body["operation_state"].as_str() {
                Some("complete") => return body,
                Some("failed") => panic!("Bluetooth operation failed: {body}"),
                _ => sleep(Duration::from_millis(10)).await,
            }
        }
        panic!("Bluetooth operation did not complete before the test deadline");
    }

    async fn wait_for_failed_bluetooth_operation(app: &WebApplication) -> Value {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        while tokio::time::Instant::now() < deadline {
            let (status, body) =
                bounded_bluetooth_wifi_api_request(app, "GET", "/api/audio/bluetooth", None).await;
            assert_eq!(status, StatusCode::OK);
            match body["operation_state"].as_str() {
                Some("failed") => return body,
                Some("complete") => panic!("Bluetooth operation unexpectedly completed: {body}"),
                _ => sleep(Duration::from_millis(10)).await,
            }
        }
        panic!("Bluetooth operation did not fail before the test deadline");
    }

    fn bluez_command_operation(command: &BlueZCommand) -> &'static str {
        match command {
            BlueZCommand::Scan => "scan",
            BlueZCommand::SetPower(_) => "set_power",
            BlueZCommand::SetAlias(_) => "set_alias",
            BlueZCommand::Pair(_) => "pair",
            BlueZCommand::Connect { .. } => "connect",
            BlueZCommand::Forget(_) => "forget",
            BlueZCommand::Disconnect(_) => "disconnect",
        }
    }

    fn take_fixture_operation_failure(
        pending: &Arc<std::sync::Mutex<Option<(String, String)>>>,
        operation: &str,
    ) -> Option<String> {
        pending.lock().ok().and_then(|mut pending| {
            if pending
                .as_ref()
                .is_some_and(|(expected, _)| expected == operation)
            {
                pending.take().map(|(_, message)| message)
            } else {
                None
            }
        })
    }

    fn bluetooth_fixture_command_matches(
        command: BlueZCommand,
        operation: &str,
        arguments: &[Value],
    ) -> bool {
        match command {
            BlueZCommand::Scan => operation == "scan" && arguments.is_empty(),
            BlueZCommand::SetPower(powered) => {
                operation == "set_power"
                    && arguments.first().and_then(Value::as_bool) == Some(powered)
            }
            BlueZCommand::SetAlias(alias) => {
                operation == "set_alias"
                    && arguments.first().and_then(Value::as_str) == Some(alias.as_str())
            }
            BlueZCommand::Pair(address) => {
                operation == "pair"
                    && arguments.first().and_then(Value::as_str) == Some(address.as_str())
            }
            BlueZCommand::Forget(address) => {
                operation == "forget"
                    && arguments.first().and_then(Value::as_str) == Some(address.as_str())
            }
            BlueZCommand::Disconnect(address) => {
                operation == "disconnect"
                    && arguments.first().and_then(Value::as_str) == Some(address.as_str())
            }
            BlueZCommand::Connect { address, role } => {
                operation == "connect"
                    && arguments.first().and_then(Value::as_str) == Some(address.as_str())
                    && arguments.get(1).and_then(Value::as_str) == Some(role.as_str())
            }
        }
    }

    fn pipewire_fixture_command_matches(
        command: ApiFixturePipeWireCommand,
        operation: &str,
        arguments: &[Value],
    ) -> bool {
        match command {
            ApiFixturePipeWireCommand::SetDefaultSink(sink) => {
                operation == "set_default_sink"
                    && arguments.first().and_then(Value::as_str) == Some(sink.as_str())
            }
            ApiFixturePipeWireCommand::SetVolume { sink, volume } => {
                operation == "set_volume"
                    && arguments.first().and_then(Value::as_str) == Some(sink.as_str())
                    && arguments.get(1).and_then(Value::as_f64) == Some(volume)
            }
            ApiFixturePipeWireCommand::SetMute { sink, muted } => {
                operation == "set_mute"
                    && arguments.first().and_then(Value::as_str) == Some(sink.as_str())
                    && arguments.get(1).and_then(Value::as_bool) == Some(muted)
            }
        }
    }

    async fn assert_bluetooth_route_fixture_case(
        app: &WebApplication,
        bluez_commands: &mut mpsc::UnboundedReceiver<BlueZCommand>,
        pipewire_commands: &mut mpsc::UnboundedReceiver<ApiFixturePipeWireCommand>,
        case: &Value,
    ) {
        let method = case["method"].as_str().expect("fixture method");
        let uri = case["path"].as_str().expect("fixture path");
        let body = case.get("body").cloned();
        let busy_deadline = tokio::time::Instant::now() + Duration::from_secs(1);
        let (status, response) = loop {
            let result = bounded_bluetooth_wifi_api_request(app, method, uri, body.clone()).await;
            if result.0 == StatusCode::CONFLICT
                && result.1["detail"] == "another Bluetooth operation is already running"
            {
                assert!(
                    tokio::time::Instant::now() < busy_deadline,
                    "Bluetooth operation gate did not release before {method} {uri}"
                );
                sleep(Duration::from_millis(5)).await;
                continue;
            }
            break result;
        };
        assert_eq!(status, StatusCode::OK, "{method} {uri}: {response}");
        let provider = case["provider"].as_str().expect("fixture provider");
        let operation = case["operation"].as_str().expect("fixture operation");
        let arguments = case["arguments"]
            .as_array()
            .expect("fixture operation arguments");
        match provider {
            "bluez" => {
                let command = timeout(Duration::from_secs(1), bluez_commands.recv())
                    .await
                    .expect("BlueZ provider should receive the route command")
                    .expect("BlueZ provider command channel should stay open");
                assert!(
                    bluetooth_fixture_command_matches(command, operation, arguments),
                    "unexpected BlueZ command for {method} {uri}"
                );
                let completed = wait_for_completed_bluetooth_operation(app).await;
                assert_eq!(completed["operation_state"], "complete");
            }
            "pipewire" => {
                let command = timeout(Duration::from_secs(1), pipewire_commands.recv())
                    .await
                    .expect("PipeWire provider should receive the route command")
                    .expect("PipeWire provider command channel should stay open");
                assert!(
                    pipewire_fixture_command_matches(command, operation, arguments),
                    "unexpected PipeWire command for {method} {uri}"
                );
            }
            _ => panic!("unknown provider in Bluetooth API fixture: {provider}"),
        }
    }

    struct ChangingSpotify {
        status: watch::Receiver<SpotifyStatus>,
    }

    impl SpotifyProvider for ChangingSpotify {
        fn start(&self) {}

        fn stop(&self) {}

        fn status(&self) -> SpotifyStatus {
            self.status.borrow().clone()
        }

        fn control<'a>(
            &'a self,
            _action: SpotifyControlAction,
            _value: Option<Value>,
        ) -> ProviderFuture<'a> {
            Box::pin(async { Err(SpotifyError::new("test", "test provider")) })
        }
    }

    #[test]
    fn origin_check_accepts_same_origin_and_rejects_cross_origin() {
        let mut headers = HeaderMap::new();
        headers.insert("host", HeaderValue::from_static("localhost:8000"));
        headers.insert("origin", HeaderValue::from_static("http://localhost:8000"));
        assert!(websocket_origin_allowed(&headers));
        headers.insert("origin", HeaderValue::from_static("https://evil.example"));
        assert!(!websocket_origin_allowed(&headers));
    }

    #[tokio::test]
    async fn dashboard_state_publishes_provider_changes_without_control_revisions() {
        let cancellation = CancellationToken::new();
        let (spotify_status, spotify_receiver) = watch::channel(SpotifyStatus {
            configured: true,
            connected: false,
            logged_in: false,
            is_active: false,
            device_name: None,
            status: SpotifyPlaybackStatus::Idle,
            track: None,
            position_ms: 0,
            duration_ms: None,
            volume: 0,
            shuffle: false,
            repeat: SpotifyRepeat::Off,
            error: None,
        });
        let spotify: Arc<dyn SpotifyProvider> = Arc::new(ChangingSpotify {
            status: spotify_receiver,
        });
        let (bluetooth, bluetooth_task) =
            BluetoothService::new(false, false, 1, 1, cancellation.clone());
        let (_control_state, control_receiver) = watch::channel(Arc::new(json!({
            "running": true,
            "revision": 7,
        })));
        let (mut dashboard_state, publisher_task) = spawn_dashboard_state_publisher(
            control_receiver,
            spotify,
            bluetooth,
            cancellation.clone(),
        );
        assert_eq!(dashboard_state.borrow()["revision"], 7);
        assert_eq!(dashboard_state.borrow()["spotify"]["connected"], false);

        spotify_status.send_modify(|status| {
            status.connected = true;
            status.logged_in = true;
            status.is_active = true;
            status.status = SpotifyPlaybackStatus::Playing;
            status.device_name = Some("LumiStripe".to_owned());
        });
        timeout(Duration::from_secs(2), async {
            loop {
                dashboard_state.changed().await.unwrap();
                if dashboard_state.borrow()["spotify"]["status"] == "playing" {
                    break;
                }
            }
        })
        .await
        .expect("provider changes should reach dashboard state");
        assert_eq!(dashboard_state.borrow()["revision"], 7);

        cancellation.cancel();
        timeout(Duration::from_secs(1), publisher_task)
            .await
            .expect("dashboard publisher should stop promptly")
            .unwrap();
        timeout(Duration::from_secs(1), bluetooth_task)
            .await
            .expect("disabled Bluetooth poller should stop promptly")
            .unwrap();
    }

    #[cfg(unix)]
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn application_shutdown_cancels_an_inflight_bluetooth_disconnect() {
        let directory = tempdir().unwrap();
        let (command_sender, mut command_receiver) = mpsc::unbounded_channel();
        let operation_dropped = Arc::new(AtomicBool::new(false));
        let bluez = Arc::new(PendingBlueZDisconnect {
            commands: command_sender,
            operation_dropped: operation_dropped.clone(),
        });
        let config = AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "hardware",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_backend: crate::config::SpotifyBackend::Disabled,
            spotify_data_dir: PathBuf::new(),
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let mut app = WebApplication::start_with_bluetooth_providers(
            config,
            None,
            bluez,
            Arc::new(EmptyPipeWire),
            None,
        )
        .await
        .expect("web application should start with injected providers");

        let response = app
            .router()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/audio/bluetooth/disconnect")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"address":"AA:BB:CC:DD:EE:02"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert!(matches!(
            timeout(Duration::from_secs(1), command_receiver.recv())
                .await
                .unwrap(),
            Some(BlueZCommand::Disconnect(address)) if address == "AA:BB:CC:DD:EE:02"
        ));
        assert!(!operation_dropped.load(Ordering::Acquire));

        timeout(Duration::from_secs(12), app.shutdown())
            .await
            .expect("application shutdown should finish within its task deadlines");
        timeout(Duration::from_secs(1), async {
            while !operation_dropped.load(Ordering::Acquire) {
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("shutdown should cancel the pending BlueZ provider operation");
    }

    #[cfg(all(feature = "bluez", feature = "pipewire", target_os = "linux"))]
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    #[ignore = "requires a live BlueZ adapter and PipeWire user daemon"]
    async fn live_native_audio_status_uses_system_providers_with_mock_outputs() {
        let directory = tempdir().unwrap();
        let config = AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "hardware",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_backend: crate::config::SpotifyBackend::Disabled,
            spotify_data_dir: PathBuf::new(),
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let mut app = WebApplication::start(config, None)
            .await
            .expect("web application should start with mock outputs and system providers");

        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        loop {
            let (status, body) = api_request(&app, "GET", "/api/audio/bluetooth", None).await;
            assert_eq!(status, StatusCode::OK);
            if body["available"] == true && body["error"].is_null() {
                let outputs = body["output_devices"]
                    .as_array()
                    .expect("Bluetooth status should always include the output-device list");
                assert!(
                    outputs.iter().all(|output| output["selector"]
                        .as_str()
                        .is_some_and(|selector| !selector.is_empty())),
                    "each available PipeWire output should have a selector"
                );
                if std::env::var_os("LUMISTRIPE_REQUIRE_PIPEWIRE_OUTPUT").is_some() {
                    assert!(
                        !outputs.is_empty(),
                        "the live PipeWire graph should expose at least one selectable output"
                    );
                }
                break;
            }
            let output_count = body["output_devices"]
                .as_array()
                .map_or(0, std::vec::Vec::len);
            let error = body["error"].as_str().unwrap_or("none");
            assert!(
                tokio::time::Instant::now() < deadline,
                "native provider status did not become available: available={}, powered={}, error={}, output_count={output_count}",
                body["available"],
                body["powered"],
                error
            );
            sleep(Duration::from_millis(50)).await;
        }

        app.shutdown().await;
    }

    #[cfg(target_os = "linux")]
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    #[ignore = "requires the installed NetworkManager wrapper and service-user sudo rule"]
    async fn live_networkmanager_status_route_uses_installed_restricted_wrapper() {
        let directory = tempdir().unwrap();
        let config = AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
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
            spotify_backend: crate::config::SpotifyBackend::Disabled,
            spotify_data_dir: PathBuf::new(),
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let mut app = WebApplication::start_with_bluetooth_providers(
            config,
            None,
            Arc::new(EmptyBlueZ),
            Arc::new(EmptyPipeWire),
            None,
        )
        .await
        .expect("web application should start with mock outputs and isolated audio providers");

        let (status, body) = api_request(&app, "GET", "/api/network/status", None).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "network status route returned {body}"
        );
        assert_eq!(body["manager_available"], true);
        assert!(body["error"].is_null());

        let (update_status, update_body) =
            api_request(&app, "GET", "/api/system/update", None).await;
        assert_eq!(
            update_status,
            StatusCode::OK,
            "update status route returned {update_body}"
        );
        assert_eq!(update_body["supported"], true);
        assert_eq!(update_body["install_state"], "idle");

        app.shutdown().await;
    }

    #[cfg(target_os = "linux")]
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    #[ignore = "requires initialized updater state and outbound GitHub access"]
    async fn live_update_check_route_refreshes_status_without_python() {
        let directory = tempdir().unwrap();
        let config = AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
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
            spotify_backend: crate::config::SpotifyBackend::Disabled,
            spotify_data_dir: PathBuf::new(),
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let mut app = WebApplication::start_with_bluetooth_providers(
            config,
            None,
            Arc::new(EmptyBlueZ),
            Arc::new(EmptyPipeWire),
            None,
        )
        .await
        .expect("web application should start with mock outputs and isolated audio providers");

        let (status, before) = api_request(&app, "GET", "/api/system/update", None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(before["supported"], true);
        let previous_check = before["checked_at"].clone();

        let (accepted, body) = api_request(&app, "POST", "/api/system/update/check", None).await;
        assert_eq!(
            accepted,
            StatusCode::ACCEPTED,
            "update check returned {body}"
        );
        assert_eq!(body["supported"], true);

        let deadline = tokio::time::Instant::now() + Duration::from_secs(40);
        loop {
            let (status, refreshed) = api_request(&app, "GET", "/api/system/update", None).await;
            assert_eq!(status, StatusCode::OK);
            if refreshed["checked_at"].is_string() && refreshed["checked_at"] != previous_check {
                assert!(
                    refreshed["check_error"].is_null(),
                    "Rust update check failed: {refreshed}"
                );
                assert!(refreshed["installed_commit"].as_str().is_some());
                assert_eq!(
                    refreshed["update_available"].as_bool(),
                    Some(refreshed["available_commit"].is_string())
                );
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "Rust update check did not finish: {refreshed}"
            );
            sleep(Duration::from_millis(50)).await;
        }

        app.shutdown().await;
    }

    #[cfg(unix)]
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn bluetooth_output_and_wifi_routes_execute_injected_providers() {
        timeout(
            BLUETOOTH_WIFI_TEST_TIMEOUT,
            bluetooth_output_and_wifi_routes_execute_injected_providers_inner(),
        )
        .await
        .expect("Bluetooth and Wi-Fi route integration test exceeded its 120 second deadline");
    }

    #[cfg(unix)]
    async fn bluetooth_output_and_wifi_routes_execute_injected_providers_inner() {
        trace_bluetooth_wifi_test("creating provider and Wi-Fi fixtures");
        let directory = tempdir().unwrap();
        let (bluez_sender, mut bluez_commands) = mpsc::unbounded_channel();
        let (pipewire_sender, mut pipewire_commands) = mpsc::unbounded_channel();
        let fail_next_pair = Arc::new(AtomicBool::new(false));
        let fail_next_bluez_operation = Arc::new(std::sync::Mutex::new(None));
        let block_next_pair = Arc::new(AtomicBool::new(false));
        let pair_gate = Arc::new(tokio::sync::Semaphore::new(0));
        let fail_next_default_sink = Arc::new(AtomicBool::new(false));
        let fail_next_pipewire_operation = Arc::new(std::sync::Mutex::new(None));
        let bluez = Arc::new(ApiFixtureBlueZ {
            commands: bluez_sender,
            fail_next_pair: fail_next_pair.clone(),
            fail_next_operation: fail_next_bluez_operation.clone(),
            block_next_pair: block_next_pair.clone(),
            pair_gate: pair_gate.clone(),
        });
        let pipewire = Arc::new(ApiFixturePipeWire {
            commands: pipewire_sender,
            fail_next_default_sink: fail_next_default_sink.clone(),
            fail_next_operation: fail_next_pipewire_operation.clone(),
        });
        let network_helper = directory.path().join("network-helper");
        std::fs::write(
            &network_helper,
            r#"#!/bin/sh
request="$(cat)"
case "$request" in
  *'"operation":"status"'*)
    printf '%s' '{"ok":true,"manager_available":true,"error":null,"adapters":[]}'
    ;;
  *'"operation":"scan"'*)
    case "$request" in
      *'"interface":"wlan0"'*) printf '%s' '{"ok":false,"error_code":"invalid","error":"The hotspot adapter cannot connect to an internet network."}' ;;
      *'"interface":"wlan9"'*) printf '%s' '{"ok":false,"error_code":"unsupported","error":"Wi-Fi scanning is not supported on this adapter."}' ;;
      *'"interface":"wlan8"'*) printf '%s' '{"ok":false,"error_code":"unavailable","error":"NetworkManager is unavailable."}' ;;
      *'"interface":"wlan7"'*) printf '%s' '{"ok":false,"error_code":"permission_denied","error":"NetworkManager could not complete the request."}' ;;
      *'"interface":"wlan6"'*) exit 2 ;;
      *'"interface":"wlan5"'*) exec /usr/bin/sleep 5 ;;
      *) printf '%s' '{"ok":true,"interface":"wlan1","networks":[{"ssid":"Party Wi-Fi","security":"WPA2","signal":88,"secured":true}]}' ;;
    esac
    ;;
  *'"password":"wrong-pass"'*)
    printf '%s' '{"ok":false,"error_code":"connection_failed","error":"Could not connect to that Wi-Fi network. Check the password and try again."}'
    ;;
  *'"operation":"connect"'*)
    printf '%s' '{"ok":true,"interface":"wlan1","ssid":"Party Wi-Fi","connected":true}'
    ;;
  *) exit 1 ;;
esac
"#,
        )
        .unwrap();
        let mut helper_permissions = std::fs::metadata(&network_helper).unwrap().permissions();
        helper_permissions.set_mode(0o700);
        std::fs::set_permissions(&network_helper, helper_permissions).unwrap();
        let network = NetworkManagerService::new(
            &network_helper,
            std::iter::empty::<String>(),
            Duration::from_secs(2),
        );
        let config = AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
                pixel_count: 8,
                backend: lumistripe_engine_config::OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "hardware",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_backend: crate::config::SpotifyBackend::Disabled,
            spotify_data_dir: PathBuf::new(),
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: directory.path().join("settings.json"),
        };
        let mut app = WebApplication::start_with_bluetooth_providers(
            config,
            None,
            bluez,
            pipewire,
            Some(network),
        )
        .await
        .expect("web application should start with injected providers");
        trace_bluetooth_wifi_test("application started; waiting for initial Bluetooth snapshot");

        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        loop {
            let (status, body) =
                bounded_bluetooth_wifi_api_request(&app, "GET", "/api/audio/bluetooth", None).await;
            assert_eq!(status, StatusCode::OK);
            if body["available"] == true && body["default_sink"] == "speaker_main" {
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "Bluetooth snapshot did not reach the fixture state: {body}"
            );
            sleep(Duration::from_millis(10)).await;
        }
        trace_bluetooth_wifi_test("initial Bluetooth snapshot ready");

        let fixture_path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/bluetooth-api-mutations-v1.json");
        let mutation_cases: Vec<Value> =
            serde_json::from_slice(&std::fs::read(&fixture_path).unwrap()).unwrap();
        assert_eq!(mutation_cases.len(), 10);
        for case in &mutation_cases {
            trace_bluetooth_wifi_test(format_args!(
                "running Bluetooth mutation fixture: {} {} ({})",
                case["method"].as_str().unwrap_or("?"),
                case["path"].as_str().unwrap_or("?"),
                case["operation"].as_str().unwrap_or("?")
            ));
            assert_bluetooth_route_fixture_case(
                &app,
                &mut bluez_commands,
                &mut pipewire_commands,
                case,
            )
            .await;
        }
        trace_bluetooth_wifi_test("Bluetooth mutation fixtures complete");

        let failure_fixture_path = fixture_path.with_file_name("bluetooth-api-failure-v1.json");
        let failure_case: Value =
            serde_json::from_slice(&std::fs::read(failure_fixture_path).unwrap()).unwrap();
        fail_next_pair.store(true, Ordering::Release);
        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            failure_case["method"].as_str().unwrap(),
            failure_case["path"].as_str().unwrap(),
            failure_case.get("body").cloned(),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "pair request: {body}");
        assert_eq!(body["operation"], failure_case["operation"]);
        assert_eq!(body["operation_state"], "running");

        let failure_deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        let failed_status = loop {
            let (status, current) =
                bounded_bluetooth_wifi_api_request(&app, "GET", "/api/audio/bluetooth", None).await;
            assert_eq!(status, StatusCode::OK);
            if current["operation_state"] == "failed" {
                break current;
            }
            assert!(
                tokio::time::Instant::now() < failure_deadline,
                "provider failure did not reach a terminal Bluetooth status: {current}"
            );
            sleep(Duration::from_millis(5)).await;
        };
        assert_eq!(failed_status["error"], failure_case["error"]);
        trace_bluetooth_wifi_test("Bluetooth provider failure fixture complete");

        let api_failures_path = fixture_path.with_file_name("bluetooth-api-failures-v1.json");
        let api_failure_cases: Vec<Value> =
            serde_json::from_slice(&std::fs::read(api_failures_path).unwrap()).unwrap();
        for case in &api_failure_cases {
            trace_bluetooth_wifi_test(format_args!(
                "running Bluetooth failure fixture: {} {} ({})",
                case["method"].as_str().unwrap_or("?"),
                case["path"].as_str().unwrap_or("?"),
                case["operation"].as_str().unwrap_or("?")
            ));
            *fail_next_bluez_operation.lock().unwrap() = Some((
                case["operation"].as_str().unwrap().to_owned(),
                case["error"].as_str().unwrap().to_owned(),
            ));
            let (status, body) = bounded_bluetooth_wifi_api_request(
                &app,
                case["method"].as_str().unwrap(),
                case["path"].as_str().unwrap(),
                case.get("body").cloned(),
            )
            .await;
            assert_eq!(status, StatusCode::OK, "Bluetooth failure request: {body}");
            assert_eq!(body["operation"], case["state_operation"]);
            assert_eq!(body["operation_state"], "running");
            let command = timeout(Duration::from_secs(1), bluez_commands.recv())
                .await
                .expect("BlueZ provider should receive the failing route command")
                .expect("BlueZ provider command channel should stay open");
            assert!(bluetooth_fixture_command_matches(
                command,
                case["operation"].as_str().unwrap(),
                case["arguments"].as_array().unwrap(),
            ));
            let failed_status = wait_for_failed_bluetooth_operation(&app).await;
            assert_eq!(failed_status["operation_state"], "failed");
            assert_eq!(failed_status["error"], case["error"]);
        }
        trace_bluetooth_wifi_test("Bluetooth API failure fixtures complete");

        let output_failure_path =
            fixture_path.with_file_name("bluetooth-api-output-failure-v1.json");
        let output_failure_case: Value =
            serde_json::from_slice(&std::fs::read(output_failure_path).unwrap()).unwrap();
        fail_next_default_sink.store(true, Ordering::Release);
        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            output_failure_case["method"].as_str().unwrap(),
            output_failure_case["path"].as_str().unwrap(),
            output_failure_case.get("body").cloned(),
        )
        .await;
        assert_eq!(status.as_u16(), output_failure_case["status"]);
        assert_eq!(body["detail"], output_failure_case["error"]);
        assert!(!fail_next_default_sink.load(Ordering::Acquire));
        assert!(pipewire_commands.try_recv().is_err());
        let (status, body) =
            bounded_bluetooth_wifi_api_request(&app, "GET", "/api/audio/bluetooth", None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["default_sink"], "speaker_main");
        trace_bluetooth_wifi_test("PipeWire output rollback fixture complete");

        let output_operation_failures_path =
            fixture_path.with_file_name("bluetooth-api-output-operation-failures-v1.json");
        let output_operation_failure_cases: Vec<Value> =
            serde_json::from_slice(&std::fs::read(output_operation_failures_path).unwrap())
                .unwrap();
        for case in &output_operation_failure_cases {
            trace_bluetooth_wifi_test(format_args!(
                "running PipeWire failure fixture: {} {} ({})",
                case["method"].as_str().unwrap_or("?"),
                case["path"].as_str().unwrap_or("?"),
                case["operation"].as_str().unwrap_or("?")
            ));
            *fail_next_pipewire_operation.lock().unwrap() = Some((
                case["operation"].as_str().unwrap().to_owned(),
                case["error"].as_str().unwrap().to_owned(),
            ));
            let (status, body) = bounded_bluetooth_wifi_api_request(
                &app,
                case["method"].as_str().unwrap(),
                case["path"].as_str().unwrap(),
                case.get("body").cloned(),
            )
            .await;
            assert_eq!(status.as_u16(), case["status"]);
            assert_eq!(body["detail"], case["error"]);
            assert!(pipewire_commands.try_recv().is_err());
            let (status, current) =
                bounded_bluetooth_wifi_api_request(&app, "GET", "/api/audio/bluetooth", None).await;
            assert_eq!(status, StatusCode::OK);
            assert_eq!(current["default_sink"], "speaker_main");
            assert_eq!(current["output_volume"], 0.75);
            assert_eq!(current["output_muted"], false);
        }
        trace_bluetooth_wifi_test("PipeWire failure fixtures complete");

        let conflict_fixture_path = fixture_path.with_file_name("bluetooth-api-conflict-v1.json");
        let conflict_fixture: Value =
            serde_json::from_slice(&std::fs::read(conflict_fixture_path).unwrap()).unwrap();
        let start_case = &conflict_fixture["start"];
        let conflict_case = &conflict_fixture["conflict"];
        trace_bluetooth_wifi_test("starting pending Bluetooth operation conflict fixture");
        block_next_pair.store(true, Ordering::Release);
        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            start_case["method"].as_str().unwrap(),
            start_case["path"].as_str().unwrap(),
            start_case.get("body").cloned(),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "pending pair request: {body}");
        assert_eq!(body["operation"], start_case["operation"]);
        assert_eq!(body["operation_state"], "running");
        let command = timeout(Duration::from_secs(1), bluez_commands.recv())
            .await
            .expect("BlueZ provider should receive the pending pair command")
            .expect("BlueZ provider command channel should stay open");
        assert!(matches!(
            command,
            BlueZCommand::Pair(address)
                if address == start_case["body"]["address"].as_str().unwrap()
        ));
        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            conflict_case["method"].as_str().unwrap(),
            conflict_case["path"].as_str().unwrap(),
            conflict_case.get("body").cloned(),
        )
        .await;
        assert_eq!(status.as_u16(), conflict_case["status"]);
        assert_eq!(body["detail"], conflict_case["error"]);
        assert!(bluez_commands.try_recv().is_err());
        pair_gate.add_permits(1);
        let completed_pair = wait_for_completed_bluetooth_operation(&app).await;
        assert_eq!(completed_pair["operation_state"], "complete");
        trace_bluetooth_wifi_test("Bluetooth operation conflict fixture complete");

        trace_bluetooth_wifi_test("running Wi-Fi status, scan, and connect routes");
        let (status, body) =
            bounded_bluetooth_wifi_api_request(&app, "GET", "/api/network/status", None).await;
        assert_eq!(status, StatusCode::OK, "network status: {body}");
        assert_eq!(body["manager_available"], true);
        assert_eq!(body["adapters"], json!([]));

        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            "POST",
            "/api/network/scan",
            Some(json!({"interface": "wlan1"})),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "network scan: {body}");
        assert_eq!(
            body,
            json!({
                "interface": "wlan1",
                "networks": [{
                    "ssid": "Party Wi-Fi",
                    "security": "WPA2",
                    "signal": 88,
                    "secured": true
                }]
            })
        );

        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            "POST",
            "/api/network/connect",
            Some(json!({
                "interface": "wlan1",
                "ssid": "Party Wi-Fi",
                "password": "correct-horse"
            })),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "network connect: {body}");
        assert_eq!(
            body,
            json!({
                "interface": "wlan1",
                "ssid": "Party Wi-Fi",
                "connected": true
            })
        );

        let network_failure_path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/network-api-connect-failure-v1.json");
        let network_failure_case: Value =
            serde_json::from_slice(&std::fs::read(network_failure_path).unwrap()).unwrap();
        let (status, body) = bounded_bluetooth_wifi_api_request(
            &app,
            network_failure_case["method"].as_str().unwrap(),
            network_failure_case["path"].as_str().unwrap(),
            network_failure_case.get("body").cloned(),
        )
        .await;
        assert_eq!(status.as_u16(), network_failure_case["status"]);
        assert_eq!(body["detail"], network_failure_case["error"]);
        assert!(
            !body
                .to_string()
                .contains(network_failure_case["body"]["password"].as_str().unwrap())
        );
        trace_bluetooth_wifi_test("Wi-Fi connect failure fixture complete");

        let network_helper_errors_path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/network-api-helper-errors-v1.json");
        let network_helper_errors: Vec<Value> =
            serde_json::from_slice(&std::fs::read(network_helper_errors_path).unwrap()).unwrap();
        for case in network_helper_errors {
            trace_bluetooth_wifi_test(format_args!(
                "running Wi-Fi helper error fixture: {}",
                case["name"].as_str().unwrap_or("?")
            ));
            if case["name"] == "helper_unspawnable" {
                std::fs::remove_file(&network_helper).unwrap();
            }
            let (status, body) = bounded_bluetooth_wifi_api_request(
                &app,
                case["method"].as_str().unwrap(),
                case["path"].as_str().unwrap(),
                case.get("body").cloned(),
            )
            .await;
            assert_eq!(status.as_u16(), case["status_code"], "{}", case["name"]);
            assert_eq!(body["detail"], case["error"], "{}", case["name"]);
            if let Some(password) = case["body"]["password"].as_str() {
                assert!(!body.to_string().contains(password), "{}", case["name"]);
            }
        }

        trace_bluetooth_wifi_test(
            "Wi-Fi helper error fixtures complete; shutting down application",
        );
        timeout(Duration::from_secs(10), app.shutdown())
            .await
            .expect("application shutdown exceeded its 10 second test deadline");
        trace_bluetooth_wifi_test("application shutdown complete");
    }
}
