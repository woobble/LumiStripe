//! Single-process output ownership, bounded frame queue, IPC, and lifecycle.

use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
use lumistripe_engine_output::{OutputError, OutputSet};
use lumistripe_engine_protocol::{
    EngineErrorCode, EngineStatus, FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus,
    LEGACY_PROTOCOL_VERSION, LegacyErrorCode, LegacyRequest, LegacyRequestCommand, LegacyResponse,
    LegacyResponseResult, MAX_CONTROL_MESSAGE_BYTES, MAX_FRAME_BYTES, PROTOCOL_VERSION, Request,
    RequestCommand, Response, ResponseResult, decode_frame_header, decode_legacy_request,
    decode_request, encode_frame_ack, encode_legacy_response, encode_response,
};
use serde::Deserialize;
use std::collections::VecDeque;
use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const CONFIGURE_TIMEOUT: Duration = Duration::from_secs(10);
const FRAME_LATENCY_SAMPLE_COUNT: usize = 128;
const CONTROL_REQUEST_TIMEOUT: Duration = Duration::from_secs(2);
const FRAME_PACKET_TIMEOUT: Duration = Duration::from_secs(2);
const FRAME_IDLE_POLL_INTERVAL: Duration = Duration::from_millis(100);
const SOCKET_WRITE_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_IPC_CONNECTIONS: usize = 16;

pub fn serve(socket_path: impl AsRef<Path>) -> io::Result<()> {
    serve_with_owner(socket_path, unsafe { libc::getppid() })
}

pub fn serve_with_owner(socket_path: impl AsRef<Path>, owner_pid: libc::pid_t) -> io::Result<()> {
    if owner_pid <= 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "engine owner PID must be positive",
        ));
    }
    let socket_path = socket_path.as_ref();
    remove_stale_socket(socket_path)?;
    let listener = UnixListener::bind(socket_path)?;
    let _socket_guard = SocketPathGuard(socket_path.to_path_buf());
    fs::set_permissions(socket_path, fs::Permissions::from_mode(0o600))?;
    listener.set_nonblocking(true)?;

    let server = Arc::new(EngineServer::new());
    let mut connections = Vec::new();
    while !server.stopping.load(Ordering::Acquire) {
        if unsafe { libc::getppid() } != owner_pid {
            server.stopping.store(true, Ordering::Release);
            if let Err(error) = server.output.shutdown() {
                eprintln!("engine owner exited before output blackout completed: {error}");
            }
            break;
        }
        reap_finished_connections(&mut connections);
        match listener.accept() {
            Ok((stream, _)) => {
                if connections.len() >= MAX_IPC_CONNECTIONS {
                    drop(stream);
                    continue;
                }
                stream.set_write_timeout(Some(SOCKET_WRITE_TIMEOUT))?;
                let server = Arc::clone(&server);
                connections.push(thread::spawn(move || {
                    if let Err(error) = handle_connection(stream, server) {
                        eprintln!("engine IPC request failed: {error}");
                    }
                }));
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(5));
            }
            Err(error) => return Err(error),
        }
    }
    drop(listener);
    for connection in connections {
        if let Err(error) = connection.join() {
            eprintln!("engine IPC connection thread panicked: {error:?}");
        }
    }
    Ok(())
}

fn reap_finished_connections(connections: &mut Vec<JoinHandle<()>>) {
    let mut running = Vec::with_capacity(connections.len());
    for connection in connections.drain(..) {
        if connection.is_finished() {
            if let Err(error) = connection.join() {
                eprintln!("engine IPC connection thread panicked: {error:?}");
            }
        } else {
            running.push(connection);
        }
    }
    *connections = running;
}

struct EngineServer {
    output: OutputWorker,
    stopping: AtomicBool,
    legacy_commands: Mutex<()>,
    legacy_pixel_count: Mutex<Option<usize>>,
    legacy_frame_index: AtomicU64,
}

impl EngineServer {
    fn new() -> Self {
        Self {
            output: OutputWorker::new(),
            stopping: AtomicBool::new(false),
            legacy_commands: Mutex::new(()),
            legacy_pixel_count: Mutex::new(None),
            legacy_frame_index: AtomicU64::new(0),
        }
    }
}

fn handle_connection(mut stream: UnixStream, server: Arc<EngineServer>) -> io::Result<()> {
    let mut prefix = [0u8; 4];
    read_exact_until(
        &mut stream,
        &mut prefix,
        Instant::now() + CONTROL_REQUEST_TIMEOUT,
    )?;
    if prefix == lumistripe_engine_protocol::FRAME_MAGIC {
        return handle_frame_stream(stream, prefix, &server);
    }

    let mut bytes = Vec::with_capacity(1024);
    bytes.extend_from_slice(&prefix);
    let mut chunk = [0u8; 4096];
    let deadline = Instant::now() + CONTROL_REQUEST_TIMEOUT;
    while !bytes.contains(&b'\n') {
        let read = read_with_deadline(&mut stream, &mut chunk, deadline)?;
        if read == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "control request ended before its newline",
            ));
        }
        bytes.extend_from_slice(&chunk[..read]);
        if bytes.len() > MAX_CONTROL_MESSAGE_BYTES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("control request exceeds {MAX_CONTROL_MESSAGE_BYTES} bytes"),
            ));
        }
    }
    let newline = bytes
        .iter()
        .position(|byte| *byte == b'\n')
        .expect("line was checked above");
    bytes.truncate(newline);

    let version = serde_json::from_slice::<RequestVersion>(&bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    if version.protocol_version == LEGACY_PROTOCOL_VERSION {
        let request = decode_legacy_request(&bytes)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        validate_request_id(&request.request_id)?;
        let (response, should_stop) = dispatch_legacy(request, &server);
        let encoded = encode_legacy_response(&response)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        stream.write_all(&encoded)?;
        stream.flush()?;
        if should_stop {
            server.stopping.store(true, Ordering::Release);
        }
        return Ok(());
    }

    let request = decode_request(&bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    validate_request_id(&request.request_id)?;
    let (response, should_stop) = dispatch(request, &server);
    let encoded = encode_response(&response)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    stream.write_all(&encoded)?;
    stream.flush()?;
    if should_stop {
        server.stopping.store(true, Ordering::Release);
    }
    Ok(())
}

#[derive(Deserialize)]
struct RequestVersion {
    protocol_version: u16,
}

fn validate_request_id(request_id: &str) -> io::Result<()> {
    if request_id.is_empty() || request_id.len() > 128 || request_id.chars().any(char::is_control) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "control request has an invalid request_id",
        ));
    }
    Ok(())
}

