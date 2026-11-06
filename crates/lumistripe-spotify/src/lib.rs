//! Native Spotify Connect provider built on the pinned librespot 0.8 API.
//!
//! This crate owns local discovery, session restoration, player lifetime and
//! serialized controls. Callers only see bounded commands and immutable
//! snapshots; Spotify protocol types stay behind this boundary.

use futures_util::StreamExt;
use librespot::{
    connect::{ConnectConfig, Spirc},
    core::{
        authentication::Credentials,
        cache::Cache,
        config::{DeviceType, SessionConfig},
        error::ErrorKind,
        session::Session,
    },
    discovery::Discovery,
    metadata::audio::UniqueFields,
    playback::{
        audio_backend,
        config::{AudioFormat, PlayerConfig},
        mixer::{self, MixerConfig},
        player::{Player, PlayerEvent, PlayerEventChannel, SinkStatus},
    },
};
use std::{
    fs::{self, OpenOptions},
    io,
    path::{Path, PathBuf},
    time::Duration,
};
use thiserror::Error;
use tokio::{
    sync::{mpsc, oneshot, watch},
    task::JoinHandle,
    time::{Instant, sleep, sleep_until, timeout},
};
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

pub const SPOTIFY_PIPEWIRE_SINK: &str = "lumistripe_spotify";
const PULSEAUDIO_BACKEND: &str = "pulseaudio";
const MAX_PENDING_COMMANDS: usize = 64;
const COMMAND_TIMEOUT: Duration = Duration::from_secs(3);
const COMMAND_RESPONSE_GRACE: Duration = Duration::from_secs(1);
const SESSION_CONNECT_TIMEOUT: Duration = Duration::from_secs(35);
const SESSION_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
const DISCOVERY_RETRY_MIN: Duration = Duration::from_millis(500);
const DISCOVERY_RETRY_MAX: Duration = Duration::from_secs(30);
const POSITION_UPDATE_INTERVAL: Duration = Duration::from_millis(250);
const AUDIO_CACHE_LIMIT_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct SpotifyConnectConfig {
    pub data_dir: PathBuf,
    pub device_name: String,
    pub audio_sink_name: String,
}

