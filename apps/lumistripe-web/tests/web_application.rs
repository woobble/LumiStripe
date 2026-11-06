use axum::Router;
use axum::body::Body;
use axum::http::{Method, Request, StatusCode, header};
use futures_util::{SinkExt, StreamExt};
use http_body_util::BodyExt;
use lumistripe_engine_config::OutputBackend;
use lumistripe_web::{ApiDoc, AppConfig, EngineOutputOptions, SpotifyBackend, WebApplication};
use serde_json::{Value, json};
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use tempfile::TempDir;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::net::TcpStream;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, accept_async, connect_async};
use tokio_util::sync::CancellationToken;
use utoipa::OpenApi;

static WEB_APPLICATION_TEST_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

struct TestServer {
    address: std::net::SocketAddr,
    cancellation: CancellationToken,
    task: JoinHandle<Result<(), std::io::Error>>,
}

impl TestServer {
    async fn start(router: Router) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let cancellation = CancellationToken::new();
        let shutdown = cancellation.clone();
        let task = tokio::spawn(async move {
            axum::serve(listener, router)
                .with_graceful_shutdown(shutdown.cancelled_owned())
                .await
        });
        Self {
            address,
            cancellation,
            task,
        }
    }

    async fn stop(self) {
        self.cancellation.cancel();
        timeout(Duration::from_secs(3), self.task)
            .await
            .expect("HTTP server should stop gracefully")
            .expect("HTTP server task should not panic")
            .expect("HTTP server should stop without an IO error");
    }
}

struct HttpResponse {
    status: StatusCode,
    headers: axum::http::HeaderMap,
    body: Value,
    body_text: String,
}