fn handle_frame_stream(
    mut stream: UnixStream,
    mut next_magic: [u8; 4],
    server: &EngineServer,
) -> io::Result<()> {
    let mut payload = Vec::new();
    loop {
        let output = &server.output;
        let mut header_bytes = [0u8; FRAME_HEADER_BYTES];
        if next_magic != lumistripe_engine_protocol::FRAME_MAGIC {
            write_frame_ack(&mut stream, output, 0, FrameAckStatus::InvalidFrame)?;
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "frame packet has an invalid magic value",
            ));
        }
        header_bytes[..4].copy_from_slice(&next_magic);
        let packet_deadline = Instant::now() + FRAME_PACKET_TIMEOUT;
        read_exact_until(&mut stream, &mut header_bytes[4..], packet_deadline)?;
        let header = match decode_frame_header(&header_bytes) {
            Ok(header) => header,
            Err(error) => {
                let _ = stream.write_all(&encode_frame_ack(FrameAck {
                    status: FrameAckStatus::InvalidFrame,
                    sequence: 0,
                    dropped_frames: output.dropped_frames(),
                }));
                return Err(io::Error::new(io::ErrorKind::InvalidData, error));
            }
        };
        if header.payload_bytes as usize > MAX_FRAME_BYTES {
            write_frame_ack(
                &mut stream,
                output,
                header.sequence,
                FrameAckStatus::InvalidFrame,
            )?;
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "frame payload exceeds the configured protocol limit",
            ));
        }
        let (expected_output_count, expected_bytes) = output.frame_dimensions();
        if expected_output_count == 0 {
            write_frame_ack(
                &mut stream,
                output,
                header.sequence,
                FrameAckStatus::NotConfigured,
            )?;
            return Ok(());
        }
        if usize::from(header.output_count) != expected_output_count
            || header.payload_bytes as usize != expected_bytes
        {
            write_frame_ack(
                &mut stream,
                output,
                header.sequence,
                FrameAckStatus::InvalidFrame,
            )?;
            return Ok(());
        }

        payload.resize(expected_bytes, 0);
        read_exact_until(&mut stream, &mut payload, packet_deadline)?;
        let status = match output.submit_frame(header.sequence, &payload) {
            Ok(()) => FrameAckStatus::Accepted,
            Err(status) => status,
        };
        write_frame_ack(&mut stream, output, header.sequence, status)?;
        match read_next_frame_magic(&mut stream, &server.stopping)? {
            Some(magic) => next_magic = magic,
            None => return Ok(()),
        }
    }
}

fn read_next_frame_magic(
    stream: &mut UnixStream,
    stopping: &AtomicBool,
) -> io::Result<Option<[u8; 4]>> {
    let mut magic = [0u8; 4];
    let mut filled = 0usize;
    let mut packet_deadline: Option<Instant> = None;
    while filled < magic.len() {
        if filled == 0 && stopping.load(Ordering::Acquire) {
            return Ok(None);
        }
        let timeout = if filled == 0 {
            FRAME_IDLE_POLL_INTERVAL
        } else {
            packet_deadline
                .expect("partial magic has a packet deadline")
                .saturating_duration_since(Instant::now())
        };
        if timeout.is_zero() {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "incomplete frame packet timed out",
            ));
        }
        stream.set_read_timeout(Some(timeout))?;
        match stream.read(&mut magic[filled..]) {
            Ok(0) if filled == 0 => return Ok(None),
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "frame packet ended during its magic value",
                ));
            }
            Ok(read) => {
                if filled == 0 {
                    packet_deadline = Some(Instant::now() + FRAME_PACKET_TIMEOUT);
                }
                filled += read;
            }
            Err(error)
                if filled == 0
                    && matches!(
                        error.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                    ) =>
            {
                continue;
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    Ok(Some(magic))
}

fn read_exact_until(
    stream: &mut UnixStream,
    mut bytes: &mut [u8],
    deadline: Instant,
) -> io::Result<()> {
    while !bytes.is_empty() {
        let read = read_with_deadline(stream, bytes, deadline)?;
        if read == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "IPC connection closed before the message was complete",
            ));
        }
        bytes = &mut bytes[read..];
    }
    Ok(())
}

fn read_with_deadline(
    stream: &mut UnixStream,
    buffer: &mut [u8],
    deadline: Instant,
) -> io::Result<usize> {
    loop {
        let timeout = deadline.saturating_duration_since(Instant::now());
        if timeout.is_zero() {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "IPC message read timed out",
            ));
        }
        stream.set_read_timeout(Some(timeout))?;
        match stream.read(buffer) {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            result => return result,
        }
    }
}

fn write_frame_ack(
    stream: &mut UnixStream,
    output: &OutputWorker,
    sequence: u64,
    status: FrameAckStatus,
) -> io::Result<()> {
    let ack = FrameAck {
        status,
        sequence,
        dropped_frames: output.dropped_frames(),
    };
    let bytes: [u8; FRAME_ACK_BYTES] = encode_frame_ack(ack);
    stream.write_all(&bytes)
}

