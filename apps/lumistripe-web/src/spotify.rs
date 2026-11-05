//! Provider boundary and Tokio client for the local Spotify Soloist service.

use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::future::Future;
use std::pin::Pin;
use std::time::Duration;
use thiserror::Error;
use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::task::JoinHandle;
use tokio::time::{Instant, sleep, timeout};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async};
use tokio_util::sync::CancellationToken;
use url::Url;

const COMMAND_TIMEOUT: Duration = Duration::from_secs(3);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_PENDING_COMMANDS: usize = 64;
const POSITION_TICK: Duration = Duration::from_millis(250);
const MAX_POSITION_MS: u64 = 9_007_199_254_740_991;
const MAX_EVENT_BYTES: usize = 1024 * 1024;

type SoloistSocket = WebSocketStream<MaybeTlsStream<TcpStream>>;
pub(crate) type ProviderFuture<'a> =
    Pin<Box<dyn Future<Output = Result<SpotifyStatus, SpotifyError>> + Send + 'a>>;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SpotifyPlaybackStatus {
    Idle,
    Playing,
    Paused,
    Buffering,
}

impl Default for SpotifyPlaybackStatus {
    fn default() -> Self {
        Self::Idle
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SpotifyRepeat {
    Off,
    Context,
    Track,
}

impl Default for SpotifyRepeat {
    fn default() -> Self {
        Self::Off
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[schema(as = SpotifyTrackInfo)]
pub(crate) struct SpotifyTrack {
    #[schema(required = false, default = "")]
    pub uri: String,
    #[schema(required = false, default = "")]
    pub name: String,
    #[schema(required = false, default = json!([]))]
    pub artists: Vec<String>,
    pub album: Option<String>,
    pub cover_url: Option<String>,
    pub duration_ms: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[schema(as = SpotifyStatusResponse)]
pub(crate) struct SpotifyStatus {
    #[schema(required = false, default = false)]
    pub configured: bool,
    #[schema(required = false, default = false)]
    pub connected: bool,
    #[schema(required = false, default = false)]
    pub logged_in: bool,
    #[schema(required = false, default = false)]
    pub is_active: bool,
    pub device_name: Option<String>,
    #[schema(required = false, schema_with = crate::api_schema::spotify_playback_status_schema)]
    pub status: SpotifyPlaybackStatus,
    pub track: Option<SpotifyTrack>,
    #[schema(required = false, minimum = 0, default = 0)]
    pub position_ms: u64,
    pub duration_ms: Option<u64>,
    #[schema(required = false, minimum = 0, maximum = 100, default = 0)]
    pub volume: u8,
    #[schema(required = false, default = false)]
    pub shuffle: bool,
    #[schema(required = false, schema_with = crate::api_schema::spotify_repeat_schema)]
    pub repeat: SpotifyRepeat,
    pub error: Option<String>,
}

impl SpotifyStatus {
    fn initial(configured: bool) -> Self {
        Self {
            configured,
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
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SpotifyControlAction {
    Play,
    Pause,
    SkipNext,
    SkipPrev,
    Seek,
    SetVolume,
    SetShuffle,
    SetRepeat,
}

#[derive(Clone, Debug, Error)]
#[error("{message}")]
pub(crate) struct SpotifyError {
    pub code: &'static str,
    pub message: String,
}

impl SpotifyError {
    pub(crate) fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn disconnected() -> Self {
        Self::new("disconnected", "Spotify Soloist is not connected.")
    }

    fn command_timeout(ambiguous: bool) -> Self {
        if ambiguous {
            Self::new(
                "timeout",
                "Spotify command timed out; delivery may still complete.",
            )
        } else {
            Self::new("timeout", "Spotify command timed out before delivery.")
        }
    }
}

/// Provider seam used by the web application; future providers implement the
/// same lifecycle, snapshot, and typed control contract.
pub(crate) trait SpotifyProvider: Send + Sync {
    fn start(&self);
    fn stop(&self);
    fn status(&self) -> SpotifyStatus;
    fn control<'a>(
        &'a self,
        action: SpotifyControlAction,
        value: Option<Value>,
    ) -> ProviderFuture<'a>;
}

#[derive(Clone)]
pub(crate) struct SoloistBackend {
    commands: mpsc::Sender<SoloistCommand>,
    status: watch::Receiver<SpotifyStatus>,
    start: watch::Sender<bool>,
    cancellation: CancellationToken,
}

struct SoloistCommand {
    payload: Value,
    deadline: Instant,
    reply: Option<oneshot::Sender<Result<(), SpotifyError>>>,
}

struct PendingCommandAck {
    command: String,
    deadline: Instant,
    reply: oneshot::Sender<Result<(), SpotifyError>>,
}

impl SoloistBackend {
    pub(crate) fn new(
        uri: impl AsRef<str>,
        configured: bool,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        let provider_cancellation = cancellation.child_token();
        let normalized_uri = normalize_soloist_uri(uri.as_ref());
        let uri = normalized_uri.as_ref().ok().cloned().filter(|_| configured);
        let mut initial = SpotifyStatus::initial(configured && uri.is_some());
        if let Err(error) = normalized_uri {
            initial.error = Some(error.message);
        }
        let (command_sender, command_receiver) = mpsc::channel(MAX_PENDING_COMMANDS);
        let (status_sender, status_receiver) = watch::channel(initial);
        let (start_sender, start_receiver) = watch::channel(false);
        let task = tokio::spawn(supervise_soloist(
            uri.clone(),
            command_receiver,
            command_sender.clone(),
            status_sender,
            start_receiver,
            provider_cancellation.clone(),
        ));
        (
            Self {
                commands: command_sender,
                status: status_receiver,
                start: start_sender,
                cancellation: provider_cancellation,
            },
            task,
        )
    }

    async fn send_payload(&self, payload: Value) -> Result<(), SpotifyError> {
        if !self.status().configured {
            return Err(SpotifyError::new(
                "unavailable",
                "Spotify is not configured for this installation.",
            ));
        }
        if !self.status().connected {
            return Err(SpotifyError::disconnected());
        }
        let (reply, receiver) = oneshot::channel();
        let command = SoloistCommand {
            payload,
            deadline: Instant::now() + COMMAND_TIMEOUT,
            reply: Some(reply),
        };
        self.commands
            .try_send(command)
            .map_err(|error| match error {
                mpsc::error::TrySendError::Full(_) => {
                    SpotifyError::new("busy", "Spotify command queue is full.")
                }
                mpsc::error::TrySendError::Closed(_) => SpotifyError::disconnected(),
            })?;
        match timeout(COMMAND_TIMEOUT, receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(SpotifyError::disconnected()),
            Err(_) => Err(SpotifyError::command_timeout(true)),
        }
    }
}

impl SpotifyProvider for SoloistBackend {
    fn start(&self) {
        self.start.send_replace(true);
    }

    fn stop(&self) {
        self.cancellation.cancel();
    }

    fn status(&self) -> SpotifyStatus {
        self.status.borrow().clone()
    }

    fn control<'a>(
        &'a self,
        action: SpotifyControlAction,
        value: Option<Value>,
    ) -> ProviderFuture<'a> {
        Box::pin(async move {
            for payload in control_payloads(action, value)? {
                self.send_payload(payload).await?;
            }
            // The returned snapshot is provider-reported state. A successful
            // socket write alone never mutates playback state optimistically.
            Ok(self.status())
        })
    }
}

fn normalize_soloist_uri(uri: &str) -> Result<String, SpotifyError> {
    if uri.len() > 256 || uri.is_empty() {
        return Err(SpotifyError::new(
            "invalid_configuration",
            "Spotify Soloist WebSocket address is invalid.",
        ));
    }
    let candidate = if uri.contains("://") {
        uri.to_owned()
    } else {
        format!("ws://{uri}")
    };
    let parsed = Url::parse(&candidate).map_err(|_| {
        SpotifyError::new(
            "invalid_configuration",
            "Spotify Soloist WebSocket address is invalid.",
        )
    })?;
    if parsed.scheme() != "ws"
        || parsed.host_str().is_none()
        || !matches!(parsed.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err(SpotifyError::new(
            "invalid_configuration",
            "Spotify Soloist must use a local unauthenticated WebSocket address.",
        ));
    }
    Ok(parsed.to_string())
}

fn control_payloads(
    action: SpotifyControlAction,
    value: Option<Value>,
) -> Result<Vec<Value>, SpotifyError> {
    let command = |name| json!({"type": "command", "command": name});
    match action {
        SpotifyControlAction::Play
        | SpotifyControlAction::Pause
        | SpotifyControlAction::SkipNext
        | SpotifyControlAction::SkipPrev => {
            let name = match action {
                SpotifyControlAction::Play => "play",
                SpotifyControlAction::Pause => "pause",
                SpotifyControlAction::SkipNext => "skip_next",
                SpotifyControlAction::SkipPrev => "skip_prev",
                _ => unreachable!(),
            };
            if value.is_some() {
                return Err(SpotifyError::new(
                    "invalid",
                    format!("{name} does not accept a value"),
                ));
            }
            Ok(vec![command(name)])
        }
        SpotifyControlAction::Seek => {
            let position = required_number(value.as_ref(), "seek position")?;
            if position < 0.0 {
                return Err(SpotifyError::new(
                    "invalid",
                    "seek position must not be negative",
                ));
            }
            if position > u64::MAX as f64 {
                return Err(SpotifyError::new(
                    "invalid",
                    "Seek position is outside the supported range.",
                ));
            }
            Ok(vec![json!({
                "type": "command",
                "command": "seek",
                "position_ms": position as u64,
            })])
        }
        SpotifyControlAction::SetVolume => {
            let volume = required_number(value.as_ref(), "volume")?;
            if !(0.0..=100.0).contains(&volume) {
                return Err(SpotifyError::new(
                    "invalid",
                    "volume must be between 0 and 100",
                ));
            }
            Ok(vec![json!({
                "type": "command",
                "command": "set_volume",
                "volume": volume as u8,
            })])
        }
        SpotifyControlAction::SetShuffle => {
            let Some(Value::Bool(enabled)) = value else {
                return Err(SpotifyError::new(
                    "invalid",
                    "shuffle requires a boolean value",
                ));
            };
            Ok(vec![json!({
                "type": "command",
                "command": "set_shuffle",
                "enabled": enabled,
            })])
        }
        SpotifyControlAction::SetRepeat => {
            let Some(Value::String(repeat)) = value else {
                return Err(SpotifyError::new(
                    "invalid",
                    "repeat must be off, context, or track",
                ));
            };
            if !matches!(repeat.as_str(), "off" | "context" | "track") {
                return Err(SpotifyError::new(
                    "invalid",
                    "repeat must be off, context, or track",
                ));
            }
            Ok(vec![
                json!({
                    "type": "command",
                    "command": "set_repeat_track",
                    "enabled": repeat == "track",
                }),
                json!({
                    "type": "command",
                    "command": "set_repeat_context",
                    "enabled": repeat == "context",
                }),
            ])
        }
    }
}

fn required_number(value: Option<&Value>, label: &str) -> Result<f64, SpotifyError> {
    let number = value
        .and_then(Value::as_f64)
        .ok_or_else(|| SpotifyError::new("invalid", format!("{label} requires a number")))?;
    if !number.is_finite() {
        return Err(SpotifyError::new(
            "invalid",
            format!("{label} must be finite"),
        ));
    }
    Ok(number)
}

async fn supervise_soloist(
    uri: Option<String>,
    mut commands: mpsc::Receiver<SoloistCommand>,
    command_sender: mpsc::Sender<SoloistCommand>,
    status: watch::Sender<SpotifyStatus>,
    mut start: watch::Receiver<bool>,
    cancellation: CancellationToken,
) {
    loop {
        if *start.borrow() {
            break;
        }
        tokio::select! {
            _ = cancellation.cancelled() => return,
            changed = start.changed() => if changed.is_err() { return; },
        }
    }
    let Some(uri) = uri else {
        return;
    };
    let mut backoff = Duration::from_millis(250);
    while !cancellation.is_cancelled() {
        match timeout(CONNECT_TIMEOUT, connect_async(&uri)).await {
            Ok(Ok((socket, _))) => {
                backoff = Duration::from_millis(250);
                status.send_modify(|current| {
                    current.connected = true;
                    current.error = None;
                });
                let session_result = run_soloist_session(
                    socket,
                    &mut commands,
                    &command_sender,
                    &status,
                    &cancellation,
                )
                .await;
                fail_queued_commands(&mut commands, SpotifyError::disconnected());
                if cancellation.is_cancelled() {
                    break;
                }
                mark_disconnected(&status, session_result.err());
            }
            Ok(Err(error)) => {
                mark_disconnected(
                    &status,
                    Some(format!("Could not connect to Spotify Soloist: {error}")),
                );
            }
            Err(_) => {
                mark_disconnected(
                    &status,
                    Some("Spotify Soloist connection timed out.".to_owned()),
                );
            }
        }
        tokio::select! {
            _ = cancellation.cancelled() => break,
            _ = sleep(backoff) => {}
        }
        backoff = (backoff * 2).min(Duration::from_secs(5));
    }
    fail_queued_commands(&mut commands, SpotifyError::disconnected());
    mark_disconnected(&status, None);
}

async fn run_soloist_session(
    socket: SoloistSocket,
    commands: &mut mpsc::Receiver<SoloistCommand>,
    command_sender: &mpsc::Sender<SoloistCommand>,
    status: &watch::Sender<SpotifyStatus>,
    cancellation: &CancellationToken,
) -> Result<(), String> {
    let (mut writer, mut reader) = socket.split();
    let mut clock = PlaybackClock::default();
    let mut pending_ack = None;
    let mut tick = tokio::time::interval(POSITION_TICK);
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let result = loop {
        let ack_deadline = pending_ack
            .as_ref()
            .map(|pending: &PendingCommandAck| pending.deadline);
        tokio::select! {
            _ = cancellation.cancelled() => {
                let _ = timeout(Duration::from_millis(250), writer.send(Message::Close(None))).await;
                break Ok(());
            }
            _ = async move {
                if let Some(deadline) = ack_deadline {
                    tokio::time::sleep_until(deadline).await;
                } else {
                    std::future::pending::<()>().await;
                }
            } => {
                if let Some(pending) = pending_ack.take() {
                    let _ = pending.reply.send(Err(SpotifyError::command_timeout(true)));
                    break Err("Spotify Soloist command acknowledgement timed out.".to_owned());
                }
            }
            command = commands.recv(), if pending_ack.is_none() => {
                let Some(command) = command else { break Ok(()); };
                if command.reply.as_ref().is_some_and(oneshot::Sender::is_closed) {
                    continue;
                }
                if Instant::now() >= command.deadline {
                    if let Some(reply) = command.reply {
                        let _ = reply.send(Err(SpotifyError::command_timeout(false)));
                    }
                    continue;
                }
                let payload = match serde_json::to_string(&command.payload) {
                    Ok(payload) => payload,
                    Err(_) => {
                        if let Some(reply) = command.reply {
                            let _ = reply.send(Err(SpotifyError::new("invalid", "Spotify command could not be encoded.")));
                        }
                        continue;
                    }
                };
                let ack_command = command
                    .payload
                    .get("command")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                if command.reply.is_some() && ack_command.is_none() {
                    if let Some(reply) = command.reply {
                        let _ = reply.send(Err(SpotifyError::new(
                            "invalid",
                            "Spotify control command is missing its command name.",
                        )));
                    }
                    continue;
                }
                let result = timeout(
                    command.deadline.saturating_duration_since(Instant::now()),
                    writer.send(Message::Text(payload.into())),
                ).await;
                match result {
                    Ok(Ok(())) => {
                        if let Some(reply) = command.reply {
                            pending_ack = Some(PendingCommandAck {
                                command: ack_command.expect("acknowledged command has a name"),
                                deadline: command.deadline,
                                reply,
                            });
                        }
                    }
                    Ok(Err(_)) => {
                        if let Some(reply) = command.reply {
                            let _ = reply.send(Err(SpotifyError::new(
                                "unavailable",
                                "Spotify command failed while being sent; delivery may be ambiguous.",
                            )));
                        }
                        break Err("Spotify Soloist connection closed while sending a command.".to_owned());
                    }
                    Err(_) => {
                        if let Some(reply) = command.reply {
                            let _ = reply.send(Err(SpotifyError::command_timeout(true)));
                        }
                        break Err("Spotify command send timed out.".to_owned());
                    }
                }
            }
            message = reader.next() => {
                let Some(message) = message else {
                    break Err("Spotify Soloist closed the connection.".to_owned());
                };
                match message {
                    Ok(Message::Text(text)) => {
                        if text.len() > MAX_EVENT_BYTES {
                            break Err("Spotify Soloist event exceeded the size limit.".to_owned());
                        }
                        apply_event(status, &mut clock, text.as_bytes(), command_sender);
                        if let Err(error) = handle_command_response(text.as_bytes(), &mut pending_ack) {
                            break Err(error);
                        }
                    }
                    Ok(Message::Binary(bytes)) => {
                        if bytes.len() > MAX_EVENT_BYTES {
                            break Err("Spotify Soloist event exceeded the size limit.".to_owned());
                        }
                        apply_event(status, &mut clock, &bytes, command_sender);
                        if let Err(error) = handle_command_response(&bytes, &mut pending_ack) {
                            break Err(error);
                        }
                    }
                    Ok(Message::Ping(bytes)) => {
                        if !matches!(timeout(Duration::from_secs(1), writer.send(Message::Pong(bytes))).await, Ok(Ok(()))) {
                            break Err("Spotify Soloist ping response failed or timed out.".to_owned());
                        }
                    }
                    Ok(Message::Close(_)) => break Err("Spotify Soloist closed the connection.".to_owned()),
                    Ok(Message::Pong(_)) | Ok(Message::Frame(_)) => {}
                    Err(_) => break Err("Spotify Soloist connection failed while reading events.".to_owned()),
                }
            }
            _ = tick.tick() => {
                publish_position(status, &clock);
                // Drop no provider updates when a burst fills the bounded
                // command queue; a later auth event or reconnect requests state.
            }
        }
    };

    if let Some(pending) = pending_ack.take() {
        let error = if result.is_ok() {
            SpotifyError::new(
                "cancelled",
                "Spotify command was sent but not acknowledged before shutdown; delivery may be ambiguous.",
            )
        } else {
            SpotifyError::new(
                "unavailable",
                "Spotify connection ended before the command was acknowledged; delivery may be ambiguous.",
            )
        };
        let _ = pending.reply.send(Err(error));
    }
    result
}

fn handle_command_response(
    payload: &[u8],
    pending_ack: &mut Option<PendingCommandAck>,
) -> Result<(), String> {
    let Ok(event) = serde_json::from_slice::<Value>(payload) else {
        return Ok(());
    };
    let Some(event) = event.as_object() else {
        return Ok(());
    };
    match event.get("type").and_then(Value::as_str) {
        Some("command_result") => {
            let acknowledged = event
                .get("command")
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    "Spotify Soloist returned an invalid command acknowledgement.".to_owned()
                })?;
            let Some(pending) = pending_ack.as_ref() else {
                return Err(
                    "Spotify Soloist acknowledged a command that was not pending.".to_owned(),
                );
            };
            if pending.command != acknowledged {
                return Err(
                    "Spotify Soloist acknowledged a different command; delivery may be ambiguous."
                        .to_owned(),
                );
            }
            let pending = pending_ack
                .take()
                .expect("pending acknowledgement was checked above");
            if Instant::now() >= pending.deadline {
                let _ = pending.reply.send(Err(SpotifyError::command_timeout(true)));
                return Err("Spotify Soloist command acknowledgement timed out.".to_owned());
            }
            let _ = pending.reply.send(Ok(()));
        }
        Some("error") => {
            if let Some(pending) = pending_ack.take() {
                let message = event
                    .get("message")
                    .and_then(Value::as_str)
                    .filter(|message| !message.is_empty())
                    .unwrap_or("Spotify rejected the command.");
                let _ = pending.reply.send(Err(SpotifyError::new(
                    "provider_rejected",
                    message.to_owned(),
                )));
            }
        }
        _ => {}
    }
    Ok(())
}

#[derive(Clone, Debug)]
struct PlaybackClock {
    anchor_ms: u64,
    anchor_at: Instant,
    speed: f64,
}

impl Default for PlaybackClock {
    fn default() -> Self {
        Self {
            anchor_ms: 0,
            anchor_at: Instant::now(),
            speed: 0.0,
        }
    }
}

impl PlaybackClock {
    fn anchor(&mut self, position_ms: u64, speed: f64) {
        self.anchor_ms = position_ms.min(MAX_POSITION_MS);
        self.anchor_at = Instant::now();
        self.speed = if speed.is_finite() {
            speed.clamp(0.0, 16.0)
        } else {
            1.0
        };
    }

    fn current_position(&self, status: &SpotifyStatus) -> u64 {
        let mut position = self.anchor_ms;
        if matches!(
            status.status,
            SpotifyPlaybackStatus::Playing | SpotifyPlaybackStatus::Buffering
        ) && self.speed > 0.0
        {
            let elapsed_ms = self.anchor_at.elapsed().as_secs_f64() * 1000.0 * self.speed;
            let elapsed_ms = elapsed_ms.min(MAX_POSITION_MS as f64) as u64;
            position = position.saturating_add(elapsed_ms);
        }
        if let Some(duration) = status.duration_ms {
            position = position.min(duration);
        }
        position.min(MAX_POSITION_MS)
    }
}

fn publish_position(status: &watch::Sender<SpotifyStatus>, clock: &PlaybackClock) {
    status.send_if_modified(|current| {
        let position_ms = clock.current_position(current);
        if current.position_ms == position_ms {
            false
        } else {
            current.position_ms = position_ms;
            true
        }
    });
}

fn apply_event(
    publisher: &watch::Sender<SpotifyStatus>,
    clock: &mut PlaybackClock,
    payload: &[u8],
    command_sender: &mpsc::Sender<SoloistCommand>,
) {
    let event: Value = match serde_json::from_slice(payload) {
        Ok(event) => event,
        Err(_) => {
            publisher.send_modify(|current| {
                current.error = Some("Spotify Soloist sent an invalid event.".to_owned());
            });
            return;
        }
    };
    let Some(event) = event.as_object() else {
        return;
    };
    let event_type = event
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    match event_type {
        "auth_state" => {
            let logged_in = event
                .get("logged_in")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            publisher.send_modify(|current| {
                current.logged_in = logged_in;
                current.is_active = event
                    .get("is_active")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                current.device_name = optional_string(event.get("device_name"));
            });
            if logged_in {
                let _ = command_sender.try_send(SoloistCommand {
                    payload: json!({"type": "command", "command": "get_state"}),
                    deadline: Instant::now() + COMMAND_TIMEOUT,
                    reply: None,
                });
            }
        }
        "playback_state" => {
            let track = event.get("item").and_then(parse_track);
            let status = playback_status(event.get("status"));
            let position = event.get("position");
            let position_ms = position
                .and_then(|position| position.get("position_ms"))
                .map(nonnegative_int)
                .unwrap_or_default();
            let speed = position
                .and_then(|position| position.get("speed"))
                .map(playback_speed)
                .unwrap_or_else(|| {
                    if status == SpotifyPlaybackStatus::Playing {
                        1.0
                    } else {
                        0.0
                    }
                });
            publisher.send_modify(|current| {
                current.status = status;
                current.duration_ms = track.as_ref().and_then(|track| track.duration_ms);
                current.track = track;
                current.volume = volume(event.get("volume"));
                current.is_active = event
                    .get("is_active")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                if let Some(options) = event.get("options").and_then(Value::as_object) {
                    current.shuffle = options
                        .get("shuffle")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    current.repeat = repeat(options.get("repeat"));
                } else {
                    current.shuffle = false;
                    current.repeat = SpotifyRepeat::Off;
                }
                current.error = None;
                current.position_ms = position_ms;
            });
            clock.anchor(position_ms, speed);
        }
        "track_changed" => {
            let track = event.get("item").and_then(parse_track);
            publisher.send_modify(|current| {
                current.duration_ms = track.as_ref().and_then(|track| track.duration_ms);
                current.track = track;
                current.position_ms = 0;
            });
            clock.anchor(0, 0.0);
        }
        "playback_changed" => {
            let position_ms = {
                let current = publisher.borrow();
                clock.current_position(&current)
            };
            let status = playback_status(event.get("status"));
            publisher.send_modify(|current| {
                current.status = status;
                current.position_ms = position_ms;
            });
            clock.anchor(
                position_ms,
                if status == SpotifyPlaybackStatus::Playing {
                    1.0
                } else {
                    0.0
                },
            );
        }
        "volume_changed" => {
            publisher.send_modify(|current| current.volume = volume(event.get("volume")));
        }
        "device_changed" => {
            publisher.send_modify(|current| {
                current.is_active = event
                    .get("is_active")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                current.device_name = optional_string(event.get("device_name"));
            });
        }
        "options_changed" => {
            if let Some(options) = event.get("options").and_then(Value::as_object) {
                publisher.send_modify(|current| {
                    if let Some(shuffle) = options.get("shuffle").and_then(Value::as_bool) {
                        current.shuffle = shuffle;
                    }
                    if options.contains_key("repeat") {
                        current.repeat = repeat(options.get("repeat"));
                    }
                });
            }
        }
        "position_sync" => {
            if let Some(position) = event.get("position").and_then(Value::as_object) {
                let position_ms = position
                    .get("position_ms")
                    .map(nonnegative_int)
                    .unwrap_or_default();
                let speed = position.get("speed").map(playback_speed).unwrap_or(1.0);
                publisher.send_modify(|current| current.position_ms = position_ms);
                clock.anchor(position_ms, speed);
            }
        }
        "error" => {
            publisher.send_modify(|current| {
                current.error = Some(
                    event
                        .get("message")
                        .and_then(Value::as_str)
                        .filter(|message| !message.is_empty())
                        .unwrap_or("Spotify command failed.")
                        .to_owned(),
                );
            });
        }
        _ => {}
    }
}

fn mark_disconnected(status: &watch::Sender<SpotifyStatus>, error: Option<String>) {
    status.send_modify(|current| {
        current.connected = false;
        current.logged_in = false;
        current.is_active = false;
        current.status = SpotifyPlaybackStatus::Idle;
        current.track = None;
        current.position_ms = 0;
        current.duration_ms = None;
        current.error = error.or_else(|| current.error.clone());
    });
}

fn fail_queued_commands(commands: &mut mpsc::Receiver<SoloistCommand>, error: SpotifyError) {
    while let Ok(command) = commands.try_recv() {
        if let Some(reply) = command.reply {
            let _ = reply.send(Err(error.clone()));
        }
    }
}

fn parse_track(value: &Value) -> Option<SpotifyTrack> {
    let object = value.as_object()?;
    let decorations = object.get("decorations");
    let identity = decorations.and_then(|value| value.get("identity"));
    let album = decorations
        .and_then(|value| value.get("parent"))
        .and_then(|value| value.get("entity"))
        .and_then(|value| value.get("decorations"))
        .and_then(|value| value.get("identity"))
        .and_then(|value| value.get("name"))
        .and_then(Value::as_str)
        .map(str::to_owned);
    let cover_url = decorations
        .and_then(|value| value.get("visual_identity"))
        .and_then(|value| value.get("cover"))
        .and_then(Value::as_array)
        .and_then(|covers| {
            covers
                .iter()
                .find_map(|cover| cover.get("url").and_then(Value::as_str).map(str::to_owned))
        });
    let artists = decorations
        .and_then(|value| value.get("creators"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|creator| {
            creator
                .get("entity")
                .and_then(|value| value.get("decorations"))
                .and_then(|value| value.get("identity"))
                .and_then(|value| value.get("name"))
                .and_then(Value::as_str)
                .filter(|name| !name.is_empty())
                .map(str::to_owned)
        })
        .collect();
    let duration_ms = decorations
        .and_then(|value| value.get("playback"))
        .and_then(|value| value.get("duration_ms"))
        .map(nonnegative_int);
    Some(SpotifyTrack {
        uri: optional_string(object.get("uri")).unwrap_or_default(),
        name: optional_string(identity.and_then(|value| value.get("name"))).unwrap_or_default(),
        artists,
        album,
        cover_url,
        duration_ms,
    })
}

fn optional_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn playback_status(value: Option<&Value>) -> SpotifyPlaybackStatus {
    match value.and_then(Value::as_str) {
        Some("playing") => SpotifyPlaybackStatus::Playing,
        Some("paused") => SpotifyPlaybackStatus::Paused,
        Some("buffering") => SpotifyPlaybackStatus::Buffering,
        _ => SpotifyPlaybackStatus::Idle,
    }
}

fn repeat(value: Option<&Value>) -> SpotifyRepeat {
    match value.and_then(Value::as_str) {
        Some("context") => SpotifyRepeat::Context,
        Some("track") => SpotifyRepeat::Track,
        _ => SpotifyRepeat::Off,
    }
}

fn nonnegative_int(value: &Value) -> u64 {
    value
        .as_u64()
        .or_else(|| {
            value
                .as_f64()
                .filter(|number| number.is_finite() && *number >= 0.0)
                .map(|number| number.min(MAX_POSITION_MS as f64) as u64)
        })
        .unwrap_or_default()
        .min(MAX_POSITION_MS)
}

fn volume(value: Option<&Value>) -> u8 {
    value
        .and_then(Value::as_f64)
        .filter(|number| number.is_finite())
        .map(|number| number.clamp(0.0, 100.0) as u8)
        .unwrap_or_default()
}

fn playback_speed(value: &Value) -> f64 {
    value
        .as_f64()
        .filter(|number| number.is_finite())
        .map(|number| number.clamp(0.0, 16.0))
        .unwrap_or(1.0)
}

#[cfg(test)]
mod tests {
    use super::{
        MAX_PENDING_COMMANDS, PlaybackClock, SoloistBackend, SoloistCommand, SpotifyControlAction,
        SpotifyPlaybackStatus, SpotifyProvider, SpotifyRepeat, SpotifyStatus, apply_event,
        control_payloads, normalize_soloist_uri, publish_position, run_soloist_session,
    };
    use futures_util::{SinkExt, StreamExt};
    use serde_json::{Value, json};
    use std::time::Duration;
    use tokio::net::TcpListener;
    use tokio::sync::{mpsc, oneshot, watch};
    use tokio::time::{sleep, timeout};
    use tokio_tungstenite::accept_async;
    use tokio_tungstenite::tungstenite::Message;
    use tokio_util::sync::CancellationToken;

    #[test]
    fn validates_soloist_address_and_never_accepts_embedded_credentials() {
        assert_eq!(
            normalize_soloist_uri("127.0.0.1:9090").unwrap(),
            "ws://127.0.0.1:9090/"
        );
        assert!(normalize_soloist_uri("ws://user:secret@127.0.0.1:9090").is_err());
        assert!(normalize_soloist_uri("https://127.0.0.1:9090").is_err());
    }

    #[tokio::test]
    async fn stopping_soloist_provider_does_not_cancel_application_tasks() {
        let cancellation = CancellationToken::new();
        let (backend, task) = SoloistBackend::new("127.0.0.1:9090", true, cancellation.clone());

        backend.stop();

        assert!(!cancellation.is_cancelled());
        timeout(Duration::from_secs(1), task)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn soloist_command_queue_is_bounded() {
        let cancellation = CancellationToken::new();
        let (backend, task) = SoloistBackend::new("127.0.0.1:9090", true, cancellation);

        for index in 0..MAX_PENDING_COMMANDS {
            backend
                .commands
                .try_send(SoloistCommand {
                    payload: json!({"index": index}),
                    deadline: super::Instant::now() + Duration::from_secs(1),
                    reply: None,
                })
                .unwrap();
        }
        let error = backend
            .commands
            .try_send(SoloistCommand {
                payload: json!({"overflow": true}),
                deadline: super::Instant::now() + Duration::from_secs(1),
                reply: None,
            })
            .unwrap_err();
        assert!(matches!(error, mpsc::error::TrySendError::Full(_)));

        backend.stop();
        timeout(Duration::from_secs(1), task)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn expired_soloist_command_is_not_delivered() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            while let Some(Ok(message)) = socket.next().await {
                match message {
                    Message::Text(_) => panic!("expired commands must not reach Soloist"),
                    Message::Close(_) => break,
                    _ => {}
                }
            }
        });
        let (socket, _) = timeout(
            Duration::from_secs(1),
            super::connect_async(format!("ws://{address}")),
        )
        .await
        .unwrap()
        .unwrap();
        let (command_sender, mut commands) = mpsc::channel(1);
        let (reply, receiver) = oneshot::channel();
        command_sender
            .send(SoloistCommand {
                payload: json!({"type":"command","command":"play"}),
                deadline: super::Instant::now() - Duration::from_secs(1),
                reply: Some(reply),
            })
            .await
            .unwrap();
        let (status, _) = watch::channel(SpotifyStatus::initial(true));
        let cancellation = CancellationToken::new();
        let task = tokio::spawn({
            let cancellation = cancellation.clone();
            async move {
                run_soloist_session(
                    socket,
                    &mut commands,
                    &command_sender,
                    &status,
                    &cancellation,
                )
                .await
            }
        });

        let error = timeout(Duration::from_secs(1), receiver)
            .await
            .unwrap()
            .unwrap()
            .unwrap_err();
        assert_eq!(error.code, "timeout");
        assert!(error.message.contains("before delivery"));
        cancellation.cancel();
        timeout(Duration::from_secs(1), task)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        timeout(Duration::from_secs(1), server)
            .await
            .unwrap()
            .unwrap();
    }

    #[test]
    fn control_payloads_preserve_the_soloist_command_protocol() {
        assert_eq!(
            control_payloads(SpotifyControlAction::Play, None).unwrap(),
            vec![json!({"type":"command","command":"play"})]
        );
        assert_eq!(
            control_payloads(SpotifyControlAction::Seek, Some(json!(12345))).unwrap(),
            vec![json!({"type":"command","command":"seek","position_ms":12345})]
        );
        assert_eq!(
            control_payloads(SpotifyControlAction::SetVolume, Some(json!(72.8))).unwrap(),
            vec![json!({"type":"command","command":"set_volume","volume":72})]
        );
        assert_eq!(
            control_payloads(SpotifyControlAction::SetShuffle, Some(json!(true))).unwrap(),
            vec![json!({"type":"command","command":"set_shuffle","enabled":true})]
        );
        assert_eq!(
            control_payloads(SpotifyControlAction::SetRepeat, Some(json!("track"))).unwrap(),
            vec![
                json!({"type":"command","command":"set_repeat_track","enabled":true}),
                json!({"type":"command","command":"set_repeat_context","enabled":false}),
            ]
        );
        assert!(control_payloads(SpotifyControlAction::Seek, Some(json!(-1))).is_err());
        assert!(control_payloads(SpotifyControlAction::SetVolume, Some(json!(101))).is_err());
        assert!(control_payloads(SpotifyControlAction::SetShuffle, Some(json!("yes"))).is_err());
    }

    #[test]
    fn projects_auth_track_options_and_malformed_events_into_typed_status() {
        let (publisher, receiver) = watch::channel(SpotifyStatus::initial(true));
        let (command_sender, _command_receiver) = mpsc::channel(MAX_PENDING_COMMANDS);
        let mut clock = super::PlaybackClock::default();
        apply_event(
            &publisher,
            &mut clock,
            br#"{"type":"auth_state","logged_in":true,"is_active":true,"device_name":"LumiStripe"}"#,
            &command_sender,
        );
        apply_event(
            &publisher,
            &mut clock,
            br#"{"type":"playback_state","status":"playing","position":{"position_ms":4200,"speed":0},"volume":68,"options":{"shuffle":true,"repeat":"context"},"is_active":true,"item":{"uri":"spotify:track:example","decorations":{"identity":{"name":"Example track"},"visual_identity":{"cover":[{"url":"https://images.example/cover.jpg"}]},"playback":{"duration_ms":180000},"parent":{"entity":{"decorations":{"identity":{"name":"Example album"}}}},"creators":[{"entity":{"decorations":{"identity":{"name":"Example artist"}}}}]}}}"#,
            &command_sender,
        );
        let status = receiver.borrow().clone();
        assert!(status.logged_in);
        assert_eq!(status.device_name.as_deref(), Some("LumiStripe"));
        assert_eq!(status.status, SpotifyPlaybackStatus::Playing);
        assert_eq!(status.position_ms, 4200);
        assert_eq!(status.volume, 68);
        assert!(status.shuffle);
        assert_eq!(status.repeat, SpotifyRepeat::Context);
        let track = status.track.unwrap();
        assert_eq!(track.name, "Example track");
        assert_eq!(track.artists, ["Example artist"]);
        assert_eq!(track.album.as_deref(), Some("Example album"));
        assert_eq!(
            track.cover_url.as_deref(),
            Some("https://images.example/cover.jpg")
        );
        assert_eq!(track.duration_ms, Some(180000));

        apply_event(
            &publisher,
            &mut clock,
            br#"{"type":"device_changed","is_active":false,"device_name":"Living room"}"#,
            &command_sender,
        );
        assert!(!receiver.borrow().is_active);
        assert_eq!(
            receiver.borrow().device_name.as_deref(),
            Some("Living room")
        );

        apply_event(&publisher, &mut clock, b"not-json", &command_sender);
        assert!(
            receiver
                .borrow()
                .error
                .as_deref()
                .unwrap()
                .contains("invalid event")
        );
    }

    #[test]
    fn malformed_track_values_do_not_create_empty_tracks() {
        let (publisher, receiver) = watch::channel(SpotifyStatus::initial(true));
        let (command_sender, _command_receiver) = mpsc::channel(MAX_PENDING_COMMANDS);
        let mut clock = super::PlaybackClock::default();

        apply_event(
            &publisher,
            &mut clock,
            br#"{"type":"playback_state","status":"playing","item":"not-a-track"}"#,
            &command_sender,
        );

        assert!(receiver.borrow().track.is_none());
        assert_eq!(receiver.borrow().status, SpotifyPlaybackStatus::Playing);
    }

    #[tokio::test]
    async fn playback_clock_advances_monotonically_and_honors_pause_and_duration() {
        let mut status = SpotifyStatus::initial(true);
        status.status = SpotifyPlaybackStatus::Playing;
        status.duration_ms = Some(150);
        let mut clock = PlaybackClock::default();

        clock.anchor(100, 1.0);
        sleep(Duration::from_millis(25)).await;
        let advanced_position = clock.current_position(&status);
        assert!(advanced_position > 100);

        status.status = SpotifyPlaybackStatus::Paused;
        clock.anchor(120, 1.0);
        sleep(Duration::from_millis(25)).await;
        assert_eq!(clock.current_position(&status), 120);

        status.status = SpotifyPlaybackStatus::Playing;
        clock.anchor(145, 1.0);
        sleep(Duration::from_millis(25)).await;
        assert_eq!(clock.current_position(&status), 150);
    }

    #[tokio::test]
    async fn playback_position_publishes_only_when_the_position_changes() {
        let mut initial = SpotifyStatus::initial(true);
        initial.status = SpotifyPlaybackStatus::Paused;
        initial.position_ms = 4200;
        let (publisher, mut receiver) = watch::channel(initial);
        let mut clock = PlaybackClock::default();
        clock.anchor(4200, 0.0);

        publish_position(&publisher, &clock);
        publish_position(&publisher, &clock);
        assert!(!receiver.has_changed().unwrap());

        publisher.send_modify(|current| current.status = SpotifyPlaybackStatus::Playing);
        let _ = receiver.borrow_and_update();
        clock.anchor(4200, 1.0);
        sleep(Duration::from_millis(20)).await;
        publish_position(&publisher, &clock);

        assert!(receiver.has_changed().unwrap());
        assert!(receiver.borrow_and_update().position_ms > 4200);
    }

    #[tokio::test]
    async fn soloist_writer_sends_controls_without_optimistic_state_changes() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (received_sender, received_receiver) = oneshot::channel();
        let (confirm_sender, confirm_receiver) = oneshot::channel();
        let (close_sender, close_receiver) = oneshot::channel();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            socket
                .send(Message::Text(
                    json!({"type":"playback_state","status":"paused","position":{"position_ms":5000}})
                        .to_string()
                        .into(),
                ))
                .await
                .unwrap();
            while let Some(Ok(message)) = socket.next().await {
                if let Message::Text(text) = message {
                    let command: Value = serde_json::from_str(text.as_str()).unwrap();
                    if command["command"] == "play" {
                        let _ = received_sender.send(command);
                        socket
                            .send(Message::Text(
                                json!({"type":"command_result","command":"play"})
                                    .to_string()
                                    .into(),
                            ))
                            .await
                            .unwrap();
                        let _ = confirm_receiver.await;
                        socket
                            .send(Message::Text(
                                json!({"type":"playback_changed","status":"playing"})
                                    .to_string()
                                    .into(),
                            ))
                            .await
                            .unwrap();
                        let _ = close_receiver.await;
                        break;
                    }
                }
            }
        });
        let cancellation = CancellationToken::new();
        let (backend, actor) =
            SoloistBackend::new(format!("ws://{address}"), true, cancellation.clone());
        backend.start();
        timeout(Duration::from_secs(2), async {
            loop {
                if backend.status().status == SpotifyPlaybackStatus::Paused {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("provider should publish initial playback state");
        let status = backend
            .control(SpotifyControlAction::Play, None)
            .await
            .unwrap();
        let received = timeout(Duration::from_secs(1), received_receiver)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(received, json!({"type":"command","command":"play"}));
        assert_eq!(status.status, SpotifyPlaybackStatus::Paused);
        confirm_sender.send(()).unwrap();
        timeout(Duration::from_secs(2), async {
            loop {
                if backend.status().status == SpotifyPlaybackStatus::Playing {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("provider confirmation should update playback state");
        close_sender.send(()).unwrap();
        cancellation.cancel();
        timeout(Duration::from_secs(2), actor)
            .await
            .unwrap()
            .unwrap();
        timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn soloist_provider_rejection_is_not_reported_as_a_successful_control() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            socket
                .send(Message::Text(
                    json!({
                        "type":"auth_state",
                        "logged_in":true,
                        "is_active":true,
                        "device_name":"LumiStripe"
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
            socket
                .send(Message::Text(
                    json!({"type":"playback_state","status":"paused"})
                        .to_string()
                        .into(),
                ))
                .await
                .unwrap();
            while let Some(Ok(message)) = socket.next().await {
                if let Message::Text(text) = message {
                    let command: Value = serde_json::from_str(text.as_str()).unwrap();
                    if command["command"] == "pause" {
                        socket
                            .send(Message::Text(
                                json!({
                                    "type":"error",
                                    "message":"command requires authentication"
                                })
                                .to_string()
                                .into(),
                            ))
                            .await
                            .unwrap();
                    }
                }
            }
        });
        let cancellation = CancellationToken::new();
        let (backend, actor) =
            SoloistBackend::new(format!("ws://{address}"), true, cancellation.clone());
        backend.start();
        timeout(Duration::from_secs(2), async {
            loop {
                let status = backend.status();
                if status.connected && status.logged_in {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("Soloist authentication should be published before control");

        let error = backend
            .control(SpotifyControlAction::Pause, None)
            .await
            .unwrap_err();
        assert_eq!(error.code, "provider_rejected");
        assert_eq!(error.message, "command requires authentication");
        assert_eq!(backend.status().status, SpotifyPlaybackStatus::Paused);
        assert_eq!(
            backend.status().error.as_deref(),
            Some(error.message.as_str())
        );

        cancellation.cancel();
        timeout(Duration::from_secs(2), actor)
            .await
            .unwrap()
            .unwrap();
        timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn missing_soloist_command_acknowledgement_is_ambiguous_and_disconnects() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let message = timeout(Duration::from_secs(1), socket.next())
                .await
                .unwrap()
                .unwrap()
                .unwrap();
            assert!(matches!(message, Message::Text(_)));
            while let Some(Ok(message)) = socket.next().await {
                if matches!(message, Message::Close(_)) {
                    break;
                }
            }
        });
        let (socket, _) = timeout(
            Duration::from_secs(1),
            super::connect_async(format!("ws://{address}")),
        )
        .await
        .unwrap()
        .unwrap();
        let (command_sender, mut commands) = mpsc::channel(1);
        let (reply, receiver) = oneshot::channel();
        command_sender
            .send(SoloistCommand {
                payload: json!({"type":"command","command":"pause"}),
                deadline: super::Instant::now() + Duration::from_millis(100),
                reply: Some(reply),
            })
            .await
            .unwrap();
        let (status, _) = watch::channel(SpotifyStatus::initial(true));
        let cancellation = CancellationToken::new();
        let task = tokio::spawn({
            let cancellation = cancellation.clone();
            async move {
                run_soloist_session(
                    socket,
                    &mut commands,
                    &command_sender,
                    &status,
                    &cancellation,
                )
                .await
            }
        });

        let error = timeout(Duration::from_secs(1), receiver)
            .await
            .unwrap()
            .unwrap()
            .unwrap_err();
        assert_eq!(error.code, "timeout");
        assert!(error.message.contains("delivery may still complete"));
        assert!(
            timeout(Duration::from_secs(1), task)
                .await
                .unwrap()
                .unwrap()
                .is_err()
        );
        timeout(Duration::from_secs(1), server)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn soloist_reconnects_and_republishes_provider_state_after_disconnect() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (first_sent_sender, first_sent_receiver) = oneshot::channel();
        let (disconnect_first_sender, disconnect_first_receiver) = oneshot::channel();
        let (second_sent_sender, second_sent_receiver) = oneshot::channel();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut first = accept_async(stream).await.unwrap();
            first
                .send(Message::Text(
                    json!({
                        "type":"auth_state",
                        "logged_in":true,
                        "is_active":true,
                        "device_name":"First device"
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
            first
                .send(Message::Text(
                    json!({
                        "type":"playback_state",
                        "status":"playing",
                        "position":{"position_ms":1200,"speed":0},
                        "item":{"uri":"spotify:track:first","decorations":{"identity":{"name":"First track"}}}
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
            let _ = first_sent_sender.send(());
            let _ = disconnect_first_receiver.await;
            let _ = first.close(None).await;
            drop(first);

            let (stream, _) = timeout(Duration::from_secs(3), listener.accept())
                .await
                .expect("the client should retry the Soloist connection")
                .unwrap();
            let mut second = accept_async(stream).await.unwrap();
            second
                .send(Message::Text(
                    json!({
                        "type":"auth_state",
                        "logged_in":true,
                        "is_active":true,
                        "device_name":"LumiStripe"
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
            second
                .send(Message::Text(
                    json!({
                        "type":"playback_state",
                        "status":"paused",
                        "position":{"position_ms":4567,"speed":0},
                        "item":{"uri":"spotify:track:second","decorations":{"identity":{"name":"Recovered track"}}}
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
            let _ = second_sent_sender.send(());
            while let Some(Ok(message)) = second.next().await {
                if matches!(message, Message::Close(_)) {
                    break;
                }
            }
        });

        let cancellation = CancellationToken::new();
        let (backend, actor) =
            SoloistBackend::new(format!("ws://{address}"), true, cancellation.clone());
        backend.start();
        first_sent_receiver.await.unwrap();
        timeout(Duration::from_secs(2), async {
            loop {
                let status = backend.status();
                if status.connected
                    && status
                        .track
                        .as_ref()
                        .is_some_and(|track| track.name == "First track")
                {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("the first provider snapshot should be published");

        disconnect_first_sender.send(()).unwrap();
        timeout(Duration::from_secs(2), async {
            loop {
                if !backend.status().connected {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("disconnect should clear the old provider connection state");
        assert_eq!(backend.status().status, SpotifyPlaybackStatus::Idle);
        assert!(backend.status().track.is_none());

        second_sent_receiver.await.unwrap();
        timeout(Duration::from_secs(2), async {
            loop {
                let status = backend.status();
                if status.connected
                    && status.logged_in
                    && status.device_name.as_deref() == Some("LumiStripe")
                    && status
                        .track
                        .as_ref()
                        .is_some_and(|track| track.name == "Recovered track")
                {
                    break;
                }
                sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("the reconnect should publish the current provider state");
        assert_eq!(backend.status().position_ms, 4567);

        cancellation.cancel();
        timeout(Duration::from_secs(2), actor)
            .await
            .unwrap()
            .unwrap();
        timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap();
    }
}
