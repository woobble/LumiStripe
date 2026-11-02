use crate::auth::{PairingAuth, PairingError, SESSION_COOKIE, SESSION_MAX_AGE_SECONDS};
use crate::config::AppConfig;
use crate::control::ControlPlane;
use crate::error::ApiError;
use crate::state::{ControlModel, PlaybackMode, animation_list};
use crate::streams::{
    AudioTelemetryReceiver, PreviewReceiver, spawn_audio_telemetry_publisher,
    spawn_health_publisher, spawn_preview_publisher, websocket_audio, websocket_preview,
    websocket_state,
};
use axum::Json;
use axum::body::Body;
use axum::extract::{Json as JsonExtractor, Request, State};
use axum::http::header::{COOKIE, HOST, ORIGIN, RETRY_AFTER, SET_COOKIE};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post, put};
use axum::{Router, middleware as axum_middleware};
use lumistripe_engine_client::EngineClient;
use lumistripe_engine_config::EngineConfig;
use lumistripe_engine_protocol::{RenderSceneUpdate, ResponseResult};
use serde::{Deserialize, Deserializer};
use serde_json::{Value, json};
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
        config: AppConfig,
        engine_binary: PathBuf,
        pairing_code: Option<String>,
    ) -> Result<Self, String> {
        let access = PairingAuth::new(pairing_code)?;
        let engine_directory = TempDir::new()
            .map_err(|error| format!("create private engine socket directory: {error}"))?;
        let socket_path = engine_directory.path().join("engine.sock");
        let client = EngineClient::new(&socket_path, Duration::from_secs(2));
        let cancellation = CancellationToken::new();
        let model = ControlModel::new(config.clone());
        let initial_scene = model.initial_scene();
        let (desired_scene, desired_scene_receiver) = watch::channel(initial_scene);
        let (health_sender, health) = watch::channel(None);
        let (audio_sender, audio) = watch::channel(None);
        let (preview_sender, preview) = watch::channel(None);
        let (ready_sender, ready_receiver) = oneshot::channel();
        let engine_manager = tokio::spawn(supervise_engine(
            engine_binary,
            socket_path,
            config.engine_config(),
            desired_scene_receiver,
            client.clone(),
            health_sender.clone(),
            cancellation.clone(),
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
        let (control, control_task) = ControlPlane::start(
            model,
            client.clone(),
            health.clone(),
            audio.clone(),
            desired_scene,
            cancellation.clone(),
        );
        let (telemetry_sender, telemetry_receiver) = watch::channel(None);
        let telemetry_task = spawn_audio_telemetry_publisher(
            client.clone(),
            health.clone(),
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
                    &engine_directory.path().join("engine.sock"),
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
        .route("/api/mode", put(set_mode))
        .route("/api/brightness", put(set_brightness))
        .route("/api/animation", put(select_animation))
        .route("/api/blackout", post(set_blackout))
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
    binary: PathBuf,
    socket_path: PathBuf,
    config: EngineConfig,
    mut desired_scene: watch::Receiver<RenderSceneUpdate>,
    client: EngineClient,
    health: watch::Sender<Option<Result<Arc<ResponseResult>, String>>>,
    cancellation: CancellationToken,
    ready: oneshot::Sender<Result<(), String>>,
) {
    let mut ready = Some(ready);
    let mut backoff = Duration::from_millis(250);
    loop {
        if cancellation.is_cancelled() {
            break;
        }
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
                .configure(config.clone())
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
            let scene = desired_scene.borrow_and_update().clone();
            match client
                .update_scene(scene)
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
            tracing::info!("Rust engine restarted and reapplied the latest scene");
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