fn dispatch(request: Request, server: &EngineServer) -> (Response, bool) {
    let request_id = request.request_id;
    if request.protocol_version != PROTOCOL_VERSION {
        return (
            Response::new(
                request_id,
                ResponseResult::Error {
                    code: EngineErrorCode::UnsupportedVersion,
                    message: format!(
                        "protocol version {} is unsupported; expected {PROTOCOL_VERSION}",
                        request.protocol_version
                    ),
                },
            ),
            false,
        );
    }

    match request.command {
        RequestCommand::Health => (
            Response::new(request_id, server.output.health_response()),
            false,
        ),
        RequestCommand::Configure { config } => {
            if let Err(error) = config.validate() {
                (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::InvalidConfig,
                            message: error.to_string(),
                        },
                    ),
                    false,
                )
            } else {
                let _legacy_guard = server
                    .legacy_commands
                    .lock()
                    .expect("legacy command lock poisoned");
                *server
                    .legacy_pixel_count
                    .lock()
                    .expect("legacy state poisoned") = None;
                match server.output.configure(config) {
                    Ok((output_count, total_pixels)) => (
                        Response::new(
                            request_id,
                            ResponseResult::Configured {
                                output_count,
                                total_pixels,
                            },
                        ),
                        false,
                    ),
                    Err(error) => (
                        Response::new(
                            request_id,
                            ResponseResult::Error {
                                code: EngineErrorCode::OutputFailure,
                                message: error,
                            },
                        ),
                        false,
                    ),
                }
            }
        }
        RequestCommand::Shutdown => match server.output.shutdown() {
            Ok(()) => (Response::new(request_id, ResponseResult::Shutdown), true),
            Err(message) => (
                Response::new(
                    request_id,
                    ResponseResult::Error {
                        code: EngineErrorCode::OutputFailure,
                        message,
                    },
                ),
                true,
            ),
        },
    }
}

fn dispatch_legacy(request: LegacyRequest, server: &EngineServer) -> (LegacyResponse, bool) {
    let request_id = request.request_id;
    if request.protocol_version != LEGACY_PROTOCOL_VERSION {
        return (
            LegacyResponse::new(
                request_id,
                LegacyResponseResult::Error {
                    code: LegacyErrorCode::UnsupportedVersion,
                    message: format!(
                        "protocol version {} is unsupported; expected {LEGACY_PROTOCOL_VERSION}",
                        request.protocol_version
                    ),
                },
            ),
            false,
        );
    }

    let _legacy_guard = server
        .legacy_commands
        .lock()
        .expect("legacy command lock poisoned");
    match request.command {
        LegacyRequestCommand::Health => {
            let pixel_count = *server
                .legacy_pixel_count
                .lock()
                .expect("legacy state poisoned");
            let frames_rendered = match server.output.health_response() {
                ResponseResult::Health {
                    frames_rendered, ..
                } => frames_rendered,
                _ => unreachable!("health_response always returns health"),
            };
            (
                LegacyResponse::new(
                    request_id,
                    LegacyResponseResult::Health {
                        status: EngineStatus::Ready,
                        pixel_count,
                        frames_rendered,
                    },
                ),
                false,
            )
        }
        LegacyRequestCommand::Configure { config } => {
            let pixel_count = config.pixel_count;
            let config = EngineConfig::new(vec![OutputConfig {
                id: "legacy".into(),
                pixel_count,
                reversed: false,
                backend: OutputBackend::Mock,
            }]);
            let config = match config {
                Ok(config) => config,
                Err(error) => {
                    return (
                        LegacyResponse::new(
                            request_id,
                            LegacyResponseResult::Error {
                                code: LegacyErrorCode::InvalidConfig,
                                message: error.to_string(),
                            },
                        ),
                        false,
                    );
                }
            };
            *server
                .legacy_pixel_count
                .lock()
                .expect("legacy state poisoned") = None;
            match server.output.configure(config) {
                Ok(_) => {
                    *server
                        .legacy_pixel_count
                        .lock()
                        .expect("legacy state poisoned") = Some(pixel_count);
                    server.legacy_frame_index.store(0, Ordering::Release);
                    (
                        LegacyResponse::new(
                            request_id,
                            LegacyResponseResult::Configured { pixel_count },
                        ),
                        false,
                    )
                }
                Err(message) => (
                    LegacyResponse::new(
                        request_id,
                        LegacyResponseResult::Error {
                            code: LegacyErrorCode::InvalidConfig,
                            message,
                        },
                    ),
                    false,
                ),
            }
        }
        LegacyRequestCommand::Render { pixels } => {
            let configured_pixel_count = *server
                .legacy_pixel_count
                .lock()
                .expect("legacy state poisoned");
            let Some(pixel_count) = configured_pixel_count else {
                return (
                    LegacyResponse::new(
                        request_id,
                        LegacyResponseResult::Error {
                            code: LegacyErrorCode::NotConfigured,
                            message: "output is not configured".into(),
                        },
                    ),
                    false,
                );
            };
            if pixels.len() != pixel_count {
                return (
                    LegacyResponse::new(
                        request_id,
                        LegacyResponseResult::Error {
                            code: LegacyErrorCode::InvalidFrame,
                            message: format!(
                                "frame has {} pixels; expected {pixel_count}",
                                pixels.len()
                            ),
                        },
                    ),
                    false,
                );
            }
            let mut rgba = Vec::with_capacity(pixel_count * 4);
            for [red, green, blue] in pixels {
                rgba.extend_from_slice(&[red, green, blue, 255]);
            }
            let frame_index = server
                .legacy_frame_index
                .load(Ordering::Acquire)
                .saturating_add(1);
            match server.output.submit_frame(frame_index, &rgba) {
                Ok(()) => {
                    server
                        .legacy_frame_index
                        .store(frame_index, Ordering::Release);
                    (
                        LegacyResponse::new(
                            request_id,
                            LegacyResponseResult::Rendered {
                                frame_index,
                                pixel_count,
                            },
                        ),
                        false,
                    )
                }
                Err(status) => (
                    LegacyResponse::new(
                        request_id,
                        LegacyResponseResult::Error {
                            code: match status {
                                FrameAckStatus::NotConfigured => LegacyErrorCode::NotConfigured,
                                FrameAckStatus::InvalidFrame => LegacyErrorCode::InvalidFrame,
                                FrameAckStatus::Accepted | FrameAckStatus::ShuttingDown => {
                                    LegacyErrorCode::NotConfigured
                                }
                            },
                            message: format!("legacy frame was rejected: {status:?}"),
                        },
                    ),
                    false,
                ),
            }
        }
        LegacyRequestCommand::Shutdown => match server.output.shutdown() {
            Ok(()) => (
                LegacyResponse::new(request_id, LegacyResponseResult::Shutdown),
                true,
            ),
            Err(message) => (
                LegacyResponse::new(
                    request_id,
                    LegacyResponseResult::Error {
                        code: LegacyErrorCode::NotConfigured,
                        message,
                    },
                ),
                true,
            ),
        },
    }
}

