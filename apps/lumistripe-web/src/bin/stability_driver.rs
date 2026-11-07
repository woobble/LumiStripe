use clap::{Parser, Subcommand};
use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::net::{TcpListener, TcpStream};
use tokio::task::JoinSet;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::header::{COOKIE, ORIGIN};
use tokio_tungstenite::{accept_async, connect_async};

#[derive(Debug, Parser)]
#[command(
    name = "lumistripe-stability-driver",
    about = "CI-only WebSocket fixtures for the Bash stability profile"
)]
struct Arguments {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Serve the loopback Soloist fixture until SIGTERM or SIGINT.
    MockSpotify {
        #[arg(long)]
        port: u16,
        #[arg(long)]
        ready_file: PathBuf,
        #[arg(long)]
        stats_file: PathBuf,
    },
    /// Read the authenticated JSON audio telemetry WebSocket for a bounded duration.
    AudioTelemetry {
        #[arg(long)]
        address: SocketAddr,
        #[arg(long)]
        cookie: String,
        #[arg(long, default_value_t = 180)]
        duration_seconds: u64,
        #[arg(long)]
        output: PathBuf,
    },
}

#[derive(Clone, Default)]
struct Counters {
    connections: Arc<AtomicU64>,
    transitions: Arc<AtomicU64>,
    interruptions: Arc<AtomicU64>,
}

#[derive(Serialize)]
struct MockStats {
    connections: u64,
    transitions: u64,
    interruptions: u64,
}

#[derive(Serialize)]
struct AudioStats {
    messages: u64,
    max_dropped_samples: u64,
}

#[tokio::main]
async fn main() -> ExitCode {
    let arguments = Arguments::parse();
    let result = match arguments.command {
        Command::MockSpotify {
            port,
            ready_file,
            stats_file,
        } => run_spotify_mock(port, &ready_file, &stats_file).await,
        Command::AudioTelemetry {
            address,
            cookie,
            duration_seconds,
            output,
        } => run_audio_telemetry(address, &cookie, duration_seconds, &output).await,
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("stability protocol driver failed: {error}");
            ExitCode::FAILURE
        }
    }
}

async fn run_spotify_mock(port: u16, ready_file: &Path, stats_file: &Path) -> Result<(), String> {
    let listener = TcpListener::bind(("127.0.0.1", port))
        .await
        .map_err(|error| format!("could not bind loopback Soloist fixture: {error}"))?;
    let address = listener
        .local_addr()
        .map_err(|error| format!("could not read Soloist fixture address: {error}"))?;
    write_json_atomic(ready_file, &json!({"uri": format!("ws://{address}")}))?;

    let counters = Counters::default();
    let mut clients = JoinSet::new();
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .map_err(|error| format!("could not install SIGTERM handler: {error}"))?;
    let mut interrupt = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt())
        .map_err(|error| format!("could not install SIGINT handler: {error}"))?;

    loop {
        tokio::select! {
            _ = terminate.recv() => break,
            _ = interrupt.recv() => break,
            accepted = listener.accept() => {
                let (stream, _) = accepted.map_err(|error| format!("Soloist fixture accept failed: {error}"))?;
                let client_counters = counters.clone();
                clients.spawn(async move {
                    serve_spotify_client(stream, client_counters).await;
                });
            }
        }
        while clients.try_join_next().is_some() {}
    }

    clients.abort_all();
    while clients.join_next().await.is_some() {}
    let stats = MockStats {
        connections: counters.connections.load(Ordering::Relaxed),
        transitions: counters.transitions.load(Ordering::Relaxed),
        interruptions: counters.interruptions.load(Ordering::Relaxed),
    };
    write_json_atomic(stats_file, &stats)
}

async fn serve_spotify_client(stream: TcpStream, counters: Counters) {
    let Ok(mut socket) = accept_async(stream).await else {
        return;
    };
    let connection_number = counters.connections.fetch_add(1, Ordering::Relaxed) + 1;
    for cycle in 0..8 {
        let status = if cycle % 2 == 0 { "playing" } else { "paused" };
        let events = [
            json!({
                "type": "auth_state",
                "logged_in": cycle % 5 != 4,
                "is_active": true,
                "device_name": "LumiStripe stability fixture"
            }),
            json!({
                "type": "playback_state",
                "status": status,
                "position": {
                    "position_ms": cycle * 500,
                    "speed": if status == "playing" { 1 } else { 0 }
                },
                "item": {
                    "uri": "spotify:track:software-stability-fixture",
                    "decorations": {"identity": {"name": "Software fixture"}}
                }
            }),
        ];
        for event in events {
            let Ok(payload) = serde_json::to_string(&event) else {
                return;
            };
            if socket.send(Message::Text(payload.into())).await.is_err() {
                return;
            }
            counters.transitions.fetch_add(1, Ordering::Relaxed);
        }
        if cycle == 3 && connection_number % 2 == 0 {
            counters.interruptions.fetch_add(1, Ordering::Relaxed);
            return;
        }
        sleep(Duration::from_millis(350)).await;
    }
}

