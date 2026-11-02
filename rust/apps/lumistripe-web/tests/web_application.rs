use axum::Router;
use axum::body::Body;
use axum::http::{Method, Request, StatusCode, header};
use futures_util::StreamExt;
use http_body_util::BodyExt;
use lumistripe_engine_config::OutputBackend;
use lumistripe_web::{AppConfig, EngineOutputOptions, WebApplication};
use serde_json::{Value, json};
use std::fs;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tempfile::TempDir;
use tokio::net::TcpListener;
use tokio::net::TcpStream;
use tokio::task::JoinHandle;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async};
use tokio_util::sync::CancellationToken;

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

fn assert_object_keys(value: &Value, expected: &[&str]) {
    let object = value.as_object().expect("response should be a JSON object");
    let mut actual = object.keys().map(String::as_str).collect::<Vec<_>>();
    let mut expected = expected.to_vec();
    actual.sort_unstable();
    expected.sort_unstable();
    assert_eq!(actual, expected);
}

#[test]
fn migrated_http_contract_matches_fastapi_openapi_subset() {
    let spec: Value = serde_json::from_str(include_str!(
        "../../../../apps/lumistripe-web/frontend/openapi.json"
    ))
    .expect("FastAPI OpenAPI document should be valid JSON");
    let contracts = [
        ("/api/health", "get", None, None),
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
            "/api/blackout",
            "post",
            Some("#/components/schemas/BlackoutRequest"),
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
    for (path, method, request_schema, response_schema) in contracts {
        let operation = paths[path][method]
            .as_object()
            .unwrap_or_else(|| panic!("FastAPI OpenAPI is missing {method} {path}"));
        let response = &operation["responses"]["200"];
        assert!(
            !response.is_null(),
            "FastAPI OpenAPI is missing the success response for {method} {path}"
        );

        match request_schema {
            Some(expected) => {
                let actual =
                    operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
                        .as_str();
                assert_eq!(actual, Some(expected), "request schema for {method} {path}");
                assert!(
                    operation["responses"]["422"].is_object(),
                    "FastAPI OpenAPI is missing validation errors for {method} {path}"
                );
            }
            None => assert!(
                operation.get("requestBody").is_none(),
                "unexpected request body in FastAPI OpenAPI for {method} {path}"
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
    }
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
    AppConfig {
        outputs: vec![EngineOutputOptions {
            id: "main".to_owned(),
            name: "Main stripe".to_owned(),
            pixel_count: 8,
            backend: OutputBackend::Mock,
        }],
        runtime_kind: "simulation",
        output_backend: "simulation",
        output_devices: vec!["simulation".to_owned()],
        spi_speed_hz: None,
        audio_device: None,
        secure_cookie: false,
        static_dir: Some(static_dir),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn serves_compatible_http_websockets_and_supervises_the_engine_process() {
    let static_dir = TempDir::new().unwrap();
    fs::write(
        static_dir.path().join("index.html"),
        "<!doctype html><title>fixture</title>",
    )
    .unwrap();
    let engine_binary = std::env::var_os("LUMISTRIPE_ENGINE_TEST_BINARY")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/debug/lumistripe-engine")
        });
    assert!(
        engine_binary.is_file(),
        "build the engine binary before this integration test: {}",
        engine_binary.display()
    );
    let mut app = WebApplication::start(
        app_config(static_dir.path().to_path_buf()),
        engine_binary,
        Some("1234".to_owned()),
    )
    .await
    .expect("web application should start the real mock-output engine");
    let router = app.router();
    let server = TestServer::start(router.clone()).await;
    let origin = "http://localhost";

    let health = request(&router, Method::GET, "/api/health", None, None, None).await;
    assert_eq!(health.status, StatusCode::OK);
    assert_eq!(health.body, json!({"status": "ok"}));

    let unauthorized = request(&router, Method::GET, "/api/state", None, None, None).await;
    assert_eq!(unauthorized.status, StatusCode::UNAUTHORIZED);

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
    let cookie_attributes = paired
        .headers
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap();
    assert!(cookie_attributes.contains("HttpOnly"));
    assert!(cookie_attributes.contains("SameSite=Strict"));
    assert!(cookie_attributes.contains("Max-Age=2592000"));

    let rejected = timeout(
        Duration::from_secs(3),
        connect_async(websocket_request(
            server.address,
            "/ws/state",
            None,
            &format!("http://{}", server.address),
        )),
    )
    .await
    .expect("unauthenticated WebSocket should reject promptly");
    assert!(matches!(
        rejected,
        Err(tokio_tungstenite::tungstenite::Error::Http(response))
            if response.status() == StatusCode::FORBIDDEN
    ));
    let rejected_origin = timeout(
        Duration::from_secs(3),
        connect_async(websocket_request(
            server.address,
            "/ws/state",
            Some(&cookie),
            "https://untrusted.invalid",
        )),
    )
    .await
    .expect("cross-origin WebSocket should reject promptly");
    assert!(matches!(
        rejected_origin,
        Err(tokio_tungstenite::tungstenite::Error::Http(response))
            if response.status() == StatusCode::FORBIDDEN
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
    assert_object_keys(
        &initial.body,
        &[
            "revision",
            "runtime",
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
    let state_message = timeout(Duration::from_secs(3), state_socket.next())
        .await
        .expect("state stream should send its latest snapshot")
        .unwrap()
        .unwrap();
    let state_json: Value = match state_message {
        Message::Text(text) => serde_json::from_str(text.as_str()).unwrap(),
        message => panic!("state stream sent unexpected message: {message:?}"),
    };
    assert_eq!(state_json["running"], true);
    assert!(state_json.get("revision").is_some());
    state_socket.close(None).await.unwrap();

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
    assert!(audio_json.get("sequence").is_some());
    assert_eq!(audio_json["bands"].as_array().unwrap().len(), 8);
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

    // Kill only the direct engine child owned by this test. The supervisor must
    // restart it, reapply the scene, and restore the ready health state.
    let child_pids = direct_engine_children(std::process::id());
    assert_eq!(
        child_pids.len(),
        1,
        "one engine child should own the mock output"
    );
    let restart_started = Instant::now();
    assert_eq!(unsafe { libc::kill(child_pids[0], libc::SIGKILL) }, 0);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(12);
    let mut recovered = false;
    while tokio::time::Instant::now() < deadline {
        let status = request(&router, Method::GET, "/api/health", None, None, None).await;
        if status.status == StatusCode::OK {
            let children = direct_engine_children(std::process::id());
            if children.len() == 1 && children[0] != child_pids[0] {
                recovered = true;
                break;
            }
        }
        sleep(Duration::from_millis(100)).await;
    }
    assert!(
        recovered,
        "engine supervisor should restart and reconfigure the child"
    );
    eprintln!(
        "mock engine crash recovery and scene reapply: {:.1} ms",
        restart_started.elapsed().as_secs_f64() * 1000.0
    );
    let after_restart = request(
        &router,
        Method::PUT,
        "/api/brightness",
        Some(json!({"brightness": 0.25})),
        Some(&cookie),
        Some(&origin),
    )
    .await;
    assert_eq!(after_restart.status, StatusCode::OK);
    assert_eq!(after_restart.body["brightness"], 0.25);

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