struct FrameSet {
    sequence: u64,
    frames: Vec<Vec<u8>>,
    queued_at: Instant,
}

impl FrameSet {
    fn new(pixel_counts: &[usize]) -> Self {
        Self {
            sequence: 0,
            frames: pixel_counts
                .iter()
                .map(|pixels| vec![0; pixels * 4])
                .collect(),
            queued_at: Instant::now(),
        }
    }

    fn copy_payload(&mut self, sequence: u64, payload: &[u8]) -> bool {
        let expected_bytes = self.frames.iter().map(Vec::len).sum::<usize>();
        if payload.len() != expected_bytes {
            return false;
        }
        let mut offset = 0;
        for frame in &mut self.frames {
            let end = offset + frame.len();
            frame.copy_from_slice(&payload[offset..end]);
            offset = end;
        }
        self.sequence = sequence;
        self.queued_at = Instant::now();
        true
    }
}

enum WorkCommand {
    Configure {
        config: EngineConfig,
        frame_buffers: [FrameSet; 2],
        response: SyncSender<Result<(usize, usize), String>>,
    },
    Shutdown {
        response: SyncSender<Result<(), String>>,
    },
}

#[derive(Default)]
struct WorkQueue {
    commands: VecDeque<WorkCommand>,
    frame_buffers: [Option<FrameSet>; 2],
    pending_frame: Option<usize>,
    command_pending: bool,
    closed: bool,
}

impl WorkQueue {
    fn reserve_configure(&mut self) -> Result<(), &'static str> {
        if self.closed {
            return Err("output worker is shutting down");
        }
        if self.command_pending {
            return Err("an output configuration is already pending");
        }
        self.command_pending = true;
        Ok(())
    }

    fn enqueue_latest_frame(&mut self, sequence: u64, payload: &[u8]) -> Result<bool, ()> {
        let pending = self.pending_frame;
        let index = pending.or_else(|| self.frame_buffers.iter().position(Option::is_some));
        let Some(index) = index else {
            return Err(());
        };
        let Some(frame) = self.frame_buffers[index].as_mut() else {
            return Err(());
        };
        if !frame.copy_payload(sequence, payload) {
            return Err(());
        }
        self.pending_frame = Some(index);
        Ok(pending.is_some())
    }
}

struct OutputWorkerShared {
    queue: Mutex<WorkQueue>,
    ready: Condvar,
    health: Mutex<RuntimeHealth>,
}