async fn run_audio_telemetry(
    address: SocketAddr,
    cookie: &str,
    duration_seconds: u64,
    output_path: &Path,
) -> Result<(), String> {
    if !(1..=1800).contains(&duration_seconds) {
        return Err("duration must be between 1 and 1800 seconds".to_owned());
    }
    if cookie.is_empty()
        || cookie
            .chars()
            .any(|character| matches!(character, '\r' | '\n'))
    {
        return Err("session cookie is empty or malformed".to_owned());
    }
    let url = format!("ws://{address}/ws/audio");
    let mut request = url
        .into_client_request()
        .map_err(|error| format!("could not construct audio WebSocket request: {error}"))?;
    request.headers_mut().insert(
        COOKIE,
        cookie
            .parse()
            .map_err(|error| format!("invalid session cookie header: {error}"))?,
    );
    request.headers_mut().insert(
        ORIGIN,
        format!("http://{address}")
            .parse()
            .map_err(|error| format!("invalid WebSocket origin header: {error}"))?,
    );
    let (mut socket, _) = timeout(Duration::from_secs(5), connect_async(request))
        .await
        .map_err(|_| "audio telemetry WebSocket connection timed out".to_owned())?
        .map_err(|error| format!("audio telemetry WebSocket connection failed: {error}"))?;

    let deadline = tokio::time::Instant::now() + Duration::from_secs(duration_seconds);
    let mut messages = 0_u64;
    let mut max_dropped_samples = 0_u64;
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            incoming = socket.next() => {
                let Some(incoming) = incoming else {
                    return Err("audio telemetry WebSocket closed before the profile ended".to_owned());
                };
                match incoming.map_err(|error| format!("audio telemetry WebSocket failed: {error}"))? {
                    Message::Text(payload) => {
                        let telemetry: Value = serde_json::from_str(payload.as_str())
                            .map_err(|error| format!("audio telemetry returned malformed JSON: {error}"))?;
                        messages += 1;
                        max_dropped_samples = max_dropped_samples.max(
                            telemetry
                                .pointer("/pcm_ingress/dropped_samples")
                                .and_then(Value::as_u64)
                                .unwrap_or(0),
                        );
                    }
                    Message::Ping(payload) => {
                        socket.send(Message::Pong(payload)).await
                            .map_err(|error| format!("could not answer audio WebSocket ping: {error}"))?;
                    }
                    Message::Close(_) => break,
                    Message::Binary(_) | Message::Pong(_) | Message::Frame(_) => {}
                }
            }
        }
    }
    let _ = timeout(Duration::from_secs(2), socket.close(None)).await;
    write_json_atomic(
        output_path,
        &AudioStats {
            messages,
            max_dropped_samples,
        },
    )
}

fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)
        .map_err(|error| format!("could not create output directory: {error}"))?;
    let temporary = path.with_extension(format!("tmp.{}", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("could not create temporary output: {error}"))?;
    serde_json::to_writer(&mut file, value)
        .map_err(|error| format!("could not encode output JSON: {error}"))?;
    file.write_all(b"\n")
        .and_then(|()| file.sync_all())
        .map_err(|error| format!("could not sync output JSON: {error}"))?;
    drop(file);
    fs::rename(&temporary, path).map_err(|error| format!("could not replace output JSON: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{Counters, serve_spotify_client};
    use futures_util::StreamExt;
    use serde_json::Value;
    use std::sync::atomic::Ordering;
    use std::time::Duration;
    use tokio::net::TcpListener;
    use tokio::time::timeout;
    use tokio_tungstenite::connect_async;
    use tokio_tungstenite::tungstenite::Message;

    #[tokio::test]
    async fn spotify_fixture_covers_login_playback_and_reconnect_transitions() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let counters = Counters::default();
        let server_counters = counters.clone();
        let server = tokio::spawn(async move {
            for _ in 0..2 {
                let (stream, _) = listener.accept().await.unwrap();
                serve_spotify_client(stream, server_counters.clone()).await;
            }
        });

        let (mut first, _) = connect_async(format!("ws://{address}")).await.unwrap();
        let mut login_states = Vec::new();
        let mut playback_states = Vec::new();
        for _ in 0..10 {
            let message = timeout(Duration::from_secs(3), first.next())
                .await
                .unwrap()
                .unwrap()
                .unwrap();
            let Message::Text(payload) = message else {
                panic!("Soloist fixture should emit JSON text events");
            };
            let event: Value = serde_json::from_str(payload.as_str()).unwrap();
            match event["type"].as_str().unwrap() {
                "auth_state" => login_states.push(event["logged_in"].as_bool().unwrap()),
                "playback_state" => {
                    playback_states.push(event["status"].as_str().unwrap().to_owned())
                }
                other => panic!("unexpected Soloist fixture event: {other}"),
            }
        }
        assert!(login_states.contains(&true));
        assert!(login_states.contains(&false));
        assert!(playback_states.iter().any(|status| status == "playing"));
        assert!(playback_states.iter().any(|status| status == "paused"));
        drop(first);

        let (mut second, _) = connect_async(format!("ws://{address}")).await.unwrap();
        let mut reconnect_events = 0;
        while reconnect_events < 8 {
            let message = timeout(Duration::from_secs(3), second.next())
                .await
                .unwrap()
                .unwrap()
                .unwrap();
            assert!(matches!(message, Message::Text(_)));
            reconnect_events += 1;
        }
        drop(second);
        timeout(Duration::from_secs(3), server)
            .await
            .unwrap()
            .unwrap();

        assert_eq!(counters.connections.load(Ordering::Relaxed), 2);
        assert!(counters.transitions.load(Ordering::Relaxed) >= 18);
        assert_eq!(counters.interruptions.load(Ordering::Relaxed), 1);
    }
}