impl SpotifyConnectConfig {
    pub fn new(data_dir: impl Into<PathBuf>, device_name: impl Into<String>) -> Self {
        Self {
            data_dir: data_dir.into(),
            device_name: device_name.into(),
            audio_sink_name: SPOTIFY_PIPEWIRE_SINK.to_owned(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PlaybackState {
    Idle,
    Playing,
    Paused,
    Buffering,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RepeatMode {
    Off,
    Context,
    Track,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Track {
    pub uri: String,
    pub name: String,
    pub artists: Vec<String>,
    pub album: Option<String>,
    pub cover_url: Option<String>,
    pub duration_ms: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Snapshot {
    pub configured: bool,
    pub connected: bool,
    pub logged_in: bool,
    pub is_active: bool,
    pub device_name: Option<String>,
    pub state: PlaybackState,
    pub track: Option<Track>,
    pub position_ms: u64,
    pub duration_ms: Option<u64>,
    pub volume: u8,
    pub shuffle: bool,
    pub repeat: RepeatMode,
    pub error: Option<String>,
}

impl Snapshot {
    fn initial(device_name: String) -> Self {
        Self {
            configured: true,
            connected: false,
            logged_in: false,
            is_active: false,
            device_name: Some(device_name),
            state: PlaybackState::Idle,
            track: None,
            position_ms: 0,
            duration_ms: None,
            volume: 0,
            shuffle: false,
            repeat: RepeatMode::Off,
            error: None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    Play,
    Pause,
    SkipNext,
    SkipPrevious,
    Seek(u64),
    SetVolume(u8),
    SetShuffle(bool),
    SetRepeat(RepeatMode),
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
#[error("{message}")]
pub struct ProviderError {
    pub code: &'static str,
    pub message: String,
}

impl ProviderError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn disconnected() -> Self {
        Self::new("disconnected", "Spotify Connect is not connected.")
    }
}

struct CommandEnvelope {
    command: Command,
    deadline: Instant,
    reply: oneshot::Sender<Result<Snapshot, ProviderError>>,
}

/// A single supervised librespot instance. `start` is explicit so callers can
/// create it before the rest of the app is ready without opening network or
/// audio resources during construction.
pub struct ConnectProvider {
    commands: mpsc::Sender<CommandEnvelope>,
    status: watch::Receiver<Snapshot>,
    device_name: watch::Sender<String>,
    start: watch::Sender<bool>,
    cancellation: CancellationToken,
}

impl ConnectProvider {
    pub fn new(
        config: SpotifyConnectConfig,
        cancellation: CancellationToken,
    ) -> (Self, JoinHandle<()>) {
        let provider_cancellation = cancellation.child_token();
        let (commands, command_receiver) = mpsc::channel(MAX_PENDING_COMMANDS);
        let initial = Snapshot::initial(config.device_name.clone());
        let (status_sender, status) = watch::channel(initial);
        let (device_name, device_name_receiver) = watch::channel(config.device_name.clone());
        let (start, start_receiver) = watch::channel(false);
        let task = tokio::spawn(supervise(
            config,
            command_receiver,
            status_sender,
            device_name_receiver,
            start_receiver,
            provider_cancellation.clone(),
        ));
        (
            Self {
                commands,
                status,
                device_name,
                start,
                cancellation: provider_cancellation,
            },
            task,
        )
    }

    pub fn start(&self) {
        self.start.send_replace(true);
    }

    pub fn stop(&self) {
        self.cancellation.cancel();
    }

    pub fn status(&self) -> Snapshot {
        self.status.borrow().clone()
    }

    pub fn set_device_name(&self, name: &str) -> Result<bool, ProviderError> {
        validate_device_name(name)?;
        if self.device_name.borrow().as_str() == name {
            return Ok(false);
        }
        self.device_name.send_replace(name.to_owned());
        Ok(true)
    }

    pub async fn control(&self, command: Command) -> Result<Snapshot, ProviderError> {
        let current = self.status();
        if !current.configured || !current.connected || !current.logged_in {
            return Err(ProviderError::disconnected());
        }
        if !current.is_active {
            return Err(ProviderError::new(
                "inactive",
                "Select LumiStripe in Spotify Connect before controlling playback.",
            ));
        }
        validate_command(command)?;
        let (reply, receiver) = oneshot::channel();
        self.commands
            .try_send(CommandEnvelope {
                command,
                deadline: Instant::now() + COMMAND_TIMEOUT,
                reply,
            })
            .map_err(|error| match error {
                mpsc::error::TrySendError::Full(_) => {
                    ProviderError::new("busy", "Spotify command queue is full.")
                }
                mpsc::error::TrySendError::Closed(_) => ProviderError::disconnected(),
            })?;
        match timeout(COMMAND_TIMEOUT + COMMAND_RESPONSE_GRACE, receiver).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(ProviderError::disconnected()),
            Err(_) => Err(ProviderError::new(
                "timeout",
                "Spotify command timed out; delivery may still complete.",
            )),
        }
    }
}

impl Drop for ConnectProvider {
    fn drop(&mut self) {
        self.cancellation.cancel();
    }
}

fn validate_device_name(name: &str) -> Result<(), ProviderError> {
    let length = name.chars().count();
    if length == 0 || length > 64 || name.chars().any(char::is_control) {
        return Err(ProviderError::new(
            "invalid",
            "Spotify device name must contain 1 to 64 printable characters.",
        ));
    }
    Ok(())
}

fn validate_command(command: Command) -> Result<(), ProviderError> {
    if let Command::Seek(position) = command {
        if position > u32::MAX as u64 {
            return Err(ProviderError::new(
                "invalid",
                "Seek position is outside the supported range.",
            ));
        }
    }
    Ok(())
}

async fn supervise(
    config: SpotifyConnectConfig,
    mut commands: mpsc::Receiver<CommandEnvelope>,
    status: watch::Sender<Snapshot>,
    mut device_name: watch::Receiver<String>,
    mut start: watch::Receiver<bool>,
    cancellation: CancellationToken,
) {
    while !*start.borrow() {
        tokio::select! {
            _ = cancellation.cancelled() => return,
            changed = start.changed() => if changed.is_err() { return; },
        }
    }

    let mut pending_credentials: Option<Credentials> = None;
    let mut backoff = DISCOVERY_RETRY_MIN;
    'restart_discovery: loop {
        if cancellation.is_cancelled() {
            break;
        }

        let name = device_name.borrow_and_update().clone();
        if let Err(error) = validate_device_name(&name) {
            update_status(&status, |snapshot| {
                snapshot.connected = false;
                snapshot.logged_in = false;
                snapshot.error = Some(error.message);
            });
            if !wait_to_retry(&mut device_name, &cancellation, backoff).await {
                break;
            }
            backoff = next_backoff(backoff);
            continue;
        }

        let storage = match prepare_storage(&config.data_dir) {
            Ok(storage) => storage,
            Err(()) => {
                update_status(&status, |snapshot| {
                    snapshot.connected = false;
                    snapshot.logged_in = false;
                    snapshot.error = Some("Spotify session storage is unavailable.".to_owned());
                });
                if !wait_to_retry(&mut device_name, &cancellation, backoff).await {
                    break;
                }
                backoff = next_backoff(backoff);
                continue;
            }
        };

        if pending_credentials.is_none() {
            pending_credentials = storage.cache.credentials();
        }
        let client_id = SessionConfig::default().client_id;
        let discovery = Discovery::builder(storage.device_id.clone(), client_id.clone())
            .name(name.clone())
            .device_type(DeviceType::Speaker)
            .launch();
        let mut discovery = match discovery {
            Ok(discovery) => discovery,
            Err(_) => {
                update_status(&status, |snapshot| {
                    snapshot.device_name = Some(name);
                    snapshot.connected = false;
                    snapshot.logged_in = false;
                    snapshot.error =
                        Some("Local Spotify Connect discovery is unavailable.".to_owned());
                });
                if !wait_to_retry(&mut device_name, &cancellation, backoff).await {
                    break;
                }
                backoff = next_backoff(backoff);
                continue;
            }
        };

        update_status(&status, |snapshot| {
            snapshot.device_name = Some(name.clone());
            snapshot.error = None;
        });

        'discovery: loop {
            if cancellation.is_cancelled() {
                break 'discovery;
            }

            if let Some(credentials) = pending_credentials.take() {
                let retry_credentials = credentials.clone();
                update_status(&status, |snapshot| {
                    snapshot.device_name = Some(name.clone());
                    snapshot.connected = false;
                    snapshot.logged_in = false;
                    snapshot.error = None;
                });

                let opening = open_session(
                    &config,
                    &storage,
                    &name,
                    &client_id,
                    credentials,
                    status.clone(),
                );
                tokio::pin!(opening);
                let open_result = tokio::select! {
                    _ = cancellation.cancelled() => break 'discovery,
                    changed = device_name.changed() => {
                        pending_credentials = Some(credentials_for_retry(
                            &storage.cache,
                            retry_credentials,
                        ));
                        if changed.is_ok() { break 'discovery; }
                        break 'discovery;
                    }
                    selected = discovery.next() => {
                        match selected {
                            Some(credentials) => {
                                pending_credentials = Some(credentials);
                                continue 'discovery;
                            }
                            None => break 'discovery,
                        }
                    }
                    result = &mut opening => result,
                };

                match open_result {
                    Ok(mut connected) => {
                        backoff = DISCOVERY_RETRY_MIN;
                        update_status(&status, |snapshot| {
                            snapshot.device_name = Some(name.clone());
                            snapshot.connected = true;
                            snapshot.logged_in = true;
                            snapshot.error = None;
                        });
                        let exit = run_connected_session(
                            &mut discovery,
                            &mut commands,
                            &status,
                            &mut device_name,
                            &cancellation,
                            &mut connected,
                        )
                        .await;
                        shutdown_session(connected).await;

                        match exit {
                            SessionExit::SelectedCredentials(credentials) => {
                                pending_credentials = Some(credentials);
                                backoff = DISCOVERY_RETRY_MIN;
                            }
                            SessionExit::Renamed => {
                                pending_credentials =
                                    Some(credentials_for_retry(&storage.cache, retry_credentials));
                                break 'discovery;
                            }
                            SessionExit::DiscoveryEnded => break 'discovery,
                            SessionExit::Cancelled => break 'discovery,
                            SessionExit::Lost => {
                                pending_credentials =
                                    Some(credentials_for_retry(&storage.cache, retry_credentials));
                                update_status(&status, |snapshot| {
                                    snapshot.connected = false;
                                    snapshot.logged_in = false;
                                    snapshot.is_active = false;
                                    snapshot.state = PlaybackState::Idle;
                                    snapshot.error = Some(
                                        "Spotify connection was lost; reconnecting.".to_owned(),
                                    );
                                });
                                fail_queued_commands(&mut commands, ProviderError::disconnected());
                                if !wait_for_retry_or_pairing(
                                    &mut discovery,
                                    &mut device_name,
                                    &mut pending_credentials,
                                    &cancellation,
                                    backoff,
                                )
                                .await
                                {
                                    break 'discovery;
                                }
                                if *device_name.borrow() != name {
                                    break 'discovery;
                                }
                                backoff = next_backoff(backoff);
                            }
                        }
                    }
                    Err(error) => {
                        if error.authentication_failed {
                            let _ = fs::remove_file(&storage.credentials_path);
                            pending_credentials = None;
                            update_status(&status, |snapshot| {
                                snapshot.connected = false;
                                snapshot.logged_in = false;
                                snapshot.is_active = false;
                                snapshot.error = Some("Spotify credentials expired or were revoked; select LumiStripe in Spotify Connect to pair again.".to_owned());
                            });
                        } else {
                            pending_credentials = Some(retry_credentials);
                            update_status(&status, |snapshot| {
                                snapshot.connected = false;
                                snapshot.logged_in = false;
                                snapshot.error =
                                    Some("Spotify could not connect; retrying.".to_owned());
                            });
                        }
                        fail_queued_commands(&mut commands, ProviderError::disconnected());
                        if !wait_for_retry_or_pairing(
                            &mut discovery,
                            &mut device_name,
                            &mut pending_credentials,
                            &cancellation,
                            backoff,
                        )
                        .await
                        {
                            break 'discovery;
                        }
                        if *device_name.borrow() != name {
                            break 'discovery;
                        }
                        backoff = next_backoff(backoff);
                    }
                }
            } else {
                tokio::select! {
                    _ = cancellation.cancelled() => break 'discovery,
                    _ = device_name.changed() => break 'discovery,
                    selected = discovery.next() => match selected {
                        Some(credentials) => {
                            pending_credentials = Some(credentials);
                            backoff = DISCOVERY_RETRY_MIN;
                        }
                        None => break 'discovery,
                    },
                    command = commands.recv() => {
                        match command {
                            Some(command) => {
                                let _ = command.reply.send(Err(ProviderError::disconnected()));
                            }
                            None => break 'discovery,
                        }
                    }
                }
            }
        }

        let _ = timeout(SESSION_SHUTDOWN_TIMEOUT, discovery.shutdown()).await;
        update_status(&status, |snapshot| {
            snapshot.connected = false;
            snapshot.logged_in = false;
            snapshot.is_active = false;
            snapshot.state = PlaybackState::Idle;
        });
        fail_queued_commands(&mut commands, ProviderError::disconnected());
        if cancellation.is_cancelled() {
            break 'restart_discovery;
        }
        if *device_name.borrow() != name {
            backoff = DISCOVERY_RETRY_MIN;
            continue 'restart_discovery;
        }
        if pending_credentials.is_some() {
            continue 'restart_discovery;
        }
        if !wait_to_retry(&mut device_name, &cancellation, backoff).await {
            break 'restart_discovery;
        }
        backoff = next_backoff(backoff);
    }

    fail_queued_commands(&mut commands, ProviderError::disconnected());
    update_status(&status, |snapshot| {
        snapshot.connected = false;
        snapshot.logged_in = false;
        snapshot.is_active = false;
        snapshot.state = PlaybackState::Idle;
    });
}

async fn wait_to_retry(
    device_name: &mut watch::Receiver<String>,
    cancellation: &CancellationToken,
    delay: Duration,
) -> bool {
    tokio::select! {
        _ = cancellation.cancelled() => false,
        changed = device_name.changed() => changed.is_ok(),
        _ = sleep(delay) => true,
    }
}

async fn wait_for_retry_or_pairing(
    discovery: &mut Discovery,
    device_name: &mut watch::Receiver<String>,
    pending_credentials: &mut Option<Credentials>,
    cancellation: &CancellationToken,
    delay: Duration,
) -> bool {
    tokio::select! {
        _ = cancellation.cancelled() => false,
        changed = device_name.changed() => changed.is_ok(),
        selected = discovery.next() => {
            match selected {
                Some(credentials) => {
                    *pending_credentials = Some(credentials);
                    true
                }
                None => false,
            }
        }
        _ = sleep(delay) => true,
    }
}

fn next_backoff(current: Duration) -> Duration {
    current.saturating_mul(2).min(DISCOVERY_RETRY_MAX)
}

fn credentials_for_retry(cache: &Cache, fallback: Credentials) -> Credentials {
    cache.credentials().unwrap_or(fallback)
}

struct Storage {
    device_id: String,
    cache: Cache,
    credentials_path: PathBuf,
}

fn prepare_storage(data_dir: &Path) -> Result<Storage, ()> {
    fs::create_dir_all(data_dir).map_err(|_| ())?;
    let directory = fs::symlink_metadata(data_dir).map_err(|_| ())?;
    if !directory.is_dir() || directory.file_type().is_symlink() {
        return Err(());
    }

    #[cfg(unix)]
    {
        use std::os::unix::{fs::MetadataExt, fs::PermissionsExt};
        if directory.uid() != unsafe { libc::geteuid() } {
            return Err(());
        }
        fs::set_permissions(data_dir, fs::Permissions::from_mode(0o700)).map_err(|_| ())?;
    }

    let credentials_path = data_dir.join("credentials.json");
    let volume_path = data_dir.join("volume");
    let device_id_path = data_dir.join("device-id");
    ensure_private_file(&credentials_path)?;
    ensure_private_file(&volume_path)?;
    let device_id = load_or_create_device_id(&device_id_path)?;
    let audio_cache_path = data_dir.join("audio-cache");
    fs::create_dir_all(&audio_cache_path).map_err(|_| ())?;
    let audio_cache_metadata = fs::symlink_metadata(&audio_cache_path).map_err(|_| ())?;
    if !audio_cache_metadata.is_dir() || audio_cache_metadata.file_type().is_symlink() {
        return Err(());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&audio_cache_path, fs::Permissions::from_mode(0o700))
            .map_err(|_| ())?;
    }

    let cache = Cache::new(
        Some(data_dir),
        Some(data_dir),
        Some(audio_cache_path.as_path()),
        Some(AUDIO_CACHE_LIMIT_BYTES),
    )
    .map_err(|_| ())?;
    Ok(Storage {
        device_id,
        cache,
        credentials_path,
    })
}

fn ensure_private_file(path: &Path) -> Result<(), ()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
        let mut options = OpenOptions::new();
        options.write(true).create_new(true).mode(0o600);
        options.custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
        match options.open(path) {
            Ok(_) => return Ok(()),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err(()),
        }
        let metadata = fs::symlink_metadata(path).map_err(|_| ())?;
        if !metadata.is_file()
            || metadata.file_type().is_symlink()
            || metadata.uid() != unsafe { libc::geteuid() }
            || metadata.nlink() != 1
        {
            return Err(());
        }
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).map_err(|_| ())?;
        Ok(())
    }
    #[cfg(not(unix))]
    {
        match OpenOptions::new().write(true).create_new(true).open(path) {
            Ok(_) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => Ok(()),
            Err(_) => Err(()),
        }
    }
}

fn load_or_create_device_id(path: &Path) -> Result<String, ()> {
    ensure_private_file(path)?;
    let metadata = fs::symlink_metadata(path).map_err(|_| ())?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(());
    }
    let value = fs::read_to_string(path).map_err(|_| ())?;
    if let Ok(id) = Uuid::parse_str(value.trim()) {
        return Ok(id.hyphenated().to_string());
    }
    if !value.is_empty() {
        return Err(());
    }

    let id = Uuid::new_v4().hyphenated().to_string();
    let mut options = OpenOptions::new();
    options.write(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW);
    }
    use std::io::Write;
    options
        .open(path)
        .map_err(|_| ())?
        .write_all(id.as_bytes())
        .map_err(|_| ())?;
    Ok(id)
}

#[derive(Debug)]
struct OpenSessionError {
    authentication_failed: bool,
}

async fn open_session(
    config: &SpotifyConnectConfig,
    storage: &Storage,
    device_name: &str,
    client_id: &str,
    credentials: Credentials,
    status: watch::Sender<Snapshot>,
) -> Result<ConnectedSession, OpenSessionError> {
    let session_config = SessionConfig {
        device_id: storage.device_id.clone(),
        client_id: client_id.to_owned(),
        ..SessionConfig::default()
    };
    let session = Session::new(session_config, Some(storage.cache.clone()));
    let Some(mixer_builder) = mixer::find(None) else {
        return Err(OpenSessionError {
            authentication_failed: false,
        });
    };
    let mixer = mixer_builder(MixerConfig::default()).map_err(|_| OpenSessionError {
        authentication_failed: false,
    })?;
    let Some(sink_builder) = audio_backend::find(Some(PULSEAUDIO_BACKEND.to_owned())) else {
        return Err(OpenSessionError {
            authentication_failed: false,
        });
    };
    let device = config.audio_sink_name.clone();
    let player = Player::new(
        PlayerConfig {
            position_update_interval: Some(POSITION_UPDATE_INTERVAL),
            ..PlayerConfig::default()
        },
        session.clone(),
        mixer.get_soft_volume(),
        move || sink_builder(Some(device), AudioFormat::S16),
    );
    let events = player.get_player_event_channel();
    let sink_status = status.clone();
    player.set_sink_event_callback(Some(Box::new(move |state| {
        if state == SinkStatus::TemporarilyClosed {
            update_status(&sink_status, |snapshot| {
                snapshot.error = Some(
                    "Spotify audio output is unavailable; check the PipeWire Spotify sink."
                        .to_owned(),
                );
            });
        } else if state == SinkStatus::Running {
            update_status(&sink_status, |snapshot| {
                if snapshot
                    .error
                    .as_deref()
                    .is_some_and(|error| error.starts_with("Spotify audio output is unavailable"))
                {
                    snapshot.error = None;
                }
            });
        }
    })));

    let connect_config = ConnectConfig {
        name: device_name.to_owned(),
        device_type: DeviceType::Speaker,
        ..ConnectConfig::default()
    };
    let connecting = Spirc::new(connect_config, session.clone(), credentials, player, mixer);
    let (spirc, spirc_task) = match timeout(SESSION_CONNECT_TIMEOUT, connecting).await {
        Ok(Ok(result)) => result,
        Ok(Err(error)) => {
            let authentication_failed = matches!(
                error.kind,
                ErrorKind::PermissionDenied | ErrorKind::Unauthenticated
            );
            tracing::warn!(kind = ?error.kind, "librespot session setup failed");
            return Err(OpenSessionError {
                authentication_failed,
            });
        }
        Err(_) => {
            tracing::warn!("librespot session setup timed out");
            return Err(OpenSessionError {
                authentication_failed: false,
            });
        }
    };
    let task = tokio::spawn(spirc_task);
    Ok(ConnectedSession {
        spirc,
        task: Some(task),
        events,
        session,
    })
}

struct ConnectedSession {
    spirc: Spirc,
    task: Option<JoinHandle<()>>,
    events: PlayerEventChannel,
    session: Session,
}

#[derive(Debug)]
enum SessionExit {
    SelectedCredentials(Credentials),
    Renamed,
    DiscoveryEnded,
    Cancelled,
    Lost,
}

struct PendingControl {
    expected: Confirmation,
    deadline: Instant,
    reply: oneshot::Sender<Result<Snapshot, ProviderError>>,
}

#[derive(Clone, Copy, Debug)]
enum Confirmation {
    Playing,
    Paused,
    TrackChangedAfter(u64),
    PreviousTrack { sequence: u64, position_ms: u64 },
    SeekNear(u32),
    Volume(u8),
    Shuffle(bool),
    Repeat(RepeatMode),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Observation {
    Playback(PlaybackState),
    TrackChanged,
    Seeked(u64),
    Volume(u8),
    Shuffle(bool),
    Repeat(RepeatMode),
}

async fn run_connected_session(
    discovery: &mut Discovery,
    commands: &mut mpsc::Receiver<CommandEnvelope>,
    status: &watch::Sender<Snapshot>,
    device_name: &mut watch::Receiver<String>,
    cancellation: &CancellationToken,
    connected: &mut ConnectedSession,
) -> SessionExit {
    let mut pending: Option<PendingControl> = None;
    let mut track_sequence = 0u64;
    loop {
        let deadline = pending.as_ref().map(|pending| pending.deadline);
        tokio::select! {
            _ = cancellation.cancelled() => {
                fail_pending(&mut pending, ProviderError::disconnected());
                return SessionExit::Cancelled;
            }
            changed = device_name.changed() => {
                fail_pending(&mut pending, ProviderError::disconnected());
                if changed.is_ok() {
                    return SessionExit::Renamed;
                }
                return SessionExit::Cancelled;
            }
            selected = discovery.next() => {
                fail_pending(&mut pending, ProviderError::disconnected());
                return match selected {
                    Some(credentials) => SessionExit::SelectedCredentials(credentials),
                    None => SessionExit::DiscoveryEnded,
                };
            }
            _ = async move {
                if let Some(deadline) = deadline {
                    sleep_until(deadline).await;
                } else {
                    std::future::pending::<()>().await;
                }
            } => {
                if let Some(pending) = pending.take() {
                    let _ = pending.reply.send(Err(ProviderError::new(
                        "timeout",
                        "Spotify command timed out; delivery may still complete.",
                    )));
                }
            }
            command = commands.recv(), if pending.is_none() => {
                let Some(command) = command else {
                    return SessionExit::Cancelled;
                };
                if command.reply.is_closed() {
                    continue;
                }
                if Instant::now() >= command.deadline {
                    let _ = command.reply.send(Err(ProviderError::new(
                        "timeout",
                        "Spotify command timed out before delivery.",
                    )));
                    continue;
                }
                let snapshot = status.borrow().clone();
                match dispatch_command(&connected.spirc, command.command, &snapshot, track_sequence) {
                    Ok(expected) => pending = Some(PendingControl {
                        expected,
                        deadline: command.deadline,
                        reply: command.reply,
                    }),
                    Err(error) => {
                        let _ = command.reply.send(Err(error));
                    }
                }
            }
            event = connected.events.recv() => {
                let Some(event) = event else {
                    fail_pending(&mut pending, ProviderError::disconnected());
                    return SessionExit::Lost;
                };
                let mut snapshot = status.borrow().clone();
                let observation = apply_player_event(&mut snapshot, event, &mut track_sequence);
                status.send_replace(snapshot.clone());
                if let (Some(pending_control), Some(observation)) = (pending.as_ref(), observation) {
                    if confirmation_matches(pending_control.expected, observation, track_sequence) {
                        if let Some(pending_control) = pending.take() {
                            let _ = pending_control.reply.send(Ok(snapshot));
                        }
                    }
                }
            }
            result = async {
                match connected.task.as_mut() {
                    Some(task) => Some(task.await),
                    None => std::future::pending().await,
                }
            } => {
                if result.is_some() {
                    connected.task.take();
                }
                fail_pending(&mut pending, ProviderError::disconnected());
                return SessionExit::Lost;
            }
        }
    }
}

fn dispatch_command(
    spirc: &Spirc,
    command: Command,
    snapshot: &Snapshot,
    track_sequence: u64,
) -> Result<Confirmation, ProviderError> {
    let result = match command {
        Command::Play => {
            spirc.play().map_err(|_| ProviderError::disconnected())?;
            Confirmation::Playing
        }
        Command::Pause => {
            spirc.pause().map_err(|_| ProviderError::disconnected())?;
            Confirmation::Paused
        }
        Command::SkipNext => {
            spirc.next().map_err(|_| ProviderError::disconnected())?;
            Confirmation::TrackChangedAfter(track_sequence)
        }
        Command::SkipPrevious => {
            spirc.prev().map_err(|_| ProviderError::disconnected())?;
            Confirmation::PreviousTrack {
                sequence: track_sequence,
                position_ms: snapshot.position_ms,
            }
        }
        Command::Seek(position) => {
            let position = u32::try_from(position).map_err(|_| {
                ProviderError::new("invalid", "Seek position is outside the supported range.")
            })?;
            if snapshot
                .duration_ms
                .is_some_and(|duration| position as u64 > duration)
            {
                return Err(ProviderError::new(
                    "invalid",
                    "Seek position is past the end of the current track.",
                ));
            }
            spirc
                .set_position_ms(position)
                .map_err(|_| ProviderError::disconnected())?;
            Confirmation::SeekNear(position)
        }
        Command::SetVolume(volume) => {
            let scaled = ((u32::from(volume) * u32::from(u16::MAX) + 50) / 100) as u16;
            spirc
                .set_volume(scaled)
                .map_err(|_| ProviderError::disconnected())?;
            Confirmation::Volume(volume)
        }
        Command::SetShuffle(enabled) => {
            spirc
                .shuffle(enabled)
                .map_err(|_| ProviderError::disconnected())?;
            Confirmation::Shuffle(enabled)
        }
        Command::SetRepeat(mode) => {
            spirc
                .repeat(mode == RepeatMode::Context)
                .map_err(|_| ProviderError::disconnected())?;
            spirc
                .repeat_track(mode == RepeatMode::Track)
                .map_err(|_| ProviderError::disconnected())?;
            Confirmation::Repeat(mode)
        }
    };
    Ok(result)
}

fn confirmation_matches(
    expected: Confirmation,
    observed: Observation,
    track_sequence: u64,
) -> bool {
    match (expected, observed) {
        (Confirmation::Playing, Observation::Playback(PlaybackState::Playing))
        | (Confirmation::Paused, Observation::Playback(PlaybackState::Paused)) => true,
        (Confirmation::TrackChangedAfter(sequence), Observation::TrackChanged) => {
            track_sequence > sequence
        }
        (Confirmation::PreviousTrack { sequence, .. }, Observation::TrackChanged) => {
            track_sequence > sequence
        }
        (Confirmation::PreviousTrack { position_ms, .. }, Observation::Seeked(new_position)) => {
            position_ms > 3_000 && new_position <= 3_000
        }
        (Confirmation::SeekNear(target), Observation::Seeked(position)) => {
            position.abs_diff(target as u64) <= 1_000
        }
        (Confirmation::Volume(target), Observation::Volume(volume)) => target == volume,
        (Confirmation::Shuffle(target), Observation::Shuffle(shuffle)) => target == shuffle,
        (Confirmation::Repeat(target), Observation::Repeat(repeat)) => target == repeat,
        _ => false,
    }
}

fn apply_player_event(
    snapshot: &mut Snapshot,
    event: PlayerEvent,
    track_sequence: &mut u64,
) -> Option<Observation> {
    match event {
        PlayerEvent::Loading { position_ms, .. } => {
            snapshot.state = PlaybackState::Buffering;
            snapshot.position_ms = u64::from(position_ms);
            Some(Observation::Playback(PlaybackState::Buffering))
        }
        PlayerEvent::Playing { position_ms, .. } => {
            snapshot.state = PlaybackState::Playing;
            snapshot.position_ms = u64::from(position_ms);
            Some(Observation::Playback(PlaybackState::Playing))
        }
        PlayerEvent::Paused { position_ms, .. } => {
            snapshot.state = PlaybackState::Paused;
            snapshot.position_ms = u64::from(position_ms);
            Some(Observation::Playback(PlaybackState::Paused))
        }
        PlayerEvent::Stopped { .. } => {
            snapshot.state = PlaybackState::Idle;
            snapshot.position_ms = 0;
            Some(Observation::Playback(PlaybackState::Idle))
        }
        PlayerEvent::TrackChanged { audio_item } => {
            let (artists, album) = match &audio_item.unique_fields {
                UniqueFields::Track { artists, album, .. } => (
                    artists.iter().map(|artist| artist.name.clone()).collect(),
                    Some(album.clone()),
                ),
                UniqueFields::Local { artists, album, .. } => {
                    (artists.iter().cloned().collect(), album.clone())
                }
                UniqueFields::Episode { show_name, .. } => {
                    (vec![show_name.clone()], Some(show_name.clone()))
                }
            };
            snapshot.duration_ms = Some(u64::from(audio_item.duration_ms));
            snapshot.track = Some(Track {
                uri: audio_item.uri.clone(),
                name: audio_item.name.clone(),
                artists,
                album,
                cover_url: audio_item.covers.first().map(|cover| cover.url.clone()),
                duration_ms: Some(u64::from(audio_item.duration_ms)),
            });
            *track_sequence = track_sequence.saturating_add(1);
            Some(Observation::TrackChanged)
        }
        PlayerEvent::PositionChanged { position_ms, .. }
        | PlayerEvent::PositionCorrection { position_ms, .. } => {
            snapshot.position_ms = u64::from(position_ms);
            None
        }
        PlayerEvent::Seeked { position_ms, .. } => {
            snapshot.position_ms = u64::from(position_ms);
            Some(Observation::Seeked(u64::from(position_ms)))
        }
        PlayerEvent::VolumeChanged { volume } => {
            snapshot.volume = volume_to_percent(volume);
            Some(Observation::Volume(snapshot.volume))
        }
        PlayerEvent::ShuffleChanged { shuffle } => {
            snapshot.shuffle = shuffle;
            Some(Observation::Shuffle(shuffle))
        }
        PlayerEvent::RepeatChanged { context, track } => {
            snapshot.repeat = repeat_mode(context, track);
            Some(Observation::Repeat(snapshot.repeat))
        }
        PlayerEvent::SessionConnected { .. } => {
            snapshot.is_active = true;
            None
        }
        PlayerEvent::SessionDisconnected { .. } => {
            snapshot.is_active = false;
            None
        }
        PlayerEvent::Unavailable { .. }
        | PlayerEvent::Preloading { .. }
        | PlayerEvent::TimeToPreloadNextTrack { .. }
        | PlayerEvent::EndOfTrack { .. }
        | PlayerEvent::PlayRequestIdChanged { .. }
        | PlayerEvent::SessionClientChanged { .. }
        | PlayerEvent::AutoPlayChanged { .. }
        | PlayerEvent::FilterExplicitContentChanged { .. } => None,
    }
}

fn repeat_mode(context: bool, track: bool) -> RepeatMode {
    if track {
        RepeatMode::Track
    } else if context {
        RepeatMode::Context
    } else {
        RepeatMode::Off
    }
}

fn volume_to_percent(volume: u16) -> u8 {
    ((u32::from(volume) * 100 + u32::from(u16::MAX) / 2) / u32::from(u16::MAX)) as u8
}

fn fail_pending(pending: &mut Option<PendingControl>, error: ProviderError) {
    if let Some(pending) = pending.take() {
        let _ = pending.reply.send(Err(error));
    }
}

fn fail_queued_commands(commands: &mut mpsc::Receiver<CommandEnvelope>, error: ProviderError) {
    while let Ok(command) = commands.try_recv() {
        let _ = command.reply.send(Err(error.clone()));
    }
}

fn update_status(status: &watch::Sender<Snapshot>, update: impl FnOnce(&mut Snapshot)) {
    status.send_modify(update);
}

async fn shutdown_session(mut connected: ConnectedSession) {
    let _ = connected.spirc.shutdown();
    if let Some(mut task) = connected.task.take() {
        if timeout(SESSION_SHUTDOWN_TIMEOUT, &mut task).await.is_err() {
            task.abort();
            let _ = task.await;
        }
    }
    connected.session.shutdown();
}

#[cfg(test)]
mod tests {
    use super::{
        AUDIO_CACHE_LIMIT_BYTES, Command, Confirmation, Observation, PlaybackState, RepeatMode,
        SPOTIFY_PIPEWIRE_SINK, Snapshot, SpotifyConnectConfig, apply_player_event,
        confirmation_matches, credentials_for_retry, ensure_private_file, next_backoff,
        prepare_storage, repeat_mode, validate_command, validate_device_name, volume_to_percent,
    };
    use librespot::{
        core::{SpotifyUri, authentication::Credentials},
        metadata::{
            artist::{ArtistRole, ArtistWithRole, ArtistsWithRole},
            audio::{AudioFiles, AudioItem, UniqueFields, item::CoverImage},
            image::ImageSize,
        },
        playback::player::PlayerEvent,
    };
    use std::{fs, os::unix::fs::PermissionsExt, time::Duration};
    use tempfile::tempdir;
    use tokio_util::sync::CancellationToken;