struct OutputWorker {
    shared: Arc<OutputWorkerShared>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl OutputWorker {
    fn new() -> Self {
        let shared = Arc::new(OutputWorkerShared {
            queue: Mutex::new(WorkQueue::default()),
            ready: Condvar::new(),
            health: Mutex::new(RuntimeHealth::new()),
        });
        let worker_shared = Arc::clone(&shared);
        let thread = thread::Builder::new()
            .name("lumistripe-rust-output".into())
            .spawn(move || output_worker_loop(worker_shared))
            .expect("Rust output worker thread could not be created");
        Self {
            shared,
            thread: Mutex::new(Some(thread)),
        }
    }

    fn configure(&self, config: EngineConfig) -> Result<(usize, usize), String> {
        config.validate().map_err(|error| error.to_string())?;
        let pixel_counts = config.pixel_counts();
        let frame_buffers = std::array::from_fn(|_| FrameSet::new(&pixel_counts));
        let (send, receive) = mpsc::sync_channel(1);
        {
            let mut queue = self.shared.queue.lock().expect("output queue poisoned");
            queue.reserve_configure().map_err(str::to_owned)?;
            let mut health = self.shared.health.lock().expect("output health poisoned");
            if queue.pending_frame.take().is_some() {
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            health.configured = false;
            health.pixel_counts.clear();
            health.last_sequence = None;
            health.frame_latency_ms = None;
            health.queue_latency_ms = None;
            health.transmission_ms = None;
            health.sequential_skew_ms = None;
            health.frame_latency_samples_ms.clear();
            health.output_rate = 0.0;
            health.rate_started_at = Instant::now();
            health.rate_frames = 0;
            health.last_successful_update_epoch_ms = None;
            queue.commands.push_back(WorkCommand::Configure {
                config,
                frame_buffers,
                response: send,
            });
        }
        self.shared.ready.notify_one();
        receive
            .recv_timeout(CONFIGURE_TIMEOUT)
            .map_err(|error| format!("output configuration did not finish: {error}"))?
    }

    fn submit_frame(&self, sequence: u64, payload: &[u8]) -> Result<(), FrameAckStatus> {
        let mut queue = self.shared.queue.lock().expect("output queue poisoned");
        if queue.closed {
            return Err(FrameAckStatus::ShuttingDown);
        }
        let mut health = self.shared.health.lock().expect("output health poisoned");
        if !health.configured {
            return Err(FrameAckStatus::NotConfigured);
        }
        let expected_bytes: usize = health.pixel_counts.iter().map(|pixels| pixels * 4).sum();
        if payload.len() != expected_bytes {
            return Err(FrameAckStatus::InvalidFrame);
        }
        if queue
            .enqueue_latest_frame(sequence, payload)
            .map_err(|()| FrameAckStatus::InvalidFrame)?
        {
            health.dropped_frames = health.dropped_frames.saturating_add(1);
        }
        drop(health);
        drop(queue);
        self.shared.ready.notify_one();
        Ok(())
    }

    fn frame_dimensions(&self) -> (usize, usize) {
        let health = self.shared.health.lock().expect("output health poisoned");
        let payload_bytes = health.pixel_counts.iter().map(|pixels| pixels * 4).sum();
        (health.pixel_counts.len(), payload_bytes)
    }

    fn dropped_frames(&self) -> u64 {
        self.shared
            .health
            .lock()
            .expect("output health poisoned")
            .dropped_frames
    }

    fn health_response(&self) -> ResponseResult {
        let health = self.shared.health.lock().expect("output health poisoned");
        ResponseResult::Health {
            status: EngineStatus::Ready,
            configured: health.configured,
            output_count: health.pixel_counts.len(),
            pixel_counts: health.pixel_counts.clone(),
            frames_rendered: health.frames_rendered,
            last_sequence: health.last_sequence,
            dropped_frames: health.dropped_frames,
            failed_frames: health.failed_frames,
            last_error: health.last_error.clone(),
            output_rate: health.output_rate,
            frame_latency_ms: health.frame_latency_ms,
            queue_latency_ms: health.queue_latency_ms,
            transmission_ms: health.transmission_ms,
            frame_latency_p50_ms: health.frame_latency_percentile_ms(0.50),
            frame_latency_p95_ms: health.frame_latency_p95_ms(),
            frame_latency_p99_ms: health.frame_latency_percentile_ms(0.99),
            sequential_skew_ms: health.sequential_skew_ms,
            last_successful_update_epoch_ms: health.last_successful_update_epoch_ms,
        }
    }

    fn shutdown(&self) -> Result<(), String> {
        let (send, receive) = mpsc::sync_channel(1);
        {
            let mut queue = self.shared.queue.lock().expect("output queue poisoned");
            if queue.closed {
                return Ok(());
            }
            queue.closed = true;
            if queue.pending_frame.take().is_some() {
                let mut health = self.shared.health.lock().expect("output health poisoned");
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            queue
                .commands
                .push_back(WorkCommand::Shutdown { response: send });
        }
        self.shared.ready.notify_one();
        let thread = self.thread.lock().expect("worker handle poisoned").take();
        match receive.recv_timeout(Duration::from_secs(5)) {
            Ok(result) => {
                if let Some(thread) = thread {
                    thread
                        .join()
                        .map_err(|error| format!("output worker panicked: {error:?}"))?;
                }
                result
            }
            Err(error) => {
                // Detach the stalled worker. The process exits after the error
                // response so the OS releases any stuck device handles.
                drop(thread);
                Err(format!("output worker did not stop in time: {error}"))
            }
        }
    }
}

impl Drop for OutputWorker {
    fn drop(&mut self) {
        if let Err(error) = self.shutdown() {
            eprintln!("engine output cleanup failed: {error}");
        }
    }
}

struct RuntimeHealth {
    configured: bool,
    pixel_counts: Vec<usize>,
    frames_rendered: u64,
    last_sequence: Option<u64>,
    dropped_frames: u64,
    failed_frames: u64,
    last_error: Option<String>,
    output_rate: f64,
    rate_started_at: Instant,
    rate_frames: u64,
    frame_latency_ms: Option<f64>,
    queue_latency_ms: Option<f64>,
    transmission_ms: Option<f64>,
    frame_latency_samples_ms: VecDeque<f64>,
    sequential_skew_ms: Option<f64>,
    last_successful_update_epoch_ms: Option<u64>,
}

impl RuntimeHealth {
    fn new() -> Self {
        Self {
            configured: false,
            pixel_counts: Vec::new(),
            frames_rendered: 0,
            last_sequence: None,
            dropped_frames: 0,
            failed_frames: 0,
            last_error: None,
            output_rate: 0.0,
            rate_started_at: Instant::now(),
            rate_frames: 0,
            frame_latency_ms: None,
            queue_latency_ms: None,
            transmission_ms: None,
            frame_latency_samples_ms: VecDeque::with_capacity(FRAME_LATENCY_SAMPLE_COUNT),
            sequential_skew_ms: None,
            last_successful_update_epoch_ms: None,
        }
    }

    fn frame_latency_p95_ms(&self) -> Option<f64> {
        self.frame_latency_percentile_ms(0.95)
    }

    fn frame_latency_percentile_ms(&self, quantile: f64) -> Option<f64> {
        if self.frame_latency_samples_ms.is_empty() {
            return None;
        }
        let mut samples = self
            .frame_latency_samples_ms
            .iter()
            .copied()
            .collect::<Vec<_>>();
        samples.sort_by(f64::total_cmp);
        let index = ((samples.len() as f64 * quantile).ceil() as usize)
            .saturating_sub(1)
            .min(samples.len() - 1);
        Some(samples[index])
    }

    fn record_frame(
        &mut self,
        sequence: u64,
        queued_at: Instant,
        queue_latency_ms: f64,
        transmission_ms: Option<f64>,
        sequential_skew_ms: Option<f64>,
    ) {
        let frame_latency_ms = queued_at.elapsed().as_secs_f64() * 1000.0;
        self.frames_rendered = self.frames_rendered.saturating_add(1);
        self.last_sequence = Some(sequence);
        self.frame_latency_ms = Some(frame_latency_ms);
        self.queue_latency_ms = Some(queue_latency_ms);
        self.transmission_ms = transmission_ms;
        self.sequential_skew_ms = sequential_skew_ms;
        if self.frame_latency_samples_ms.len() == FRAME_LATENCY_SAMPLE_COUNT {
            self.frame_latency_samples_ms.pop_front();
        }
        self.frame_latency_samples_ms.push_back(frame_latency_ms);
        self.last_successful_update_epoch_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .ok()
            .map(|duration| duration.as_millis() as u64);
        self.rate_frames = self.rate_frames.saturating_add(1);
        let elapsed = self.rate_started_at.elapsed().as_secs_f64();
        if elapsed >= 1.0 {
            self.output_rate = self.rate_frames as f64 / elapsed;
            self.rate_started_at = Instant::now();
            self.rate_frames = 0;
        }
        self.last_error = None;
    }

    fn record_error(&mut self, error: impl Into<String>) {
        self.failed_frames = self.failed_frames.saturating_add(1);
        self.last_error = Some(error.into().chars().take(256).collect());
    }
}

enum WorkItem {
    Command(WorkCommand),
    Frame(usize, FrameSet),
    Stop,
}

fn output_worker_loop(shared: Arc<OutputWorkerShared>) {
    let mut outputs: Option<OutputSet> = None;
    loop {
        let item = {
            let mut queue = shared.queue.lock().expect("output queue poisoned");
            while queue.commands.is_empty() && queue.pending_frame.is_none() && !queue.closed {
                queue = shared.ready.wait(queue).expect("output queue poisoned");
            }
            if let Some(command) = queue.commands.pop_front() {
                WorkItem::Command(command)
            } else if let Some(index) = queue.pending_frame.take() {
                let frame = queue.frame_buffers[index]
                    .take()
                    .expect("pending frame buffer must be available");
                WorkItem::Frame(index, frame)
            } else {
                WorkItem::Stop
            }
        };
        match item {
            WorkItem::Command(WorkCommand::Configure {
                config,
                frame_buffers,
                response,
            }) => {
                let mut blackout_error = None;
                if let Some(mut current) = outputs.take() {
                    if let Err(error) = current.blackout() {
                        blackout_error = Some(error.to_string());
                    }
                }
                if let Some(error) = blackout_error {
                    let mut queue = shared.queue.lock().expect("output queue poisoned");
                    queue.frame_buffers = [None, None];
                    queue.command_pending = false;
                    shared
                        .health
                        .lock()
                        .expect("output health poisoned")
                        .record_error(error.clone());
                    shared
                        .health
                        .lock()
                        .expect("output health poisoned")
                        .configured = false;
                    let _ =
                        response.send(Err(format!("could not black out current outputs: {error}")));
                    continue;
                }
                match OutputSet::open(config) {
                    Ok(configured) => {
                        let output_count = configured.output_count();
                        let total_pixels = configured.total_pixels();
                        let pixel_counts = configured.pixel_counts();
                        outputs = Some(configured);
                        let mut queue = shared.queue.lock().expect("output queue poisoned");
                        queue.frame_buffers = frame_buffers.map(Some);
                        queue.command_pending = false;
                        let mut health = shared.health.lock().expect("output health poisoned");
                        health.configured = true;
                        health.pixel_counts = pixel_counts;
                        health.last_sequence = None;
                        health.last_error = None;
                        health.frame_latency_samples_ms.clear();
                        health.frame_latency_ms = None;
                        health.queue_latency_ms = None;
                        health.transmission_ms = None;
                        health.sequential_skew_ms = None;
                        let _ = response.send(Ok((output_count, total_pixels)));
                    }
                    Err(error) => {
                        let message = error.to_string();
                        let mut queue = shared.queue.lock().expect("output queue poisoned");
                        queue.frame_buffers = [None, None];
                        queue.command_pending = false;
                        shared
                            .health
                            .lock()
                            .expect("output health poisoned")
                            .record_error(message.clone());
                        shared
                            .health
                            .lock()
                            .expect("output health poisoned")
                            .configured = false;
                        let _ = response.send(Err(message));
                    }
                }
            }
            WorkItem::Command(WorkCommand::Shutdown { response }) => {
                let mut blackout_error = None;
                if let Some(current) = outputs.as_mut() {
                    if let Err(error) = current.blackout() {
                        blackout_error = Some(error.to_string());
                        shared
                            .health
                            .lock()
                            .expect("output health poisoned")
                            .record_error(error.to_string());
                    }
                }
                let mut health = shared.health.lock().expect("output health poisoned");
                health.configured = false;
                let _ = response.send(match blackout_error {
                    Some(error) => Err(error),
                    None => Ok(()),
                });
                return;
            }
            WorkItem::Frame(index, frame) => {
                let queue_latency_ms = frame.queued_at.elapsed().as_secs_f64() * 1000.0;
                let result = outputs
                    .as_mut()
                    .ok_or_else(|| OutputError::InvalidConfig("outputs are not configured".into()))
                    .and_then(|set| {
                        set.render(&frame.frames)
                            .map(|_| (set.last_transmission_ms(), set.last_sequential_skew_ms()))
                    });
                let mut health = shared.health.lock().expect("output health poisoned");
                match result {
                    Ok((transmission_ms, sequential_skew_ms)) => health.record_frame(
                        frame.sequence,
                        frame.queued_at,
                        queue_latency_ms,
                        transmission_ms,
                        sequential_skew_ms,
                    ),
                    Err(error) => health.record_error(error.to_string()),
                }
                drop(health);
                let mut queue = shared.queue.lock().expect("output queue poisoned");
                queue.frame_buffers[index] = Some(frame);
            }
            WorkItem::Stop => return,
        }
    }
}

fn remove_stale_socket(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_socket() => match UnixStream::connect(path) {
            Ok(_) => Err(io::Error::new(
                io::ErrorKind::AddrInUse,
                format!("engine socket is already active at {}", path.display()),
            )),
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::ConnectionRefused | io::ErrorKind::NotFound
                ) =>
            {
                match fs::remove_file(path) {
                    Err(remove_error) if remove_error.kind() != io::ErrorKind::NotFound => {
                        Err(remove_error)
                    }
                    _ => Ok(()),
                }
            }
            Err(error) => Err(error),
        },
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            format!("refusing to replace non-socket path {}", path.display()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

struct SocketPathGuard(PathBuf);

impl Drop for SocketPathGuard {
    fn drop(&mut self) {
        match fs::remove_file(&self.0) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => {
                eprintln!(
                    "could not remove engine socket {}: {error}",
                    self.0.display()
                );
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{EngineServer, FrameSet, RuntimeHealth, WorkQueue, remove_stale_socket, serve};
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use lumistripe_engine_protocol::{
        FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus, FrameHeader, LegacyConfig,
        LegacyRequest, LegacyRequestCommand, LegacyResponseResult, PROTOCOL_VERSION, Request,
        RequestCommand, Response, ResponseResult, decode_frame_ack, decode_legacy_response,
        decode_response, encode_frame_header, encode_legacy_request, encode_request,
    };
    use std::fs;
    use std::io::{Read, Write};
    use std::os::unix::net::{UnixListener, UnixStream};
    use std::path::PathBuf;
    use std::thread;
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    struct ServerThread {
        socket: PathBuf,
        thread: Option<thread::JoinHandle<()>>,
    }

    impl ServerThread {
        fn start() -> Self {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let socket = std::env::temp_dir().join(format!("ls-engine-test-{nonce}.sock"));
            let thread_socket = socket.clone();
            let thread = thread::spawn(move || serve(thread_socket).unwrap());
            for _ in 0..100 {
                if socket.exists() {
                    return Self {
                        socket,
                        thread: Some(thread),
                    };
                }
                thread::sleep(Duration::from_millis(5));
            }
            panic!("engine test server did not create its socket");
        }

        fn request(&self, command: RequestCommand) -> Response {
            let request = Request::new("test-request", command);
            let mut stream = UnixStream::connect(&self.socket).unwrap();
            stream
                .write_all(&encode_request(&request).unwrap())
                .unwrap();
            let mut response = Vec::new();
            let mut byte = [0u8; 1];
            while byte[0] != b'\n' {
                stream.read_exact(&mut byte).unwrap();
                if byte[0] == b'\n' {
                    break;
                }
                response.push(byte[0]);
            }
            decode_response(&response).unwrap()
        }

        fn legacy_request(&self, command: LegacyRequestCommand) -> super::LegacyResponse {
            let request = LegacyRequest::new("legacy-test-request", command);
            let mut stream = UnixStream::connect(&self.socket).unwrap();
            stream
                .write_all(&encode_legacy_request(&request).unwrap())
                .unwrap();
            let mut response = Vec::new();
            let mut byte = [0u8; 1];
            loop {
                stream.read_exact(&mut byte).unwrap();
                if byte[0] == b'\n' {
                    break;
                }
                response.push(byte[0]);
            }
            decode_legacy_response(&response).unwrap()
        }

        fn shutdown(mut self) {
            let response = self.request(RequestCommand::Shutdown);
            assert!(matches!(response.result, ResponseResult::Shutdown));
            self.thread.take().unwrap().join().unwrap();
            assert!(!self.socket.exists());
        }
    }

    fn mock_config(pixel_counts: &[usize]) -> EngineConfig {
        EngineConfig::new(
            pixel_counts
                .iter()
                .enumerate()
                .map(|(index, pixel_count)| OutputConfig {
                    id: format!("strip-{index}"),
                    pixel_count: *pixel_count,
                    reversed: index == 1,
                    backend: OutputBackend::Mock,
                })
                .collect(),
        )
        .unwrap()
    }

    fn send_frame(stream: &mut UnixStream, sequence: u64, frames: &[&[u8]]) -> FrameAck {
        let payload_bytes = frames.iter().map(|frame| frame.len()).sum::<usize>();
        let header: [u8; FRAME_HEADER_BYTES] = encode_frame_header(FrameHeader {
            output_count: frames.len() as u16,
            sequence,
            payload_bytes: payload_bytes as u32,
        })
        .unwrap();
        stream.write_all(&header).unwrap();
        for frame in frames {
            stream.write_all(frame).unwrap();
        }
        let mut ack = [0u8; FRAME_ACK_BYTES];
        stream.read_exact(&mut ack).unwrap();
        decode_frame_ack(&ack).unwrap()
    }

    fn health(response: Response) -> (u64, u64, usize) {
        match response.result {
            ResponseResult::Health {
                frames_rendered,
                dropped_frames,
                output_count,
                ..
            } => (frames_rendered, dropped_frames, output_count),
            other => panic!("unexpected health result {other:?}"),
        }
    }

    #[test]
    fn unix_socket_configures_two_outputs_and_accepts_compact_binary_frames() {
        let server = ServerThread::start();
        let response = server.request(RequestCommand::Health);
        assert_eq!(health(response), (0, 0, 0));
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[2, 1]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured {
                output_count: 2,
                total_pixels: 3
            }
        ));

        let mut frames = UnixStream::connect(&server.socket).unwrap();
        let ack = send_frame(
            &mut frames,
            7,
            &[&[1, 2, 3, 255, 4, 5, 6, 255], &[7, 8, 9, 255]],
        );
        assert_eq!(ack.status, FrameAckStatus::Accepted);
        assert_eq!(ack.sequence, 7);

        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let current = health(server.request(RequestCommand::Health));
            if current.0 == 1 {
                assert_eq!(current, (1, 0, 2));
                break;
            }
            assert!(Instant::now() < deadline, "frame was not transmitted");
            thread::sleep(Duration::from_millis(5));
        }
        // Shutdown must also release an idle persistent frame stream promptly.
        server.shutdown();
    }

    #[test]
    fn legacy_v1_clients_keep_single_output_health_render_and_shutdown() {
        let server = ServerThread::start();
        let health = server.legacy_request(LegacyRequestCommand::Health);
        assert!(matches!(
            health.result,
            LegacyResponseResult::Health {
                status: super::EngineStatus::Ready,
                pixel_count: None,
                frames_rendered: 0
            }
        ));

        let configured = server.legacy_request(LegacyRequestCommand::Configure {
            config: LegacyConfig { pixel_count: 2 },
        });
        assert!(matches!(
            configured.result,
            LegacyResponseResult::Configured { pixel_count: 2 }
        ));

        let rendered = server.legacy_request(LegacyRequestCommand::Render {
            pixels: vec![[10, 20, 30], [40, 50, 60]],
        });
        assert!(matches!(
            rendered.result,
            LegacyResponseResult::Rendered {
                frame_index: 1,
                pixel_count: 2
            }
        ));

        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let health = server.legacy_request(LegacyRequestCommand::Health);
            match health.result {
                LegacyResponseResult::Health {
                    pixel_count: Some(2),
                    frames_rendered: 1,
                    ..
                } => break,
                LegacyResponseResult::Health { .. } => {
                    assert!(
                        Instant::now() < deadline,
                        "legacy frame was not transmitted"
                    );
                    thread::sleep(Duration::from_millis(5));
                }
                other => panic!("unexpected health result {other:?}"),
            }
        }

        let shutdown = server.legacy_request(LegacyRequestCommand::Shutdown);
        assert!(matches!(shutdown.result, LegacyResponseResult::Shutdown));
        server.thread.unwrap().join().unwrap();
        assert!(!server.socket.exists());
    }

    #[test]
    fn rejects_unsupported_versions_and_invalid_configurations() {
        let server = ServerThread::start();
        let mut unsupported = UnixStream::connect(&server.socket).unwrap();
        let mut request = Request::new("old-client", RequestCommand::Health);
        request.protocol_version = PROTOCOL_VERSION + 1;
        unsupported
            .write_all(&encode_request(&request).unwrap())
            .unwrap();
        let mut line = Vec::new();
        let mut byte = [0u8; 1];
        loop {
            unsupported.read_exact(&mut byte).unwrap();
            if byte[0] == b'\n' {
                break;
            }
            line.push(byte[0]);
        }
        let response = decode_response(&line).unwrap();
        assert!(matches!(
            response.result,
            ResponseResult::Error {
                code: super::EngineErrorCode::UnsupportedVersion,
                ..
            }
        ));

        let invalid = EngineConfig {
            outputs: vec![OutputConfig {
                id: "bad".into(),
                pixel_count: 0,
                reversed: false,
                backend: OutputBackend::Mock,
            }],
        };
        let response = server.request(RequestCommand::Configure { config: invalid });
        assert!(matches!(
            response.result,
            ResponseResult::Error {
                code: super::EngineErrorCode::InvalidConfig,
                ..
            }
        ));
        server.shutdown();
    }

    #[test]
    fn malformed_control_requests_do_not_stop_the_server() {
        let server = ServerThread::start();
        let malformed = Request::new("", RequestCommand::Health);
        let mut stream = UnixStream::connect(&server.socket).unwrap();
        stream
            .write_all(&encode_request(&malformed).unwrap())
            .unwrap();
        let mut response = [0u8; 1];
        assert_eq!(stream.read(&mut response).unwrap(), 0);

        assert_eq!(health(server.request(RequestCommand::Health)), (0, 0, 0));
        server.shutdown();
    }

    #[test]
    fn latest_pending_frame_replaces_stale_frame() {
        let frame_buffers = std::array::from_fn(|_| Some(FrameSet::new(&[1])));
        let mut queue = WorkQueue {
            frame_buffers,
            ..WorkQueue::default()
        };
        assert!(!queue.enqueue_latest_frame(1, &[1, 2, 3, 4]).unwrap());
        assert!(queue.enqueue_latest_frame(2, &[5, 6, 7, 8]).unwrap());
        let pending = queue.pending_frame.unwrap();
        assert_eq!(queue.frame_buffers[pending].as_ref().unwrap().sequence, 2);
        assert_eq!(
            queue.frame_buffers[pending].as_ref().unwrap().frames[0],
            [5, 6, 7, 8]
        );
    }

    #[test]
    fn work_queue_allows_only_one_pending_configuration() {
        let mut queue = WorkQueue::default();
        assert_eq!(queue.reserve_configure(), Ok(()));
        assert_eq!(
            queue.reserve_configure(),
            Err("an output configuration is already pending")
        );
        queue.command_pending = false;
        assert_eq!(queue.reserve_configure(), Ok(()));
        queue.closed = true;
        assert_eq!(
            queue.reserve_configure(),
            Err("output worker is shutting down")
        );
    }

    #[test]
    fn preallocated_frame_slots_keep_active_ownership_separate_from_pending() {
        let mut queue = WorkQueue {
            frame_buffers: std::array::from_fn(|_| Some(FrameSet::new(&[1]))),
            ..WorkQueue::default()
        };
        assert!(!queue.enqueue_latest_frame(1, &[1, 1, 1, 255]).unwrap());
        let active_index = queue.pending_frame.take().unwrap();
        let active = queue.frame_buffers[active_index].take().unwrap();

        assert!(!queue.enqueue_latest_frame(2, &[2, 2, 2, 255]).unwrap());
        assert!(queue.enqueue_latest_frame(3, &[3, 3, 3, 255]).unwrap());
        let pending_index = queue.pending_frame.unwrap();
        assert_ne!(active_index, pending_index);
        assert_eq!(active.sequence, 1);
        assert_eq!(
            queue.frame_buffers[pending_index]
                .as_ref()
                .unwrap()
                .sequence,
            3
        );
        queue.frame_buffers[active_index] = Some(active);
    }

    #[test]
    fn health_reports_nearest_rank_output_latency_percentiles() {
        let mut health = RuntimeHealth::new();
        health
            .frame_latency_samples_ms
            .extend([1.0, 2.0, 3.0, 4.0, 100.0]);

        assert_eq!(health.frame_latency_percentile_ms(0.50), Some(3.0));
        assert_eq!(health.frame_latency_p95_ms(), Some(100.0));
        assert_eq!(health.frame_latency_percentile_ms(0.99), Some(100.0));
    }

    #[test]
    fn health_keeps_queue_wait_transmission_and_skew_separate() {
        let mut health = RuntimeHealth::new();
        health.record_frame(9, Instant::now(), 1.25, Some(2.5), Some(0.75));

        assert!(health.frame_latency_ms.is_some());
        assert_eq!(health.queue_latency_ms, Some(1.25));
        assert_eq!(health.transmission_ms, Some(2.5));
        assert_eq!(health.sequential_skew_ms, Some(0.75));
    }

    #[test]
    fn health_is_available_before_configuration() {
        let server = EngineServer::new();
        assert!(matches!(
            server.output.health_response(),
            ResponseResult::Health {
                configured: false,
                output_count: 0,
                ..
            }
        ));
        server.output.shutdown().unwrap();
    }

    #[test]
    fn does_not_replace_a_regular_file_at_the_socket_path() {
        let path = std::env::temp_dir().join(format!(
            "ls-engine-not-socket-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::write(&path, b"keep").unwrap();
        let error = remove_stale_socket(&path).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::AlreadyExists);
        assert_eq!(fs::read(&path).unwrap(), b"keep");
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn does_not_replace_an_active_socket() {
        let path = std::env::temp_dir().join(format!(
            "ls-engine-active-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _listener = UnixListener::bind(&path).unwrap();
        let error = remove_stale_socket(&path).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::AddrInUse);
        assert!(path.exists());
    }
}