async fn request(
    router: &Router,
    method: Method,
    uri: &str,
    body: Option<Value>,
    cookie: Option<&str>,
    origin: Option<&str>,
) -> HttpResponse {
    let mut builder = Request::builder()
        .method(method)
        .uri(uri)
        .header(header::HOST, "localhost")
        .header(header::CONTENT_TYPE, "application/json");
    if let Some(cookie) = cookie {
        builder = builder.header(header::COOKIE, cookie);
    }
    if let Some(origin) = origin {
        builder = builder.header(header::ORIGIN, origin);
    }
    let body = body.map_or_else(Body::empty, |body| Body::from(body.to_string()));
    let response = tower::ServiceExt::oneshot(router.clone(), builder.body(body).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = response.into_body().collect().await.unwrap().to_bytes();
    let body_text = String::from_utf8_lossy(&body).into_owned();
    let body = serde_json::from_slice(&body).unwrap_or(Value::Null);
    HttpResponse {
        status,
        headers,
        body,
        body_text,
    }
}

async fn process_http_request(
    address: std::net::SocketAddr,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> Result<(u16, String), String> {
    let mut stream = TcpStream::connect(address)
        .await
        .map_err(|error| error.to_string())?;
    let body = body.unwrap_or_default();
    stream
        .write_all(
            format!(
                "{method} {path} HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .as_bytes(),
        )
        .await
        .map_err(|error| error.to_string())?;
    let mut response = Vec::new();
    timeout(Duration::from_secs(3), stream.read_to_end(&mut response))
        .await
        .map_err(|_| "HTTP response timed out".to_owned())?
        .map_err(|error| error.to_string())?;
    let response = String::from_utf8_lossy(&response);
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "HTTP response did not include headers".to_owned())?;
    let status = headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| "HTTP response did not include a valid status".to_owned())?;
    Ok((status, body.to_owned()))
}

async fn process_http_get(
    address: std::net::SocketAddr,
    path: &str,
) -> Result<(u16, String), String> {
    process_http_request(address, "GET", path, None).await
}

#[cfg(unix)]
struct ProcessGroupGuard(std::process::Child);

#[cfg(unix)]
impl Drop for ProcessGroupGuard {
    fn drop(&mut self) {
        let process_group = -(self.0.id() as libc::pid_t);
        unsafe {
            libc::kill(process_group, libc::SIGKILL);
        }
        let _ = self.0.wait();
    }
}

fn assert_object_keys(value: &Value, expected: &[&str]) {
    let object = value.as_object().expect("response should be a JSON object");
    let mut actual = object.keys().map(String::as_str).collect::<Vec<_>>();
    let mut expected = expected.to_vec();
    actual.sort_unstable();
    expected.sort_unstable();
    assert_eq!(actual, expected);
}

#[test]
fn checked_in_openapi_matches_rust_api_doc() {
    let spec: Value = serde_json::from_str(include_str!("../frontend/openapi.json"))
        .expect("Rust-generated OpenAPI document should be valid JSON");
    let rust_doc =
        serde_json::to_value(ApiDoc::openapi()).expect("Rust ApiDoc should serialize to JSON");
    assert_eq!(
        spec, rust_doc,
        "checked-in OpenAPI must be generated by ApiDoc"
    );
    let contracts = [
        ("/api/health", "get", None, None),
        (
            "/api/system/stats",
            "get",
            None,
            Some("#/components/schemas/SystemStats"),
        ),
        (
            "/api/system/update",
            "get",
            None,
            Some("#/components/schemas/UpdateStatus"),
        ),
        (
            "/api/system/update/check",
            "post",
            None,
            Some("#/components/schemas/UpdateStatus"),
        ),
        (
            "/api/system/update/install",
            "post",
            None,
            Some("#/components/schemas/UpdateStatus"),
        ),
        (
            "/api/network/status",
            "get",
            None,
            Some("#/components/schemas/NetworkStatusResponse"),
        ),
        (
            "/api/network/scan",
            "post",
            Some("#/components/schemas/WifiScanRequest"),
            Some("#/components/schemas/WifiScanResponse"),
        ),
        (
            "/api/network/connect",
            "post",
            Some("#/components/schemas/WifiConnectRequest"),
            Some("#/components/schemas/WifiConnectResponse"),
        ),
        (
            "/api/state",
            "get",
            None,
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/animations",
            "get",
            None,
            Some("#/components/schemas/AnimationList"),
        ),
        (
            "/api/mode",
            "put",
            Some("#/components/schemas/ModeRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/brightness",
            "put",
            Some("#/components/schemas/BrightnessRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/animation",
            "put",
            Some("#/components/schemas/AnimationRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/animations/{animation_name}/settings",
            "get",
            None,
            Some("#/components/schemas/AnimationSettingsResponse"),
        ),
        (
            "/api/animations/{animation_name}/settings",
            "put",
            Some("#/components/schemas/AnimationSettingsUpdateRequest"),
            Some("#/components/schemas/AnimationSettingsResponse"),
        ),
        (
            "/api/animations/{animation_name}/settings/reset",
            "post",
            None,
            Some("#/components/schemas/AnimationSettingsResponse"),
        ),
        (
            "/api/blackout",
            "post",
            Some("#/components/schemas/BlackoutRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/identity",
            "get",
            None,
            Some("#/components/schemas/IdentitySettingsResponse"),
        ),
        (
            "/api/identity",
            "put",
            Some("#/components/schemas/IdentitySettingsRequest"),
            Some("#/components/schemas/IdentitySettingsResponse"),
        ),
        (
            "/api/stripes",
            "get",
            None,
            Some("#/components/schemas/StripeTopology"),
        ),
        (
            "/api/stripes",
            "put",
            Some("#/components/schemas/StripeTopologyRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/stripes/test",
            "post",
            Some("#/components/schemas/StripeTestRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/audio/settings",
            "get",
            None,
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/audio/spotify",
            "get",
            None,
            Some("#/components/schemas/SpotifyStatusResponse"),
        ),
        (
            "/api/audio/spotify/control",
            "post",
            Some("#/components/schemas/SpotifyControlRequest"),
            Some("#/components/schemas/SpotifyStatusResponse"),
        ),
        (
            "/api/audio/bluetooth",
            "get",
            None,
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/power",
            "put",
            Some("#/components/schemas/BluetoothPowerRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/alias",
            "put",
            Some("#/components/schemas/BluetoothAliasRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/scan",
            "post",
            None,
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/pair",
            "post",
            Some("#/components/schemas/BluetoothDeviceRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/connect",
            "post",
            Some("#/components/schemas/BluetoothDeviceRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/forget",
            "post",
            Some("#/components/schemas/BluetoothDeviceRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/bluetooth/disconnect",
            "post",
            Some("#/components/schemas/BluetoothDeviceRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/output",
            "put",
            Some("#/components/schemas/AudioOutputSelectionRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/output/volume",
            "put",
            Some("#/components/schemas/AudioOutputVolumeRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/output/mute",
            "put",
            Some("#/components/schemas/AudioOutputMuteRequest"),
            Some("#/components/schemas/BluetoothStatusResponse"),
        ),
        (
            "/api/audio/settings",
            "put",
            Some("#/components/schemas/AudioSettingsRequest"),
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/audio/settings/reset",
            "post",
            Some("#/components/schemas/AudioResetRequest"),
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/audio/device",
            "put",
            Some("#/components/schemas/AudioDeviceRequest"),
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/audio/source",
            "put",
            Some("#/components/schemas/AudioSourceRequest"),
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/audio/sync-offset",
            "put",
            Some("#/components/schemas/AudioSyncOffsetRequest"),
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/audio/sync-calibration",
            "post",
            Some("#/components/schemas/AudioSyncCalibrationRequest"),
            Some("#/components/schemas/AudioSyncCalibrationResponse"),
        ),
        (
            "/api/audio/calibration/session",
            "post",
            Some("#/components/schemas/AudioCalibrationStartRequest"),
            Some("#/components/schemas/AudioCalibrationSessionResponse"),
        ),
        (
            "/api/audio/calibration/session/{session_id}",
            "get",
            None,
            Some("#/components/schemas/AudioCalibrationSessionResponse"),
        ),
        (
            "/api/audio/calibration/session/{session_id}/music",
            "post",
            None,
            Some("#/components/schemas/AudioCalibrationSessionResponse"),
        ),
        (
            "/api/audio/calibration/session/{session_id}/finish",
            "post",
            Some("#/components/schemas/AudioCalibrationFinishRequest"),
            Some("#/components/schemas/AudioSettingsResponse"),
        ),
        (
            "/api/calibration/session",
            "post",
            Some("#/components/schemas/CalibrationStartRequest"),
            Some("#/components/schemas/CalibrationSessionResponse"),
        ),
        (
            "/api/calibration/session/{session_id}",
            "put",
            Some("#/components/schemas/CalibrationUpdateRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/calibration/session/{session_id}/finish",
            "post",
            Some("#/components/schemas/CalibrationFinishRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/music-idle",
            "put",
            Some("#/components/schemas/MusicIdleRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/startup",
            "get",
            None,
            Some("#/components/schemas/StartupSettingsResponse"),
        ),
        (
            "/api/startup",
            "put",
            Some("#/components/schemas/StartupSettingsRequest"),
            Some("#/components/schemas/StartupSettingsResponse"),
        ),
        (
            "/api/transitions/settings",
            "get",
            None,
            Some("#/components/schemas/TransitionSettingsResponse"),
        ),
        (
            "/api/transitions/settings",
            "put",
            Some("#/components/schemas/TransitionSettingsRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/sequencer/settings",
            "get",
            None,
            Some("#/components/schemas/SequencerSettingsResponse"),
        ),
        (
            "/api/sequencer/settings",
            "put",
            Some("#/components/schemas/SequencerSettingsRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/sequencer/sequences",
            "get",
            None,
            Some("#/components/schemas/SequenceLibraryResponse"),
        ),
        (
            "/api/sequencer/sequences",
            "post",
            Some("#/components/schemas/SequenceCreateRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/sequencer/sequences/{sequence_id}",
            "put",
            Some("#/components/schemas/SequenceDefinitionRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/sequencer/sequences/{sequence_id}",
            "delete",
            None,
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/sequencer/active",
            "put",
            Some("#/components/schemas/SequenceActiveRequest"),
            Some("#/components/schemas/DashboardState"),
        ),
        (
            "/api/auth/status",
            "get",
            None,
            Some("#/components/schemas/AccessStatus"),
        ),
        (
            "/api/auth/pair",
            "post",
            Some("#/components/schemas/PairingRequest"),
            Some("#/components/schemas/AccessStatus"),
        ),
        (
            "/api/auth/logout",
            "post",
            None,
            Some("#/components/schemas/AccessStatus"),
        ),
    ];

    let paths = spec["paths"]
        .as_object()
        .expect("OpenAPI paths should exist");
    let mut expected_subset = std::collections::BTreeMap::new();
    let mut openapi_subset = std::collections::BTreeMap::new();
    for (path, method, request_schema, response_schema) in contracts {
        let operation = paths[path][method]
            .as_object()
            .unwrap_or_else(|| panic!("Rust OpenAPI is missing {method} {path}"));
        let success_code = if matches!(
            path,
            "/api/system/update/check" | "/api/system/update/install"
        ) {
            "202"
        } else {
            "200"
        };
        let response = &operation["responses"][success_code];
        assert!(
            !response.is_null(),
            "Rust OpenAPI is missing the {success_code} response for {method} {path}"
        );
        let actual_request_schema = operation
            .get("requestBody")
            .and_then(|request| request["content"]["application/json"]["schema"]["$ref"].as_str());

        match request_schema {
            Some(expected) => {
                assert_eq!(
                    actual_request_schema,
                    Some(expected),
                    "request schema for {method} {path}"
                );
                assert!(
                    operation["responses"]["422"].is_object(),
                    "Rust OpenAPI is missing validation errors for {method} {path}"
                );
            }
            None => assert!(
                operation.get("requestBody").is_none(),
                "unexpected request body in Rust OpenAPI for {method} {path}"
            ),
        }

        if let Some(expected) = response_schema {
            let actual = response["content"]["application/json"]["schema"]["$ref"].as_str();
            assert_eq!(
                actual,
                Some(expected),
                "response schema for {method} {path}"
            );
        }
        let actual_response_schema =
            response["content"]["application/json"]["schema"]["$ref"].as_str();
        let operation_key = format!("{method} {path}");
        expected_subset.insert(
            operation_key.clone(),
            json!({
                "request_schema": request_schema,
                "response_schema": response_schema,
            }),
        );
        openapi_subset.insert(
            operation_key,
            json!({
                "request_schema": actual_request_schema,
                "response_schema": actual_response_schema,
            }),
        );
    }
    assert_eq!(
        expected_subset, openapi_subset,
        "Rust OpenAPI request and response references must match the route contract"
    );
}

async fn websocket(
    address: std::net::SocketAddr,
    path: &str,
    cookie: &str,
) -> WebSocketStream<MaybeTlsStream<TcpStream>> {
    let request = websocket_request(address, path, Some(cookie), &format!("http://{address}"));
    timeout(Duration::from_secs(3), connect_async(request))
        .await
        .expect("WebSocket handshake should complete")
        .expect("WebSocket endpoint should accept the authenticated client")
        .0
}

fn websocket_request(
    address: std::net::SocketAddr,
    path: &str,
    cookie: Option<&str>,
    origin: &str,
) -> axum::http::Request<()> {
    let mut request = format!("ws://{address}{path}")
        .into_client_request()
        .unwrap();
    if let Some(cookie) = cookie {
        request
            .headers_mut()
            .insert(header::COOKIE, cookie.parse().unwrap());
    }
    request
        .headers_mut()
        .insert(header::ORIGIN, origin.parse().unwrap());
    request
}

fn app_config(static_dir: PathBuf) -> AppConfig {
    let settings_file = static_dir.join("settings.json");
    AppConfig {
        outputs: vec![EngineOutputOptions {
            id: "main".to_owned(),
            name: "Main stripe".to_owned(),
            pixel_count: 8,
            backend: OutputBackend::Mock,
            reversed: false,
            zone_count: 1,
            zone_pattern: lumistripe_web::ZonePattern::Repeat,
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
        spotify_backend: SpotifyBackend::Disabled,
        spotify_data_dir: PathBuf::new(),
        bluetooth_max_inputs: 1,
        bluetooth_max_outputs: 1,
        secure_cookie: false,
        static_dir: Some(static_dir),
        settings_file,
    }
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn simulator_cli_starts_native_web_stack_with_python_unavailable() {
    use std::os::unix::process::CommandExt as _;

    let _guard = WEB_APPLICATION_TEST_LOCK.lock().await;
    let temporary = TempDir::new().unwrap();
    let home = temporary.path().join("home");
    let static_dir = temporary.path().join("static");
    fs::create_dir_all(&home).unwrap();
    fs::create_dir_all(&static_dir).unwrap();
    fs::write(
        static_dir.join("index.html"),
        "<!doctype html><title>simulator process fixture</title>",
    )
    .unwrap();
    let settings_file = temporary.path().join("simulator-settings.json");
    let log_path = temporary.path().join("simulator-web.log");
    let port_listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = port_listener.local_addr().unwrap().port();
    drop(port_listener);

    let output = fs::File::create(&log_path).unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_lumistripe-web"));
    command
        .args([
            "--simulate",
            "--host",
            "0.0.0.0",
            "--port",
            &port.to_string(),
            "--pixels",
            "8",
            "--pixels-2",
            "4",
        ])
        .arg("--static-dir")
        .arg(&static_dir)
        .arg("--settings-file")
        .arg(&settings_file)
        .env("PATH", "/lumistripe-test-path-without-python")
        .env("HOME", &home)
        .env("LUMI_PROJECT_DIR", temporary.path())
        .env(
            "LUMI_UPDATE_STATE_FILE",
            temporary.path().join("update-status.json"),
        )
        .env("LUMISTRIPE_ENABLE_SPOTIFY", "false")
        .env_remove("LUMI_PAIRING_CODE")
        .env_remove("LUMI_WEB_BACKEND")
        .stdin(Stdio::null())
        .stdout(Stdio::from(output.try_clone().unwrap()))
        .stderr(Stdio::from(output));
    command.process_group(0);
    let mut process = ProcessGroupGuard(command.spawn().unwrap());
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut ready = false;
    while Instant::now() < deadline {
        if let Some(status) = process.0.try_wait().unwrap() {
            panic!(
                "simulator CLI exited before serving requests ({status}):\n{}",
                fs::read_to_string(&log_path).unwrap_or_default()
            );
        }
        if matches!(process_http_get(address, "/api/health").await, Ok((200, body)) if body.contains("\"status\":\"ok\""))
        {
            ready = true;
            break;
        }
        sleep(Duration::from_millis(50)).await;
    }
    assert!(
        ready,
        "simulator CLI did not become ready:\n{}",
        fs::read_to_string(&log_path).unwrap_or_default()
    );

    let (state_status, state_body) = process_http_get(address, "/api/state")
        .await
        .expect("simulator state request should succeed");
    assert_eq!(state_status, 200, "{state_body}");
    assert!(
        state_body.contains("\"runtime\":\"simulation\""),
        "{state_body}"
    );
    assert!(
        state_body.contains("\"output_backend\":\"simulation\""),
        "{state_body}"
    );

    let (demo_status, demo_body) = process_http_request(
        address,
        "PUT",
        "/api/audio/source",
        Some(r#"{"source":"demo"}"#),
    )
    .await
    .expect("simulator demo-audio request should succeed");
    assert_eq!(demo_status, 200, "{demo_body}");
    assert!(
        demo_body.contains("\"active_source\":\"demo\""),
        "{demo_body}"
    );

    let (dynamic_status, dynamic_body) =
        process_http_request(address, "PUT", "/api/mode", Some(r#"{"mode":"dynamic"}"#))
            .await
            .expect("simulator dynamic-mode request should succeed with demo audio");
    assert_eq!(dynamic_status, 200, "{dynamic_body}");
    assert!(
        dynamic_body.contains("\"mode\":\"dynamic\""),
        "{dynamic_body}"
    );

    let (calibration_status, calibration_body) = process_http_request(
        address,
        "POST",
        "/api/audio/calibration/session",
        Some(r#"{"device":"USB microphone"}"#),
    )
    .await
    .expect("simulator microphone-calibration request should complete");
    assert_eq!(calibration_status, 409, "{calibration_body}");
    assert!(
        calibration_body.contains("microphone input is unavailable"),
        "{calibration_body}"
    );

    let (frontend_status, frontend_body) = process_http_get(address, "/")
        .await
        .expect("simulator frontend request should succeed");
    assert_eq!(frontend_status, 200, "{frontend_body}");
    assert!(frontend_body.contains("simulator process fixture"));

    unsafe {
        libc::kill(process.0.id() as libc::pid_t, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = process.0.try_wait().unwrap() {
            assert!(
                status.success(),
                "simulator CLI did not shut down cleanly:\n{}",
                fs::read_to_string(&log_path).unwrap_or_default()
            );
            break;
        }
        assert!(
            Instant::now() < deadline,
            "simulator CLI did not stop on SIGTERM"
        );
        sleep(Duration::from_millis(50)).await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn simulated_multi_output_workflow_keeps_mock_outputs_and_persists_settings() {
    if std::env::var_os("LUMISTRIPE_PYTHON_UNAVAILABLE_TEST_CHILD").is_none() {
        let output = Command::new(std::env::current_exe().expect("test executable path"))
            .args([
                "--exact",
                "simulated_multi_output_workflow_keeps_mock_outputs_and_persists_settings",
                "--nocapture",
                "--test-threads=1",
            ])
            .env("PATH", "/lumistripe-test-path-without-python")
            .env("LUMISTRIPE_PYTHON_UNAVAILABLE_TEST_CHILD", "1")
            .output()
            .expect("run simulator integration workflow without Python in PATH");
        assert!(
            output.status.success(),
            "Python-free simulator integration failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        return;
    }

    for interpreter in ["python", "python3"] {
        let error = Command::new(interpreter)
            .output()
            .expect_err("Python must be unavailable to the simulator process");
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound);
    }

    let _guard = WEB_APPLICATION_TEST_LOCK.lock().await;
    let static_dir = TempDir::new().unwrap();
    fs::write(
        static_dir.path().join("index.html"),
        "<!doctype html><title>simulator fixture</title>",
    )
    .unwrap();

    let config = app_config(static_dir.path().to_path_buf());
    let settings_file = config.settings_file.clone();
    let mut app = WebApplication::start(config.clone(), None)
        .await
        .expect("simulator should start its in-process engine with mock outputs");
    let router = app.router();
    let origin = "http://localhost";

    let startup = request(
        &router,
        Method::PUT,
        "/api/startup",
        Some(json!({"restore_last_state": true})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(startup.status, StatusCode::OK, "{}", startup.body_text);
    assert!(startup.body["restore_last_state"].as_bool().unwrap());

    let selected_animation = request(
        &router,
        Method::PUT,
        "/api/animation",
        Some(json!({"name": "rainbow"})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(selected_animation.status, StatusCode::OK);
    let demo_audio = request(
        &router,
        Method::PUT,
        "/api/audio/source",
        Some(json!({"source": "demo"})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(
        demo_audio.status,
        StatusCode::OK,
        "{}",
        demo_audio.body_text
    );
    let cycling_mode = request(
        &router,
        Method::PUT,
        "/api/mode",
        Some(json!({"mode": "cycling"})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(
        cycling_mode.status,
        StatusCode::OK,
        "{}",
        cycling_mode.body_text
    );
    assert_eq!(cycling_mode.body["mode"], "cycling");
    let static_mode = request(
        &router,
        Method::PUT,
        "/api/mode",
        Some(json!({"mode": "static"})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(
        static_mode.status,
        StatusCode::OK,
        "{}",
        static_mode.body_text
    );
    assert_eq!(static_mode.body["mode"], "static");
    let dynamic_mode = request(
        &router,
        Method::PUT,
        "/api/mode",
        Some(json!({"mode": "dynamic", "music_recognition_enabled": true})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(
        dynamic_mode.status,
        StatusCode::OK,
        "{}",
        dynamic_mode.body_text
    );
    let brightness = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": 0.4})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(
        brightness.status,
        StatusCode::OK,
        "{}",
        brightness.body_text
    );

    let topology = json!({
        "layout": "independent",
        "outputs": [
            {
                "id": "main",
                "name": "Main stripe",
                "pixels": 8,
                "backend": "spi",
                "reversed": false,
                "zone_count": 2,
                "zone_pattern": "repeat",
                "spi_device": "/dev/spidev0.0",
                "spi_speed_hz": 1000000,
                "chip": "/dev/gpiochip0",
                "data_pin": 14,
                "clock_pin": 15,
                "voltage_v": 5.0,
                "full_white_current_a": 0.06,
                "power_limit_watts": null
            },
            {
                "id": "secondary",
                "name": "Secondary stripe",
                "pixels": 12,
                "backend": "spi",
                "reversed": false,
                "zone_count": 3,
                "zone_pattern": "repeat",
                "spi_device": "/dev/spidev0.1",
                "spi_speed_hz": 1000000,
                "chip": "/dev/gpiochip0",
                "data_pin": 14,
                "clock_pin": 15,
                "voltage_v": 5.0,
                "full_white_current_a": 0.06,
                "power_limit_watts": null
            }
        ],
        "power_budget_enabled": true,
        "power_budget_watts": 15.0,
        "shared_supply_enabled": true,
        "supply_capacity_watts": 200.0,
        "pi_reserve_watts": 15.0
    });
    let topology_update = request(
        &router,
        Method::PUT,
        "/api/stripes",
        Some(topology),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(
        topology_update.status,
        StatusCode::OK,
        "{}",
        topology_update.body_text
    );

    let state = timeout(Duration::from_secs(3), async {
        loop {
            let state = request(&router, Method::GET, "/api/state", None, None, None).await;
            assert_eq!(state.status, StatusCode::OK, "{}", state.body_text);
            if state.body["stripe_topology"]["outputs"]
                .as_array()
                .is_some_and(|outputs| outputs.len() == 2)
            {
                break state;
            }
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("dashboard state should publish the committed stripe topology");
    assert_eq!(state.body["runtime"], "simulation");
    assert_eq!(state.body["output_backend"], "simulation");
    assert_eq!(state.body["mode"], "dynamic");
    assert_eq!(state.body["animation"], "rainbow");
    assert_eq!(state.body["active_audio_source"], "demo");
    assert_eq!(state.body["output_failure_count"], 0);
    assert_eq!(
        state.body["stripe_topology"]["outputs"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(state.body["stripe_topology"]["outputs"][0]["zone_count"], 2);
    assert_eq!(state.body["stripe_topology"]["outputs"][1]["zone_count"], 3);
    assert!(state.body["power_budget"]["enabled"].as_bool().unwrap());

    let server = TestServer::start(router).await;
    let preview = websocket_request(
        server.address,
        "/ws/preview",
        None,
        &format!("http://{}", server.address),
    );
    let (mut preview_socket, _) = timeout(Duration::from_secs(3), connect_async(preview))
        .await
        .expect("simulator preview should connect")
        .expect("simulator preview WebSocket should accept the client");
    let preview_message = timeout(Duration::from_secs(3), preview_socket.next())
        .await
        .expect("simulator should publish a preview frame")
        .unwrap()
        .unwrap();
    match preview_message {
        Message::Binary(packet) => {
            assert!(packet.len() > 12);
            assert_eq!(&packet[..4], b"LSFP");
            assert_eq!(packet[4], 1);
        }
        message => panic!("simulator preview sent an unexpected message: {message:?}"),
    }
    preview_socket.close(None).await.unwrap();
    server.stop().await;
    app.shutdown().await;

    assert!(
        settings_file.is_file(),
        "simulator should persist its settings"
    );
    let persisted = fs::read_to_string(&settings_file).unwrap();
    assert!(persisted.contains("secondary"));

    let mut restarted = WebApplication::start(config, None)
        .await
        .expect("simulator should restore persisted settings on restart");
    let restored = request(
        &restarted.router(),
        Method::GET,
        "/api/state",
        None,
        None,
        None,
    )
    .await;
    assert_eq!(restored.status, StatusCode::OK, "{}", restored.body_text);
    assert_eq!(restored.body["runtime"], "simulation");
    assert_eq!(restored.body["output_backend"], "simulation");
    assert_eq!(restored.body["mode"], "dynamic");
    assert_eq!(restored.body["animation"], "rainbow");
    assert_eq!(restored.body["brightness"], 0.4);
    assert_eq!(restored.body["output_failure_count"], 0);
    assert_eq!(
        restored.body["stripe_topology"]["outputs"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        restored.body["stripe_topology"]["outputs"][0]["zone_count"],
        2
    );
    assert_eq!(
        restored.body["stripe_topology"]["outputs"][1]["zone_count"],
        3
    );
    assert!(restored.body["power_budget"]["enabled"].as_bool().unwrap());
    restarted.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn serves_compatible_http_websockets_with_an_in_process_engine() {
    let _guard = WEB_APPLICATION_TEST_LOCK.lock().await;
    let static_dir = TempDir::new().unwrap();
    fs::write(
        static_dir.path().join("index.html"),
        "<!doctype html><title>fixture</title>",
    )
    .unwrap();
    fs::write(
        static_dir.path().join("settings.json"),
        r#"{"version":12,"future_feature":{"keep":true},"music_idle":{"shared":{"mode":"off","future_idle":{"keep":true}}},"transition_settings":{"enabled":true,"duration_ms":300,"effect":"crossfade","future_option":{"keep":true}}}"#,
    )
    .unwrap();
    let mut app = WebApplication::start(
        app_config(static_dir.path().to_path_buf()),
        Some("1234".to_owned()),
    )
    .await
    .expect("web application should start its mock-output engine in process");
    assert!(direct_engine_children(std::process::id()).is_empty());
    let router = app.router();
    let server = TestServer::start(router.clone()).await;
    let origin = "http://localhost";

    let health = request(&router, Method::GET, "/api/health", None, None, None).await;
    assert_eq!(health.status, StatusCode::OK);
    assert_eq!(health.body, json!({"status": "ok"}));

    let unauthorized = request(&router, Method::GET, "/api/state", None, None, None).await;
    assert_eq!(unauthorized.status, StatusCode::UNAUTHORIZED);
    let unauthorized_stats =
        request(&router, Method::GET, "/api/system/stats", None, None, None).await;
    assert_eq!(unauthorized_stats.status, StatusCode::UNAUTHORIZED);
    let unauthorized_update =
        request(&router, Method::GET, "/api/system/update", None, None, None).await;
    assert_eq!(unauthorized_update.status, StatusCode::UNAUTHORIZED);
    let unauthorized_network = request(
        &router,
        Method::GET,
        "/api/network/status",
        None,
        None,
        None,
    )
    .await;
    assert_eq!(unauthorized_network.status, StatusCode::UNAUTHORIZED);
    let unauthorized_spotify =
        request(&router, Method::GET, "/api/audio/spotify", None, None, None).await;
    assert_eq!(unauthorized_spotify.status, StatusCode::UNAUTHORIZED);
    let unauthorized_bluetooth = request(
        &router,
        Method::GET,
        "/api/audio/bluetooth",
        None,
        None,
        None,
    )
    .await;
    assert_eq!(unauthorized_bluetooth.status, StatusCode::UNAUTHORIZED);
    let unauthorized_invalid_body = request(
        &router,
        Method::PUT,
        "/api/music-idle",
        Some(json!([])),
        None,
        None,
    )
    .await;
    assert_eq!(unauthorized_invalid_body.status, StatusCode::UNAUTHORIZED);

    let paired = request(
        &router,
        Method::POST,
        "/api/auth/pair",
        Some(json!({"code": "1234"})),
        None,
        Some(&origin),
    )
    .await;
    assert_eq!(paired.status, StatusCode::OK);
    assert_eq!(
        paired.body,
        json!({"required": true, "authenticated": true})
    );
    let cookie = paired
        .headers
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned();

    // Probe every Rust-documented path without invoking its operation. A
    // mounted path rejects an unsupported method as 405; an unmounted path
    // falls through to the API 404 handler.
    let spec: Value = serde_json::from_str(include_str!("../frontend/openapi.json"))
        .expect("Rust-generated OpenAPI document should be valid JSON");
    for (path, _) in spec["paths"]
        .as_object()
        .expect("OpenAPI paths should exist")
    {
        let path = path
            .replace("{animation_name}", "rainbow_cycle")
            .replace("{session_id}", "parity-session")
            .replace("{sequence_id}", "parity-sequence");
        let response = request(&router, Method::PATCH, &path, None, Some(&cookie), None).await;
        assert_eq!(
            response.status,
            StatusCode::METHOD_NOT_ALLOWED,
            "documented route should be mounted: {path}"
        );
    }

    // Confirm the documented GET and DELETE methods, plus JSON-body methods,
    // reach their handlers. Invalid array bodies fail validation before any
    // provider or state-changing operation can run. No-body service commands
    // such as update installation and Bluetooth scanning are deliberately
    // excluded from this probe.
    for (path, operations) in spec["paths"]
        .as_object()
        .expect("OpenAPI paths should exist")
    {
        let path = path
            .replace("{animation_name}", "rainbow_cycle")
            .replace("{session_id}", "parity-session")
            .replace("{sequence_id}", "parity-sequence");
        let operations = operations
            .as_object()
            .expect("OpenAPI path operations should be objects");
        for (method, operation) in operations {
            let method = match method.as_str() {
                "get" => Some(Method::GET),
                "delete" => Some(Method::DELETE),
                "put" if operation.get("requestBody").is_some() => Some(Method::PUT),
                "post" if operation.get("requestBody").is_some() => Some(Method::POST),
                _ => None,
            };
            let Some(method) = method else {
                continue;
            };
            let method_label = method.to_string();
            let invalid_json_body = if operation.get("requestBody").is_some() {
                Some(json!([]))
            } else {
                None
            };
            let response = request(
                &router,
                method,
                &path,
                invalid_json_body,
                Some(&cookie),
                None,
            )
            .await;
            if response.status == StatusCode::METHOD_NOT_ALLOWED {
                panic!("documented method is not mounted: {method_label} {path}");
            }
            if operation.get("requestBody").is_some() {
                assert_eq!(
                    response.status,
                    StatusCode::UNPROCESSABLE_ENTITY,
                    "invalid request body should fail validation: {method_label} {path}"
                );
            }
        }
    }

    let update_status = request(
        &router,
        Method::GET,
        "/api/system/update",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(update_status.status, StatusCode::OK);
    assert_object_keys(
        &update_status.body,
        &[
            "supported",
            "installed_commit",
            "available_commit",
            "update_available",
            "release_status",
            "working_tree_dirty",
            "checked_at",
            "check_error",
            "install_state",
            "install_started_at",
            "install_finished_at",
            "install_error",
        ],
    );
    let spotify_status = request(
        &router,
        Method::GET,
        "/api/audio/spotify",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(spotify_status.status, StatusCode::OK);
    assert_object_keys(
        &spotify_status.body,
        &[
            "configured",
            "connected",
            "logged_in",
            "is_active",
            "device_name",
            "status",
            "track",
            "position_ms",
            "duration_ms",
            "volume",
            "shuffle",
            "repeat",
            "error",
        ],
    );
    let invalid_spotify_control = request(
        &router,
        Method::POST,
        "/api/audio/spotify/control",
        Some(json!({"action": "set_volume", "value": 101})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(invalid_spotify_control.status, StatusCode::CONFLICT);
    let bluetooth_status = request(
        &router,
        Method::GET,
        "/api/audio/bluetooth",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(bluetooth_status.status, StatusCode::OK);
    assert_object_keys(
        &bluetooth_status.body,
        &[
            "available",
            "powered",
            "adapter_alias",
            "scanning",
            "streaming",
            "devices",
            "connected_inputs",
            "connected_outputs",
            "connected_device",
            "input_source",
            "output_devices",
            "default_sink",
            "output_volume",
            "output_muted",
            "output_ready",
            "capabilities",
            "operation",
            "operation_id",
            "operation_state",
            "error",
        ],
    );
    let invalid_output_volume = request(
        &router,
        Method::PUT,
        "/api/audio/output/volume",
        Some(json!({"selector": "alsa_output.pci-1", "volume": 1.2})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        invalid_output_volume.status,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    let cookie_attributes = paired
        .headers
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap();
    assert!(cookie_attributes.contains("HttpOnly"));
    assert!(cookie_attributes.contains("SameSite=Strict"));
    assert!(cookie_attributes.contains("Max-Age=2592000"));

    let (mut unauthenticated, response) = timeout(
        Duration::from_secs(3),
        connect_async(websocket_request(
            server.address,
            "/ws/state",
            None,
            &format!("http://{}", server.address),
        )),
    )
    .await
    .expect("unauthenticated WebSocket should close promptly")
    .expect("unauthenticated WebSocket should complete the upgrade");
    assert_eq!(response.status(), StatusCode::SWITCHING_PROTOCOLS);
    let close = timeout(Duration::from_secs(3), unauthenticated.next())
        .await
        .expect("unauthenticated WebSocket should send a pairing close")
        .expect("WebSocket should return the server close frame")
        .expect("WebSocket should close without a transport error");
    assert!(matches!(
        close,
        Message::Close(Some(frame))
            if frame.code == CloseCode::from(4401)
                && frame.reason == "pairing required"
    ));
    let (mut invalid_origin_socket, response) = timeout(
        Duration::from_secs(3),
        connect_async(websocket_request(
            server.address,
            "/ws/state",
            Some(&cookie),
            "https://untrusted.invalid",
        )),
    )
    .await
    .expect("cross-origin WebSocket should close promptly")
    .expect("cross-origin WebSocket should complete the upgrade");
    assert_eq!(response.status(), StatusCode::SWITCHING_PROTOCOLS);
    let close = timeout(Duration::from_secs(3), invalid_origin_socket.next())
        .await
        .expect("cross-origin WebSocket should send a policy close")
        .expect("WebSocket should return the server close frame")
        .expect("WebSocket should close without a transport error");
    assert!(matches!(
        close,
        Message::Close(Some(frame))
            if frame.code == CloseCode::from(1008)
                && frame.reason == "invalid origin"
    ));

    let initial = request(
        &router,
        Method::GET,
        "/api/state",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(initial.status, StatusCode::OK);
    assert_eq!(initial.body["running"], true);
    assert!(initial.body.get("stripe_topology").is_some());
    assert_eq!(initial.body["spotify"]["connected"], false);
    assert_eq!(initial.body["bluetooth"]["capabilities"]["max_inputs"], 1);

    let system_stats = request(
        &router,
        Method::GET,
        "/api/system/stats",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(system_stats.status, StatusCode::OK);
    assert_object_keys(
        &system_stats.body,
        &[
            "cpu_temperature_c",
            "cpu_usage_percent",
            "memory_used_bytes",
            "memory_total_bytes",
            "memory_usage_percent",
            "disk_used_bytes",
            "disk_total_bytes",
            "disk_usage_percent",
            "uptime_seconds",
            "process_memory_used_bytes",
            "process_thread_count",
            "sampled_at",
        ],
    );
    assert!(
        system_stats.body["sampled_at"]
            .as_str()
            .is_some_and(|value| value.ends_with('Z'))
    );

    let rejected_network_interface = request(
        &router,
        Method::POST,
        "/api/network/scan",
        Some(json!({"interface": "wlan0;id"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(rejected_network_interface.status, StatusCode::CONFLICT);

    let secret = "private-wifi-secret";
    let rejected_wifi_credentials = request(
        &router,
        Method::POST,
        "/api/network/connect",
        Some(json!({
            "interface": "wlan0",
            "ssid": "x".repeat(33),
            "password": secret,
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        rejected_wifi_credentials.status,
        StatusCode::UNPROCESSABLE_ENTITY
    );
    assert!(!rejected_wifi_credentials.body_text.contains(secret));

    let initial_topology = request(
        &router,
        Method::GET,
        "/api/stripes",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(initial_topology.status, StatusCode::OK);
    assert_eq!(initial_topology.body["outputs"][0]["id"], "main");

    let identity = request(
        &router,
        Method::GET,
        "/api/identity",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(identity.status, StatusCode::OK);
    assert_eq!(identity.body["application_name"], "LumiStripe");
    assert_eq!(identity.body["spotify_device_name"], "LumiStripe");
    assert_eq!(identity.body["spotify_restart_requested"], false);
    let identity_updated = request(
        &router,
        Method::PUT,
        "/api/identity",
        Some(json!({
            "application_name": "Studio lights",
            "spotify_device_name": "Studio player",
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(identity_updated.status, StatusCode::OK);
    assert_eq!(identity_updated.body["application_name"], "Studio lights");
    assert_eq!(
        identity_updated.body["spotify_device_name"],
        "Studio player"
    );
    assert_eq!(identity_updated.body["spotify_restart_requested"], false);
    let identity_persisted: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(identity_persisted["application_name"], "Studio lights");
    assert_eq!(identity_persisted["spotify_device_name"], "Studio player");
    assert_eq!(identity_persisted["future_feature"]["keep"], true);

    let audio_settings = request(
        &router,
        Method::GET,
        "/api/audio/settings",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(
        audio_settings.status,
        StatusCode::OK,
        "{}",
        audio_settings.body_text
    );
    assert_object_keys(
        &audio_settings.body,
        &[
            "source",
            "active_source",
            "monitoring",
            "active_device",
            "fallback_device",
            "active_device_name",
            "input_transport",
            "input_sample_rate",
            "input_channels",
            "pcm_ingress",
            "dsp_processing_p50_ns",
            "dsp_processing_p95_ns",
            "last_feature_timestamp_ns",
            "devices",
            "settings",
            "configured_noise_floor",
            "sync_route",
            "sync_offset_ms",
            "hardware_gain_supported",
            "hardware_gain_writable",
            "hardware_gain_backend",
            "hardware_gain_control",
            "hardware_gain_value",
            "hardware_gain_error",
            "bluetooth",
            "spotify",
            "error",
        ],
    );
    assert_eq!(audio_settings.body["input_transport"], "off");
    assert_eq!(audio_settings.body["pcm_ingress"]["enabled"], false);
    assert_object_keys(
        &audio_settings.body["settings"],
        &[
            "target_level",
            "hardware_gain_target",
            "noise_floor",
            "input_noise_floor",
            "dynamic_response",
            "rms_attack",
            "rms_release",
            "band_attack",
            "band_release",
            "beat_release",
            "energy_threshold",
            "onset_threshold",
            "beat_density_threshold",
            "brightness_threshold",
            "spectral_balance_ratio",
        ],
    );
    assert_object_keys(
        &audio_settings.body["spotify"],
        &[
            "configured",
            "connected",
            "logged_in",
            "is_active",
            "device_name",
            "status",
            "track",
            "position_ms",
            "duration_ms",
            "volume",
            "shuffle",
            "repeat",
            "error",
        ],
    );
    assert_object_keys(
        &audio_settings.body["bluetooth"],
        &[
            "available",
            "powered",
            "adapter_alias",
            "scanning",
            "streaming",
            "devices",
            "connected_inputs",
            "connected_outputs",
            "connected_device",
            "input_source",
            "output_devices",
            "default_sink",
            "output_volume",
            "output_muted",
            "output_ready",
            "capabilities",
            "operation",
            "operation_id",
            "operation_state",
            "error",
        ],
    );

    let audio_profile = request(
        &router,
        Method::PUT,
        "/api/audio/settings",
        Some(json!({
            "device": "USB microphone",
            "settings": {"target_level": 0.5, "input_noise_floor": 0.02, "energy_threshold": 0.06}
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        audio_profile.status,
        StatusCode::OK,
        "{}",
        audio_profile.body_text
    );
    assert_eq!(audio_profile.body["settings"]["target_level"], 0.5);
    assert_eq!(audio_profile.body["settings"]["input_noise_floor"], 0.02);
    let settings_after_profile: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(
        settings_after_profile["selected_audio_device"],
        "USB microphone"
    );
    assert_eq!(
        settings_after_profile["audio_profiles"]["USB microphone"]["target_level"],
        0.5
    );
    assert_eq!(settings_after_profile["future_feature"]["keep"], true);

    let audio_offset = request(
        &router,
        Method::PUT,
        "/api/audio/sync-offset",
        Some(json!({"offset_ms": -23.5})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        audio_offset.status,
        StatusCode::OK,
        "{}",
        audio_offset.body_text
    );
    assert_eq!(audio_offset.body["sync_offset_ms"], -23.5);
    let settings_after_offset: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(
        settings_after_offset["audio_sync_offsets_ms"]["off|USB microphone|default"],
        -23.5
    );

    let audio_profile_reset = request(
        &router,
        Method::POST,
        "/api/audio/settings/reset",
        Some(json!({"device": "USB microphone"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(audio_profile_reset.status, StatusCode::OK);
    assert_eq!(audio_profile_reset.body["settings"]["target_level"], 0.36);
    assert_eq!(audio_profile_reset.body["sync_offset_ms"], -23.5);

    let telemetry_profile = request(
        &router,
        Method::PUT,
        "/api/audio/settings",
        Some(json!({
            "device": "USB microphone",
            "settings": {"noise_floor": 0.041, "input_noise_floor": 0.022}
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(telemetry_profile.status, StatusCode::OK);

    let sync_calibration_stopped = request(
        &router,
        Method::POST,
        "/api/audio/sync-calibration",
        Some(json!({"active": false})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(sync_calibration_stopped.status, StatusCode::OK);
    assert_eq!(sync_calibration_stopped.body["start_at_ms"], Value::Null);

    let no_microphone = request(
        &router,
        Method::POST,
        "/api/audio/calibration/session",
        Some(json!({"device": "missing microphone"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(no_microphone.status, StatusCode::CONFLICT);
    let missing_audio_calibration = request(
        &router,
        Method::GET,
        "/api/audio/calibration/session/not-a-session",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(missing_audio_calibration.status, StatusCode::NOT_FOUND);

    let audio_source_off = request(
        &router,
        Method::PUT,
        "/api/audio/source",
        Some(json!({"source": "off"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        audio_source_off.status,
        StatusCode::OK,
        "{}",
        audio_source_off.body_text
    );
    assert_eq!(audio_source_off.body["source"], "off");
    let unsupported_audio_source = request(
        &router,
        Method::PUT,
        "/api/audio/source",
        Some(json!({"source": "spotify"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(unsupported_audio_source.status, StatusCode::CONFLICT);

    let invalid_topology = request(
        &router,
        Method::PUT,
        "/api/stripes",
        Some(json!({"layout":"mirrored","outputs":[{"id":"dup","name":"A","pixels":8},{"id":"dup","name":"B","pixels":8}]})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(invalid_topology.status, StatusCode::UNPROCESSABLE_ENTITY);

    let stripe_test = request(
        &router,
        Method::POST,
        "/api/stripes/test",
        Some(json!({"stripe_id":"main","pattern":"blue"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        stripe_test.status,
        StatusCode::OK,
        "{}",
        stripe_test.body_text
    );
    assert_eq!(
        stripe_test.body["stripe_topology"]["outputs"][0]["id"],
        "main"
    );

    let calibration_started = request(
        &router,
        Method::POST,
        "/api/calibration/session",
        Some(json!({"output_index": 0})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(calibration_started.status, StatusCode::OK);
    assert_eq!(
        calibration_started.body["state"]["calibration"]["active"],
        true
    );
    let calibration_id = calibration_started.body["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    let calibration_update = request(
        &router,
        Method::PUT,
        &format!("/api/calibration/session/{calibration_id}"),
        Some(json!({"red":128,"green":200,"blue":240,"pattern":"red"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(calibration_update.status, StatusCode::OK);
    assert_eq!(calibration_update.body["calibration"]["pattern"], "red");
    let calibration_saved = request(
        &router,
        Method::POST,
        &format!("/api/calibration/session/{calibration_id}/finish"),
        Some(json!({"save":true})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(calibration_saved.status, StatusCode::OK);
    assert_eq!(calibration_saved.body["calibration"]["active"], false);
    let saved_corrections: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(saved_corrections["color_correction"]["main"]["red"], 128);

    let calibration_cancelled = request(
        &router,
        Method::POST,
        "/api/calibration/session",
        Some(json!({"output_index": 0})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    let cancelled_id = calibration_cancelled.body["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    let _ = request(
        &router,
        Method::PUT,
        &format!("/api/calibration/session/{cancelled_id}"),
        Some(json!({"red":10,"green":20,"blue":30,"pattern":"blue"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    let calibration_restored = request(
        &router,
        Method::POST,
        &format!("/api/calibration/session/{cancelled_id}/finish"),
        Some(json!({"save":false})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(calibration_restored.status, StatusCode::OK);
    let corrections_after_cancel: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(
        corrections_after_cancel["color_correction"]["main"]["red"],
        128
    );

    let startup = request(
        &router,
        Method::GET,
        "/api/startup",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(startup.status, StatusCode::OK);
    assert_eq!(startup.body["restore_last_state"], false);
    assert_eq!(startup.body["remembered"]["mode"], "static");

    let startup_updated = request(
        &router,
        Method::PUT,
        "/api/startup",
        Some(json!({"restore_last_state": true})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(startup_updated.status, StatusCode::OK);
    assert_eq!(startup_updated.body["restore_last_state"], true);
    assert_eq!(
        startup_updated.body["remembered"]["animation"],
        "rainbow_cycle"
    );

    let idle = request(
        &router,
        Method::PUT,
        "/api/music-idle",
        Some(json!({
            "mode": "animation",
            "color": "#123456",
            "animation": "rainbow_cycle",
            "brightness": 0.45
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(idle.status, StatusCode::OK);
    assert_eq!(idle.body["music_idle"]["mode"], "animation");
    assert_eq!(idle.body["music_idle"]["animation"], "rainbow_cycle");

    let initial_animation_settings = request(
        &router,
        Method::GET,
        "/api/animations/rainbow_cycle/settings",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(initial_animation_settings.status, StatusCode::OK);
    assert_eq!(initial_animation_settings.body["values"]["speed"], 50);

    let mut updated_values = initial_animation_settings.body["values"].clone();
    updated_values["speed"] = json!(70);
    let updated_animation_settings = request(
        &router,
        Method::PUT,
        "/api/animations/rainbow_cycle/settings",
        Some(json!({"values": updated_values})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        updated_animation_settings.status,
        StatusCode::OK,
        "{}",
        updated_animation_settings.body_text
    );
    assert_eq!(updated_animation_settings.body["values"]["speed"], 70);

    let reset_animation_settings = request(
        &router,
        Method::POST,
        "/api/animations/rainbow_cycle/settings/reset",
        None,
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(reset_animation_settings.status, StatusCode::OK);
    assert_eq!(reset_animation_settings.body["values"]["speed"], 50);

    let initial_transitions = request(
        &router,
        Method::GET,
        "/api/transitions/settings",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(initial_transitions.status, StatusCode::OK);
    assert_eq!(
        initial_transitions.body,
        json!({"enabled": true, "duration_ms": 300, "effect": "crossfade"})
    );

    let initial_sequencer = request(
        &router,
        Method::GET,
        "/api/sequencer/settings",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(initial_sequencer.status, StatusCode::OK);
    assert_eq!(
        initial_sequencer.body,
        json!({"switch_quantization": "off", "bars_per_phrase": 8})
    );

    let initial_library = request(
        &router,
        Method::GET,
        "/api/sequencer/sequences",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(initial_library.status, StatusCode::OK);
    assert_eq!(
        initial_library.body,
        json!({"sequences": [], "active_sequence_id": null})
    );

    let invalid_sequence = request(
        &router,
        Method::POST,
        "/api/sequencer/sequences",
        Some(json!({
            "id": "invalid",
            "name": "Invalid",
            "repeat": true,
            "steps": [{"animation_name": "rainbow_cycle", "duration_bars": 3}]
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(invalid_sequence.status, StatusCode::UNPROCESSABLE_ENTITY);

    let created_sequence = request(
        &router,
        Method::POST,
        "/api/sequencer/sequences",
        Some(json!({
            "id": "night",
            "name": "Night",
            "repeat": true,
            "steps": [{"animation_name": "rainbow_cycle", "duration_bars": 2}]
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(created_sequence.status, StatusCode::OK);
    assert_eq!(
        created_sequence.body["sequence_library"]["sequences"][0],
        json!({"id": "night", "name": "Night"})
    );

    let activated_sequence = request(
        &router,
        Method::PUT,
        "/api/sequencer/active",
        Some(json!({"sequence_id": "night"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(activated_sequence.status, StatusCode::OK);
    assert_eq!(
        activated_sequence.body["sequence_library"]["active_sequence_id"],
        "night"
    );
    let persisted_sequence: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(persisted_sequence["animation_sequences"][0]["id"], "night");
    assert_eq!(persisted_sequence["active_sequence_id"], "night");

    let updated_sequence = request(
        &router,
        Method::PUT,
        "/api/sequencer/sequences/night",
        Some(json!({
            "name": "Evening",
            "repeat": false,
            "steps": [{"animation_name": "rainbow_cycle", "duration_bars": 4}]
        })),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(updated_sequence.status, StatusCode::OK);
    assert_eq!(
        updated_sequence.body["sequence_library"]["sequences"][0]["name"],
        "Evening"
    );

    let deleted_sequence = request(
        &router,
        Method::DELETE,
        "/api/sequencer/sequences/night",
        None,
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(deleted_sequence.status, StatusCode::OK);
    assert_eq!(
        deleted_sequence.body["sequence_library"],
        json!({"sequences": [], "active_sequence_id": null})
    );
    let persisted_after_delete: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(persisted_after_delete["animation_sequences"], json!([]));
    assert_eq!(persisted_after_delete["active_sequence_id"], Value::Null);

    let sequencer = request(
        &router,
        Method::PUT,
        "/api/sequencer/settings",
        Some(json!({"switch_quantization": "phrase", "bars_per_phrase": 4})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(sequencer.status, StatusCode::OK);
    assert_eq!(
        sequencer.body["sequencer_settings"],
        json!({"switch_quantization": "phrase", "bars_per_phrase": 4})
    );

    let transitions = request(
        &router,
        Method::PUT,
        "/api/transitions/settings",
        Some(json!({"enabled": false, "duration_ms": 600, "effect": "wipe"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(transitions.status, StatusCode::OK);
    assert_eq!(
        transitions.body["transition_settings"],
        json!({"enabled": false, "duration_ms": 600, "effect": "wipe"})
    );
    let persisted: Value =
        serde_json::from_slice(&fs::read(static_dir.path().join("settings.json")).unwrap())
            .unwrap();
    assert_eq!(persisted["transition_settings"]["duration_ms"], 600);
    assert_eq!(persisted["switch_quantization"], "phrase");
    assert_eq!(persisted["bars_per_phrase"], 4);
    assert_eq!(persisted["restore_last_state"], true);
    assert_eq!(
        persisted["remembered_playback"]["animation"],
        "rainbow_cycle"
    );
    assert_eq!(persisted["music_idle"]["shared"]["mode"], "animation");
    assert_eq!(
        persisted["animation_settings"]["shared"]["rainbow_cycle"]["speed"],
        50
    );
    assert_eq!(persisted["future_feature"]["keep"], true);
    assert_eq!(
        persisted["transition_settings"]["future_option"]["keep"],
        true
    );
    assert_eq!(
        persisted["music_idle"]["shared"]["future_idle"]["keep"],
        true
    );

    let startup_disabled = request(
        &router,
        Method::PUT,
        "/api/startup",
        Some(json!({"restore_last_state": false})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(startup_disabled.status, StatusCode::OK);

    let settings_path = static_dir.path().join("settings.json");
    fs::remove_file(&settings_path).unwrap();
    fs::create_dir(&settings_path).unwrap();
    let persistence_failure = request(
        &router,
        Method::PUT,
        "/api/transitions/settings",
        Some(json!({"enabled": true, "duration_ms": 900, "effect": "fade_black"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        persistence_failure.status,
        StatusCode::INTERNAL_SERVER_ERROR
    );
    let after_rollback = request(
        &router,
        Method::GET,
        "/api/state",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(
        after_rollback.body["transition_settings"],
        json!({"enabled": false, "duration_ms": 600, "effect": "wipe"})
    );
    assert_object_keys(
        &initial.body,
        &[
            "revision",
            "runtime",
            "spotify",
            "bluetooth",
            "output_backend",
            "output_devices",
            "spi_speed_hz",
            "running",
            "mode",
            "solid_color",
            "animation",
            "brightness",
            "blackout",
            "music_active",
            "music_recognition_enabled",
            "music_idle",
            "music_gate",
            "bpm",
            "audio_status",
            "configured_audio_source",
            "active_audio_source",
            "audio_monitoring",
            "active_effects",
            "selector_diagnostics",
            "uptime_seconds",
            "frame_rate",
            "render_time_ms",
            "scheduler_time_ms",
            "worker_heartbeat_age_seconds",
            "missed_frame_count",
            "recent_frame_miss_rate",
            "output_frame_rate",
            "output_frame_latency_ms",
            "output_queue_latency_ms",
            "output_transmission_ms",
            "output_frame_latency_p50_ms",
            "output_frame_latency_p95_ms",
            "output_frame_latency_p99_ms",
            "output_ipc_latency_ms",
            "output_sequential_skew_ms",
            "output_heartbeat_age_seconds",
            "output_dropped_frame_count",
            "output_failure_count",
            "output_error",
            "command_queue_depth",
            "audio_health",
            "audio_callback_age_seconds",
            "audio_frame_age_seconds",
            "last_output_at",
            "last_output_age_seconds",
            "application_version",
            "application_name",
            "color_corrections",
            "calibration",
            "stripe_topology",
            "power_budget",
            "sequencer_settings",
            "transition_settings",
            "sequence_library",
            "stripe_playback",
            "diagnostic_issues",
            "error",
        ],
    );

    let animations = request(
        &router,
        Method::GET,
        "/api/animations",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(animations.status, StatusCode::OK);
    assert_eq!(animations.body["items"].as_array().unwrap().len(), 54);
    assert_object_keys(
        &animations.body["items"][0],
        &[
            "name",
            "display_name",
            "description",
            "role",
            "mood",
            "dynamic_safe",
            "sequence_eligible",
            "settings",
        ],
    );

    let brightness = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": "0.5", "ignored_legacy_field": true})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(brightness.status, StatusCode::OK);
    assert_eq!(brightness.body["brightness"], 0.5);

    let cycling = request(
        &router,
        Method::PUT,
        "/api/mode",
        Some(json!({"mode": "cycling"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(cycling.status, StatusCode::OK);
    assert_eq!(cycling.body["mode"], "cycling");

    let mode = request(
        &router,
        Method::PUT,
        "/api/mode",
        Some(json!({"mode": "solid", "color": "#112233"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(mode.status, StatusCode::OK);
    assert_eq!(mode.body["mode"], "solid");
    assert_eq!(mode.body["solid_color"], "#112233");

    let animation = request(
        &router,
        Method::PUT,
        "/api/animation",
        Some(json!({"name": "comet"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(animation.status, StatusCode::OK);
    assert_eq!(animation.body["animation"], "comet");

    let blackout = request(
        &router,
        Method::POST,
        "/api/blackout",
        Some(json!({"enabled": true})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(blackout.status, StatusCode::OK);
    assert_eq!(blackout.body["blackout"], true);

    let (concurrent_brightness, concurrent_blackout) = tokio::join!(
        request(
            &router,
            Method::PUT,
            "/api/brightness",
            Some(json!({"brightness": 0.75})),
            Some(&cookie),
            Some(&origin),
        ),
        request(
            &router,
            Method::POST,
            "/api/blackout",
            Some(json!({"enabled": false})),
            Some(&cookie),
            Some(&origin),
        ),
    );
    assert_eq!(concurrent_brightness.status, StatusCode::OK);
    assert_eq!(concurrent_blackout.status, StatusCode::OK);
    let concurrent_state = request(
        &router,
        Method::GET,
        "/api/state",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(concurrent_state.body["brightness"], 0.75);
    assert_eq!(concurrent_state.body["blackout"], false);

    let invalid = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": 1.5})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(invalid.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(
        invalid.body,
        json!({
            "detail": [{
                "type": "less_than_equal",
                "loc": ["body", "brightness"],
                "msg": "Input should be less than or equal to 1",
                "input": 1.5,
                "ctx": {"le": 1.0},
            }],
        })
    );

    let cross_origin = request(
        &router,
        Method::POST,
        "/api/blackout",
        Some(json!({"enabled": false})),
        Some(&cookie),
        Some("https://untrusted.invalid"),
    )
    .await;
    assert_eq!(cross_origin.status, StatusCode::FORBIDDEN);

    let root = request(&router, Method::GET, "/", None, None, None).await;
    // Static HTML is intentionally not JSON; its fallback is still served.
    assert_eq!(root.status, StatusCode::OK);
    assert!(root.body_text.contains("<title>fixture</title>"));

    let mut state_socket = websocket(server.address, "/ws/state", &cookie).await;
    let mut second_state_socket = websocket(server.address, "/ws/state", &cookie).await;
    let state_message = timeout(Duration::from_secs(3), state_socket.next())
        .await
        .expect("state stream should send its latest snapshot")
        .unwrap()
        .unwrap();
    let state_json: Value = match state_message {
        Message::Text(text) => serde_json::from_str(text.as_str()).unwrap(),
        message => panic!("state stream sent unexpected message: {message:?}"),
    };
    let second_state_message = timeout(Duration::from_secs(3), second_state_socket.next())
        .await
        .expect("second state client should receive its latest snapshot")
        .unwrap()
        .unwrap();
    let second_state_json: Value = match second_state_message {
        Message::Text(text) => serde_json::from_str(text.as_str()).unwrap(),
        message => panic!("second state stream sent unexpected message: {message:?}"),
    };
    assert_eq!(state_json, second_state_json);
    assert_eq!(state_json["running"], true);
    assert!(state_json.get("revision").is_some());
    assert_eq!(state_json["spotify"]["configured"], false);
    assert_eq!(state_json["bluetooth"]["capabilities"]["max_outputs"], 1);

    let shared_state_update = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": 0.63})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(shared_state_update.status, StatusCode::OK);
    let (state_update, second_state_update) = tokio::join!(
        timeout(Duration::from_secs(3), state_socket.next()),
        timeout(Duration::from_secs(3), second_state_socket.next()),
    );
    let state_update = state_update
        .expect("first state client should receive the shared update")
        .unwrap()
        .unwrap();
    let second_state_update = second_state_update
        .expect("second state client should receive the shared update")
        .unwrap()
        .unwrap();
    let state_update_json: Value = match state_update {
        Message::Text(text) => serde_json::from_str(text.as_str()).unwrap(),
        message => panic!("first state stream sent unexpected message: {message:?}"),
    };
    let second_state_update_json: Value = match second_state_update {
        Message::Text(text) => serde_json::from_str(text.as_str()).unwrap(),
        message => panic!("second state stream sent unexpected message: {message:?}"),
    };
    assert_eq!(state_update_json, second_state_update_json);
    assert_eq!(state_update_json["brightness"], 0.63);
    assert!(
        state_update_json["revision"].as_u64().unwrap() > state_json["revision"].as_u64().unwrap()
    );
    state_socket.close(None).await.unwrap();
    second_state_socket.close(None).await.unwrap();

    let mut audio_socket = websocket(server.address, "/ws/audio", &cookie).await;
    let audio_message = timeout(Duration::from_secs(3), audio_socket.next())
        .await
        .expect("audio stream should send telemetry")
        .unwrap()
        .unwrap();
    let audio_json: Value = match audio_message {
        Message::Text(text) => serde_json::from_str(text.as_str()).unwrap(),
        message => panic!("audio stream sent unexpected message: {message:?}"),
    };
    let current_audio_settings = request(
        &router,
        Method::GET,
        "/api/audio/settings",
        None,
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(current_audio_settings.status, StatusCode::OK);
    assert!(audio_json.get("sequence").is_some());
    assert_eq!(audio_json["bands"].as_array().unwrap().len(), 8);
    assert_eq!(audio_json["configured_noise_floor"], 0.041);
    assert_eq!(
        audio_json["sync_route"],
        current_audio_settings.body["sync_route"]
    );
    assert_eq!(
        audio_json["sync_offset_ms"],
        current_audio_settings.body["sync_offset_ms"]
    );
    assert_object_keys(
        &audio_json,
        &[
            "sequence",
            "fresh",
            "input_level",
            "input_peak",
            "clipping_fraction",
            "microphone_gate_open",
            "configured_input_noise_floor",
            "processed_level",
            "bands",
            "beat",
            "beat_strength",
            "bpm",
            "bpm_confidence",
            "beat_phase",
            "beat_index",
            "next_beat_in_ms",
            "beat_event_sequence",
            "dropped_beat_events",
            "pending_beat_events",
            "beat_scheduler_late_ticks_dropped",
            "beat_scheduler_late_ticks_coalesced",
            "beat_scheduler_lateness_p95_ms",
            "calibration_output_callback_count",
            "calibration_output_sample_rate",
            "calibration_output_block_duration_ms",
            "calibration_output_callback_p50_ms",
            "calibration_output_callback_p95_ms",
            "calibration_output_callback_p99_ms",
            "calibration_output_interval_jitter_p95_ms",
            "calibration_output_underflow_count",
            "calibration_output_overflow_count",
            "calibration_output_callback_error_count",
            "sync_offset_ms",
            "sync_route",
            "input_transport",
            "input_sample_rate",
            "input_channels",
            "pcm_ingress",
            "dsp_processing_p50_ns",
            "dsp_processing_p95_ns",
            "last_feature_timestamp_ns",
            "estimated_noise_floor",
            "configured_noise_floor",
            "normalization_gain",
            "hardware_gain_value",
            "program_loudness",
            "musical_impact",
            "gate",
            "gate_preview",
            "gate_energy",
            "gate_onset",
            "gate_beat_density",
            "gate_brightness",
            "gate_spectral_balance",
            "gate_reason",
            "gate_checks",
            "music_structure_phase",
            "music_structure_confidence",
            "music_structure_buildup_progress",
            "music_structure_drop_count",
            "music_structure_active",
            "music_structure_fresh",
            "drop_accent_status",
            "drop_accent_reason",
            "health",
        ],
    );
    audio_socket.close(None).await.unwrap();

    let mut preview_socket = websocket(server.address, "/ws/preview", &cookie).await;
    let preview_message = timeout(Duration::from_secs(3), preview_socket.next())
        .await
        .expect("preview stream should send a rendered binary frame")
        .unwrap()
        .unwrap();
    let first_preview_sequence = match preview_message {
        Message::Binary(packet) => {
            assert!(packet.len() > 12);
            assert_eq!(&packet[..4], b"LSFP");
            assert_eq!(packet[4], 1);
            u32::from_le_bytes(packet[6..10].try_into().unwrap())
        }
        message => panic!("preview stream sent unexpected message: {message:?}"),
    };
    let next_preview_message = timeout(Duration::from_secs(1), preview_socket.next())
        .await
        .expect("preview stream should continue sending newly rendered frames")
        .unwrap()
        .unwrap();
    match next_preview_message {
        Message::Binary(packet) => {
            let sequence = u32::from_le_bytes(packet[6..10].try_into().unwrap());
            assert_ne!(sequence, first_preview_sequence);
        }
        message => panic!("preview stream sent unexpected message: {message:?}"),
    }
    preview_socket.close(None).await.unwrap();

    let demo_audio = request(
        &router,
        Method::PUT,
        "/api/audio/source",
        Some(json!({"source": "demo"})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(
        demo_audio.status,
        StatusCode::OK,
        "{}",
        demo_audio.body_text
    );
    assert_eq!(demo_audio.body["source"], "demo");

    let restored_audio = request(
        &router,
        Method::GET,
        "/api/state",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(restored_audio.status, StatusCode::OK);
    assert_eq!(restored_audio.body["active_audio_source"], "demo");
    assert_eq!(
        restored_audio.body["audio_status"],
        "Using the internal demo beat."
    );
    let after_direct_update = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": 0.25})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(after_direct_update.status, StatusCode::OK);
    assert_eq!(after_direct_update.body["brightness"], 0.25);

    let logged_out = request(
        &router,
        Method::POST,
        "/api/auth/logout",
        None,
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(logged_out.status, StatusCode::OK);
    let revoked = request(
        &router,
        Method::GET,
        "/api/state",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(revoked.status, StatusCode::UNAUTHORIZED);

    server.stop().await;
    app.shutdown().await;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    while tokio::time::Instant::now() < deadline
        && !direct_engine_children(std::process::id()).is_empty()
    {
        sleep(Duration::from_millis(20)).await;
    }
    assert!(direct_engine_children(std::process::id()).is_empty());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spotify_playback_and_application_shutdown_release_engine_resources() {
    let _guard = WEB_APPLICATION_TEST_LOCK.lock().await;
    let static_dir = TempDir::new().unwrap();

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let spotify_uri = format!("ws://{}", listener.local_addr().unwrap());
    let (spotify_closed_sender, spotify_closed_receiver) = oneshot::channel();
    let soloist_fixture = tokio::spawn(async move {
        let (stream, _) = timeout(Duration::from_secs(5), listener.accept())
            .await
            .expect("Rust backend should connect to the local Soloist fixture")
            .unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        socket
            .send(Message::Text(
                json!({
                    "type": "auth_state",
                    "logged_in": true,
                    "is_active": true,
                    "device_name": "LumiStripe"
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
        socket
            .send(Message::Text(
                json!({
                    "type": "playback_state",
                    "status": "playing",
                    "position": {"position_ms": 1200, "speed": 1},
                    "item": {
                        "uri": "spotify:track:engine-restart-fixture",
                        "decorations": {"identity": {"name": "Restart fixture"}}
                    }
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
        while let Some(Ok(message)) = socket.next().await {
            if matches!(message, Message::Close(_)) {
                break;
            }
        }
        let _ = spotify_closed_sender.send(());
    });

    let mut config = app_config(static_dir.path().to_path_buf());
    config.spotify_ws_uri = spotify_uri;
    config.spotify_backend = SpotifyBackend::Soloist;
    let mut app = WebApplication::start(config, Some("1234".to_owned()))
        .await
        .expect("web application should start with the injected Soloist endpoint");
    let router = app.router();
    let origin = "http://localhost";
    let paired = request(
        &router,
        Method::POST,
        "/api/auth/pair",
        Some(json!({"code": "1234"})),
        None,
        Some(origin),
    )
    .await;
    assert_eq!(paired.status, StatusCode::OK, "{}", paired.body_text);
    let cookie = paired
        .headers
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_owned();

    let spotify_deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    let initial_spotify = loop {
        let response = request(
            &router,
            Method::GET,
            "/api/audio/spotify",
            None,
            Some(&cookie),
            None,
        )
        .await;
        if response.status == StatusCode::OK
            && response.body["connected"] == true
            && response.body["logged_in"] == true
            && response.body["status"] == "playing"
        {
            break response;
        }
        assert!(
            tokio::time::Instant::now() < spotify_deadline,
            "Soloist fixture did not publish active playback: {}",
            response.body_text
        );
        sleep(Duration::from_millis(10)).await;
    };
    assert_eq!(initial_spotify.body["track"]["name"], "Restart fixture");

    let brightness = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": 0.35})),
        Some(&cookie),
        Some(origin),
    )
    .await;
    assert_eq!(brightness.status, StatusCode::OK);

    let after_update = request(
        &router,
        Method::GET,
        "/api/state",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(after_update.status, StatusCode::OK);
    assert_eq!(after_update.body["brightness"], 0.35);
    assert_eq!(after_update.body["spotify"]["status"], "playing");
    assert_eq!(
        after_update.body["spotify"]["track"]["name"],
        "Restart fixture"
    );

    app.shutdown().await;
    timeout(Duration::from_secs(3), spotify_closed_receiver)
        .await
        .expect("application shutdown should close the Soloist connection")
        .expect("Soloist fixture should observe the close frame");
    timeout(Duration::from_secs(3), soloist_fixture)
        .await
        .expect("Soloist fixture task should stop")
        .expect("Soloist fixture task should not panic");
    let spotify_after_shutdown = request(
        &router,
        Method::GET,
        "/api/audio/spotify",
        None,
        Some(&cookie),
        None,
    )
    .await;
    assert_eq!(spotify_after_shutdown.status, StatusCode::OK);
    assert_eq!(spotify_after_shutdown.body["connected"], false);
    assert!(direct_engine_children(std::process::id()).is_empty());
}

fn direct_engine_children(owner_pid: u32) -> Vec<i32> {
    let Ok(entries) = fs::read_dir("/proc") else {
        return Vec::new();
    };
    entries
        .flatten()
        .filter_map(|entry| entry.file_name().to_str()?.parse::<i32>().ok())
        .filter_map(|pid| {
            let process_dir = PathBuf::from("/proc").join(pid.to_string());
            let status = fs::read_to_string(process_dir.join("status")).ok()?;
            let parent = status
                .lines()
                .find_map(|line| line.strip_prefix("PPid:"))?
                .trim()
                .parse::<u32>()
                .ok()?;
            if parent != owner_pid {
                return None;
            }
            let command = fs::read(process_dir.join("cmdline")).ok()?;
            let command = String::from_utf8_lossy(&command);
            (command.contains("lumistripe-engine") && command.contains("--owner-pid"))
                .then_some(pid)
        })
        .collect()
}
