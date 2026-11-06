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
        NUM_CHANNELS, SAMPLE_RATE,
        audio_backend::{self, Sink, SinkResult},
        config::{AudioFormat, PlayerConfig},
        convert::Converter,
        decoder::AudioPacket,
        mixer::{self, MixerConfig},
        player::{Player, PlayerEvent, PlayerEventChannel, SinkStatus},
    },
};
use std::{
    fmt,
    fs::{self, OpenOptions},
    io,
    panic::{AssertUnwindSafe, catch_unwind},
    path::{Path, PathBuf},
    sync::Arc,
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

/// Provider playback state used by the engine's direct-input fallback policy.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DecodedPcmPlaybackState {
    Idle,
    Loading,
    Playing,
    Paused,
    Failed,
}

/// Bounded handoff for decoded PCM; implementations must return promptly.
pub trait DecodedPcmTap: Send + Sync + fmt::Debug {
    fn stream_generation(&self) -> u64;
    fn begin_generation(&self);
    fn set_playback_state(&self, state: DecodedPcmPlaybackState);
    fn submit_interleaved_f64(
        &self,
        samples: &[f64],
        sample_rate: u32,
        channels: u16,
        stream_generation: u64,
        sample_position: u64,
    );
    fn record_unsupported_packet(&self);
}

#[derive(Clone)]
pub struct SpotifyConnectConfig {
    pub data_dir: PathBuf,
    pub device_name: String,
    pub audio_sink_name: String,
    pub pcm_tap: Option<Arc<dyn DecodedPcmTap>>,
}

impl fmt::Debug for SpotifyConnectConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SpotifyConnectConfig")
            .field("data_dir", &self.data_dir)
            .field("device_name", &self.device_name)
            .field("audio_sink_name", &self.audio_sink_name)
            .field("pcm_tap_enabled", &self.pcm_tap.is_some())
            .finish()
    }
}

impl SpotifyConnectConfig {
    pub fn new(data_dir: impl Into<PathBuf>, device_name: impl Into<String>) -> Self {
        Self {
            data_dir: data_dir.into(),
            device_name: device_name.into(),
            audio_sink_name: SPOTIFY_PIPEWIRE_SINK.to_owned(),
            pcm_tap: None,
        }
    }