    #[test]
    fn local_storage_persists_device_identity_and_restricts_permissions() {
        let directory = tempdir().unwrap();
        let data_dir = directory.path().join("spotify");
        let first = prepare_storage(&data_dir).unwrap();
        let second = prepare_storage(&data_dir).unwrap();
        assert_eq!(first.device_id, second.device_id);
        assert_eq!(
            fs::metadata(&data_dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(data_dir.join("credentials.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(data_dir.join("device-id"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(AUDIO_CACHE_LIMIT_BYTES, 64 * 1024 * 1024);
    }

    #[test]
    fn credentials_are_loaded_from_the_private_cache() {
        let directory = tempdir().unwrap();
        let storage = prepare_storage(&directory.path().join("spotify")).unwrap();
        let credentials = Credentials::with_password("test-user", "synthetic-password");
        storage.cache.save_credentials(&credentials);
        assert_eq!(storage.cache.credentials(), Some(credentials));
    }

    #[test]
    fn session_retries_prefer_cached_credentials_and_keep_new_pairings() {
        let directory = tempdir().unwrap();
        let storage = prepare_storage(&directory.path().join("spotify")).unwrap();
        let cached = Credentials::with_password("cached-user", "cached-secret");
        let newly_selected = Credentials::with_password("paired-user", "paired-secret");
        storage.cache.save_credentials(&cached);
        assert_eq!(
            credentials_for_retry(&storage.cache, newly_selected.clone()),
            cached
        );

        let another_pairing = Credentials::with_password("new-user", "new-secret");
        let empty_storage = prepare_storage(&directory.path().join("empty")).unwrap();
        assert_eq!(
            credentials_for_retry(&empty_storage.cache, another_pairing.clone()),
            another_pairing
        );
    }

    #[test]
    fn rejects_symlink_credentials_files() {
        let directory = tempdir().unwrap();
        let target = directory.path().join("target");
        fs::write(&target, "").unwrap();
        let path = directory.path().join("credentials.json");
        std::os::unix::fs::symlink(&target, &path).unwrap();
        assert!(ensure_private_file(&path).is_err());
    }

    #[test]
    fn validates_device_names_and_seek_range() {
        assert!(validate_device_name("LumiStripe").is_ok());
        assert!(validate_device_name("").is_err());
        assert!(validate_device_name(&"x".repeat(65)).is_err());
        assert!(validate_device_name("name\nother").is_err());
        assert!(validate_command(Command::Seek(u32::MAX as u64)).is_ok());
        assert!(validate_command(Command::Seek(u32::MAX as u64 + 1)).is_err());
    }

    #[test]
    fn maps_player_events_and_track_metadata_to_existing_status_values() {
        let mut status = Snapshot::initial("LumiStripe".to_owned());
        let track_uri = SpotifyUri::from_uri("spotify:track:0123456789012345678901").unwrap();
        let artist_uri = SpotifyUri::from_uri("spotify:artist:0123456789012345678901").unwrap();
        let audio_item = AudioItem {
            track_id: track_uri.clone(),
            uri: "spotify:track:0123456789012345678901".to_owned(),
            files: AudioFiles::default(),
            name: "Example track".to_owned(),
            covers: vec![CoverImage {
                url: "https://images.example/cover.jpg".to_owned(),
                size: ImageSize::LARGE,
                width: 640,
                height: 640,
            }],
            language: Vec::new(),
            duration_ms: 180_000,
            is_explicit: false,
            availability: Ok(()),
            alternatives: None,
            unique_fields: UniqueFields::Track {
                artists: ArtistsWithRole(vec![ArtistWithRole {
                    id: artist_uri,
                    name: "Example artist".to_owned(),
                    role: ArtistRole::ARTIST_ROLE_MAIN_ARTIST,
                }]),
                album: "Example album".to_owned(),
                album_artists: vec!["Example artist".to_owned()],
                popularity: 50,
                number: 1,
                disc_number: 1,
            },
        };
        let mut sequence = 0;

        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::TrackChanged {
                    audio_item: Box::new(audio_item),
                },
                &mut sequence,
            ),
            Some(Observation::TrackChanged)
        );
        assert_eq!(sequence, 1);
        let track = status.track.as_ref().unwrap();
        assert_eq!(track.uri, "spotify:track:0123456789012345678901");
        assert_eq!(track.name, "Example track");
        assert_eq!(track.artists, ["Example artist"]);
        assert_eq!(track.album.as_deref(), Some("Example album"));
        assert_eq!(
            track.cover_url.as_deref(),
            Some("https://images.example/cover.jpg")
        );
        assert_eq!(track.duration_ms, Some(180_000));
        assert_eq!(status.duration_ms, Some(180_000));

        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::Playing {
                    play_request_id: 1,
                    track_id: track_uri.clone(),
                    position_ms: 3_000,
                },
                &mut sequence,
            ),
            Some(Observation::Playback(PlaybackState::Playing))
        );
        assert_eq!(status.position_ms, 3_000);
        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::Seeked {
                    play_request_id: 1,
                    track_id: track_uri.clone(),
                    position_ms: 8_000,
                },
                &mut sequence,
            ),
            Some(Observation::Seeked(8_000))
        );
        assert_eq!(status.position_ms, 8_000);
        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::RepeatChanged {
                    context: true,
                    track: false,
                },
                &mut sequence,
            ),
            Some(Observation::Repeat(RepeatMode::Context))
        );
        assert_eq!(status.repeat, RepeatMode::Context);
        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::RepeatChanged {
                    context: true,
                    track: true,
                },
                &mut sequence,
            ),
            Some(Observation::Repeat(RepeatMode::Track))
        );
        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::ShuffleChanged { shuffle: true },
                &mut sequence,
            ),
            Some(Observation::Shuffle(true))
        );
        assert_eq!(
            apply_player_event(
                &mut status,
                PlayerEvent::VolumeChanged { volume: u16::MAX },
                &mut sequence,
            ),
            Some(Observation::Volume(100))
        );
        assert_eq!(status.volume, 100);

        apply_player_event(
            &mut status,
            PlayerEvent::SessionConnected {
                connection_id: "connection".to_owned(),
                user_name: "listener".to_owned(),
            },
            &mut sequence,
        );
        assert!(status.is_active);
        apply_player_event(
            &mut status,
            PlayerEvent::SessionDisconnected {
                connection_id: "connection".to_owned(),
                user_name: "listener".to_owned(),
            },
            &mut sequence,
        );
        assert!(!status.is_active);
        apply_player_event(
            &mut status,
            PlayerEvent::Stopped {
                play_request_id: 1,
                track_id: track_uri,
            },
            &mut sequence,
        );
        assert_eq!(status.state, PlaybackState::Idle);
        assert_eq!(status.position_ms, 0);
    }

    #[test]
    fn command_confirmation_requires_matching_provider_events() {
        assert!(confirmation_matches(
            Confirmation::Playing,
            Observation::Playback(PlaybackState::Playing),
            0,
        ));
        assert!(!confirmation_matches(
            Confirmation::Paused,
            Observation::Playback(PlaybackState::Playing),
            0,
        ));
        assert!(confirmation_matches(
            Confirmation::TrackChangedAfter(4),
            Observation::TrackChanged,
            5,
        ));
        assert!(!confirmation_matches(
            Confirmation::TrackChangedAfter(5),
            Observation::TrackChanged,
            5,
        ));
        assert!(confirmation_matches(
            Confirmation::SeekNear(10_000),
            Observation::Seeked(10_500),
            0,
        ));
        assert!(confirmation_matches(
            Confirmation::PreviousTrack {
                sequence: 0,
                position_ms: 8_000,
            },
            Observation::Seeked(0),
            0,
        ));
    }

    #[test]
    fn pipewire_sink_selection_is_explicit_and_pulseaudio_backed() {
        assert_eq!(SPOTIFY_PIPEWIRE_SINK, "lumistripe_spotify");
        assert!(librespot::playback::audio_backend::find(Some("pulseaudio".to_owned())).is_some());
    }

    #[test]
    fn retry_backoff_is_exponential_and_bounded() {
        assert_eq!(
            next_backoff(Duration::from_millis(500)),
            Duration::from_secs(1)
        );
        assert_eq!(
            next_backoff(Duration::from_secs(30)),
            Duration::from_secs(30)
        );
    }

    #[tokio::test]
    async fn provider_can_be_cancelled_without_opening_discovery() {
        let directory = tempdir().unwrap();
        let cancellation = CancellationToken::new();
        let (provider, task) = super::ConnectProvider::new(
            SpotifyConnectConfig::new(directory.path(), "LumiStripe"),
            cancellation,
        );
        provider.stop();
        tokio::time::timeout(Duration::from_secs(1), task)
            .await
            .unwrap()
            .unwrap();
    }

    #[test]
    fn volume_and_repeat_conversions_cover_supported_boundaries() {
        assert_eq!(volume_to_percent(0), 0);
        assert_eq!(volume_to_percent(u16::MAX), 100);
        assert_eq!(repeat_mode(false, false), RepeatMode::Off);
        assert_eq!(repeat_mode(true, false), RepeatMode::Context);
        assert_eq!(repeat_mode(false, true), RepeatMode::Track);
        let _ = SpotifyConnectConfig::new("/tmp/spotify", "LumiStripe");
    }
}