    pub fn with_pcm_tap(mut self, pcm_tap: Arc<dyn DecodedPcmTap>) -> Self {
        self.pcm_tap = Some(pcm_tap);
        self
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
            snapshot.error = pending_credentials.is_none().then(|| {
                "Spotify authentication is required; select LumiStripe in Spotify Connect to pair."
                    .to_owned()
            });
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

struct DecodedPcmSink {
    output: Box<dyn Sink>,
    tap: Arc<dyn DecodedPcmTap>,
    generation: u64,
    sample_position: u64,
    tap_failed: bool,
}

impl DecodedPcmSink {
    fn new(output: Box<dyn Sink>, tap: Arc<dyn DecodedPcmTap>) -> Self {
        let mut sink = Self {
            output,
            tap,
            generation: 0,
            sample_position: 0,
            tap_failed: false,
        };
        if sink.run_tap(|tap| tap.begin_generation()).is_none() {
            return sink;
        }
        if let Some(generation) = sink.run_tap(|tap| tap.stream_generation()) {
            sink.generation = generation;
        }
        sink
    }

    fn run_tap<R>(&mut self, operation: impl FnOnce(&dyn DecodedPcmTap) -> R) -> Option<R> {
        if self.tap_failed {
            return None;
        }
        match catch_unwind(AssertUnwindSafe(|| operation(self.tap.as_ref()))) {
            Ok(value) => Some(value),
            Err(_) => {
                self.mark_tap_failed();
                None
            }
        }
    }

    fn mark_tap_failed(&mut self) {
        if self.tap_failed {
            return;
        }
        self.tap_failed = true;
        let _ = catch_unwind(AssertUnwindSafe(|| {
            self.tap.set_playback_state(DecodedPcmPlaybackState::Failed)
        }));
        tracing::warn!("Spotify decoded PCM analysis tap failed; speaker playback continues");
    }
}

impl Sink for DecodedPcmSink {
    fn start(&mut self) -> SinkResult<()> {
        let result = self.output.start();
        if result.is_err() {
            self.mark_tap_failed();
        }
        result
    }

    fn stop(&mut self) -> SinkResult<()> {
        let result = self.output.stop();
        if result.is_err() {
            self.mark_tap_failed();
        }
        result
    }

    fn write(&mut self, packet: AudioPacket, converter: &mut Converter) -> SinkResult<()> {
        let mut next_generation = self.generation;
        let mut next_position = self.sample_position;
        let tap_result = self.run_tap(|tap| match &packet {
            AudioPacket::Samples(samples) => {
                let channel_count = usize::from(NUM_CHANNELS);
                if samples.len() % channel_count == 0 {
                    let frame_count = samples.len() / channel_count;
                    let generation = tap.stream_generation();
                    if generation != next_generation {
                        next_generation = generation;
                        next_position = 0;
                    }
                    tap.submit_interleaved_f64(
                        samples,
                        SAMPLE_RATE,
                        u16::from(NUM_CHANNELS),
                        generation,
                        next_position,
                    );
                    if let Some(position) = next_position.checked_add(frame_count as u64) {
                        next_position = position;
                    } else {
                        tap.record_unsupported_packet();
                        return Err(());
                    }
                } else {
                    tap.record_unsupported_packet();
                }
                Ok((next_generation, next_position))
            }
            AudioPacket::Raw(_) => {
                tap.record_unsupported_packet();
                Ok((next_generation, next_position))
            }
        });
        match tap_result {
            Some(Ok((generation, position))) => {
                self.generation = generation;
                self.sample_position = position;
            }
            Some(Err(())) => self.mark_tap_failed(),
            None => {}
        }
        let result = self.output.write(packet, converter);
        if result.is_err() {
            self.mark_tap_failed();
        }
        result
    }
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
    let pcm_tap = config.pcm_tap.clone();
    let player = Player::new(
        PlayerConfig {
            position_update_interval: Some(POSITION_UPDATE_INTERVAL),
            ..PlayerConfig::default()
        },
        session.clone(),
        mixer.get_soft_volume(),
        move || {
            let output = sink_builder(Some(device), AudioFormat::S16);
            match pcm_tap {
                Some(tap) => Box::new(DecodedPcmSink::new(output, tap)) as Box<dyn Sink>,
                None => output,
            }
        },
    );
    let events = player.get_player_event_channel();
    let sink_status = status.clone();
    let pcm_status = config.pcm_tap.clone();
    player.set_sink_event_callback(Some(Box::new(move |state| {
        if state == SinkStatus::TemporarilyClosed {
            if let Some(pcm_tap) = &pcm_status {
                pcm_tap.set_playback_state(DecodedPcmPlaybackState::Failed);
            }
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
        pcm_tap: config.pcm_tap.clone(),
    })
}

struct ConnectedSession {
    spirc: Spirc,
    task: Option<JoinHandle<()>>,
    events: PlayerEventChannel,
    session: Session,
    pcm_tap: Option<Arc<dyn DecodedPcmTap>>,
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
                update_decoded_pcm_state(connected.pcm_tap.as_deref(), &event);
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

fn update_decoded_pcm_state(tap: Option<&dyn DecodedPcmTap>, event: &PlayerEvent) {
    let Some(tap) = tap else {
        return;
    };
    match event {
        PlayerEvent::Loading { .. } => {
            tap.begin_generation();
            tap.set_playback_state(DecodedPcmPlaybackState::Loading);
        }
        PlayerEvent::Playing { .. } => {
            tap.set_playback_state(DecodedPcmPlaybackState::Playing);
        }
        PlayerEvent::Paused { .. } => {
            tap.set_playback_state(DecodedPcmPlaybackState::Paused);
        }
        PlayerEvent::Stopped { .. } => {
            tap.begin_generation();
            tap.set_playback_state(DecodedPcmPlaybackState::Idle);
        }
        PlayerEvent::TrackChanged { .. } | PlayerEvent::Seeked { .. } => {
            tap.begin_generation();
        }
        PlayerEvent::SessionDisconnected { .. } => {
            tap.begin_generation();
            tap.set_playback_state(DecodedPcmPlaybackState::Idle);
        }
        PlayerEvent::Unavailable { .. }
        | PlayerEvent::Preloading { .. }
        | PlayerEvent::TimeToPreloadNextTrack { .. }
        | PlayerEvent::EndOfTrack { .. }
        | PlayerEvent::PositionChanged { .. }
        | PlayerEvent::PositionCorrection { .. }
        | PlayerEvent::VolumeChanged { .. }
        | PlayerEvent::ShuffleChanged { .. }
        | PlayerEvent::RepeatChanged { .. }
        | PlayerEvent::SessionConnected { .. }
        | PlayerEvent::PlayRequestIdChanged { .. }
        | PlayerEvent::SessionClientChanged { .. }
        | PlayerEvent::AutoPlayChanged { .. }
        | PlayerEvent::FilterExplicitContentChanged { .. } => {}
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
    if let Some(tap) = &connected.pcm_tap {
        tap.begin_generation();
        tap.set_playback_state(DecodedPcmPlaybackState::Idle);
    }
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
        AUDIO_CACHE_LIMIT_BYTES, Command, Confirmation, DecodedPcmPlaybackState, DecodedPcmSink,
        DecodedPcmTap, Observation, PlaybackState, RepeatMode, SPOTIFY_PIPEWIRE_SINK, Snapshot,
        SpotifyConnectConfig, apply_player_event, confirmation_matches, credentials_for_retry,
        ensure_private_file, next_backoff, prepare_storage, repeat_mode, update_decoded_pcm_state,
        validate_command, validate_device_name, volume_to_percent,
    };
    use librespot::{
        core::{SpotifyUri, authentication::Credentials},
        metadata::{
            artist::{ArtistRole, ArtistWithRole, ArtistsWithRole},
            audio::{AudioFiles, AudioItem, UniqueFields, item::CoverImage},
            image::ImageSize,
        },
        playback::{
            audio_backend::{Sink, SinkResult},
            convert::Converter,
            decoder::AudioPacket,
            player::PlayerEvent,
        },
    };
    use lumistripe_engine_audio::{
        AudioConfig, PcmIngressSender, PcmPlaybackState, monotonic_time_ns,
    };
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use lumistripe_engine_protocol::{
        AudioInputTransport, EngineAudioStatus, RenderOutput, RenderSceneUpdate,
        RenderSchedulerConfig, RequestCommand, ResponseResult,
    };
    use lumistripe_engine_runtime::{EngineHandle, EngineRuntime};
    use serde_json::Value;
    use std::{
        fs,
        os::unix::fs::PermissionsExt,
        path::PathBuf,
        sync::{
            Arc, Mutex,
            atomic::{AtomicBool, AtomicU64, Ordering},
        },
        time::{Duration, Instant},
    };
    use tempfile::tempdir;
    use tokio_util::sync::CancellationToken;

    #[derive(Debug, Default)]
    struct RecordingPcmTap {
        generation: AtomicU64,
        states: Mutex<Vec<DecodedPcmPlaybackState>>,
        submissions: Mutex<Vec<(Vec<f64>, u32, u16, u64, u64)>>,
        unsupported_packets: AtomicU64,
        panic_on_submit: AtomicBool,
    }

    impl DecodedPcmTap for RecordingPcmTap {
        fn stream_generation(&self) -> u64 {
            self.generation.load(Ordering::Relaxed)
        }

        fn begin_generation(&self) {
            self.generation.fetch_add(1, Ordering::Relaxed);
        }

        fn set_playback_state(&self, state: DecodedPcmPlaybackState) {
            self.states.lock().unwrap().push(state);
        }

        fn submit_interleaved_f64(
            &self,
            samples: &[f64],
            sample_rate: u32,
            channels: u16,
            stream_generation: u64,
            sample_position: u64,
        ) {
            assert!(
                !self.panic_on_submit.load(Ordering::Relaxed),
                "synthetic tap panic"
            );
            self.submissions.lock().unwrap().push((
                samples.to_vec(),
                sample_rate,
                channels,
                stream_generation,
                sample_position,
            ));
        }

        fn record_unsupported_packet(&self) {
            self.unsupported_packets.fetch_add(1, Ordering::Relaxed);
        }
    }

    #[derive(Default)]
    struct RecordingSink {
        samples: Arc<Mutex<Vec<Vec<f64>>>>,
        raw: Arc<Mutex<Vec<Vec<u8>>>>,
    }

    impl Sink for RecordingSink {
        fn write(&mut self, packet: AudioPacket, _converter: &mut Converter) -> SinkResult<()> {
            match packet {
                AudioPacket::Samples(samples) => self.samples.lock().unwrap().push(samples),
                AudioPacket::Raw(bytes) => self.raw.lock().unwrap().push(bytes),
            }
            Ok(())
        }
    }

    struct DiscardingSink;

    impl Sink for DiscardingSink {
        fn write(&mut self, _packet: AudioPacket, _converter: &mut Converter) -> SinkResult<()> {
            Ok(())
        }
    }

    #[derive(Debug)]
    struct RuntimePcmTap {
        ingress: PcmIngressSender,
        timestamp_origin_ns: u64,
        deterministic_timestamps: bool,
        last_timestamp_ns: AtomicU64,
    }

    impl RuntimePcmTap {
        fn new(ingress: PcmIngressSender, deterministic_timestamps: bool) -> Self {
            Self {
                ingress,
                timestamp_origin_ns: monotonic_time_ns(),
                deterministic_timestamps,
                last_timestamp_ns: AtomicU64::new(0),
            }
        }
    }

    impl DecodedPcmTap for RuntimePcmTap {
        fn stream_generation(&self) -> u64 {
            self.ingress.generation()
        }

        fn begin_generation(&self) {
            self.ingress.begin_generation();
        }

        fn set_playback_state(&self, state: DecodedPcmPlaybackState) {
            let state = match state {
                DecodedPcmPlaybackState::Idle => PcmPlaybackState::Idle,
                DecodedPcmPlaybackState::Loading => PcmPlaybackState::Loading,
                DecodedPcmPlaybackState::Playing => PcmPlaybackState::Playing,
                DecodedPcmPlaybackState::Paused => PcmPlaybackState::Paused,
                DecodedPcmPlaybackState::Failed => PcmPlaybackState::Failed,
            };
            self.ingress.set_playback_state(state);
        }

        fn submit_interleaved_f64(
            &self,
            samples: &[f64],
            sample_rate: u32,
            channels: u16,
            stream_generation: u64,
            sample_position: u64,
        ) {
            let frames = samples.len() / usize::from(channels.max(1));
            let duration_ns = (frames as u128 * 1_000_000_000_u128 / u128::from(sample_rate.max(1)))
                .min(u64::MAX as u128) as u64;
            let submitted_at_ns = if self.deterministic_timestamps {
                let end_position = sample_position.saturating_add(frames as u64);
                self.timestamp_origin_ns.saturating_add(
                    (end_position as u128 * 1_000_000_000_u128 / u128::from(sample_rate.max(1)))
                        .min(u64::MAX as u128) as u64,
                )
            } else {
                let mut previous = self.last_timestamp_ns.load(Ordering::Relaxed);
                loop {
                    let submitted = monotonic_time_ns().max(previous.saturating_add(duration_ns));
                    match self.last_timestamp_ns.compare_exchange_weak(
                        previous,
                        submitted,
                        Ordering::Relaxed,
                        Ordering::Relaxed,
                    ) {
                        Ok(_) => break submitted,
                        Err(current) => previous = current,
                    }
                }
            };
            let _ = self.ingress.submit_interleaved_f64_at(
                samples,
                sample_rate,
                channels,
                stream_generation,
                sample_position,
                submitted_at_ns,
            );
        }

        fn record_unsupported_packet(&self) {
            self.ingress.record_unsupported_packet();
        }
    }

    fn fixture_bass_sine_raw() -> (Value, Value, Vec<f32>) {
        let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/audio-reference-v1");
        let reference: Value = serde_json::from_slice(
            &fs::read(fixture_dir.join("reference.json")).expect("audio reference exists"),
        )
        .expect("audio reference is valid JSON");
        let fixture = reference["fixtures"]
            .as_array()
            .unwrap()
            .iter()
            .find(|fixture| fixture["name"] == "bass_sine_raw")
            .expect("bass sine fixture exists")
            .clone();
        let tolerance = reference["tolerance"].clone();
        let bytes = fs::read(fixture_dir.join(fixture["pcm_file"].as_str().unwrap()))
            .expect("fixture PCM exists");
        let samples = bytes
            .chunks_exact(4)
            .map(|chunk| f32::from_le_bytes(chunk.try_into().unwrap()))
            .collect();
        (fixture, tolerance, samples)
    }

    async fn wait_for_audio_samples(handle: &EngineHandle, target: u64) -> EngineAudioStatus {
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if let ResponseResult::AudioStatus { status } = handle.audio_status().await.unwrap() {
                if status.processor.samples_seen >= target {
                    return status;
                }
            }
            assert!(
                Instant::now() < deadline,
                "audio worker did not consume {target} samples"
            );
            tokio::task::yield_now().await;
        }
    }

    fn assert_close(
        expected: f64,
        actual: f64,
        absolute_tolerance: f64,
        relative_tolerance: f64,
        context: &str,
    ) {
        let tolerance = absolute_tolerance.max(expected.abs() * relative_tolerance);
        assert!(
            (expected - actual).abs() <= tolerance,
            "{context}: expected {expected:.9}, got {actual:.9}, tolerance {tolerance:.3e}"
        );
    }

    #[test]
    fn decoded_pcm_tap_observes_stereo_samples_and_forwards_them_unchanged() {
        let tap = Arc::new(RecordingPcmTap::default());
        let output_samples = Arc::new(Mutex::new(Vec::new()));
        let output_raw = Arc::new(Mutex::new(Vec::new()));
        let output = RecordingSink {
            samples: Arc::clone(&output_samples),
            raw: Arc::clone(&output_raw),
        };
        let mut sink = DecodedPcmSink::new(Box::new(output), tap.clone());
        let samples = vec![0.25, -0.25, 0.5, -0.5];
        let mut converter = Converter::new(None);

        sink.write(AudioPacket::Samples(samples.clone()), &mut converter)
            .unwrap();

        assert_eq!(*output_samples.lock().unwrap(), [samples.clone()]);
        assert!(output_raw.lock().unwrap().is_empty());
        assert_eq!(
            *tap.submissions.lock().unwrap(),
            [(
                samples,
                librespot::playback::SAMPLE_RATE,
                u16::from(librespot::playback::NUM_CHANNELS),
                1,
                0
            )]
        );

        sink.write(AudioPacket::Raw(vec![1, 2, 3]), &mut converter)
            .unwrap();
        assert_eq!(*output_raw.lock().unwrap(), [vec![1, 2, 3]]);
        assert_eq!(tap.unsupported_packets.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn tap_panic_marks_analysis_failed_without_interrupting_speaker_output() {
        let tap = Arc::new(RecordingPcmTap::default());
        tap.panic_on_submit.store(true, Ordering::Relaxed);
        let output_samples = Arc::new(Mutex::new(Vec::new()));
        let output_raw = Arc::new(Mutex::new(Vec::new()));
        let output = RecordingSink {
            samples: Arc::clone(&output_samples),
            raw: Arc::clone(&output_raw),
        };
        let mut sink = DecodedPcmSink::new(Box::new(output), tap.clone());
        let samples = vec![0.25, -0.25, 0.5, -0.5];
        let mut converter = Converter::new(None);

        sink.write(AudioPacket::Samples(samples.clone()), &mut converter)
            .unwrap();

        assert_eq!(*output_samples.lock().unwrap(), [samples]);
        assert_eq!(
            tap.states.lock().unwrap().last(),
            Some(&DecodedPcmPlaybackState::Failed)
        );
    }

    #[test]
    fn sink_stop_start_preserves_pause_timeline_and_generation_change_resets_position() {
        let tap = Arc::new(RecordingPcmTap::default());
        let output_samples = Arc::new(Mutex::new(Vec::new()));
        let output_raw = Arc::new(Mutex::new(Vec::new()));
        let output = RecordingSink {
            samples: Arc::clone(&output_samples),
            raw: Arc::clone(&output_raw),
        };
        let mut sink = DecodedPcmSink::new(Box::new(output), tap.clone());
        let mut converter = Converter::new(None);
        let samples = vec![0.1, 0.1, 0.2, 0.2];

        sink.start().unwrap();
        sink.write(AudioPacket::Samples(samples.clone()), &mut converter)
            .unwrap();
        tap.set_playback_state(DecodedPcmPlaybackState::Paused);
        sink.stop().unwrap();
        sink.start().unwrap();
        sink.write(AudioPacket::Samples(samples.clone()), &mut converter)
            .unwrap();
        tap.begin_generation();
        sink.write(AudioPacket::Samples(samples), &mut converter)
            .unwrap();

        let submissions = tap.submissions.lock().unwrap();
        assert_eq!(
            submissions
                .iter()
                .map(|submission| (submission.3, submission.4))
                .collect::<Vec<_>>(),
            [(1, 0), (1, 2), (2, 0)]
        );
        assert_eq!(output_samples.lock().unwrap().len(), 3);
        assert!(output_raw.lock().unwrap().is_empty());
    }

    #[test]
    fn player_events_advance_pcm_generations_and_report_playback_state() {
        let tap = RecordingPcmTap::default();
        let track_id = SpotifyUri::from_uri("spotify:track:0123456789012345678901").unwrap();

        update_decoded_pcm_state(
            Some(&tap),
            &PlayerEvent::Loading {
                play_request_id: 1,
                track_id: track_id.clone(),
                position_ms: 0,
            },
        );
        assert_eq!(tap.stream_generation(), 1);
        update_decoded_pcm_state(
            Some(&tap),
            &PlayerEvent::Playing {
                play_request_id: 1,
                track_id: track_id.clone(),
                position_ms: 0,
            },
        );
        update_decoded_pcm_state(
            Some(&tap),
            &PlayerEvent::Seeked {
                play_request_id: 1,
                track_id: track_id.clone(),
                position_ms: 200,
            },
        );
        assert_eq!(tap.stream_generation(), 2);
        update_decoded_pcm_state(
            Some(&tap),
            &PlayerEvent::Paused {
                play_request_id: 1,
                track_id,
                position_ms: 500,
            },
        );

        assert_eq!(
            *tap.states.lock().unwrap(),
            [
                DecodedPcmPlaybackState::Loading,
                DecodedPcmPlaybackState::Playing,
                DecodedPcmPlaybackState::Paused
            ]
        );
    }

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

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn decoded_pcm_runs_through_ingress_dsp_structure_and_mock_led_output() {
        let (fixture, tolerance, mono_samples) = fixture_bass_sine_raw();
        let config: AudioConfig =
            serde_json::from_value(fixture["config"].clone()).expect("fixture audio config");
        let frames_per_packet = fixture["feed_size"].as_u64().unwrap() as usize;
        let mut runtime = EngineRuntime::start().expect("start mock engine runtime");
        let handle = runtime.handle();
        assert!(matches!(
            handle
                .configure(
                    EngineConfig::new(vec![OutputConfig {
                        id: "synthetic-led".to_owned(),
                        pixel_count: 8,
                        reversed: false,
                        backend: OutputBackend::Mock,
                    }])
                    .expect("valid mock output config")
                )
                .await
                .unwrap(),
            ResponseResult::Configured {
                output_count: 1,
                total_pixels: 8
            }
        ));
        let scene_timestamp_ns = monotonic_time_ns();
        assert!(matches!(
            handle
                .update_scene(RenderSceneUpdate {
                    revision: 1,
                    timestamp_ns: scene_timestamp_ns,
                    brightness: 1.0,
                    power: Default::default(),
                    outputs: vec![RenderOutput {
                        animation: "rainbow".to_owned(),
                        ..RenderOutput::default()
                    }],
                    scheduler: RenderSchedulerConfig { max_fps: 60 },
                })
                .await
                .unwrap(),
            ResponseResult::SceneUpdated {
                revision: 1,
                replaced: false
            }
        ));
        let ingress = handle.audio_pcm_ingress().unwrap();
        assert!(matches!(
            handle
                .request(RequestCommand::AudioStartDirect {
                    fallback_selector: None,
                    config,
                })
                .await
                .unwrap(),
            ResponseResult::AudioConfigured { active: true }
        ));

        let tap = Arc::new(RuntimePcmTap::new(ingress.clone(), true));
        let timestamp_origin_ns = tap.timestamp_origin_ns;
        tap.set_playback_state(DecodedPcmPlaybackState::Playing);
        let output_samples = Arc::new(Mutex::new(Vec::new()));
        let output_raw = Arc::new(Mutex::new(Vec::new()));
        let mut sink = DecodedPcmSink::new(
            Box::new(RecordingSink {
                samples: Arc::clone(&output_samples),
                raw: Arc::clone(&output_raw),
            }),
            tap.clone(),
        );
        sink.start().unwrap();

        let expected_frames = fixture["frames"].as_array().unwrap();
        let expected_structures = fixture["structures"].as_array().unwrap();
        let absolute_tolerance = tolerance["absolute"].as_f64().unwrap();
        let relative_tolerance = tolerance["relative"].as_f64().unwrap();
        let mut checked_frames = 0usize;
        let mut previous_sequence = 0_u64;

        for offset in (0..mono_samples.len()).step_by(frames_per_packet) {
            let end = (offset + frames_per_packet).min(mono_samples.len());
            let stereo = mono_samples[offset..end]
                .iter()
                .flat_map(|sample| [f64::from(*sample), f64::from(*sample)])
                .collect::<Vec<_>>();
            sink.write(AudioPacket::Samples(stereo), &mut Converter::new(None))
                .unwrap();

            let status = wait_for_audio_samples(&handle, end as u64).await;
            assert_eq!(status.input_transport, AudioInputTransport::DirectPcm);
            assert_eq!(status.pcm_ingress.dropped_samples, 0);
            assert_eq!(status.pcm_ingress.unsupported_packets, 0);
            assert_eq!(status.pcm_ingress.queue_occupancy, 0);
            assert_eq!(status.processor.samples_seen, end as u64);

            if status.frame.sequence > previous_sequence {
                let expected = &expected_frames[checked_frames];
                assert_eq!(
                    status.frame.sequence,
                    expected["frame"]["sequence"].as_u64().unwrap()
                );
                assert_eq!(
                    status.frame.fresh,
                    expected["frame"]["fresh"].as_bool().unwrap()
                );
                assert_eq!(
                    status.frame.beat,
                    expected["frame"]["beat"].as_bool().unwrap()
                );
                assert_close(
                    expected["frame"]["rms"].as_f64().unwrap(),
                    f64::from(status.frame.rms),
                    absolute_tolerance,
                    relative_tolerance,
                    &format!("frame {} RMS", status.frame.sequence),
                );
                assert_close(
                    expected["frame"]["beat_strength"].as_f64().unwrap(),
                    f64::from(status.frame.beat_strength),
                    absolute_tolerance,
                    relative_tolerance,
                    &format!("frame {} beat strength", status.frame.sequence),
                );
                for (band_index, (expected_band, actual_band)) in expected["frame"]["bands"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .zip(status.frame.bands)
                    .enumerate()
                {
                    assert_close(
                        expected_band.as_f64().unwrap(),
                        f64::from(actual_band),
                        absolute_tolerance,
                        relative_tolerance,
                        &format!("frame {} band {band_index}", status.frame.sequence),
                    );
                }
                for (field, actual) in [
                    ("bass_energy", status.features.bass_energy),
                    ("mid_energy", status.features.mid_energy),
                    ("treble_energy", status.features.treble_energy),
                    ("bpm", status.features.bpm),
                    ("beat_confidence", status.features.beat_confidence),
                ] {
                    assert_close(
                        expected["features"][field].as_f64().unwrap(),
                        f64::from(actual),
                        absolute_tolerance,
                        relative_tolerance,
                        &format!("frame {} {field}", status.frame.sequence),
                    );
                }
                let expected_timestamp = expected["frame"]["timestamp"].as_f64().unwrap();
                let actual_timestamp =
                    status.frame.timestamp - timestamp_origin_ns as f64 / 1_000_000_000.0;
                assert_close(
                    expected_timestamp,
                    actual_timestamp,
                    0.000_002,
                    0.0,
                    &format!("frame {} timestamp", status.frame.sequence),
                );
                let expected_structure = &expected_structures[checked_frames];
                let actual_phase = format!("{:?}", status.structure.phase).to_lowercase();
                assert_eq!(actual_phase, expected_structure["phase"].as_str().unwrap());
                assert_close(
                    expected_structure["confidence"].as_f64().unwrap(),
                    status.structure.confidence,
                    0.003,
                    0.003,
                    &format!("structure {} confidence", status.frame.sequence),
                );
                assert_close(
                    expected_structure["buildup_progress"].as_f64().unwrap(),
                    status.structure.buildup_progress,
                    0.003,
                    0.003,
                    &format!("structure {} buildup", status.frame.sequence),
                );
                previous_sequence = status.frame.sequence;
                checked_frames += 1;
            }
        }

        assert_eq!(checked_frames, expected_frames.len());
        let final_status = wait_for_audio_samples(&handle, mono_samples.len() as u64).await;
        assert_eq!(
            final_status.pcm_ingress.samples_received,
            mono_samples.len() as u64
        );
        assert_eq!(
            final_status.processor.samples_seen,
            mono_samples.len() as u64
        );
        assert_eq!(
            final_status.processor.fft_count,
            fixture["final_stats"]["fft_count"].as_u64().unwrap()
        );
        assert_eq!(
            final_status.processor.feed_count,
            fixture["final_stats"]["feed_count"].as_u64().unwrap()
        );
        assert_eq!(
            final_status.frame.beat,
            expected_frames.last().unwrap()["frame"]["beat"]
                .as_bool()
                .unwrap()
        );
        let forwarded = output_samples
            .lock()
            .unwrap()
            .iter()
            .flatten()
            .copied()
            .collect::<Vec<_>>();
        assert!(output_raw.lock().unwrap().is_empty());
        assert_eq!(forwarded.len(), mono_samples.len() * 2);
        for (index, (actual, source)) in forwarded
            .iter()
            .zip(
                mono_samples
                    .iter()
                    .flat_map(|sample| [f64::from(*sample); 2]),
            )
            .enumerate()
        {
            assert_eq!(*actual, source, "forwarded stereo sample {index}");
        }
        sink.stop().unwrap();

        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            if let ResponseResult::Health {
                frames_rendered,
                audio_update_sequence,
                ..
            } = handle.health().await.unwrap()
                && frames_rendered > 0
                && audio_update_sequence.is_some_and(|sequence| sequence > 1)
            {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "mock renderer did not consume audio updates"
            );
            tokio::task::yield_now().await;
        }

        let _ = handle.request(RequestCommand::AudioStop).await.unwrap();
        tokio::task::spawn_blocking(move || runtime.shutdown())
            .await
            .unwrap()
            .unwrap();
    }

    #[cfg(target_os = "linux")]
    #[derive(Clone, Copy)]
    struct ProcessMetrics {
        rss_bytes: u64,
        threads: u64,
        cpu_ticks: u64,
        ticks_per_second: u64,
    }

    #[cfg(target_os = "linux")]
    fn process_metrics() -> ProcessMetrics {
        let status = fs::read_to_string("/proc/self/status").expect("Linux process status");
        let value = |name: &str| -> u64 {
            status
                .lines()
                .find_map(|line| {
                    line.strip_prefix(name)
                        .and_then(|tail| tail.split_whitespace().next())
                        .and_then(|value| value.parse().ok())
                })
                .unwrap_or_else(|| panic!("{name} missing from /proc/self/status"))
        };
        let stat = fs::read_to_string("/proc/self/stat").expect("Linux process stat");
        let fields = stat[stat.rfind(')').expect("process command end") + 1..]
            .split_whitespace()
            .collect::<Vec<_>>();
        let user_ticks = fields[11].parse::<u64>().expect("process user CPU ticks");
        let system_ticks = fields[12].parse::<u64>().expect("process system CPU ticks");
        ProcessMetrics {
            rss_bytes: value("VmRSS:") * 1024,
            threads: value("Threads:"),
            cpu_ticks: user_ticks.saturating_add(system_ticks),
            ticks_per_second: unsafe { libc::sysconf(libc::_SC_CLK_TCK) }.max(1) as u64,
        }
    }

    #[cfg(target_os = "linux")]
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[ignore = "bounded synthetic stress run; invoke explicitly to collect process metrics"]
    async fn synthetic_start_seek_switch_overload_and_shutdown_stay_bounded() {
        let before = process_metrics();
        let started_at = Instant::now();
        let mut runtime = EngineRuntime::start().expect("start mock engine runtime");
        let handle = runtime.handle();
        let ingress = handle.audio_pcm_ingress().unwrap();
        handle
            .configure(
                EngineConfig::new(vec![OutputConfig {
                    id: "stress-led".to_owned(),
                    pixel_count: 8,
                    reversed: false,
                    backend: OutputBackend::Mock,
                }])
                .unwrap(),
            )
            .await
            .unwrap();
        handle
            .update_scene(RenderSceneUpdate {
                revision: 1,
                timestamp_ns: monotonic_time_ns(),
                brightness: 1.0,
                power: Default::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".to_owned(),
                    ..RenderOutput::default()
                }],
                scheduler: RenderSchedulerConfig { max_fps: 60 },
            })
            .await
            .unwrap();
        handle
            .request(RequestCommand::AudioStartDirect {
                fallback_selector: None,
                config: AudioConfig::default(),
            })
            .await
            .unwrap();

        let tap = Arc::new(RuntimePcmTap::new(ingress.clone(), false));
        let tap_trait: Arc<dyn DecodedPcmTap> = tap.clone();
        let mut sink = DecodedPcmSink::new(Box::new(DiscardingSink), tap_trait);
        sink.start().unwrap();
        tap.set_playback_state(DecodedPcmPlaybackState::Playing);

        let active = process_metrics();
        let mut peak_queue = 0_u64;
        let mut max_dsp_p95_ns = 0_u64;
        let mut sample_count = 0_u64;
        let mut amplitude = 0.1_f64;
        for cycle in 0..8 {
            if cycle > 0 {
                tap.set_playback_state(DecodedPcmPlaybackState::Idle);
                handle
                    .request(RequestCommand::AudioStart {
                        selector: Some("demo".to_owned()),
                        config: AudioConfig::default(),
                    })
                    .await
                    .unwrap();
                handle
                    .request(RequestCommand::AudioStartDirect {
                        fallback_selector: None,
                        config: AudioConfig::default(),
                    })
                    .await
                    .unwrap();
                tap.set_playback_state(DecodedPcmPlaybackState::Playing);
                sink.start().unwrap();
            }
            for packet_index in 0..512 {
                if packet_index % 128 == 0 {
                    tap.begin_generation();
                    amplitude = if amplitude < 0.2 { 0.35 } else { 0.1 };
                }
                let packet = vec![amplitude; 1_024];
                sink.write(AudioPacket::Samples(packet), &mut Converter::new(None))
                    .unwrap();
                sample_count += 512;
                let ingress_stats = ingress.stats();
                peak_queue = peak_queue.max(ingress_stats.queue_occupancy as u64);
                assert!(ingress_stats.queue_occupancy <= ingress_stats.queue_capacity);
                if packet_index % 64 == 0 {
                    if let ResponseResult::AudioStatus { status } =
                        handle.audio_status().await.unwrap()
                    {
                        max_dsp_p95_ns = max_dsp_p95_ns.max(status.dsp_processing_p95_ns);
                    }
                }
            }
            sink.stop().unwrap();
        }

        handle.request(RequestCommand::AudioStop).await.unwrap();
        let dropped_samples = ingress.stats().dropped_samples;
        assert!(
            dropped_samples > 0,
            "producer burst should exercise bounded overflow"
        );
        assert!(peak_queue <= ingress.stats().queue_capacity as u64);
        tokio::task::spawn_blocking(move || runtime.shutdown())
            .await
            .unwrap()
            .unwrap();
        assert!(!handle.is_accepting());

        let deadline = Instant::now() + Duration::from_secs(2);
        let after = loop {
            let metrics = process_metrics();
            if metrics.threads <= before.threads + 2 || Instant::now() >= deadline {
                break metrics;
            }
            tokio::task::yield_now().await;
        };
        let elapsed_seconds = started_at.elapsed().as_secs_f64();
        let cpu_seconds =
            after.cpu_ticks.saturating_sub(before.cpu_ticks) as f64 / after.ticks_per_second as f64;
        let cpu_percent_one_core = cpu_seconds / elapsed_seconds * 100.0;
        let rss_growth_mib =
            after.rss_bytes.saturating_sub(before.rss_bytes) as f64 / (1024.0 * 1024.0);
        eprintln!(
            "synthetic PCM stress: {sample_count} samples, {dropped_samples} dropped, peak queue {peak_queue}/{} batches, DSP p95 {max_dsp_p95_ns} ns, CPU {cpu_percent_one_core:.1}% of one core, RSS growth {rss_growth_mib:.1} MiB, threads {} -> {} -> {}, elapsed {:.2}s",
            ingress.stats().queue_capacity,
            before.threads,
            active.threads,
            after.threads,
            elapsed_seconds,
        );
        assert!(after.rss_bytes <= before.rss_bytes + 32 * 1024 * 1024);
        assert!(after.threads <= before.threads + 2);
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
