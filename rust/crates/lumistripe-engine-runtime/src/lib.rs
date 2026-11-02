//! Single-process output ownership, bounded frame queue, IPC, and lifecycle.

mod activity_gate;
mod audio_worker;
mod beat_clock;
mod effect_scheduler;
mod latest_slot;
mod quantized_switch;
mod sequence;

#[cfg(all(test, feature = "loom-tests"))]
mod loom_tests;

use activity_gate::{MusicActivityTracker, MusicGateState};
use audio_worker::NativeAudioWorker;
use beat_clock::BeatClockTracker;
use effect_scheduler::EffectScheduleCoordinator;
use latest_slot::LatestSlot;
#[cfg(all(test, feature = "loom-tests"))]
use loom::sync::{Condvar as QueueCondvar, Mutex as QueueMutex};
use lumistripe_engine_animation_definitions::catalog;
use lumistripe_engine_animation_state::{
    AnimationInstance, RenderingInput, supported_animations, supports_animation,
};
use lumistripe_engine_compositing::{TransitionMode, apply_transition, screen_composite};
use lumistripe_engine_config::{
    EngineConfig, MAX_OUTPUT_COUNT, MAX_PIXEL_COUNT, OutputBackend, OutputConfig,
};
use lumistripe_engine_effects::{EffectStack, PythonRandom};
use lumistripe_engine_output::{OutputError, OutputSet};
use lumistripe_engine_protocol::{
    EngineErrorCode, EngineStatus, FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus,
    LEGACY_PROTOCOL_VERSION, LegacyErrorCode, LegacyRequest, LegacyRequestCommand, LegacyResponse,
    LegacyResponseResult, MAX_CONTROL_MESSAGE_BYTES, MAX_FRAME_BYTES, PREVIOUS_PROTOCOL_VERSION,
    PROTOCOL_VERSION, RenderAnimationChoice, RenderAudioInput, RenderAudioUpdate,
    RenderBeatClockState, RenderCycleOrder, RenderCycleTiming, RenderCycling,
    RenderDynamicIdleMode, RenderDynamicSelector, RenderEffect, RenderEffectSchedulerState,
    RenderFrameRequest, RenderMusicStructure, RenderOutput, RenderPowerConfig, RenderSceneUpdate,
    RenderSchedulerConfig, RenderSelectorFeatures, RenderSequencePlaybackState,
    RenderStructureEvent, RenderTransition, Request, RequestCommand, Response, ResponseResult,
    decode_frame_header, decode_legacy_request, decode_request, encode_frame_ack,
    encode_legacy_response, encode_response,
};
use lumistripe_engine_render_primitives::{AudioInput, FrameBuffer, Rgba, ZoneMapping, alpha_byte};
use quantized_switch::QuantizedSwitchState;
use sequence::SequenceCoordinator;
use serde::Deserialize;
use std::collections::VecDeque;
use std::fs;
use std::io::{self, Read, Write};
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Mutex};
#[cfg(not(all(test, feature = "loom-tests")))]
use std::sync::{Condvar as QueueCondvar, Mutex as QueueMutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const CONFIGURE_TIMEOUT: Duration = Duration::from_secs(10);
const FRAME_LATENCY_SAMPLE_COUNT: usize = 128;
const CONTROL_REQUEST_TIMEOUT: Duration = Duration::from_secs(2);
const FRAME_PACKET_TIMEOUT: Duration = Duration::from_secs(2);
const FRAME_IDLE_POLL_INTERVAL: Duration = Duration::from_millis(100);
const SOCKET_WRITE_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_IPC_CONNECTIONS: usize = 16;
const MAX_RENDER_PREVIEW_PIXELS: usize = 128;
const MAX_RENDER_PREVIEW_BYTES: usize = MAX_RENDER_PREVIEW_PIXELS * 4;

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
    audio: NativeAudioWorker,
    output: OutputWorker,
    stopping: AtomicBool,
    legacy_commands: Mutex<()>,
    legacy_pixel_count: Mutex<Option<usize>>,
    legacy_frame_index: AtomicU64,
}

impl EngineServer {
    fn new() -> Self {
        let output = OutputWorker::new();
        let audio = NativeAudioWorker::new(output.audio_handle());
        Self {
            audio,
            output,
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
    let request_protocol_version = request.protocol_version;
    let (mut response, should_stop) = dispatch(request, &server);
    if matches!(
        request_protocol_version,
        PREVIOUS_PROTOCOL_VERSION | PROTOCOL_VERSION
    ) {
        response.protocol_version = request_protocol_version;
    }
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
    let protocol_version = request.protocol_version;
    if protocol_version != PROTOCOL_VERSION && protocol_version != PREVIOUS_PROTOCOL_VERSION {
        return (
            Response::new(
                request_id,
                ResponseResult::Error {
                    code: EngineErrorCode::UnsupportedVersion,
                    message: format!(
                        "protocol version {} is unsupported; expected {PROTOCOL_VERSION}",
                        protocol_version
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
        RequestCommand::RenderCapabilities => (
            Response::new(
                request_id,
                ResponseResult::RenderCapabilities {
                    animations: supported_animations()
                        .iter()
                        .map(|name| (*name).to_owned())
                        .collect(),
                },
            ),
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
        RequestCommand::Render { frame, scheduler } => {
            let sequence = frame.sequence;
            match server.output.submit_render(frame, scheduler) {
                Ok(replaced) => (
                    Response::new(
                        request_id,
                        ResponseResult::RenderQueued { sequence, replaced },
                    ),
                    false,
                ),
                Err(status) => {
                    let (code, message) = match status {
                        FrameAckStatus::NotConfigured => (
                            EngineErrorCode::NotConfigured,
                            "Rust engine has no configured outputs".to_owned(),
                        ),
                        FrameAckStatus::ShuttingDown => (
                            EngineErrorCode::ShuttingDown,
                            "Rust engine is shutting down".to_owned(),
                        ),
                        FrameAckStatus::InvalidFrame | FrameAckStatus::Accepted => (
                            EngineErrorCode::InvalidRender,
                            "Rust engine rejected an invalid render request".to_owned(),
                        ),
                    };
                    (
                        Response::new(request_id, ResponseResult::Error { code, message }),
                        false,
                    )
                }
            }
        }
        RequestCommand::SceneUpdate { update } => {
            let revision = update.revision;
            match server.output.submit_scene_update(update) {
                Ok(replaced) => (
                    Response::new(
                        request_id,
                        ResponseResult::SceneUpdated { revision, replaced },
                    ),
                    false,
                ),
                Err(status) => {
                    let (code, message) = render_error(status);
                    (
                        Response::new(request_id, ResponseResult::Error { code, message }),
                        false,
                    )
                }
            }
        }
        RequestCommand::AudioUpdate { update } => {
            let sequence = update.sequence;
            match server.output.submit_audio_update(update) {
                Ok(accepted) => (
                    Response::new(
                        request_id,
                        ResponseResult::AudioUpdated { sequence, accepted },
                    ),
                    false,
                ),
                Err(status) => {
                    let (code, message) = render_error(status);
                    (
                        Response::new(request_id, ResponseResult::Error { code, message }),
                        false,
                    )
                }
            }
        }
        RequestCommand::AudioStart { selector, config } => {
            if protocol_version != PROTOCOL_VERSION {
                return unsupported_audio_protocol(request_id, protocol_version);
            }
            if let Err(error) = config.validate() {
                return (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::InvalidConfig,
                            message: error,
                        },
                    ),
                    false,
                );
            }
            match server.audio.start(selector, config) {
                Ok(active) => (
                    Response::new(request_id, ResponseResult::AudioConfigured { active }),
                    false,
                ),
                Err(message) => (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::AudioFailure,
                            message,
                        },
                    ),
                    false,
                ),
            }
        }
        RequestCommand::AudioConfigure { config } => {
            if protocol_version != PROTOCOL_VERSION {
                return unsupported_audio_protocol(request_id, protocol_version);
            }
            if let Err(message) = config.validate() {
                return (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::InvalidConfig,
                            message,
                        },
                    ),
                    false,
                );
            }
            match server.audio.configure(config) {
                Ok(active) => (
                    Response::new(request_id, ResponseResult::AudioConfigured { active }),
                    false,
                ),
                Err(message) => (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::AudioFailure,
                            message,
                        },
                    ),
                    false,
                ),
            }
        }
        RequestCommand::AudioStop => {
            if protocol_version != PROTOCOL_VERSION {
                return unsupported_audio_protocol(request_id, protocol_version);
            }
            match server.audio.stop() {
                Ok(()) => (
                    Response::new(request_id, ResponseResult::AudioStopped),
                    false,
                ),
                Err(message) => (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::AudioFailure,
                            message,
                        },
                    ),
                    false,
                ),
            }
        }
        RequestCommand::AudioDevices => {
            if protocol_version != PROTOCOL_VERSION {
                return unsupported_audio_protocol(request_id, protocol_version);
            }
            match server.audio.devices() {
                Ok(devices) => (
                    Response::new(request_id, ResponseResult::AudioDevices { devices }),
                    false,
                ),
                Err(message) => (
                    Response::new(
                        request_id,
                        ResponseResult::Error {
                            code: EngineErrorCode::AudioFailure,
                            message,
                        },
                    ),
                    false,
                ),
            }
        }
        RequestCommand::AudioStatus => {
            if protocol_version != PROTOCOL_VERSION {
                return unsupported_audio_protocol(request_id, protocol_version);
            }
            (
                Response::new(
                    request_id,
                    ResponseResult::AudioStatus {
                        status: server.audio.status(),
                    },
                ),
                false,
            )
        }
        RequestCommand::Shutdown => {
            server.audio.shutdown();
            match server.output.shutdown() {
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
            }
        }
    }
}

fn unsupported_audio_protocol(request_id: String, protocol_version: u16) -> (Response, bool) {
    (
        Response::new(
            request_id,
            ResponseResult::Error {
                code: EngineErrorCode::UnsupportedVersion,
                message: format!(
                    "Rust audio commands require protocol version {PROTOCOL_VERSION}; got {protocol_version}"
                ),
            },
        ),
        false,
    )
}

fn render_error(status: FrameAckStatus) -> (EngineErrorCode, String) {
    match status {
        FrameAckStatus::NotConfigured => (
            EngineErrorCode::NotConfigured,
            "Rust engine has no configured outputs".to_owned(),
        ),
        FrameAckStatus::ShuttingDown => (
            EngineErrorCode::ShuttingDown,
            "Rust engine is shutting down".to_owned(),
        ),
        FrameAckStatus::InvalidFrame | FrameAckStatus::Accepted => (
            EngineErrorCode::InvalidRender,
            "Rust engine rejected an invalid render update".to_owned(),
        ),
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

#[cfg(all(test, not(feature = "loom-tests")))]
fn render_frame_request(
    request: &RenderFrameRequest,
    frame: &mut FrameSet,
    renderers: &mut [Option<RenderOutputState>],
) -> Result<RenderPowerSummary, String> {
    render_frame_request_at(
        request,
        request.timestamp_ns,
        request.delta_ns,
        frame,
        renderers,
    )
}

fn render_frame_request_at(
    request: &RenderFrameRequest,
    timestamp_ns: u64,
    delta_ns: u64,
    frame: &mut FrameSet,
    renderers: &mut [Option<RenderOutputState>],
) -> Result<RenderPowerSummary, String> {
    if request.outputs.len() != frame.frames.len() || request.outputs.len() != renderers.len() {
        return Err("render output count does not match configured buffers".to_owned());
    }
    if request.outputs.len() > 2 || !valid_render_power_config(&request.power) {
        return Err("render power configuration is invalid".to_owned());
    }
    let input = RenderingInput {
        timestamp_ns,
        delta_ns,
        brightness: request.brightness,
        audio: request.audio.as_ref().map(|audio| AudioInput {
            rms: audio.rms,
            bands: audio.bands,
            beat: audio.beat,
            beat_strength: audio.beat_strength,
        }),
    };
    for (index, render) in request.outputs.iter().enumerate() {
        if render.zone_count == 0
            || render.zone_count > frame.frames[index].len() / 4
            || !matches!(render.zone_pattern.as_str(), "repeat" | "alternate")
        {
            return Err("render output has invalid zone mapping geometry".to_owned());
        }
        if !valid_render_output_parameters(render) {
            return Err("render output parameters are invalid".to_owned());
        }
        let physical_pixel_count = frame.frames[index].len() / 4;
        let logical_pixel_count = physical_pixel_count.div_ceil(render.zone_count);
        let scene_pixel_count = render.scene_pixel_count.unwrap_or(logical_pixel_count);
        let scene_end = render
            .scene_offset
            .checked_add(logical_pixel_count)
            .ok_or_else(|| "render scene slice geometry overflows".to_owned())?;
        if scene_pixel_count == 0
            || scene_pixel_count > MAX_PIXEL_COUNT * MAX_OUTPUT_COUNT
            || (render.scene_resample
                && (render.scene_pixel_count.is_none()
                    || render.scene_offset != 0
                    || scene_pixel_count < logical_pixel_count))
            || (!render.scene_resample && scene_end > scene_pixel_count)
            || (render.scene_offset > 0 && render.scene_pixel_count.is_none())
        {
            return Err("render scene slice geometry is invalid".to_owned());
        }
        let state = renderers[index].as_ref();
        let needs_animation_validation = state.is_none_or(|state| {
            state.animation.as_ref().is_none_or(|instance| {
                instance.name() != render.animation || !instance.settings_match(&render.settings)
            })
        });
        if needs_animation_validation {
            AnimationInstance::new(&render.animation, &render.settings)
                .map_err(|error| error.to_string())?;
        }
        if state.is_none_or(|state| {
            !state.geometry_matches(physical_pixel_count, render)
                || state.effect_definitions != render.effects
                || state.max_overlay_strength != render.max_overlay_strength as f32
        }) {
            let mut probe = RenderOutputState::new(scene_pixel_count)?;
            probe.configure_effects(&render.effects, render.max_overlay_strength)?;
        }
    }
    for (index, render) in request.outputs.iter().enumerate() {
        let renderer = &mut renderers[index];
        if renderer
            .as_ref()
            .is_none_or(|state| !state.geometry_matches(frame.frames[index].len() / 4, render))
        {
            let physical_pixel_count = frame.frames[index].len() / 4;
            let logical_pixel_count = physical_pixel_count.div_ceil(render.zone_count);
            let scene_pixel_count = render.scene_pixel_count.unwrap_or(logical_pixel_count);
            let mut state = RenderOutputState::new(scene_pixel_count)?;
            state.configure_geometry(physical_pixel_count, render)?;
            *renderer = Some(state);
        }
        let renderer = renderer.as_mut().expect("renderer was initialized");
        let scene_changed = renderer.configure_animation(
            &render.animation,
            &render.settings,
            render.scene_revision,
        )?;
        renderer.configure_effects(&render.effects, render.max_overlay_strength)?;
        let bytes = std::mem::take(&mut frame.frames[index]);
        let mut output = FrameBuffer::from_bytes(bytes).map_err(|error| error.to_string())?;
        let output_input = RenderingInput {
            brightness: render.brightness.unwrap_or(input.brightness),
            audio: render.audio_enabled.then_some(input.audio).flatten(),
            ..input
        };
        let rendered = renderer.render(
            output_input,
            render.base_intensity,
            scene_changed,
            render.transition.as_ref(),
            render.solid_color,
            &mut output,
        );
        frame.frames[index] = output.into_bytes();
        rendered.map_err(|error| error.to_string())?;
    }
    finalize_render_frames(request, &mut frame.frames)
}

#[derive(Clone, Debug, PartialEq)]
struct RenderPowerSummary {
    estimated_watts: f64,
    applied_scale: f64,
    output_watts: [f64; 2],
    output_count: usize,
    limiting_output_index: Option<usize>,
    preview_rgba: [[u8; MAX_RENDER_PREVIEW_BYTES]; 2],
    preview_counts: [usize; 2],
}

fn valid_render_power_config(power: &RenderPowerConfig) -> bool {
    power
        .budget_watts
        .is_none_or(|budget| budget.is_finite() && budget >= 0.0)
        && (!power.enabled || power.budget_watts.is_some())
}

fn finalize_render_frames(
    request: &RenderFrameRequest,
    frames: &mut [Vec<u8>],
) -> Result<RenderPowerSummary, String> {
    if request.outputs.len() != frames.len() || request.outputs.len() > 2 {
        return Err("render output count does not match power configuration".to_owned());
    }
    if !valid_render_power_config(&request.power) {
        return Err("render power configuration is invalid".to_owned());
    }

    let mut watts = [0.0; 2];
    let mut estimated_watts = 0.0;
    for (index, (output, frame)) in request.outputs.iter().zip(frames.iter_mut()).enumerate() {
        if frame.len() % 4 != 0 {
            return Err("render output is not an RGBA frame".to_owned());
        }
        if output.blackout {
            frame.fill(0);
            continue;
        }
        let [red_scale, green_scale, blue_scale] = output.color_correction;
        if output.color_correction != [255; 3] {
            for pixel in frame.chunks_exact_mut(4) {
                pixel[0] = ((u16::from(pixel[0]) * u16::from(red_scale)) / 255) as u8;
                pixel[1] = ((u16::from(pixel[1]) * u16::from(green_scale)) / 255) as u8;
                pixel[2] = ((u16::from(pixel[2]) * u16::from(blue_scale)) / 255) as u8;
            }
        }
        let alpha_weighted_channels = frame
            .chunks_exact(4)
            .map(|pixel| {
                (f64::from(pixel[0]) + f64::from(pixel[1]) + f64::from(pixel[2]))
                    * f64::from(pixel[3])
            })
            .sum::<f64>();
        let output_watts = alpha_weighted_channels / (3.0 * 255.0 * 255.0)
            * output.full_white_current_a
            * output.voltage_v;
        if !output_watts.is_finite() {
            return Err("render output power estimate is not finite".to_owned());
        }
        watts[index] = output_watts;
        estimated_watts += output_watts;
    }

    if !estimated_watts.is_finite() {
        return Err("render power estimate is not finite".to_owned());
    }
    let mut applied_scale = 1.0_f64;
    let mut limiting_output_index = None;
    if request.power.enabled {
        let budget = request.power.budget_watts.expect("validated budget");
        let mut limiting_candidate = None;
        if estimated_watts > 0.0 {
            limiting_candidate = Some((budget / estimated_watts, None));
        }
        for (index, (output, output_watts)) in request.outputs.iter().zip(watts).enumerate() {
            if let Some(limit) = output.power_limit_watts
                && output_watts > 0.0
            {
                let candidate = limit / output_watts;
                if limiting_candidate.is_none_or(|(current, _)| candidate < current) {
                    limiting_candidate = Some((candidate, Some(index)));
                }
            }
        }
        if let Some((candidate, output_index)) = limiting_candidate {
            applied_scale = candidate.clamp(0.0, 1.0);
            limiting_output_index = output_index;
        }
    }
    if applied_scale < 1.0 {
        for (output, frame) in request.outputs.iter().zip(frames.iter_mut()) {
            if output.blackout {
                continue;
            }
            for pixel in frame.chunks_exact_mut(4) {
                pixel[3] = (f64::from(pixel[3]) * applied_scale).floor() as u8;
            }
        }
    }
    let mut preview_rgba = [[0; MAX_RENDER_PREVIEW_BYTES]; 2];
    let mut preview_counts = [0; 2];
    for (index, (output, frame)) in request.outputs.iter().zip(frames.iter()).enumerate() {
        preview_counts[index] =
            sample_preview_pixels(frame, output.reversed, &mut preview_rgba[index]);
    }
    Ok(RenderPowerSummary {
        estimated_watts,
        applied_scale,
        output_watts: watts,
        output_count: request.outputs.len(),
        limiting_output_index,
        preview_rgba,
        preview_counts,
    })
}

fn sample_preview_pixels(
    source: &[u8],
    reversed: bool,
    destination: &mut [u8; MAX_RENDER_PREVIEW_BYTES],
) -> usize {
    let pixel_count = source.len() / 4;
    let sample_count = pixel_count.min(MAX_RENDER_PREVIEW_PIXELS);
    if sample_count == 0 {
        return 0;
    }
    for sample_index in 0..sample_count {
        let mut source_index = if sample_count == 1 {
            0
        } else {
            sample_index * (pixel_count - 1) / (sample_count - 1)
        };
        if reversed {
            source_index = pixel_count - 1 - source_index;
        }
        let source_offset = source_index * 4;
        let destination_offset = sample_index * 4;
        destination[destination_offset..destination_offset + 4]
            .copy_from_slice(&source[source_offset..source_offset + 4]);
    }
    sample_count
}

fn valid_render_output_parameters(output: &RenderOutput) -> bool {
    output.base_intensity.is_finite()
        && (0.0..=1.0).contains(&output.base_intensity)
        && output.max_overlay_strength.is_finite()
        && (0.0 < output.max_overlay_strength && output.max_overlay_strength <= 1.0)
        && output
            .brightness
            .is_none_or(|brightness| brightness.is_finite() && (0.0..=1.0).contains(&brightness))
        && output.effects.len() <= 2
        && output
            .music_structure
            .is_none_or(|structure| structure.is_valid())
        && output.scene_pixel_count.is_none_or(|pixel_count| {
            pixel_count > 0 && pixel_count <= MAX_PIXEL_COUNT * MAX_OUTPUT_COUNT
        })
        && (!output.scene_resample
            || (output.scene_pixel_count.is_some() && output.scene_offset == 0))
        && (output.solid_color.is_none() || output.effects.is_empty())
        && output.zone_count > 0
        && matches!(output.zone_pattern.as_str(), "repeat" | "alternate")
        && output.transition.as_ref().is_none_or(|transition| {
            transition.duration_ns > 0
                && matches!(
                    transition.mode.as_str(),
                    "crossfade" | "fade_black" | "wipe"
                )
        })
        && output.effects.iter().all(|effect| {
            !effect.name.is_empty()
                && effect.intensity.is_finite()
                && (0.0..=1.0).contains(&effect.intensity)
                && effect.trigger_beat_strength.is_finite()
                && (0.0..=1.0).contains(&effect.trigger_beat_strength)
        })
        && output.voltage_v.is_finite()
        && output.voltage_v > 0.0
        && output.full_white_current_a.is_finite()
        && output.full_white_current_a > 0.0
        && (output.full_white_current_a * output.voltage_v).is_finite()
        && output.full_white_current_a * output.voltage_v
            <= f64::MAX / (MAX_OUTPUT_COUNT * MAX_PIXEL_COUNT) as f64
        && output
            .power_limit_watts
            .is_none_or(|limit| limit.is_finite() && limit > 0.0)
}

fn valid_structure_events(events: &[RenderStructureEvent], output_count: usize) -> bool {
    if events.len() > lumistripe_engine_protocol::MAX_RENDER_STRUCTURE_EVENTS
        || output_count > MAX_OUTPUT_COUNT
    {
        return false;
    }
    let mut last_sequences = [None; MAX_OUTPUT_COUNT];
    for event in events {
        if event.output_index >= output_count || !event.is_valid() {
            return false;
        }
        let last = &mut last_sequences[event.output_index];
        if last.is_some_and(|sequence| event.sequence <= sequence) {
            return false;
        }
        *last = Some(event.sequence);
    }
    true
}

fn valid_audio_input(audio: &RenderAudioInput, output_count: usize) -> bool {
    audio.rms.is_finite()
        && audio.beat_strength.is_finite()
        && audio.bands.iter().all(|band| band.is_finite())
        && audio.fallback_bpm.is_none_or(f64::is_finite)
        && audio
            .beat_events
            .iter()
            .all(|event| event.strength.is_finite() && (0.0..=1.0).contains(&event.strength))
        && audio
            .selector_features
            .is_none_or(RenderSelectorFeatures::is_valid)
        && audio
            .effect_features
            .is_none_or(lumistripe_engine_protocol::RenderEffectFeatures::is_valid)
        && valid_structure_events(&audio.structure_events, output_count)
}

fn audio_feature_to_led_latency_ms(
    output_timestamp_ns: u64,
    feature_timestamp_ns: u64,
    output_elapsed: Duration,
) -> Option<f64> {
    (feature_timestamp_ns <= output_timestamp_ns).then(|| {
        output_timestamp_ns.saturating_sub(feature_timestamp_ns) as f64 / 1_000_000.0
            + output_elapsed.as_secs_f64() * 1000.0
    })
}

#[derive(Debug)]
struct RenderOutputState {
    animation: Option<AnimationInstance>,
    effects: Option<EffectStack>,
    effect_definitions: Vec<RenderEffect>,
    max_overlay_strength: f32,
    base_frame: Option<FrameBuffer>,
    logical_frame: Option<FrameBuffer>,
    output_frame: Option<FrameBuffer>,
    pixel_count: usize,
    output_pixel_count: usize,
    physical_pixel_count: usize,
    scene_offset: usize,
    zone_count: usize,
    zone_pattern: String,
    scene_resample: bool,
    zone_mapping: Option<ZoneMapping>,
    last_frame: Option<FrameBuffer>,
    last_animation_name: Option<String>,
    last_scene_revision: Option<u64>,
    transition_source: Option<FrameBuffer>,
    transition_frame: Option<FrameBuffer>,
    active_transition: Option<RenderTransition>,
}

impl RenderOutputState {
    fn new(pixel_count: usize) -> Result<Self, String> {
        if pixel_count == 0 {
            return Err("render output buffer must contain pixels".to_owned());
        }
        Ok(Self {
            animation: None,
            effects: None,
            effect_definitions: Vec::new(),
            max_overlay_strength: 0.65,
            base_frame: None,
            logical_frame: None,
            output_frame: None,
            pixel_count,
            output_pixel_count: pixel_count,
            physical_pixel_count: pixel_count,
            scene_offset: 0,
            zone_count: 1,
            zone_pattern: "repeat".to_owned(),
            scene_resample: false,
            zone_mapping: None,
            last_frame: None,
            last_animation_name: None,
            last_scene_revision: None,
            transition_source: None,
            transition_frame: None,
            active_transition: None,
        })
    }

    fn geometry_matches(&self, physical_pixel_count: usize, output: &RenderOutput) -> bool {
        let output_pixel_count = physical_pixel_count.div_ceil(output.zone_count);
        let scene_pixel_count = output.scene_pixel_count.unwrap_or(output_pixel_count);
        self.physical_pixel_count == physical_pixel_count
            && self.zone_count == output.zone_count
            && self.zone_pattern == output.zone_pattern
            && self.output_pixel_count == output_pixel_count
            && self.pixel_count == scene_pixel_count
            && self.scene_offset == output.scene_offset
            && self.scene_resample == output.scene_resample
    }

    fn configure_geometry(
        &mut self,
        physical_pixel_count: usize,
        output: &RenderOutput,
    ) -> Result<(), String> {
        if physical_pixel_count == 0
            || output.zone_count == 0
            || output.zone_count > physical_pixel_count
            || !matches!(output.zone_pattern.as_str(), "repeat" | "alternate")
        {
            return Err("render output has invalid zone mapping geometry".to_owned());
        }
        let output_pixel_count = physical_pixel_count.div_ceil(output.zone_count);
        let scene_pixel_count = output.scene_pixel_count.unwrap_or(output_pixel_count);
        let scene_end = output
            .scene_offset
            .checked_add(output_pixel_count)
            .ok_or_else(|| "render scene slice geometry overflows".to_owned())?;
        if scene_pixel_count == 0
            || (output.scene_resample
                && (output.scene_pixel_count.is_none()
                    || output.scene_offset != 0
                    || scene_pixel_count < output_pixel_count))
            || (!output.scene_resample && scene_end > scene_pixel_count)
        {
            return Err("render scene slice is outside its canvas".to_owned());
        }
        self.physical_pixel_count = physical_pixel_count;
        self.zone_count = output.zone_count;
        self.zone_pattern.clone_from(&output.zone_pattern);
        self.pixel_count = scene_pixel_count;
        self.output_pixel_count = output_pixel_count;
        self.scene_offset = output.scene_offset;
        self.scene_resample = output.scene_resample;
        self.zone_mapping = if output.zone_count > 1 {
            Some(
                ZoneMapping::new(
                    physical_pixel_count,
                    output.zone_count,
                    output.zone_pattern == "alternate",
                )
                .map_err(|error| error.to_string())?,
            )
        } else {
            None
        };
        self.logical_frame =
            Some(FrameBuffer::new(scene_pixel_count).map_err(|error| error.to_string())?);
        self.output_frame =
            Some(FrameBuffer::new(output_pixel_count).map_err(|error| error.to_string())?);
        self.last_frame =
            Some(FrameBuffer::new(scene_pixel_count).map_err(|error| error.to_string())?);
        Ok(())
    }

    fn configure_animation(
        &mut self,
        name: &str,
        settings: &serde_json::Map<String, serde_json::Value>,
        scene_revision: u64,
    ) -> Result<bool, String> {
        let scene_changed = self
            .last_animation_name
            .as_ref()
            .is_some_and(|previous| previous != name)
            || self
                .last_scene_revision
                .is_some_and(|previous| previous != scene_revision);
        let restart_scene = self
            .last_scene_revision
            .is_some_and(|previous| previous != scene_revision);
        if restart_scene
            || self
                .animation
                .as_ref()
                .is_none_or(|instance| instance.name() != name)
        {
            self.animation =
                Some(AnimationInstance::new(name, settings).map_err(|error| error.to_string())?);
        } else if !self
            .animation
            .as_ref()
            .expect("existing animation was checked")
            .settings_match(settings)
        {
            self.animation
                .as_mut()
                .expect("existing animation was checked")
                .set_settings(settings)
                .map_err(|error| error.to_string())?;
        }
        if self.last_animation_name.as_deref() != Some(name) {
            self.last_animation_name = Some(name.to_owned());
        }
        self.last_scene_revision = Some(scene_revision);
        Ok(scene_changed)
    }

    fn configure_effects(
        &mut self,
        definitions: &[RenderEffect],
        max_overlay_strength: f64,
    ) -> Result<(), String> {
        if !max_overlay_strength.is_finite()
            || !(0.0 < max_overlay_strength && max_overlay_strength <= 1.0)
        {
            return Err("render effect overlay strength must be in (0, 1]".to_owned());
        }
        if definitions.len() > 2 {
            return Err("render output supports at most two effects".to_owned());
        }
        if self.effect_definitions == definitions
            && self.max_overlay_strength == max_overlay_strength as f32
        {
            return Ok(());
        }
        let mut stack = match self.effects.take() {
            Some(stack) => stack,
            None => {
                EffectStack::new(max_overlay_strength as f32).map_err(|error| error.to_string())?
            }
        };
        stack
            .set_max_overlay_strength(max_overlay_strength as f32)
            .map_err(|error| error.to_string())?;
        let configurations = definitions
            .iter()
            .map(|effect| {
                (
                    effect.name.as_str(),
                    &effect.settings,
                    effect.intensity as f32,
                )
            })
            .collect::<Vec<_>>();
        stack
            .reconfigure(&configurations, self.pixel_count)
            .map_err(|error| error.to_string())?;
        self.effects = (!definitions.is_empty()).then_some(stack);
        self.effect_definitions = definitions.to_vec();
        self.max_overlay_strength = max_overlay_strength as f32;
        if !definitions.is_empty() && self.base_frame.is_none() {
            self.base_frame =
                Some(FrameBuffer::new(self.pixel_count).map_err(|error| error.to_string())?);
        }
        Ok(())
    }

    fn render(
        &mut self,
        input: RenderingInput,
        base_intensity: f64,
        scene_changed: bool,
        transition: Option<&RenderTransition>,
        solid_color: Option<[u8; 3]>,
        destination: &mut FrameBuffer,
    ) -> Result<(), String> {
        if !base_intensity.is_finite() || !(0.0..=1.0).contains(&base_intensity) {
            return Err("render base intensity must be between zero and one".to_owned());
        }
        if solid_color.is_some() {
            self.active_transition = None;
        } else if scene_changed {
            self.active_transition = None;
            if let (Some(transition), Some(previous_frame)) = (transition, &self.last_frame) {
                if self.transition_source.is_none() {
                    self.transition_source = Some(
                        FrameBuffer::new(self.pixel_count).map_err(|error| error.to_string())?,
                    );
                }
                if self.transition_frame.is_none() {
                    self.transition_frame = Some(
                        FrameBuffer::new(self.pixel_count).map_err(|error| error.to_string())?,
                    );
                }
                self.transition_source
                    .as_mut()
                    .expect("transition source was allocated")
                    .as_bytes_mut()
                    .copy_from_slice(previous_frame.as_bytes());
                self.active_transition = Some(transition.clone());
            }
        }

        let animation = self
            .animation
            .as_mut()
            .expect("render animation was configured");
        let logical = self
            .logical_frame
            .as_mut()
            .expect("logical output frame was allocated");
        if let Some([red, green, blue]) = solid_color {
            logical.fill(Rgba::new(
                red,
                green,
                blue,
                alpha_byte(base_intensity, input.brightness),
            ));
        } else if let Some(effects) = &mut self.effects {
            let base = self
                .base_frame
                .as_mut()
                .expect("effect base frame was allocated");
            animation
                .render_next(input, base)
                .map_err(|error| error.to_string())?;
            let trigger_beats = [
                self.effect_definitions
                    .first()
                    .filter(|effect| effect.trigger_beat)
                    .map(|effect| effect.trigger_beat_strength),
                self.effect_definitions
                    .get(1)
                    .filter(|effect| effect.trigger_beat)
                    .map(|effect| effect.trigger_beat_strength),
            ];
            effects
                .render_and_composite_with_trigger_beats(
                    base,
                    logical,
                    input,
                    base_intensity as f32,
                    &trigger_beats,
                )
                .map_err(|error| error.to_string())?;
        } else {
            animation
                .render_next(input, logical)
                .map_err(|error| error.to_string())?;
            if base_intensity < 1.0 {
                if self.base_frame.is_none() {
                    self.base_frame = Some(
                        FrameBuffer::new(self.pixel_count).map_err(|error| error.to_string())?,
                    );
                }
                let base = self
                    .base_frame
                    .as_mut()
                    .expect("base render frame was allocated");
                base.as_bytes_mut().copy_from_slice(logical.as_bytes());
                screen_composite(base, logical, base_intensity as f32, None, None)
                    .map_err(|error| error.to_string())?;
            }
        }

        if let Some(transition) = &self.active_transition {
            let mode = match transition.mode.as_str() {
                "crossfade" => TransitionMode::Crossfade,
                "fade_black" => TransitionMode::FadeBlack,
                "wipe" => TransitionMode::Wipe,
                _ => unreachable!("transitions were validated before queueing"),
            };
            let amount = input.timestamp_ns.saturating_sub(transition.started_at_ns) as f64
                / transition.duration_ns as f64;
            let transition_frame = self
                .transition_frame
                .as_mut()
                .expect("active transition frame was allocated");
            apply_transition(
                self.transition_source
                    .as_ref()
                    .expect("active transition source was allocated"),
                logical,
                transition_frame,
                mode,
                amount,
            )
            .map_err(|error| error.to_string())?;
            logical
                .as_bytes_mut()
                .copy_from_slice(transition_frame.as_bytes());
            if amount >= 1.0 {
                self.active_transition = None;
            }
        }

        self.last_frame
            .as_mut()
            .expect("last rendered frame was allocated")
            .as_bytes_mut()
            .copy_from_slice(logical.as_bytes());
        let scene_start = self.scene_offset * 4;
        let scene_end = scene_start + self.output_pixel_count * 4;
        let output_logical = self
            .output_frame
            .as_mut()
            .expect("output frame was allocated");
        if self.scene_resample {
            for (index, target) in output_logical
                .as_bytes_mut()
                .chunks_exact_mut(4)
                .enumerate()
            {
                let source_index = if self.output_pixel_count == 1 {
                    0
                } else {
                    ((index as f64 * (self.pixel_count - 1) as f64)
                        / (self.output_pixel_count - 1) as f64)
                        .round_ties_even() as usize
                };
                let source_offset = source_index * 4;
                target.copy_from_slice(&logical.as_bytes()[source_offset..source_offset + 4]);
            }
        } else {
            let scene_slice = logical
                .as_bytes()
                .get(scene_start..scene_end)
                .ok_or_else(|| "render scene slice is outside its canvas".to_owned())?;
            output_logical.as_bytes_mut().copy_from_slice(scene_slice);
        }
        if let Some(mapping) = &self.zone_mapping {
            mapping
                .map_frame(output_logical, destination)
                .map_err(|error| error.to_string())
        } else {
            destination
                .as_bytes_mut()
                .copy_from_slice(output_logical.as_bytes());
            Ok(())
        }
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
    pending_render: LatestSlot<PendingRender>,
    scheduled_render: Option<ScheduledRender>,
    last_render_request_sequence: Option<u64>,
    last_render_request_timestamp_ns: Option<u64>,
    last_scene_timestamp_ns: Option<u64>,
    last_render_timestamp_ns: Option<u64>,
    command_pending: bool,
    closed: bool,
}

struct PendingRender {
    request: Arc<RenderFrameRequest>,
    timestamp_ns: u64,
    delta_ns: u64,
    queued_at: Instant,
}

struct ScheduledRender {
    request: Arc<RenderFrameRequest>,
    period: Duration,
    max_fps: u16,
    next_deadline: Instant,
    clock_origin: Instant,
    clock_origin_ns: u64,
    last_timestamp_ns: u64,
    cycle_states: Vec<CycleRuntimeState>,
    selector_states: Vec<SelectorRuntimeState>,
    beat_clocks: Vec<BeatClockTracker>,
    sequence_states: Vec<SequenceCoordinator>,
    effect_states: Vec<EffectScheduleCoordinator>,
    structure_event_sequences: Vec<u64>,
    last_audio_update_sequence: Option<u64>,
    last_audio_timestamp_ns: Option<u64>,
}

#[derive(Clone)]
struct CycleRuntimeState {
    started_at: Instant,
    random_state: PythonRandom,
}

impl CycleRuntimeState {
    fn new(cycling: Option<&RenderCycling>, now: Instant, entropy_seed: u64) -> Self {
        let seed = cycling
            .and_then(|configuration| configuration.seed)
            .unwrap_or(entropy_seed);
        Self {
            started_at: now,
            random_state: PythonRandom::from_u64(seed),
        }
    }
}

#[derive(Clone)]
struct SelectorRuntimeState {
    initialized: bool,
    last_switch_at: Instant,
    last_feature_sequence: Option<u64>,
    last_activity_sequence: Option<u64>,
    recent_names: VecDeque<String>,
    random_state: PythonRandom,
    quantized_switch: QuantizedSwitchState,
    activity_tracker: Option<MusicActivityTracker>,
    gate_state: MusicGateState,
    displayed_gate_state: Option<MusicGateState>,
    active_choice: Option<RenderAnimationChoice>,
    active_template: Option<RenderOutput>,
    base_scene_changed: bool,
}

impl SelectorRuntimeState {
    fn new(
        selector: Option<RenderDynamicSelector>,
        output: &RenderOutput,
        now: Instant,
        entropy_seed: u64,
    ) -> Self {
        let seed = selector
            .and_then(|configuration| configuration.seed)
            .unwrap_or(entropy_seed);
        let history_size = selector.map_or(0, |configuration| configuration.history_size);
        let activity_config = selector.and_then(|configuration| configuration.activity);
        Self {
            initialized: false,
            last_switch_at: now,
            last_feature_sequence: None,
            last_activity_sequence: None,
            recent_names: VecDeque::with_capacity(history_size.min(256)),
            random_state: PythonRandom::from_u64(seed),
            quantized_switch: QuantizedSwitchState::default(),
            activity_tracker: activity_config.map(MusicActivityTracker::new),
            gate_state: if activity_config.is_some() {
                MusicGateState::Idle
            } else {
                MusicGateState::Music
            },
            displayed_gate_state: None,
            active_choice: selector.map(|_| RenderAnimationChoice {
                animation: output.animation.clone(),
                settings: output.settings.clone(),
            }),
            active_template: selector.map(|_| output.clone()),
            base_scene_changed: false,
        }
    }
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

    fn enqueue_latest_render(&mut self, request: PendingRender) -> Result<bool, ()> {
        if self.closed {
            return Err(());
        }
        if self
            .pending_render
            .as_ref()
            .is_some_and(|pending| pending.request.sequence >= request.request.sequence)
        {
            return Err(());
        }
        let replaced = self.pending_render.replace(request).is_some();
        Ok(replaced)
    }

    fn enqueue_scheduled_render(&mut self, request: PendingRender) -> Result<bool, ()> {
        if self.closed {
            return Err(());
        }
        let replaced = self.pending_render.replace(request).is_some();
        Ok(replaced)
    }
}

struct OutputWorkerShared {
    queue: QueueMutex<WorkQueue>,
    scheduler_ready: QueueCondvar,
    output_ready: QueueCondvar,
    health: Mutex<RuntimeHealth>,
}

impl OutputWorkerShared {
    fn notify_queue_changed(&self) {
        self.scheduler_ready.notify_one();
        self.output_ready.notify_one();
    }
}

#[derive(Clone)]
pub(crate) struct OutputAudioHandle {
    shared: Arc<OutputWorkerShared>,
    command_order: Arc<Mutex<()>>,
}

impl OutputAudioHandle {
    pub(crate) fn submit_audio_update(
        &self,
        update: RenderAudioUpdate,
    ) -> Result<bool, FrameAckStatus> {
        submit_audio_update_shared(&self.shared, &self.command_order, update)
    }

    pub(crate) fn audio_acknowledgments(&self) -> (usize, u64, Vec<u64>) {
        let health = self.shared.health.lock().expect("output health poisoned");
        let beat_sequence = health
            .beat_clocks
            .iter()
            .map(|clock| clock.last_event_sequence)
            .min()
            .unwrap_or(0);
        (
            health.pixel_counts.len(),
            beat_sequence,
            health.structure_event_sequences.clone(),
        )
    }
}

fn submit_audio_update_shared(
    shared: &Arc<OutputWorkerShared>,
    command_order: &Arc<Mutex<()>>,
    update: RenderAudioUpdate,
) -> Result<bool, FrameAckStatus> {
    let _command_order = command_order
        .lock()
        .expect("output command-order lock poisoned");
    let mut queue = shared.queue.lock().expect("output queue poisoned");
    if queue.closed {
        return Err(FrameAckStatus::ShuttingDown);
    }
    let mut health = shared.health.lock().expect("output health poisoned");
    if !health.configured {
        return Err(FrameAckStatus::NotConfigured);
    }
    let latest_request = {
        let Some(scheduled) = queue.scheduled_render.as_mut() else {
            return Err(FrameAckStatus::InvalidFrame);
        };
        if update
            .audio
            .as_ref()
            .is_some_and(|audio| !valid_audio_input(audio, scheduled.request.outputs.len()))
        {
            return Err(FrameAckStatus::InvalidFrame);
        }
        if scheduled
            .last_audio_update_sequence
            .is_some_and(|sequence| update.sequence <= sequence)
        {
            return Ok(false);
        }
        if scheduled
            .last_audio_timestamp_ns
            .is_some_and(|timestamp| update.timestamp_ns < timestamp)
        {
            return Ok(false);
        }
        scheduled.last_audio_update_sequence = Some(update.sequence);
        health.audio_update_sequence = Some(update.sequence);
        scheduled.last_audio_timestamp_ns = Some(update.timestamp_ns);
        Arc::make_mut(&mut scheduled.request).audio = update.audio;
        Arc::clone(&scheduled.request)
    };
    if let Some(pending) = queue.pending_render.as_mut() {
        pending.request = latest_request;
    }
    drop(health);
    drop(queue);
    shared.notify_queue_changed();
    Ok(true)
}

struct OutputWorker {
    shared: Arc<OutputWorkerShared>,
    command_order: Arc<Mutex<()>>,
    thread: Mutex<Option<JoinHandle<()>>>,
    scheduler_thread: Mutex<Option<JoinHandle<()>>>,
}

impl OutputWorker {
    fn new() -> Self {
        let shared = Arc::new(OutputWorkerShared {
            queue: QueueMutex::new(WorkQueue::default()),
            scheduler_ready: QueueCondvar::new(),
            output_ready: QueueCondvar::new(),
            health: Mutex::new(RuntimeHealth::new()),
        });
        let worker_shared = Arc::clone(&shared);
        let thread = thread::Builder::new()
            .name("lumistripe-rust-output".into())
            .spawn(move || output_worker_loop(worker_shared))
            .expect("Rust output worker thread could not be created");
        let scheduler_shared = Arc::clone(&shared);
        let scheduler_thread = thread::Builder::new()
            .name("lumistripe-rust-frame-scheduler".into())
            .spawn(move || frame_scheduler_loop(scheduler_shared))
            .expect("Rust frame scheduler thread could not be created");
        Self {
            shared,
            command_order: Arc::new(Mutex::new(())),
            thread: Mutex::new(Some(thread)),
            scheduler_thread: Mutex::new(Some(scheduler_thread)),
        }
    }

    fn configure(&self, config: EngineConfig) -> Result<(usize, usize), String> {
        let _command_order = self
            .command_order
            .lock()
            .expect("output command-order lock poisoned");
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
            if queue.pending_render.take().is_some() {
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            queue.scheduled_render = None;
            queue.last_render_request_sequence = None;
            queue.last_render_request_timestamp_ns = None;
            queue.last_scene_timestamp_ns = None;
            queue.last_render_timestamp_ns = None;
            health.configured = false;
            health.scheduler_missed_deadlines = 0;
            health.scene_control_revision = None;
            health.audio_update_sequence = None;
            health.selected_animations.clear();
            health.scene_revisions.clear();
            health.structure_event_sequences.clear();
            health.cycling_active.clear();
            health.dynamic_gate_states.clear();
            health.beat_clocks.clear();
            health.sequence_playback.clear();
            health.effect_schedulers.clear();
            health.pixel_counts.clear();
            health.last_sequence = None;
            health.frame_latency_ms = None;
            health.queue_latency_ms = None;
            health.transmission_ms = None;
            health.audio_feature_to_led_latency_ms = None;
            health.sequential_skew_ms = None;
            health.frame_latency_samples_ms.clear();
            health.audio_feature_to_led_samples_ms.clear();
            health.output_rate = 0.0;
            health.render_time_ms = None;
            health.estimated_power_watts = None;
            health.applied_power_scale = None;
            health.output_power_watts = None;
            health.power_output_count = 0;
            health.limiting_output_index = None;
            health.preview_rgba = None;
            health.preview_counts = [0; 2];
            health.rate_started_at = Instant::now();
            health.rate_frames = 0;
            health.last_successful_update_epoch_ms = None;
            queue.commands.push_back(WorkCommand::Configure {
                config,
                frame_buffers,
                response: send,
            });
        }
        self.shared.notify_queue_changed();
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
        queue.scheduled_render = None;
        health.selected_animations.clear();
        health.scene_revisions.clear();
        health.structure_event_sequences.clear();
        health.cycling_active.clear();
        health.dynamic_gate_states.clear();
        health.beat_clocks.clear();
        health.sequence_playback.clear();
        health.effect_schedulers.clear();
        if queue.pending_render.take().is_some() {
            health.dropped_frames = health.dropped_frames.saturating_add(1);
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
        self.shared.notify_queue_changed();
        Ok(())
    }

    fn submit_render(
        &self,
        request: RenderFrameRequest,
        scheduler_config: Option<RenderSchedulerConfig>,
    ) -> Result<bool, FrameAckStatus> {
        let _command_order = self
            .command_order
            .lock()
            .expect("output command-order lock poisoned");
        self.submit_render_ordered(request, scheduler_config, true)
    }

    fn submit_scene_update(&self, update: RenderSceneUpdate) -> Result<bool, FrameAckStatus> {
        let _command_order = self
            .command_order
            .lock()
            .expect("output command-order lock poisoned");
        {
            let health = self.shared.health.lock().expect("output health poisoned");
            if health
                .scene_control_revision
                .is_some_and(|revision| update.revision <= revision)
            {
                return Err(FrameAckStatus::InvalidFrame);
            }
        }
        {
            let queue = self.shared.queue.lock().expect("output queue poisoned");
            if queue
                .last_scene_timestamp_ns
                .is_some_and(|timestamp| update.timestamp_ns < timestamp)
            {
                return Err(FrameAckStatus::InvalidFrame);
            }
        }
        let audio = {
            let queue = self.shared.queue.lock().expect("output queue poisoned");
            queue
                .scheduled_render
                .as_ref()
                .and_then(|scheduled| scheduled.request.audio.clone())
        };
        let frame = RenderFrameRequest {
            sequence: update.revision,
            timestamp_ns: update.timestamp_ns,
            delta_ns: 0,
            brightness: update.brightness,
            audio,
            power: update.power,
            outputs: update.outputs,
        };
        let replaced = self.submit_render_ordered(frame, Some(update.scheduler), false)?;
        {
            let mut queue = self.shared.queue.lock().expect("output queue poisoned");
            queue.last_scene_timestamp_ns = Some(update.timestamp_ns);
        }
        let mut health = self.shared.health.lock().expect("output health poisoned");
        health.scene_control_revision = Some(update.revision);
        Ok(replaced)
    }

    fn submit_audio_update(&self, update: RenderAudioUpdate) -> Result<bool, FrameAckStatus> {
        submit_audio_update_shared(&self.shared, &self.command_order, update)
    }

    fn audio_handle(&self) -> OutputAudioHandle {
        OutputAudioHandle {
            shared: Arc::clone(&self.shared),
            command_order: Arc::clone(&self.command_order),
        }
    }

    fn submit_render_ordered(
        &self,
        request: RenderFrameRequest,
        scheduler_config: Option<RenderSchedulerConfig>,
        validate_request_order: bool,
    ) -> Result<bool, FrameAckStatus> {
        let mut queue = self.shared.queue.lock().expect("output queue poisoned");
        if queue.closed {
            return Err(FrameAckStatus::ShuttingDown);
        }
        let mut health = self.shared.health.lock().expect("output health poisoned");
        if !health.configured {
            return Err(FrameAckStatus::NotConfigured);
        }
        let valid_audio = request
            .audio
            .as_ref()
            .is_none_or(|audio| valid_audio_input(audio, request.outputs.len()));
        let latest_sequence = queue
            .last_render_request_sequence
            .into_iter()
            .chain(health.last_sequence)
            .max();
        if request.outputs.len() != health.pixel_counts.len()
            || !request.brightness.is_finite()
            || !(0.0..=1.0).contains(&request.brightness)
            || !valid_audio
            || (validate_request_order
                && latest_sequence.is_some_and(|last_sequence| request.sequence <= last_sequence))
            || (validate_request_order
                && queue
                    .last_render_request_timestamp_ns
                    .is_some_and(|timestamp| request.timestamp_ns < timestamp))
            || scheduler_config.is_some_and(|scheduler| !scheduler.is_valid())
            || request
                .outputs
                .iter()
                .zip(&health.pixel_counts)
                .any(|(output, pixel_count)| output.zone_count > *pixel_count)
            || request.outputs.iter().any(|output| {
                !supports_animation(&output.animation)
                    || AnimationInstance::validate(&output.animation, &output.settings).is_err()
                    || !valid_render_output_parameters(output)
                    || output
                        .cycling
                        .as_ref()
                        .is_some_and(|cycling| !valid_render_cycling(cycling))
            })
        {
            return Err(FrameAckStatus::InvalidFrame);
        }
        let sequence = request.sequence;
        let timestamp_ns = request.timestamp_ns;
        let previous_output_timestamp_ns = queue.last_render_timestamp_ns;
        let entropy_seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;
        health.selected_animations = request
            .outputs
            .iter()
            .map(|output| output.animation.clone())
            .collect();
        health.scene_revisions = request
            .outputs
            .iter()
            .map(|output| output.scene_revision)
            .collect();
        health.cycling_active = request
            .outputs
            .iter()
            .map(|output| output.cycling.is_some())
            .collect();
        if scheduler_config.is_none() {
            health.beat_clocks.clear();
            health.structure_event_sequences.clear();
            health.sequence_playback.clear();
            health.effect_schedulers.clear();
        }
        if validate_request_order {
            queue.last_render_request_sequence = Some(sequence);
            queue.last_render_request_timestamp_ns = Some(timestamp_ns);
        }
        let mut request = Arc::new(request);
        let replaced = if let Some(scheduler_config) = scheduler_config {
            let now = Instant::now();
            let period = render_period(&request, scheduler_config);
            let previous = queue.scheduled_render.take();
            if let Some(previous_schedule) = previous.as_ref() {
                let incoming = Arc::make_mut(&mut request);
                for (index, output) in incoming.outputs.iter_mut().enumerate() {
                    let old_output = &previous_schedule.request.outputs[index];
                    let old_config = old_output
                        .cycling
                        .as_ref()
                        .and_then(|cycling| cycling.effect_scheduler.as_ref());
                    let new_config = output
                        .cycling
                        .as_ref()
                        .and_then(|cycling| cycling.effect_scheduler.as_ref());
                    if old_config == new_config && new_config.is_some() {
                        output.effects.clone_from(&old_output.effects);
                        output.base_intensity = old_output.base_intensity;
                        output.max_overlay_strength = old_output.max_overlay_strength;
                    }
                }
            }
            let replaced = previous.is_some();
            if queue.pending_render.take().is_some() {
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            let (
                next_deadline,
                last_timestamp_ns,
                cycle_states,
                selector_states,
                beat_clocks,
                sequence_states,
                effect_states,
                structure_event_sequences,
                last_audio_update_sequence,
                last_audio_timestamp_ns,
            ) = previous.map_or_else(
                || {
                    (
                        now,
                        previous_output_timestamp_ns
                            .unwrap_or_else(|| timestamp_ns.saturating_sub(request.delta_ns)),
                        request
                            .outputs
                            .iter()
                            .enumerate()
                            .map(|(_, output)| {
                                CycleRuntimeState::new(output.cycling.as_ref(), now, entropy_seed)
                            })
                            .collect(),
                        request
                            .outputs
                            .iter()
                            .map(|output| {
                                SelectorRuntimeState::new(
                                    output.cycling.as_ref().and_then(|cycling| cycling.selector),
                                    output,
                                    now,
                                    entropy_seed,
                                )
                            })
                            .collect(),
                        vec![BeatClockTracker::default(); request.outputs.len()],
                        vec![SequenceCoordinator::default(); request.outputs.len()],
                        vec![EffectScheduleCoordinator::default(); request.outputs.len()],
                        vec![0; request.outputs.len()],
                        None,
                        None,
                    )
                },
                |previous| {
                    let cycle_states = request
                        .outputs
                        .iter()
                        .enumerate()
                        .map(|(index, output)| {
                            let old_output = &previous.request.outputs[index];
                            if old_output.animation == output.animation
                                && old_output.cycling == output.cycling
                            {
                                previous.cycle_states[index].clone()
                            } else {
                                CycleRuntimeState::new(output.cycling.as_ref(), now, entropy_seed)
                            }
                        })
                        .collect();
                    let selector_states = request
                        .outputs
                        .iter()
                        .enumerate()
                        .map(|(index, output)| {
                            let old_selector = previous.request.outputs[index]
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.selector);
                            let new_selector =
                                output.cycling.as_ref().and_then(|cycling| cycling.selector);
                            let old_sequence = previous.request.outputs[index]
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.sequence.as_ref());
                            let new_sequence = output
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.sequence.as_ref());
                            if old_selector == new_selector && new_selector.is_some() {
                                let mut state = previous.selector_states[index].clone();
                                if old_sequence != new_sequence {
                                    state.initialized = false;
                                    state.last_feature_sequence = None;
                                    state.last_switch_at = now;
                                    state.recent_names.clear();
                                    state.quantized_switch.reset();
                                }
                                let base_changed = state.active_template.as_ref().is_some_and(
                                    |previous_template| {
                                        previous_template.animation != output.animation
                                            || previous_template.settings != output.settings
                                            || previous_template.scene_revision
                                                != output.scene_revision
                                    },
                                );
                                if base_changed {
                                    let playback_choice_changed = state
                                        .active_template
                                        .as_ref()
                                        .is_some_and(|previous_template| {
                                            previous_template.animation != output.animation
                                                || previous_template.settings != output.settings
                                        });
                                    state.active_choice = Some(RenderAnimationChoice {
                                        animation: output.animation.clone(),
                                        settings: output.settings.clone(),
                                    });
                                    if playback_choice_changed {
                                        state.initialized = false;
                                        state.quantized_switch.reset();
                                    }
                                    state.base_scene_changed = true;
                                }
                                state.active_template = Some(output.clone());
                                state
                            } else {
                                SelectorRuntimeState::new(new_selector, output, now, entropy_seed)
                            }
                        })
                        .collect();
                    let beat_clocks = if previous.beat_clocks.len() == request.outputs.len() {
                        previous.beat_clocks
                    } else {
                        vec![BeatClockTracker::default(); request.outputs.len()]
                    };
                    let sequence_states = request
                        .outputs
                        .iter()
                        .enumerate()
                        .map(|(index, output)| {
                            let old_sequence = previous.request.outputs[index]
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.sequence.as_ref());
                            let new_sequence = output
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.sequence.as_ref());
                            if old_sequence == new_sequence
                                && previous.sequence_states.len() == request.outputs.len()
                            {
                                previous.sequence_states[index].clone()
                            } else {
                                SequenceCoordinator::default()
                            }
                        })
                        .collect();
                    let effect_states = request
                        .outputs
                        .iter()
                        .enumerate()
                        .map(|(index, output)| {
                            let old_config = previous.request.outputs[index]
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.effect_scheduler.as_ref());
                            let new_config = output
                                .cycling
                                .as_ref()
                                .and_then(|cycling| cycling.effect_scheduler.as_ref());
                            if old_config == new_config
                                && new_config.is_some()
                                && previous.effect_states.len() == request.outputs.len()
                            {
                                previous.effect_states[index].clone()
                            } else {
                                EffectScheduleCoordinator::default()
                            }
                        })
                        .collect();
                    let structure_event_sequences =
                        if previous.structure_event_sequences.len() == request.outputs.len() {
                            previous.structure_event_sequences
                        } else {
                            vec![0; request.outputs.len()]
                        };
                    let last_audio_update_sequence = previous.last_audio_update_sequence;
                    let last_audio_timestamp_ns = previous.last_audio_timestamp_ns;
                    (
                        previous.next_deadline,
                        previous.last_timestamp_ns,
                        cycle_states,
                        selector_states,
                        beat_clocks,
                        sequence_states,
                        effect_states,
                        structure_event_sequences,
                        last_audio_update_sequence,
                        last_audio_timestamp_ns,
                    )
                },
            );
            queue.scheduled_render = Some(ScheduledRender {
                request: Arc::clone(&request),
                period,
                max_fps: scheduler_config.max_fps,
                next_deadline,
                clock_origin: now,
                clock_origin_ns: timestamp_ns
                    .max(previous_output_timestamp_ns.unwrap_or(last_timestamp_ns)),
                last_timestamp_ns,
                cycle_states,
                selector_states,
                beat_clocks,
                sequence_states,
                effect_states,
                structure_event_sequences,
                last_audio_update_sequence,
                last_audio_timestamp_ns,
            });
            replaced
        } else {
            let had_schedule = queue.scheduled_render.take().is_some();
            let previous_timestamp_ns = queue.last_render_timestamp_ns;
            let timestamp_ns = timestamp_ns.max(previous_timestamp_ns.unwrap_or(0));
            let delta_ns = if timestamp_ns == request.timestamp_ns {
                request.delta_ns
            } else {
                previous_timestamp_ns
                    .map(|previous| timestamp_ns.saturating_sub(previous))
                    .unwrap_or(request.delta_ns)
            };
            queue.last_render_timestamp_ns = Some(timestamp_ns);
            let pending = PendingRender {
                request: Arc::clone(&request),
                timestamp_ns,
                delta_ns,
                queued_at: Instant::now(),
            };
            let replaced = queue
                .enqueue_latest_render(pending)
                .map_err(|()| FrameAckStatus::InvalidFrame)?;
            if replaced {
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            if had_schedule && !replaced {
                true
            } else {
                replaced
            }
        };
        drop(health);
        drop(queue);
        self.shared.notify_queue_changed();
        Ok(replaced)
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
            scheduler_missed_deadlines: health.scheduler_missed_deadlines,
            selected_animations: health.selected_animations.clone(),
            scene_revisions: health.scene_revisions.clone(),
            cycling_active: health.cycling_active.clone(),
            dynamic_gate_states: health.dynamic_gate_states.clone(),
            beat_clocks: health.beat_clocks.clone(),
            structure_event_sequences: health.structure_event_sequences.clone(),
            sequence_playback: health.sequence_playback.clone(),
            effect_schedulers: health.effect_schedulers.clone(),
            last_error: health.last_error.clone(),
            output_rate: health.output_rate,
            frame_latency_ms: health.frame_latency_ms,
            queue_latency_ms: health.queue_latency_ms,
            transmission_ms: health.transmission_ms,
            frame_latency_p50_ms: health.frame_latency_percentile_ms(0.50),
            frame_latency_p95_ms: health.frame_latency_p95_ms(),
            frame_latency_p99_ms: health.frame_latency_percentile_ms(0.99),
            audio_feature_to_led_latency_ms: health.audio_feature_to_led_latency_ms,
            audio_feature_to_led_p50_ms: health.audio_feature_to_led_percentile_ms(0.50),
            audio_feature_to_led_p95_ms: health.audio_feature_to_led_percentile_ms(0.95),
            audio_feature_to_led_p99_ms: health.audio_feature_to_led_percentile_ms(0.99),
            sequential_skew_ms: health.sequential_skew_ms,
            last_successful_update_epoch_ms: health.last_successful_update_epoch_ms,
            render_time_ms: health.render_time_ms,
            scene_control_revision: health.scene_control_revision,
            audio_update_sequence: health.audio_update_sequence,
            estimated_power_watts: health.estimated_power_watts,
            applied_power_scale: health.applied_power_scale,
            output_power_watts: health
                .output_power_watts
                .map(|watts| watts[..health.power_output_count].to_vec()),
            limiting_output_index: health.limiting_output_index,
            preview_outputs: health.preview_rgba.map(|outputs| {
                (0..health.power_output_count)
                    .map(|index| outputs[index][..health.preview_counts[index] * 4].to_vec())
                    .collect()
            }),
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
            queue.scheduled_render = None;
            if queue.pending_frame.take().is_some() {
                let mut health = self.shared.health.lock().expect("output health poisoned");
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            if queue.pending_render.take().is_some() {
                let mut health = self.shared.health.lock().expect("output health poisoned");
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            queue
                .commands
                .push_back(WorkCommand::Shutdown { response: send });
        }
        self.shared.notify_queue_changed();
        if let Some(scheduler_thread) = self
            .scheduler_thread
            .lock()
            .expect("scheduler thread handle poisoned")
            .take()
        {
            scheduler_thread
                .join()
                .map_err(|error| format!("Rust frame scheduler panicked: {error:?}"))?;
        }
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
    scheduler_missed_deadlines: u64,
    scene_control_revision: Option<u64>,
    audio_update_sequence: Option<u64>,
    selected_animations: Vec<String>,
    scene_revisions: Vec<u64>,
    structure_event_sequences: Vec<u64>,
    cycling_active: Vec<bool>,
    dynamic_gate_states: Vec<String>,
    beat_clocks: Vec<RenderBeatClockState>,
    sequence_playback: Vec<RenderSequencePlaybackState>,
    effect_schedulers: Vec<RenderEffectSchedulerState>,
    last_error: Option<String>,
    output_rate: f64,
    rate_started_at: Instant,
    rate_frames: u64,
    frame_latency_ms: Option<f64>,
    queue_latency_ms: Option<f64>,
    transmission_ms: Option<f64>,
    frame_latency_samples_ms: VecDeque<f64>,
    audio_feature_to_led_latency_ms: Option<f64>,
    audio_feature_to_led_samples_ms: VecDeque<f64>,
    sequential_skew_ms: Option<f64>,
    last_successful_update_epoch_ms: Option<u64>,
    render_time_ms: Option<f64>,
    estimated_power_watts: Option<f64>,
    applied_power_scale: Option<f64>,
    output_power_watts: Option<[f64; 2]>,
    power_output_count: usize,
    limiting_output_index: Option<usize>,
    preview_rgba: Option<[[u8; MAX_RENDER_PREVIEW_BYTES]; 2]>,
    preview_counts: [usize; 2],
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
            scheduler_missed_deadlines: 0,
            scene_control_revision: None,
            audio_update_sequence: None,
            selected_animations: Vec::new(),
            scene_revisions: Vec::new(),
            structure_event_sequences: Vec::new(),
            cycling_active: Vec::new(),
            dynamic_gate_states: Vec::new(),
            beat_clocks: Vec::new(),
            sequence_playback: Vec::new(),
            effect_schedulers: Vec::new(),
            last_error: None,
            output_rate: 0.0,
            rate_started_at: Instant::now(),
            rate_frames: 0,
            frame_latency_ms: None,
            queue_latency_ms: None,
            transmission_ms: None,
            frame_latency_samples_ms: VecDeque::with_capacity(FRAME_LATENCY_SAMPLE_COUNT),
            audio_feature_to_led_latency_ms: None,
            audio_feature_to_led_samples_ms: VecDeque::with_capacity(FRAME_LATENCY_SAMPLE_COUNT),
            sequential_skew_ms: None,
            last_successful_update_epoch_ms: None,
            render_time_ms: None,
            estimated_power_watts: None,
            applied_power_scale: None,
            output_power_watts: None,
            power_output_count: 0,
            limiting_output_index: None,
            preview_rgba: None,
            preview_counts: [0; 2],
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

    fn audio_feature_to_led_percentile_ms(&self, quantile: f64) -> Option<f64> {
        if self.audio_feature_to_led_samples_ms.is_empty() {
            return None;
        }
        let mut samples = self
            .audio_feature_to_led_samples_ms
            .iter()
            .copied()
            .collect::<Vec<_>>();
        samples.sort_by(f64::total_cmp);
        let index = ((samples.len() as f64 * quantile).ceil() as usize)
            .saturating_sub(1)
            .min(samples.len() - 1);
        Some(samples[index])
    }

    fn record_audio_feature_to_led_latency(&mut self, latency_ms: Option<f64>) {
        self.audio_feature_to_led_latency_ms = latency_ms;
        if let Some(latency_ms) = latency_ms {
            if self.audio_feature_to_led_samples_ms.len() == FRAME_LATENCY_SAMPLE_COUNT {
                self.audio_feature_to_led_samples_ms.pop_front();
            }
            self.audio_feature_to_led_samples_ms.push_back(latency_ms);
        } else {
            self.audio_feature_to_led_samples_ms.clear();
        }
    }

    fn record_frame(
        &mut self,
        sequence: u64,
        queued_at: Instant,
        queue_latency_ms: f64,
        transmission_ms: Option<f64>,
        sequential_skew_ms: Option<f64>,
        audio_feature_to_led_latency_ms: Option<f64>,
    ) {
        let frame_latency_ms = queued_at.elapsed().as_secs_f64() * 1000.0;
        self.frames_rendered = self.frames_rendered.saturating_add(1);
        self.last_sequence = Some(sequence);
        self.frame_latency_ms = Some(frame_latency_ms);
        self.queue_latency_ms = Some(queue_latency_ms);
        self.transmission_ms = transmission_ms;
        self.sequential_skew_ms = sequential_skew_ms;
        self.record_audio_feature_to_led_latency(audio_feature_to_led_latency_ms);
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

    fn record_render(&mut self, render_time_ms: f64, power: RenderPowerSummary) {
        self.render_time_ms = Some(render_time_ms);
        self.estimated_power_watts = Some(power.estimated_watts);
        self.applied_power_scale = Some(power.applied_scale);
        self.output_power_watts = Some(power.output_watts);
        self.power_output_count = power.output_count;
        self.limiting_output_index = power.limiting_output_index;
        self.preview_rgba = Some(power.preview_rgba);
        self.preview_counts = power.preview_counts;
    }

    fn clear_render_telemetry(&mut self) {
        self.render_time_ms = None;
        self.estimated_power_watts = None;
        self.applied_power_scale = None;
        self.output_power_watts = None;
        self.power_output_count = 0;
        self.limiting_output_index = None;
        self.preview_rgba = None;
        self.preview_counts = [0; 2];
    }

    fn record_error(&mut self, error: impl Into<String>) {
        self.failed_frames = self.failed_frames.saturating_add(1);
        self.last_error = Some(error.into().chars().take(256).collect());
    }
}

enum WorkItem {
    Command(WorkCommand),
    Frame(usize, FrameSet),
    Render(usize, FrameSet, PendingRender),
    Stop,
}

fn render_period(request: &RenderFrameRequest, scheduler: RenderSchedulerConfig) -> Duration {
    let animation_period_ms = request
        .outputs
        .iter()
        .filter_map(|output| catalog().animation(&output.animation))
        .map(|definition| u64::from(definition.frame_ms))
        .min()
        .unwrap_or(50);
    let animation_period = Duration::from_millis(animation_period_ms);
    let fps_period = Duration::from_nanos(1_000_000_000 / u64::from(scheduler.max_fps));
    animation_period.max(fps_period)
}

fn valid_render_cycling(cycling: &RenderCycling) -> bool {
    if cycling.interval_ns == 0
        || !matches!(
            cycling.transition_mode.as_str(),
            "crossfade" | "fade_black" | "wipe"
        )
        || cycling.choices.len() > catalog().animations.len()
    {
        return false;
    }
    let mut names = std::collections::BTreeSet::new();
    cycling.choices.iter().all(|choice| {
        !choice.animation.is_empty()
            && names.insert(choice.animation.as_str())
            && supports_animation(&choice.animation)
            && catalog()
                .animation(&choice.animation)
                .is_some_and(|definition| definition.automatic)
            && AnimationInstance::validate(&choice.animation, &choice.settings).is_ok()
    }) && cycling.selector.is_none_or(RenderDynamicSelector::is_valid)
        && (cycling.dynamic_idle.is_none() || cycling.selector.is_some())
        && cycling.sequence.as_ref().is_none_or(|sequence| {
            sequence.is_valid()
                && cycling.selector.is_some()
                && sequence
                    .steps
                    .iter()
                    .all(|step| names.contains(step.animation.as_str()))
        })
        && cycling.effect_scheduler.as_ref().is_none_or(|scheduler| {
            scheduler.is_valid()
                && cycling.selector.is_some()
                && scheduler.choices.iter().all(|choice| {
                    catalog().effect(&choice.name).is_some()
                        && catalog().animation(&choice.name).is_some_and(|definition| {
                            definition.validate_settings(&choice.settings, true).is_ok()
                        })
                })
        })
        && cycling.dynamic_idle.as_ref().is_none_or(|idle| {
            idle.is_valid()
                && (idle.mode != RenderDynamicIdleMode::Animation
                    || idle.animation.as_ref().is_some_and(|animation| {
                        supports_animation(animation)
                            && AnimationInstance::validate(animation, &idle.settings).is_ok()
                    }))
        })
}

fn next_cycling_choice<'a>(
    cycling: &'a RenderCycling,
    current: &str,
    random_state: &mut PythonRandom,
) -> Option<&'a RenderAnimationChoice> {
    if cycling.choices.is_empty() {
        return None;
    }
    let current_index = cycling
        .choices
        .iter()
        .position(|choice| choice.animation == current);
    let index = match cycling.order {
        RenderCycleOrder::Sequential => current_index
            .map(|index| (index + 1) % cycling.choices.len())
            .unwrap_or(0),
        RenderCycleOrder::Shuffle if cycling.choices.len() > 1 => {
            let available = cycling.choices.len() - usize::from(current_index.is_some());
            let selected = random_state.randbelow(available);
            cycling
                .choices
                .iter()
                .enumerate()
                .filter(|(index, _)| Some(*index) != current_index)
                .nth(selected)
                .map(|(index, _)| index)?
        }
        RenderCycleOrder::Shuffle => current_index.unwrap_or(0),
    };
    cycling.choices.get(index)
}

fn select_dynamic_choice<'a>(
    cycling: &'a RenderCycling,
    current: &str,
    features: &RenderSelectorFeatures,
    selector: RenderDynamicSelector,
    feature_sequence: u64,
    structure_event: bool,
    state: &mut SelectorRuntimeState,
    now: Instant,
) -> Option<&'a RenderAnimationChoice> {
    if state
        .last_feature_sequence
        .is_some_and(|last_sequence| feature_sequence <= last_sequence)
        && !structure_event
    {
        return None;
    }
    if state
        .last_feature_sequence
        .is_none_or(|last_sequence| feature_sequence > last_sequence)
    {
        state.last_feature_sequence = Some(feature_sequence);
    }

    let mut best_choice = None;
    let mut best_score = f64::NEG_INFINITY;
    let mut current_score = None;
    for choice in &cycling.choices {
        let score = score_dynamic_choice(
            choice,
            features,
            current,
            &state.recent_names,
            selector.randomness,
            &mut state.random_state,
        );
        if choice.animation == current {
            current_score = Some(score);
        }
        if score > best_score {
            best_choice = Some(choice);
            best_score = score;
        }
    }
    let Some(best_choice) = best_choice else {
        state.initialized = true;
        return None;
    };

    let selected = if !state.initialized {
        state.initialized = true;
        Some(best_choice)
    } else if best_choice.animation == current {
        None
    } else {
        let elapsed_ns = now
            .saturating_duration_since(state.last_switch_at)
            .as_nanos();
        let cooldown_ready = elapsed_ns >= u128::from(selector.switch_cooldown_ns);
        let min_ready = elapsed_ns >= u128::from(selector.min_duration_ns);
        let max_due = elapsed_ns >= u128::from(selector.max_duration_ns);
        let score_gap = best_score - current_score.unwrap_or(0.0);
        let switch = if features.drop_detected {
            false
        } else if features.section_change
            && min_ready
            && cooldown_ready
            && score_gap >= selector.switch_margin * 0.5
        {
            true
        } else if max_due && cooldown_ready {
            true
        } else {
            min_ready && cooldown_ready && score_gap >= selector.switch_margin
        };
        switch.then_some(best_choice)
    };

    selected
}

fn score_dynamic_choice(
    choice: &RenderAnimationChoice,
    features: &RenderSelectorFeatures,
    current: &str,
    recent_names: &VecDeque<String>,
    randomness: f64,
    random_state: &mut PythonRandom,
) -> f64 {
    let Some(definition) = catalog().animation(&choice.animation) else {
        return f64::NEG_INFINITY;
    };
    let metadata = &definition.dynamic_metadata;
    let energy = first_nonzero_clamped(&[features.energy_level, features.energy, features.volume]);
    let bass = first_nonzero_clamped(&[features.bass_energy, features.bass]);
    let mid = clamp_unit(features.mid_energy);
    let treble = clamp_unit(features.treble_energy);
    let beat_confidence = clamp_unit(features.beat_confidence);
    let bpm = features.bpm.max(0.0);

    let mut score = metadata_number(metadata, "weight", 1.0);
    let energy_fit = range_fit(
        energy,
        metadata_number(metadata, "energy_min", 0.0),
        metadata_number(metadata, "energy_max", 1.0),
        0.25,
    );
    score += energy_fit * 0.85;
    let bpm_fit = range_fit(
        bpm,
        metadata_number(metadata, "bpm_min", 0.0),
        metadata_number(metadata, "bpm_max", 260.0),
        60.0,
    );
    score += bpm_fit * 0.35;
    let prefers_bass = metadata_number(metadata, "prefers_bass", 0.0);
    let prefers_mid = metadata_number(metadata, "prefers_mid", 0.0);
    let prefers_treble = metadata_number(metadata, "prefers_treble", 0.0);
    let spectrum_fit = (1.0 - (prefers_bass - bass).abs()) * prefers_bass
        + (1.0 - (prefers_mid - mid).abs()) * prefers_mid
        + (1.0 - (prefers_treble - treble).abs()) * prefers_treble;
    score += spectrum_fit * 0.55;

    if metadata_boolean(metadata, "supports_beats", true) {
        score += beat_confidence * 0.28;
    } else if beat_confidence > 0.35 {
        score -= beat_confidence * 0.25;
    }
    if features.drop_detected {
        if metadata_boolean(metadata, "supports_drops", false) {
            score += 1.1;
        } else {
            score -= 0.18;
        }
    }
    if features.silence {
        if metadata_boolean(metadata, "supports_silence", false) {
            score += 0.9;
        } else {
            score -= 0.65;
        }
    }
    score += (1.0 - (metadata_number(metadata, "intensity", 0.5) - energy).abs()).max(0.0) * 0.28;
    if choice.animation == current {
        score += 0.18;
    }
    if let Some(distance) = recent_names
        .iter()
        .position(|name| name == &choice.animation)
        .map(|index| recent_names.len() - index)
    {
        score -= 0.18 + 0.12 * distance as f64;
    }
    if randomness > 0.0 {
        score += random_state.uniform(-randomness, randomness);
    }
    score
}

fn remember_dynamic_name(history: &mut VecDeque<String>, name: &str, limit: usize) {
    if limit == 0 || name.is_empty() {
        history.clear();
        return;
    }
    history.retain(|previous| previous != name);
    if history.len() == limit {
        history.pop_front();
    }
    if history.len() < limit {
        history.push_back(name.to_owned());
    }
}

fn first_nonzero_clamped(values: &[f64]) -> f64 {
    values
        .iter()
        .copied()
        .find(|value| *value != 0.0)
        .map_or(0.0, clamp_unit)
}

fn clamp_unit(value: f64) -> f64 {
    value.clamp(0.0, 1.0)
}

fn range_fit(value: f64, low: f64, high: f64, scale: f64) -> f64 {
    if (low..=high).contains(&value) {
        return 1.0;
    }
    let distance = if value < low {
        low - value
    } else {
        value - high
    };
    (1.0 - distance / scale.max(1e-6)).clamp(0.0, 1.0)
}

fn metadata_number(metadata: &serde_json::Value, name: &str, fallback: f64) -> f64 {
    metadata
        .get(name)
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(fallback)
}

fn metadata_boolean(metadata: &serde_json::Value, name: &str, fallback: bool) -> bool {
    metadata
        .get(name)
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(fallback)
}

fn update_dynamic_output(
    cycling: &RenderCycling,
    selector: RenderDynamicSelector,
    current_output: &RenderOutput,
    features: &RenderSelectorFeatures,
    feature_sequence: u64,
    structure_event: bool,
    section_change_at_ns: Option<u64>,
    now: Instant,
    timestamp_ns: u64,
    beat_clock: RenderBeatClockState,
    state: &mut SelectorRuntimeState,
    sequence_state: &mut SequenceCoordinator,
) -> (RenderOutput, String, String) {
    let Some(mut active_output) = state.active_template.clone() else {
        return (
            current_output.clone(),
            "disabled".to_owned(),
            current_output.animation.clone(),
        );
    };

    if let Some(tracker) = state.activity_tracker.as_mut()
        && state
            .last_activity_sequence
            .is_none_or(|last_sequence| feature_sequence > last_sequence)
    {
        state.last_activity_sequence = Some(feature_sequence);
        let update = tracker.update(*features, now);
        state.gate_state = update.state;
        if update.activated {
            state.initialized = false;
        }
    }

    if let Some(active_choice) = state.active_choice.as_ref() {
        active_output.animation = active_choice.animation.clone();
        active_output.settings = active_choice.settings.clone();
    }
    let sequence_step = sequence_state.update(
        cycling.sequence.as_ref(),
        beat_clock,
        timestamp_ns,
        selector.sync_offset_ns,
        selector.clock_confidence_threshold,
        selector.subdivisions_per_beat,
        state.gate_state == MusicGateState::Music,
        section_change_at_ns,
    );
    let sequence_controls_playback = sequence_state.controls_playback();
    if let (Some(sequence), Some(step_index)) = (cycling.sequence.as_ref(), sequence_step)
        && let Some(step) = sequence.steps.get(step_index)
        && let Some(choice) = cycling
            .choices
            .iter()
            .find(|choice| choice.animation == step.animation)
    {
        state.active_choice = Some(choice.clone());
        state.last_switch_at = now;
        state.quantized_switch.reset();
        active_output.animation.clone_from(&choice.animation);
        active_output.settings.clone_from(&choice.settings);
    }
    let new_features = state
        .last_feature_sequence
        .is_none_or(|last_sequence| feature_sequence > last_sequence)
        || structure_event;
    let was_initialized = state.initialized;
    let candidate =
        if state.gate_state == MusicGateState::Music && new_features && !sequence_controls_playback
        {
            select_dynamic_choice(
                cycling,
                &active_output.animation,
                features,
                selector,
                feature_sequence,
                structure_event,
                state,
                now,
            )
            .cloned()
        } else {
            None
        };
    if state.gate_state == MusicGateState::Music && !sequence_controls_playback {
        let intent = new_features.then_some(candidate.clone());
        let due_choice = if !was_initialized && candidate.is_some() {
            state.quantized_switch.reset();
            candidate
        } else {
            let transition_active = current_output
                .transition
                .as_ref()
                .is_some_and(|transition| {
                    timestamp_ns
                        < transition
                            .started_at_ns
                            .saturating_add(transition.duration_ns)
                });
            state.quantized_switch.update(
                intent,
                selector,
                beat_clock,
                timestamp_ns,
                transition_active,
            )
        };
        if let Some(choice) = due_choice {
            if choice.animation != active_output.animation {
                remember_dynamic_name(
                    &mut state.recent_names,
                    &active_output.animation,
                    selector.history_size,
                );
                state.last_switch_at = now;
            }
            state.active_choice = Some(choice.clone());
            active_output.animation = choice.animation;
            active_output.settings = choice.settings;
        }
    } else {
        state.quantized_switch.reset();
    }

    let selected_animation = active_output.animation.clone();
    if state.gate_state != MusicGateState::Music
        && let Some(idle) = cycling.dynamic_idle.as_ref()
    {
        match idle.mode {
            RenderDynamicIdleMode::Off => {
                active_output.blackout = true;
                active_output.audio_enabled = false;
                active_output.solid_color = None;
                active_output.effects.clear();
            }
            RenderDynamicIdleMode::Solid => {
                active_output.audio_enabled = false;
                active_output.solid_color = Some(idle.color);
                active_output.brightness = Some(idle.brightness);
                active_output.base_intensity = 1.0;
                active_output.effects.clear();
            }
            RenderDynamicIdleMode::Animation => {
                if let Some(animation) = idle.animation.as_ref() {
                    active_output.animation.clone_from(animation);
                }
                active_output.settings.clone_from(&idle.settings);
                active_output.audio_enabled = false;
                active_output.solid_color = None;
                active_output.brightness = Some(idle.brightness);
                active_output.base_intensity = 1.0;
                active_output.effects.clear();
            }
        }
    } else {
        active_output.audio_enabled = true;
    }

    let display_changed = current_output.animation != active_output.animation
        || current_output.settings != active_output.settings
        || current_output.blackout != active_output.blackout
        || current_output.solid_color != active_output.solid_color
        || current_output.brightness != active_output.brightness
        || current_output.audio_enabled != active_output.audio_enabled
        || current_output.effects != active_output.effects
        || current_output.base_intensity != active_output.base_intensity
        || state.displayed_gate_state != Some(state.gate_state)
        || state.base_scene_changed;
    if display_changed {
        active_output.scene_revision = current_output.scene_revision.wrapping_add(1);
        active_output.transition = (cycling.transition_duration_ns > 0).then(|| RenderTransition {
            started_at_ns: timestamp_ns,
            duration_ns: cycling.transition_duration_ns,
            mode: cycling.transition_mode.clone(),
        });
    } else {
        active_output.scene_revision = current_output.scene_revision;
        active_output.transition = current_output.transition.clone();
    }
    state.displayed_gate_state = Some(state.gate_state);
    state.base_scene_changed = false;
    (
        active_output,
        state.gate_state.as_str().to_owned(),
        selected_animation,
    )
}

fn advance_cycle_after_render(
    shared: &OutputWorkerShared,
    request: &Arc<RenderFrameRequest>,
    renderers: &[Option<RenderOutputState>],
    timestamp_ns: u64,
) {
    let now = Instant::now();
    let mut queue = shared.queue.lock().expect("output queue poisoned");
    let Some((
        selected_animations,
        scene_revisions,
        structure_event_sequences,
        cycling_active,
        dynamic_gate_states,
        beat_clocks,
        sequence_playback,
        effect_schedulers,
        changed,
    )) = (|| {
        let scheduled = queue.scheduled_render.as_mut()?;
        if !Arc::ptr_eq(&scheduled.request, request) {
            return None;
        }

        let audio = request.audio.as_ref();
        let beat_events = audio.map_or(&[][..], |audio| audio.beat_events.as_slice());
        let fallback_bpm = audio.and_then(|audio| audio.fallback_bpm);
        for tracker in &mut scheduled.beat_clocks {
            tracker.update(beat_events, timestamp_ns, fallback_bpm);
        }
        let beat_clocks = scheduled
            .beat_clocks
            .iter()
            .map(|tracker| tracker.state(timestamp_ns))
            .collect::<Vec<_>>();

        let output_count = scheduled.request.outputs.len();
        if scheduled.structure_event_sequences.len() != output_count {
            scheduled.structure_event_sequences = vec![0; output_count];
        }
        let mut section_change_events = vec![false; output_count];
        let mut section_change_timestamps_ns = vec![None; output_count];
        let mut drop_events = vec![false; output_count];
        let mut structure_states = vec![None; output_count];
        let mut blocked_structure_events = vec![false; output_count];
        if let Some(audio) = audio {
            for event in &audio.structure_events {
                if blocked_structure_events[event.output_index] {
                    continue;
                }
                let Some(last_sequence) = scheduled
                    .structure_event_sequences
                    .get_mut(event.output_index)
                else {
                    continue;
                };
                if event.sequence <= *last_sequence || event.timestamp_ns > timestamp_ns {
                    if event.timestamp_ns > timestamp_ns {
                        blocked_structure_events[event.output_index] = true;
                    }
                    continue;
                }
                *last_sequence = event.sequence;
                structure_states[event.output_index] = Some(RenderMusicStructure {
                    phase: event.phase,
                    confidence: event.confidence,
                    buildup_progress: event.buildup_progress,
                    drop_event: event.drop_event,
                });
                section_change_events[event.output_index] |= event.section_change;
                if event.section_change {
                    section_change_timestamps_ns[event.output_index] = Some(event.timestamp_ns);
                }
                drop_events[event.output_index] |= event.drop_event;
            }
        }

        let mut changed = false;
        let mut selected_animations = scheduled
            .request
            .outputs
            .iter()
            .map(|output| output.animation.clone())
            .collect::<Vec<_>>();
        let mut dynamic_gate_states = vec!["disabled".to_owned(); scheduled.request.outputs.len()];
        for (index, renderer) in renderers.iter().enumerate() {
            if let Some(structure) = structure_states[index] {
                let active_output = &mut Arc::make_mut(&mut scheduled.request).outputs[index];
                if active_output.music_structure != Some(structure) {
                    active_output.music_structure = Some(structure);
                    changed = true;
                }
            }
            let output = scheduled.request.outputs[index].clone();
            let Some(cycling) = output.cycling.clone() else {
                continue;
            };
            if cycling.choices.is_empty() {
                continue;
            }
            if let Some(selector) = cycling.selector {
                let mut features = audio
                    .and_then(|audio| audio.selector_features)
                    .unwrap_or_default();
                features.section_change |= section_change_events[index];
                features.drop_detected |= drop_events[index];
                let structure_event = section_change_events[index] || drop_events[index];
                let feature_sequence = audio
                    .and_then(|audio| audio.feature_sequence)
                    .unwrap_or(scheduled.request.sequence);
                let (dynamic_output, gate_state, selected_animation) = update_dynamic_output(
                    &cycling,
                    selector,
                    &output,
                    &features,
                    feature_sequence,
                    structure_event,
                    section_change_timestamps_ns[index],
                    now,
                    timestamp_ns,
                    beat_clocks[index],
                    &mut scheduled.selector_states[index],
                    &mut scheduled.sequence_states[index],
                );
                selected_animations[index] = selected_animation;
                dynamic_gate_states[index] = gate_state;
                if dynamic_output != output {
                    Arc::make_mut(&mut scheduled.request).outputs[index] = dynamic_output;
                    changed = true;
                }
                continue;
            }
            let Some(animation) = renderer.as_ref().and_then(|state| state.animation.as_ref())
            else {
                continue;
            };
            let due = match cycling.timing {
                RenderCycleTiming::Fixed => {
                    now.saturating_duration_since(scheduled.cycle_states[index].started_at)
                        >= Duration::from_nanos(cycling.interval_ns)
                }
                RenderCycleTiming::PerAnimation => catalog()
                    .animation(animation.name())
                    .is_some_and(|definition| {
                        animation.frame_index() >= u64::from(definition.frames_per_cycle)
                    }),
            };
            if !due {
                continue;
            }
            let current = output.animation.clone();
            let Some(choice) = next_cycling_choice(
                &cycling,
                &current,
                &mut scheduled.cycle_states[index].random_state,
            )
            .cloned() else {
                continue;
            };
            let output = &mut Arc::make_mut(&mut scheduled.request).outputs[index];
            output.animation = choice.animation;
            output.settings = choice.settings;
            output.scene_revision = output.scene_revision.wrapping_add(1);
            output.transition = (cycling.transition_duration_ns > 0).then(|| RenderTransition {
                started_at_ns: timestamp_ns,
                duration_ns: cycling.transition_duration_ns,
                mode: cycling.transition_mode,
            });
            scheduled.cycle_states[index].started_at = now;
            changed = true;
        }
        if scheduled.effect_states.len() != scheduled.request.outputs.len() {
            scheduled.effect_states.resize_with(
                scheduled.request.outputs.len(),
                EffectScheduleCoordinator::default,
            );
        }
        let mut effect_schedulers =
            vec![RenderEffectSchedulerState::default(); scheduled.request.outputs.len()];
        let mut has_effect_scheduler = false;
        for index in 0..scheduled.request.outputs.len() {
            let output = scheduled.request.outputs[index].clone();
            let Some(config) = output
                .cycling
                .as_ref()
                .and_then(|cycling| cycling.effect_scheduler.as_ref())
            else {
                scheduled.effect_states[index].reset();
                continue;
            };
            has_effect_scheduler = true;
            let transition_active = output.transition.is_some_and(|transition| {
                transition
                    .started_at_ns
                    .saturating_add(transition.duration_ns)
                    > timestamp_ns
            });
            let mut structure = output.music_structure;
            if drop_events[index] {
                if let Some(structure) = structure.as_mut() {
                    structure.drop_event = true;
                }
            }
            let (base_intensity, max_overlay_strength, effects, state) =
                scheduled.effect_states[index].update(
                    Some(config),
                    audio,
                    structure,
                    beat_clocks[index],
                    timestamp_ns,
                    dynamic_gate_states[index] == "music",
                    transition_active,
                );
            let active_output = &mut Arc::make_mut(&mut scheduled.request).outputs[index];
            if active_output.effects != effects
                || active_output.base_intensity != base_intensity
                || active_output.max_overlay_strength != max_overlay_strength
            {
                active_output.effects = effects;
                active_output.base_intensity = base_intensity;
                active_output.max_overlay_strength = max_overlay_strength;
                changed = true;
            }
            effect_schedulers[index] = state;
        }
        if !has_effect_scheduler {
            effect_schedulers.clear();
        }
        if changed {
            scheduled.period = render_period(
                &scheduled.request,
                RenderSchedulerConfig {
                    max_fps: scheduled.max_fps,
                },
            );
            scheduled.next_deadline = now.checked_add(scheduled.period).unwrap_or(now);
        }
        let has_sequences = scheduled.request.outputs.iter().any(|output| {
            output
                .cycling
                .as_ref()
                .is_some_and(|cycling| cycling.sequence.is_some())
        });
        let sequence_playback = if has_sequences {
            scheduled
                .request
                .outputs
                .iter()
                .enumerate()
                .map(|(index, output)| {
                    scheduled.sequence_states[index].playback_state(
                        output
                            .cycling
                            .as_ref()
                            .and_then(|cycling| cycling.sequence.as_ref()),
                    )
                })
                .collect()
        } else {
            Vec::new()
        };
        Some((
            scheduled
                .request
                .outputs
                .iter()
                .enumerate()
                .map(|(index, output)| {
                    if output
                        .cycling
                        .as_ref()
                        .is_some_and(|cycling| cycling.selector.is_some())
                    {
                        selected_animations[index].clone()
                    } else {
                        output.animation.clone()
                    }
                })
                .collect::<Vec<_>>(),
            scheduled
                .request
                .outputs
                .iter()
                .map(|output| output.scene_revision)
                .collect::<Vec<_>>(),
            scheduled.structure_event_sequences.clone(),
            scheduled
                .request
                .outputs
                .iter()
                .map(|output| output.cycling.is_some())
                .collect::<Vec<_>>(),
            dynamic_gate_states,
            beat_clocks,
            sequence_playback,
            effect_schedulers,
            changed,
        ))
    })()
    else {
        return;
    };

    let dropped_stale_render = changed
        && queue
            .pending_render
            .as_ref()
            .is_some_and(|pending| Arc::ptr_eq(&pending.request, request));
    if dropped_stale_render {
        queue.pending_render.clear();
    }
    {
        let mut health = shared.health.lock().expect("output health poisoned");
        health.selected_animations = selected_animations;
        health.scene_revisions = scene_revisions;
        health.structure_event_sequences = structure_event_sequences;
        health.cycling_active = cycling_active;
        health.dynamic_gate_states = dynamic_gate_states;
        health.beat_clocks = beat_clocks;
        health.sequence_playback = sequence_playback;
        health.effect_schedulers = effect_schedulers;
        if dropped_stale_render {
            health.dropped_frames = health.dropped_frames.saturating_add(1);
        }
    }
    if changed {
        shared.scheduler_ready.notify_one();
    }
}

fn advance_frame_deadline(candidate: Instant, now: Instant, period: Duration) -> (Instant, u64) {
    if candidate > now {
        return (candidate, 0);
    }
    let period_ns = period.as_nanos().max(1);
    let skipped = now.saturating_duration_since(candidate).as_nanos() / period_ns + 1;
    let advance_ns = period_ns.saturating_mul(skipped);
    let seconds = (advance_ns / 1_000_000_000).min(u64::MAX as u128) as u64;
    let advance = Duration::new(seconds, (advance_ns % 1_000_000_000) as u32);
    let next = candidate
        .checked_add(advance)
        .or_else(|| now.checked_add(period))
        .unwrap_or(now);
    (next, skipped.min(u64::MAX as u128) as u64)
}

fn frame_scheduler_loop(shared: Arc<OutputWorkerShared>) {
    loop {
        let mut queue = shared.queue.lock().expect("output queue poisoned");
        loop {
            if queue.closed {
                return;
            }
            let Some(scheduled) = queue.scheduled_render.as_ref() else {
                queue = shared
                    .scheduler_ready
                    .wait(queue)
                    .expect("output queue poisoned");
                continue;
            };
            let now = Instant::now();
            if now < scheduled.next_deadline {
                let timeout = scheduled.next_deadline - now;
                let (updated_queue, _) = shared
                    .scheduler_ready
                    .wait_timeout(queue, timeout)
                    .expect("output queue poisoned");
                queue = updated_queue;
                continue;
            }

            let scheduled = queue
                .scheduled_render
                .as_mut()
                .expect("scheduled render was checked above");
            let timestamp_ns = scheduled.clock_origin_ns.saturating_add(
                now.saturating_duration_since(scheduled.clock_origin)
                    .as_nanos()
                    .min(u64::MAX as u128) as u64,
            );
            let delta_ns = timestamp_ns.saturating_sub(scheduled.last_timestamp_ns);
            scheduled.last_timestamp_ns = timestamp_ns;
            let candidate = scheduled
                .next_deadline
                .checked_add(scheduled.period)
                .unwrap_or(now);
            let (next_deadline, missed) = advance_frame_deadline(candidate, now, scheduled.period);
            scheduled.next_deadline = next_deadline;
            let pending = PendingRender {
                request: Arc::clone(&scheduled.request),
                timestamp_ns,
                delta_ns,
                queued_at: now,
            };
            queue.last_render_timestamp_ns = Some(timestamp_ns);
            let replaced = queue
                .enqueue_scheduled_render(pending)
                .expect("open scheduler cannot enqueue after queue closure");
            let mut health = shared.health.lock().expect("output health poisoned");
            if replaced {
                health.dropped_frames = health.dropped_frames.saturating_add(1);
            }
            health.scheduler_missed_deadlines =
                health.scheduler_missed_deadlines.saturating_add(missed);
            drop(health);
            shared.output_ready.notify_one();
            break;
        }
    }
}

fn output_worker_loop(shared: Arc<OutputWorkerShared>) {
    let mut outputs: Option<OutputSet> = None;
    let mut renderers: Vec<Option<RenderOutputState>> = Vec::new();
    loop {
        let item = {
            let mut queue = shared.queue.lock().expect("output queue poisoned");
            while queue.commands.is_empty()
                && queue.pending_frame.is_none()
                && queue.pending_render.is_none()
                && !queue.closed
            {
                queue = shared
                    .output_ready
                    .wait(queue)
                    .expect("output queue poisoned");
            }
            if let Some(command) = queue.commands.pop_front() {
                WorkItem::Command(command)
            } else if queue.pending_render.is_some() && queue.pending_frame.is_some() {
                let frame_index = queue.pending_frame.expect("pending frame index");
                let pending_frame_sequence = queue.frame_buffers[frame_index]
                    .as_ref()
                    .expect("pending frame buffer")
                    .sequence;
                let pending_render_sequence = queue
                    .pending_render
                    .as_ref()
                    .expect("pending render")
                    .request
                    .sequence;
                if pending_render_sequence >= pending_frame_sequence {
                    let request = queue.pending_render.take().expect("pending render");
                    queue.pending_frame = None;
                    let frame = queue.frame_buffers[frame_index]
                        .take()
                        .expect("pending frame buffer must be available");
                    let mut health = shared.health.lock().expect("output health poisoned");
                    health.dropped_frames = health.dropped_frames.saturating_add(1);
                    WorkItem::Render(frame_index, frame, request)
                } else {
                    queue.pending_frame = None;
                    let frame = queue.frame_buffers[frame_index]
                        .take()
                        .expect("pending frame buffer must be available");
                    WorkItem::Frame(frame_index, frame)
                }
            } else if let Some(request) = queue.pending_render.take() {
                let index = queue
                    .frame_buffers
                    .iter()
                    .position(Option::is_some)
                    .expect("one render frame buffer remains available");
                let frame = queue.frame_buffers[index]
                    .take()
                    .expect("render frame buffer must be available");
                WorkItem::Render(index, frame, request)
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
                        renderers = (0..output_count).map(|_| None).collect();
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
                        health.render_time_ms = None;
                        health.estimated_power_watts = None;
                        health.applied_power_scale = None;
                        health.output_power_watts = None;
                        health.power_output_count = 0;
                        health.limiting_output_index = None;
                        health.preview_rgba = None;
                        health.preview_counts = [0; 2];
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
                    Ok((transmission_ms, sequential_skew_ms)) => {
                        health.clear_render_telemetry();
                        health.record_frame(
                            frame.sequence,
                            frame.queued_at,
                            queue_latency_ms,
                            transmission_ms,
                            sequential_skew_ms,
                            None,
                        );
                    }
                    Err(error) => health.record_error(error.to_string()),
                }
                drop(health);
                let mut queue = shared.queue.lock().expect("output queue poisoned");
                queue.frame_buffers[index] = Some(frame);
            }
            WorkItem::Render(index, mut frame, request) => {
                frame.sequence = request.request.sequence;
                frame.queued_at = request.queued_at;
                let scheduled_request = Arc::clone(&request.request);
                let render_started_at = Instant::now();
                let rendered = render_frame_request_at(
                    &request.request,
                    request.timestamp_ns,
                    request.delta_ns,
                    &mut frame,
                    &mut renderers,
                )
                .map(|power_summary| {
                    (
                        power_summary,
                        render_started_at.elapsed().as_secs_f64() * 1000.0,
                    )
                });
                let result = rendered.and_then(|(power_summary, render_time_ms)| {
                    outputs
                        .as_mut()
                        .ok_or_else(|| "outputs are not configured".to_owned())
                        .and_then(|set| {
                            set.render(&frame.frames)
                                .map(|_| {
                                    (
                                        power_summary,
                                        render_time_ms,
                                        set.last_transmission_ms(),
                                        set.last_sequential_skew_ms(),
                                    )
                                })
                                .map_err(|error| error.to_string())
                        })
                });
                let queue_latency_ms = frame.queued_at.elapsed().as_secs_f64() * 1000.0;
                let feature_to_led_latency_ms = request
                    .request
                    .audio
                    .as_ref()
                    .and_then(|audio| audio.feature_timestamp_ns)
                    .and_then(|timestamp_ns| {
                        audio_feature_to_led_latency_ms(
                            request.timestamp_ns,
                            timestamp_ns,
                            frame.queued_at.elapsed(),
                        )
                    });
                if result.is_ok() {
                    advance_cycle_after_render(
                        &shared,
                        &scheduled_request,
                        &renderers,
                        request.timestamp_ns,
                    );
                }
                let mut health = shared.health.lock().expect("output health poisoned");
                match result {
                    Ok((power_summary, render_time_ms, transmission_ms, sequential_skew_ms)) => {
                        health.record_render(render_time_ms, power_summary);
                        health.record_frame(
                            frame.sequence,
                            frame.queued_at,
                            queue_latency_ms,
                            transmission_ms,
                            sequential_skew_ms,
                            feature_to_led_latency_ms,
                        );
                    }
                    Err(error) => health.record_error(error),
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

#[cfg(all(test, not(feature = "loom-tests")))]
mod tests {
    use super::SequenceCoordinator;
    use super::{
        AnimationInstance, BeatClockTracker, EngineServer, FrameSet, MAX_RENDER_PREVIEW_PIXELS,
        PythonRandom, RenderingInput, RuntimeHealth, SelectorRuntimeState, TransitionMode,
        WorkQueue, advance_frame_deadline, apply_transition, audio_feature_to_led_latency_ms,
        finalize_render_frames, next_cycling_choice, remove_stale_socket, render_frame_request,
        render_frame_request_at, select_dynamic_choice, serve, update_dynamic_output,
    };
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use lumistripe_engine_protocol::{
        FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus, FrameHeader, LegacyConfig,
        LegacyRequest, LegacyRequestCommand, LegacyResponseResult, MAX_CONTROL_MESSAGE_BYTES,
        MAX_FRAME_BYTES, PREVIOUS_PROTOCOL_VERSION, PROTOCOL_VERSION, RenderAnimationChoice,
        RenderAnimationSequence, RenderAnimationSequenceStep, RenderAudioInput, RenderAudioUpdate,
        RenderBeatClockState, RenderBeatEvent, RenderCycleOrder, RenderCycleTiming, RenderCycling,
        RenderDynamicIdle, RenderDynamicIdleMode, RenderDynamicSelector, RenderEffect,
        RenderFrameRequest, RenderMusicActivityConfig, RenderMusicStructurePhase, RenderOutput,
        RenderPowerConfig, RenderSceneUpdate, RenderSchedulerConfig, RenderSelectorFeatures,
        RenderStructureEvent, RenderSwitchQuantization, RenderTransition, Request, RequestCommand,
        Response, ResponseResult, decode_frame_ack, decode_legacy_response, decode_response,
        encode_frame_header, encode_legacy_request, encode_request,
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
            self.request_with_version(PROTOCOL_VERSION, command)
        }

        fn request_with_version(&self, protocol_version: u16, command: RequestCommand) -> Response {
            let mut request = Request::new("test-request", command);
            request.protocol_version = protocol_version;
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
    fn oversized_binary_frame_is_rejected_without_stopping_the_server() {
        let server = ServerThread::start();
        let mut stream = UnixStream::connect(&server.socket).unwrap();
        let mut header = [0u8; FRAME_HEADER_BYTES];
        header[..4].copy_from_slice(b"LSF2");
        header[4..6].copy_from_slice(&2u16.to_le_bytes());
        header[6..8].copy_from_slice(&1u16.to_le_bytes());
        header[8..16].copy_from_slice(&9u64.to_le_bytes());
        header[16..20].copy_from_slice(&((MAX_FRAME_BYTES + 1) as u32).to_le_bytes());
        stream.write_all(&header).unwrap();

        let mut encoded_ack = [0u8; FRAME_ACK_BYTES];
        stream.read_exact(&mut encoded_ack).unwrap();
        let ack = decode_frame_ack(&encoded_ack).unwrap();
        assert_eq!(ack.status, FrameAckStatus::InvalidFrame);
        assert_eq!(ack.sequence, 0);

        assert_eq!(health(server.request(RequestCommand::Health)), (0, 0, 0));
        server.shutdown();
    }

    #[test]
    fn unix_socket_queues_typed_scene_render_without_pixel_payloads() {
        let server = ServerThread::start();
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[64, 32]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured { .. }
        ));
        let response = server.request(RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence: 11,
                timestamp_ns: 1_000_000_000,
                delta_ns: 16_000_000,
                brightness: 0.8,
                audio: Some(RenderAudioInput {
                    rms: 0.4,
                    bands: [0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.1],
                    beat: true,
                    beat_strength: 0.9,
                    feature_timestamp_ns: None,
                    feature_sequence: None,
                    fallback_bpm: Some(120.0),
                    beat_events: Vec::new(),
                    structure_events: Vec::new(),
                    selector_features: None,
                    effect_features: None,
                    beat_event_stream: false,
                }),
                power: Default::default(),
                outputs: vec![
                    RenderOutput {
                        animation: "rainbow".into(),
                        settings: serde_json::Map::new(),
                        effects: Vec::new(),
                        base_intensity: 1.0,
                        max_overlay_strength: 0.65,
                        zone_count: 1,
                        zone_pattern: "repeat".into(),
                        transition: None,
                        blackout: false,
                        ..RenderOutput::default()
                    },
                    RenderOutput {
                        animation: "strobe".into(),
                        settings: serde_json::Map::new(),
                        effects: Vec::new(),
                        base_intensity: 1.0,
                        max_overlay_strength: 0.65,
                        zone_count: 1,
                        zone_pattern: "repeat".into(),
                        transition: None,
                        blackout: false,
                        ..RenderOutput::default()
                    },
                ],
            },
            scheduler: None,
        });
        assert!(matches!(
            response.result,
            ResponseResult::RenderQueued {
                sequence: 11,
                replaced: false
            }
        ));
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let current = health(server.request(RequestCommand::Health));
            if current.0 == 1 {
                assert_eq!(current, (1, 0, 2));
                break;
            }
            assert!(Instant::now() < deadline, "scene was not rendered");
            thread::sleep(Duration::from_millis(5));
        }
        server.shutdown();
    }

    #[test]
    fn scheduled_scene_renders_again_without_another_ipc_request() {
        let server = ServerThread::start();
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[8]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured {
                output_count: 1,
                ..
            }
        ));

        let queued = server.request(RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence: 1,
                timestamp_ns: 1_000_000_000,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: None,
                power: RenderPowerConfig::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".into(),
                    ..RenderOutput::default()
                }],
            },
            scheduler: Some(RenderSchedulerConfig { max_fps: 100 }),
        });
        assert!(matches!(queued.result, ResponseResult::RenderQueued { .. }));

        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let ResponseResult::Health {
                frames_rendered, ..
            } = server.request(RequestCommand::Health).result
            else {
                panic!("expected health response");
            };
            if frames_rendered >= 3 {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "scheduled frames were not rendered"
            );
            thread::sleep(Duration::from_millis(5));
        }
        server.shutdown();
    }

    #[test]
    fn scheduled_scene_controls_are_ordered_and_audio_updates_are_replaceable() {
        let server = ServerThread::start();
        assert!(matches!(
            server
                .request(RequestCommand::Configure {
                    config: mock_config(&[8]),
                })
                .result,
            ResponseResult::Configured { .. }
        ));

        let scene = |revision, timestamp_ns| RenderSceneUpdate {
            revision,
            timestamp_ns,
            brightness: 1.0,
            power: RenderPowerConfig::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                ..RenderOutput::default()
            }],
            scheduler: RenderSchedulerConfig { max_fps: 100 },
        };
        assert!(matches!(
            server
                .request(RequestCommand::SceneUpdate {
                    update: scene(1, 1_000_000_000),
                })
                .result,
            ResponseResult::SceneUpdated {
                revision: 1,
                replaced: false
            }
        ));

        // Legacy render sequence values and scheduled scene revisions are
        // independent counters; a high render sequence must not reject a
        // later, correctly ordered scene revision.
        assert!(matches!(
            server
                .request(RequestCommand::Render {
                    frame: RenderFrameRequest {
                        sequence: 50,
                        timestamp_ns: 1_010_000_000,
                        delta_ns: 10_000_000,
                        brightness: 1.0,
                        audio: None,
                        power: RenderPowerConfig::default(),
                        outputs: vec![RenderOutput {
                            animation: "rainbow".into(),
                            ..RenderOutput::default()
                        }],
                    },
                    scheduler: None,
                })
                .result,
            ResponseResult::RenderQueued { sequence: 50, .. }
        ));
        assert!(matches!(
            server
                .request(RequestCommand::SceneUpdate {
                    update: scene(2, 1_020_000_000),
                })
                .result,
            ResponseResult::SceneUpdated { revision: 2, .. }
        ));

        let audio = RenderAudioInput {
            rms: 0.4,
            bands: [0.1; 8],
            beat: false,
            beat_strength: 0.0,
            feature_timestamp_ns: None,
            feature_sequence: Some(1),
            fallback_bpm: None,
            beat_events: Vec::new(),
            structure_events: Vec::new(),
            selector_features: None,
            effect_features: None,
            beat_event_stream: false,
        };
        assert!(matches!(
            server
                .request(RequestCommand::AudioUpdate {
                    update: RenderAudioUpdate {
                        sequence: 1,
                        timestamp_ns: 1_030_000_000,
                        audio: Some(audio.clone()),
                    },
                })
                .result,
            ResponseResult::AudioUpdated {
                sequence: 1,
                accepted: true
            }
        ));
        assert!(matches!(
            server
                .request(RequestCommand::AudioUpdate {
                    update: RenderAudioUpdate {
                        sequence: 1,
                        timestamp_ns: 1_040_000_000,
                        audio: Some(audio.clone()),
                    },
                })
                .result,
            ResponseResult::AudioUpdated {
                sequence: 1,
                accepted: false
            }
        ));
        assert!(matches!(
            server
                .request(RequestCommand::AudioUpdate {
                    update: RenderAudioUpdate {
                        sequence: 2,
                        timestamp_ns: 1_025_000_000,
                        audio: Some(audio),
                    },
                })
                .result,
            ResponseResult::AudioUpdated {
                sequence: 2,
                accepted: false
            }
        ));

        let ResponseResult::Health {
            scene_control_revision,
            audio_update_sequence,
            ..
        } = server.request(RequestCommand::Health).result
        else {
            panic!("expected health response");
        };
        assert_eq!(scene_control_revision, Some(2));
        assert_eq!(audio_update_sequence, Some(1));
        server.shutdown();
    }

    #[test]
    fn scheduled_beat_clock_consumes_events_and_reports_per_output_state() {
        let server = ServerThread::start();
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[8]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured {
                output_count: 1,
                ..
            }
        ));

        let beat_events = (0..7)
            .map(|index| RenderBeatEvent {
                sequence: index + 1,
                timestamp_ns: 1_000_000_000 + index * 500_000_000,
                strength: 0.9,
            })
            .collect();
        let queued = server.request(RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence: 1,
                timestamp_ns: 5_000_000_000,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: Some(RenderAudioInput {
                    rms: 0.5,
                    bands: [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
                    beat: true,
                    beat_strength: 0.9,
                    feature_timestamp_ns: None,
                    feature_sequence: None,
                    fallback_bpm: Some(120.0),
                    beat_events,
                    structure_events: Vec::new(),
                    selector_features: None,
                    effect_features: None,
                    beat_event_stream: false,
                }),
                power: RenderPowerConfig::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".into(),
                    ..RenderOutput::default()
                }],
            },
            scheduler: Some(RenderSchedulerConfig { max_fps: 100 }),
        });
        assert!(matches!(queued.result, ResponseResult::RenderQueued { .. }));

        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let ResponseResult::Health { beat_clocks, .. } =
                server.request(RequestCommand::Health).result
            else {
                panic!("expected health response");
            };
            if beat_clocks.len() == 1 && beat_clocks[0].beat_index >= 7 {
                assert!((beat_clocks[0].bpm - 120.0).abs() < 0.1);
                assert!(beat_clocks[0].confidence > 0.8);
                assert!(beat_clocks[0].next_beat_at_ns.unwrap() > 5_000_000_000);
                break;
            }
            assert!(Instant::now() < deadline, "Rust beat clock was not updated");
            thread::sleep(Duration::from_millis(2));
        }

        let stopped = server.request(RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence: 2,
                timestamp_ns: 5_016_000_000,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: None,
                power: RenderPowerConfig::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".into(),
                    ..RenderOutput::default()
                }],
            },
            scheduler: None,
        });
        assert!(matches!(
            stopped.result,
            ResponseResult::RenderQueued { .. }
        ));
        let ResponseResult::Health { beat_clocks, .. } =
            server.request(RequestCommand::Health).result
        else {
            panic!("expected health response");
        };
        assert!(beat_clocks.is_empty());
        server.shutdown();
    }

    #[test]
    fn cycling_scene_selects_the_next_animation_inside_the_rust_engine() {
        let server = ServerThread::start();
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[8]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured {
                output_count: 1,
                ..
            }
        ));
        let cycling = RenderCycling {
            order: RenderCycleOrder::Sequential,
            timing: RenderCycleTiming::Fixed,
            interval_ns: 200_000_000,
            seed: Some(7),
            choices: vec![
                RenderAnimationChoice {
                    animation: "rainbow".into(),
                    settings: serde_json::Map::new(),
                },
                RenderAnimationChoice {
                    animation: "rainbow_cycle".into(),
                    settings: serde_json::Map::new(),
                },
            ],
            transition_duration_ns: 50_000_000,
            transition_mode: "wipe".into(),
            selector: None,
            dynamic_idle: None,
            sequence: None,
            effect_scheduler: None,
        };
        let queued = server.request(RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence: 1,
                timestamp_ns: 1_000_000_000,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: None,
                power: RenderPowerConfig::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".into(),
                    cycling: Some(cycling),
                    ..RenderOutput::default()
                }],
            },
            scheduler: Some(RenderSchedulerConfig { max_fps: 100 }),
        });
        assert!(matches!(queued.result, ResponseResult::RenderQueued { .. }));

        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let ResponseResult::Health {
                selected_animations,
                scene_revisions,
                ..
            } = server.request(RequestCommand::Health).result
            else {
                panic!("expected health response");
            };
            if selected_animations == ["rainbow_cycle"] {
                assert_eq!(scene_revisions, [1]);
                break;
            }
            assert!(
                Instant::now() < deadline,
                "Rust cycling policy did not select the next animation"
            );
            thread::sleep(Duration::from_millis(2));
        }
        server.shutdown();
    }

    #[test]
    fn scheduled_dynamic_selector_chooses_a_drop_animation_inside_rust() {
        let server = ServerThread::start();
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[8]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured {
                output_count: 1,
                ..
            }
        ));
        let selector = RenderDynamicSelector {
            min_duration_ns: 0,
            max_duration_ns: 45_000_000_000,
            switch_cooldown_ns: 0,
            drop_cooldown_ns: 15_000_000_000,
            randomness: 0.0,
            history_size: 5,
            seed: Some(7),
            switch_margin: 0.12,
            switch_quantization: RenderSwitchQuantization::Off,
            bars_per_phrase: 8,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: None,
        };
        let dynamic = RenderCycling {
            order: RenderCycleOrder::Sequential,
            timing: RenderCycleTiming::Fixed,
            interval_ns: 1,
            seed: Some(7),
            choices: ["rainbow", "shockwave"]
                .into_iter()
                .map(|animation| RenderAnimationChoice {
                    animation: animation.into(),
                    settings: serde_json::Map::new(),
                })
                .collect(),
            transition_duration_ns: 50_000_000,
            transition_mode: "crossfade".into(),
            selector: Some(selector),
            dynamic_idle: None,
            sequence: None,
            effect_scheduler: None,
        };
        let queued = server.request(RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence: 1,
                timestamp_ns: 1_000_000_000,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: Some(RenderAudioInput {
                    rms: 0.7,
                    bands: [0.9, 0.8, 0.6, 0.4, 0.3, 0.2, 0.1, 0.1],
                    beat: true,
                    beat_strength: 0.9,
                    feature_timestamp_ns: None,
                    feature_sequence: Some(1),
                    fallback_bpm: Some(130.0),
                    beat_events: Vec::new(),
                    structure_events: vec![RenderStructureEvent {
                        output_index: 0,
                        sequence: 19,
                        timestamp_ns: 900_000_000,
                        phase: RenderMusicStructurePhase::Drop,
                        confidence: 0.9,
                        buildup_progress: 0.7,
                        drop_event: true,
                        section_change: true,
                        phase_change: false,
                    }],
                    selector_features: Some(RenderSelectorFeatures {
                        energy_level: 0.8,
                        energy: 0.8,
                        volume: 0.8,
                        brightness: 0.6,
                        onset_strength: 0.7,
                        bass_energy: 0.9,
                        bass: 0.9,
                        mid_energy: 0.3,
                        treble_energy: 0.6,
                        beat_confidence: 0.9,
                        bpm: 130.0,
                        beat: true,
                        silence: false,
                        drop_detected: false,
                        section_change: false,
                    }),
                    effect_features: None,
                    beat_event_stream: false,
                }),
                power: RenderPowerConfig::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".into(),
                    cycling: Some(dynamic),
                    ..RenderOutput::default()
                }],
            },
            scheduler: Some(RenderSchedulerConfig { max_fps: 100 }),
        });
        assert!(matches!(queued.result, ResponseResult::RenderQueued { .. }));

        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let ResponseResult::Health {
                selected_animations,
                scene_revisions,
                cycling_active,
                structure_event_sequences,
                ..
            } = server.request(RequestCommand::Health).result
            else {
                panic!("expected health response");
            };
            if selected_animations == ["shockwave"] {
                assert_eq!(scene_revisions, [1]);
                assert_eq!(cycling_active, [true]);
                assert_eq!(structure_event_sequences, [19]);
                break;
            }
            assert!(
                Instant::now() < deadline,
                "Rust Dynamic selector did not choose the drop animation"
            );
            thread::sleep(Duration::from_millis(2));
        }
        server.shutdown();
    }

    #[test]
    fn scheduled_dynamic_activity_gate_owns_idle_blackout_and_activation() {
        let server = ServerThread::start();
        let configured = server.request(RequestCommand::Configure {
            config: mock_config(&[8]),
        });
        assert!(matches!(
            configured.result,
            ResponseResult::Configured {
                output_count: 1,
                ..
            }
        ));
        let selector = RenderDynamicSelector {
            min_duration_ns: 0,
            max_duration_ns: 45_000_000_000,
            switch_cooldown_ns: 0,
            drop_cooldown_ns: 15_000_000_000,
            randomness: 0.0,
            history_size: 5,
            seed: Some(7),
            switch_margin: 0.12,
            switch_quantization: RenderSwitchQuantization::Off,
            bars_per_phrase: 8,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: Some(RenderMusicActivityConfig {
                activation_delay_ns: 0,
                idle_enter_frames: 2,
                ..RenderMusicActivityConfig::default()
            }),
        };
        let dynamic = RenderCycling {
            order: RenderCycleOrder::Sequential,
            timing: RenderCycleTiming::Fixed,
            interval_ns: 1,
            seed: Some(7),
            choices: ["rainbow", "shockwave"]
                .into_iter()
                .map(|animation| RenderAnimationChoice {
                    animation: animation.into(),
                    settings: serde_json::Map::new(),
                })
                .collect(),
            transition_duration_ns: 0,
            transition_mode: "crossfade".into(),
            selector: Some(selector),
            dynamic_idle: Some(RenderDynamicIdle {
                mode: RenderDynamicIdleMode::Off,
                animation: None,
                settings: serde_json::Map::new(),
                color: [0, 0, 0],
                brightness: 1.0,
            }),
            sequence: None,
            effect_scheduler: None,
        };
        let request = |sequence: u64, features: RenderSelectorFeatures| RequestCommand::Render {
            frame: RenderFrameRequest {
                sequence,
                timestamp_ns: 1_000_000_000 + sequence * 16_000_000,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: Some(RenderAudioInput {
                    rms: features.energy,
                    bands: [0.8; 8],
                    beat: features.beat,
                    beat_strength: 0.9,
                    feature_timestamp_ns: None,
                    feature_sequence: Some(sequence),
                    fallback_bpm: Some(120.0),
                    beat_events: Vec::new(),
                    structure_events: Vec::new(),
                    selector_features: Some(features),
                    effect_features: None,
                    beat_event_stream: false,
                }),
                power: RenderPowerConfig::default(),
                outputs: vec![RenderOutput {
                    animation: "rainbow".into(),
                    cycling: Some(dynamic.clone()),
                    ..RenderOutput::default()
                }],
            },
            scheduler: Some(RenderSchedulerConfig { max_fps: 100 }),
        };

        let queued = server.request(request(1, RenderSelectorFeatures::default()));
        assert!(matches!(queued.result, ResponseResult::RenderQueued { .. }));
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let ResponseResult::Health {
                frames_rendered,
                dynamic_gate_states,
                preview_outputs,
                ..
            } = server.request(RequestCommand::Health).result
            else {
                panic!("expected health response");
            };
            if frames_rendered >= 2 && dynamic_gate_states == ["idle"] {
                assert!(
                    preview_outputs.as_ref().is_some_and(|outputs| {
                        outputs.len() == 1 && outputs[0].iter().all(|channel| *channel == 0)
                    }),
                    "idle preview was not black: {preview_outputs:?}"
                );
                break;
            }
            assert!(
                Instant::now() < deadline,
                "Dynamic idle gate was not reported"
            );
            thread::sleep(Duration::from_millis(2));
        }

        let music = RenderSelectorFeatures {
            energy_level: 0.8,
            energy: 0.8,
            volume: 0.8,
            brightness: 0.6,
            onset_strength: 0.7,
            bass_energy: 0.9,
            bass: 0.9,
            mid_energy: 0.3,
            treble_energy: 0.6,
            beat_confidence: 0.9,
            bpm: 120.0,
            beat: true,
            silence: false,
            drop_detected: true,
            section_change: false,
        };
        let idle_frames_rendered = match server.request(RequestCommand::Health).result {
            ResponseResult::Health {
                frames_rendered, ..
            } => frames_rendered,
            _ => panic!("expected health response"),
        };
        let queued = server.request(request(2, music));
        assert!(matches!(queued.result, ResponseResult::RenderQueued { .. }));
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            let ResponseResult::Health {
                frames_rendered,
                dynamic_gate_states,
                selected_animations,
                preview_outputs,
                ..
            } = server.request(RequestCommand::Health).result
            else {
                panic!("expected health response");
            };
            if frames_rendered >= idle_frames_rendered + 2 && dynamic_gate_states == ["music"] {
                assert_eq!(selected_animations, ["shockwave"]);
                assert!(preview_outputs.is_some_and(|outputs| {
                    outputs.len() == 1 && outputs[0].iter().any(|channel| *channel != 0)
                }));
                break;
            }
            assert!(Instant::now() < deadline, "Dynamic gate did not activate");
            thread::sleep(Duration::from_millis(2));
        }
        server.shutdown();
    }

    #[test]
    fn dynamic_selector_consumes_each_audio_feature_sequence_once() {
        let selector = RenderDynamicSelector {
            min_duration_ns: 0,
            max_duration_ns: 0,
            switch_cooldown_ns: 0,
            drop_cooldown_ns: 0,
            randomness: 0.15,
            history_size: 5,
            seed: Some(7),
            switch_margin: 0.12,
            switch_quantization: RenderSwitchQuantization::Off,
            bars_per_phrase: 8,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: None,
        };
        let cycling = RenderCycling {
            order: RenderCycleOrder::Sequential,
            timing: RenderCycleTiming::Fixed,
            interval_ns: 1,
            seed: Some(7),
            choices: ["rainbow", "rainbow_cycle", "shockwave"]
                .into_iter()
                .map(|animation| RenderAnimationChoice {
                    animation: animation.into(),
                    settings: serde_json::Map::new(),
                })
                .collect(),
            transition_duration_ns: 0,
            transition_mode: "crossfade".into(),
            selector: Some(selector),
            dynamic_idle: None,
            sequence: None,
            effect_scheduler: None,
        };
        let features = RenderSelectorFeatures::default();
        let now = Instant::now();
        let initial_output = RenderOutput {
            animation: "rainbow".into(),
            ..RenderOutput::default()
        };
        let mut state = SelectorRuntimeState::new(Some(selector), &initial_output, now, 7);

        assert!(
            select_dynamic_choice(
                &cycling, "rainbow", &features, selector, 42, false, &mut state, now,
            )
            .is_some()
        );
        let random_state_after_first_update = state.random_state.clone();

        assert!(
            select_dynamic_choice(
                &cycling,
                "rainbow",
                &features,
                selector,
                42,
                false,
                &mut state,
                now + Duration::from_secs(1),
            )
            .is_none()
        );
        assert_eq!(state.random_state, random_state_after_first_update);
        assert!(
            select_dynamic_choice(
                &cycling,
                "rainbow",
                &features,
                selector,
                41,
                false,
                &mut state,
                now + Duration::from_secs(2),
            )
            .is_none()
        );
        assert_eq!(state.random_state, random_state_after_first_update);
        let random_state_before_structure_event = state.random_state.clone();
        let _ = select_dynamic_choice(
            &cycling,
            "rainbow",
            &features,
            selector,
            41,
            true,
            &mut state,
            now + Duration::from_secs(3),
        );
        assert_ne!(state.random_state, random_state_before_structure_event);
        assert_eq!(state.last_feature_sequence, Some(42));
    }

    #[test]
    fn rust_dynamic_sequence_owns_selection_and_advances_for_three_simulated_minutes() {
        let selector = RenderDynamicSelector {
            min_duration_ns: 0,
            max_duration_ns: 0,
            switch_cooldown_ns: 0,
            drop_cooldown_ns: 0,
            randomness: 0.0,
            history_size: 5,
            seed: Some(7),
            switch_margin: 0.0,
            switch_quantization: RenderSwitchQuantization::Off,
            bars_per_phrase: 8,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: None,
        };
        let cycling = RenderCycling {
            order: RenderCycleOrder::Sequential,
            timing: RenderCycleTiming::Fixed,
            interval_ns: 1,
            seed: Some(7),
            choices: ["rainbow", "shockwave"]
                .into_iter()
                .map(|animation| RenderAnimationChoice {
                    animation: animation.into(),
                    settings: serde_json::Map::new(),
                })
                .collect(),
            transition_duration_ns: 100_000_000,
            transition_mode: "crossfade".into(),
            selector: Some(selector),
            dynamic_idle: None,
            sequence: Some(RenderAnimationSequence {
                id: "night-drive".into(),
                repeat: true,
                steps: vec![
                    RenderAnimationSequenceStep {
                        animation: "shockwave".into(),
                        duration_bars: 1,
                    },
                    RenderAnimationSequenceStep {
                        animation: "rainbow".into(),
                        duration_bars: 2,
                    },
                ],
            }),
            effect_scheduler: None,
        };
        let initial_output = RenderOutput {
            animation: "rainbow".into(),
            cycling: Some(cycling.clone()),
            ..RenderOutput::default()
        };
        let simulation_origin = Instant::now();
        let mut selector_state =
            SelectorRuntimeState::new(Some(selector), &initial_output, simulation_origin, 7);
        let mut sequence_state = SequenceCoordinator::default();
        let features = RenderSelectorFeatures::default();
        let before_bar = RenderBeatClockState {
            sampled_at_ns: 0,
            bpm: 120.0,
            phase: 0.0,
            confidence: 0.9,
            beat_index: 1,
            next_beat_at_ns: Some(500_000_000),
            last_beat_at_ns: Some(0),
            last_event_sequence: 0,
        };
        let (output, _, _) = update_dynamic_output(
            &cycling,
            selector,
            &initial_output,
            &features,
            1,
            false,
            None,
            simulation_origin,
            0,
            before_bar,
            &mut selector_state,
            &mut sequence_state,
        );
        assert_eq!(output.animation, "rainbow");
        assert!(sequence_state.controls_playback());

        let at_bar = RenderBeatClockState {
            sampled_at_ns: 2_000_000_000,
            bpm: 120.0,
            phase: 0.0,
            confidence: 0.9,
            beat_index: 5,
            next_beat_at_ns: Some(2_500_000_000),
            last_beat_at_ns: Some(2_000_000_000),
            last_event_sequence: 0,
        };
        let (output, _, selected) = update_dynamic_output(
            &cycling,
            selector,
            &output,
            &features,
            2,
            false,
            None,
            simulation_origin + Duration::from_secs(2),
            2_000_000_000,
            at_bar,
            &mut selector_state,
            &mut sequence_state,
        );
        assert_eq!(selected, "shockwave");
        assert_eq!(output.animation, "shockwave");
        let state = sequence_state.playback_state(cycling.sequence.as_ref());
        assert_eq!(state.sequence_id.as_deref(), Some("night-drive"));
        assert!(state.active);
        assert_eq!(state.current_step_index, Some(0));
        assert_eq!(state.animation.as_deref(), Some("shockwave"));
        assert_eq!(state.remaining_bars, Some(1));

        let mut beat_tracker = BeatClockTracker::default();
        let mut next_beat_index = 0_u64;
        let mut beat_events = Vec::with_capacity(2);
        let mut simulated_output = output;
        let mut frame_set = FrameSet::new(&[8]);
        let mut renderers = vec![None];
        let mut render_request = RenderFrameRequest {
            sequence: 0,
            timestamp_ns: 0,
            delta_ns: 0,
            brightness: 0.8,
            audio: None,
            power: RenderPowerConfig::default(),
            outputs: vec![simulated_output.clone()],
        };
        let mut last_step = Some(0);
        let mut step_changes = 0;
        let mut animation_changes = 0;
        let mut lit_frames = 0;
        let frame_period_ns = 1_000_000_000_u64 / 30;
        let beat_period_ns = 500_000_000_u64;
        let simulated_duration_ns = 180 * 1_000_000_000_u64;
        let simulated_frames = simulated_duration_ns / frame_period_ns;

        for frame in 0..=simulated_frames {
            let now_ns = 2_000_000_000 + frame * frame_period_ns;
            beat_events.clear();
            while next_beat_index * beat_period_ns <= now_ns {
                let beat_index = next_beat_index;
                next_beat_index += 1;
                if beat_index > 0 && beat_index % 97 == 41 {
                    continue;
                }
                let nominal_timestamp_ns = beat_index * beat_period_ns;
                let jitter_ns = (beat_index % 5) as i64 * 1_000_000 - 2_000_000;
                let timestamp_ns = if beat_index == 0 {
                    0
                } else if beat_index == 190 {
                    150 * beat_period_ns
                } else {
                    nominal_timestamp_ns.saturating_add_signed(jitter_ns)
                };
                let event = RenderBeatEvent {
                    sequence: beat_index + 1,
                    timestamp_ns,
                    strength: 0.9,
                };
                beat_events.push(event);
                if beat_index > 0 && beat_index % 113 == 17 {
                    beat_events.push(event);
                }
            }
            let beat_clock = beat_tracker.update(&beat_events, now_ns, Some(120.0));
            let previous_animation = simulated_output.animation.clone();
            let (next_output, gate_state, selected) = update_dynamic_output(
                &cycling,
                selector,
                &simulated_output,
                &features,
                frame + 3,
                false,
                None,
                simulation_origin + Duration::from_nanos(now_ns),
                now_ns,
                beat_clock,
                &mut selector_state,
                &mut sequence_state,
            );
            assert_eq!(gate_state, "music");
            assert_eq!(selected, next_output.animation);
            animation_changes += usize::from(next_output.animation != previous_animation);
            simulated_output = next_output;

            render_request.sequence = frame + 1;
            render_request.timestamp_ns = now_ns;
            render_request.delta_ns = frame_period_ns;
            render_request.audio = Some(RenderAudioInput {
                rms: 0.55,
                bands: [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
                beat: beat_clock.phase < 0.05,
                beat_strength: if beat_clock.phase < 0.05 { 0.9 } else { 0.0 },
                feature_timestamp_ns: Some(now_ns),
                feature_sequence: Some(frame + 3),
                fallback_bpm: Some(120.0),
                beat_events: beat_events.clone(),
                structure_events: Vec::new(),
                selector_features: Some(features),
                effect_features: None,
                beat_event_stream: true,
            });
            render_request.outputs[0] = simulated_output.clone();
            let power = render_frame_request_at(
                &render_request,
                now_ns,
                frame_period_ns,
                &mut frame_set,
                &mut renderers,
            )
            .expect("simulated Dynamic frame renders");
            assert_eq!(power.output_count, 1);
            lit_frames += usize::from(
                frame_set.frames[0]
                    .chunks_exact(4)
                    .any(|pixel| pixel[..3].iter().any(|channel| *channel > 0)),
            );

            let state = sequence_state.playback_state(cycling.sequence.as_ref());
            assert!(state.active);
            assert!(!state.waiting_for_clock);
            assert_eq!(
                state.animation.as_deref(),
                Some(simulated_output.animation.as_str())
            );
            if state.current_step_index != last_step {
                step_changes += 1;
                last_step = state.current_step_index;
            }
        }

        assert!(step_changes > 40, "only {step_changes} sequence steps ran");
        assert!(
            animation_changes > 40,
            "only {animation_changes} scenes selected"
        );
        assert!(lit_frames > 0, "Rust rendered no lit frames");
        assert!(beat_tracker.state(182_000_000_000).confidence > 0.8);
        let state = sequence_state.playback_state(cycling.sequence.as_ref());
        assert_eq!(
            state.animation.as_deref(),
            Some(simulated_output.animation.as_str())
        );
        assert!(state.remaining_bars.is_some_and(|bars| bars <= 2));
    }

    #[test]
    fn seeded_shuffle_is_repeatable_and_skips_the_current_animation() {
        let cycling = RenderCycling {
            order: RenderCycleOrder::Shuffle,
            timing: RenderCycleTiming::Fixed,
            interval_ns: 1,
            seed: Some(43),
            choices: ["a", "b", "c", "d"]
                .into_iter()
                .map(|animation| RenderAnimationChoice {
                    animation: animation.into(),
                    settings: serde_json::Map::new(),
                })
                .collect(),
            transition_duration_ns: 0,
            transition_mode: "crossfade".into(),
            selector: None,
            dynamic_idle: None,
            sequence: None,
            effect_scheduler: None,
        };
        let mut left_state = PythonRandom::from_u64(43);
        let mut right_state = PythonRandom::from_u64(43);
        let left = (0..16)
            .map(|_| {
                next_cycling_choice(&cycling, "b", &mut left_state)
                    .unwrap()
                    .animation
                    .clone()
            })
            .collect::<Vec<_>>();
        let right = (0..16)
            .map(|_| {
                next_cycling_choice(&cycling, "b", &mut right_state)
                    .unwrap()
                    .animation
                    .clone()
            })
            .collect::<Vec<_>>();
        assert_eq!(left, right);
        assert_eq!(
            left,
            [
                "a", "c", "d", "a", "c", "c", "d", "d", "a", "c", "d", "c", "d", "a", "d", "c",
            ]
        );
        assert!(left.iter().all(|animation| animation != "b"));
    }

    #[test]
    fn render_capabilities_are_available_before_output_configuration() {
        let server = ServerThread::start();
        let response = server.request(RequestCommand::RenderCapabilities);
        assert!(matches!(
            response.result,
            ResponseResult::RenderCapabilities { animations }
                if animations == [
                    "rainbow",
                    "rainbow_cycle",
                    "theater_chase",
                    "strobe",
                    "pulse",
                    "police",
                    "bpm",
                    "wave",
                    "sinelon",
                    "bouncing_ball",
                    "color_wipe",
                    "juggle",
                    "comet",
                    "aurora",
                    "fire",
                    "twinkle",
                    "rainbow_strobe",
                    "laser_sweep",
                    "disco_sparkle",
                    "dual_comet",
                    "dance_floor",
                    "red_rave_sweep",
                    "red_rave_chase",
                    "red_blackout_strobe",
                    "glow_rush",
                    "rave_pulse",
                    "rave_scanner",
                    "neon_confetti",
                    "strobe_chase",
                    "plasma_rave",
                    "dual_laser",
                    "disco_comet",
                    "neon_storm",
                    "peak_mirror",
                    "shockwave",
                    "center_burst",
                    "mirror_flash",
                    "spectrum_flash",
                    "club_flash",
                    "color_burst",
                    "drop_explosion",
                    "drop_wave",
                    "bass_drop",
                    "beat_explosion",
                    "beat_ripple",
                    "beat_tunnel",
                    "beat_wave",
                    "hard_beat",
                    "confetti",
                    "comet_storm",
                    "electric_storm",
                    "firework_burst",
                    "lightning_strike",
                    "pixel_explosion",
                ]
        ));
        assert_eq!(health(server.request(RequestCommand::Health)), (0, 0, 0));
        server.shutdown();
    }

    #[test]
    fn render_request_writes_pixels_into_the_preallocated_frame_set() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![
                RenderOutput {
                    animation: "rainbow".into(),
                    settings: serde_json::Map::new(),
                    effects: Vec::new(),
                    base_intensity: 1.0,
                    max_overlay_strength: 0.65,
                    zone_count: 1,
                    zone_pattern: "repeat".into(),
                    transition: None,
                    blackout: false,
                    ..RenderOutput::default()
                },
                RenderOutput {
                    animation: "strobe".into(),
                    settings: serde_json::Map::new(),
                    effects: Vec::new(),
                    base_intensity: 1.0,
                    max_overlay_strength: 0.65,
                    zone_count: 1,
                    zone_pattern: "repeat".into(),
                    transition: None,
                    blackout: false,
                    ..RenderOutput::default()
                },
            ],
        };
        let mut frame = FrameSet::new(&[32, 24]);
        let addresses = [frame.frames[0].as_ptr(), frame.frames[1].as_ptr()];
        let mut renderers = vec![None, None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert_eq!(frame.frames[0].as_ptr(), addresses[0]);
        assert_eq!(frame.frames[1].as_ptr(), addresses[1]);
        assert!(frame.frames[0].chunks_exact(4).any(|pixel| pixel[3] > 0));
        assert!(frame.frames[1].chunks_exact(4).all(|pixel| pixel[3] > 0));
        assert!(
            frame.frames[1]
                .chunks_exact(4)
                .all(|pixel| pixel == &frame.frames[1][..4])
        );
        assert_eq!(
            renderers[0]
                .as_ref()
                .unwrap()
                .animation
                .as_ref()
                .unwrap()
                .frame_index(),
            1
        );
        assert_eq!(
            renderers[1]
                .as_ref()
                .unwrap()
                .animation
                .as_ref()
                .unwrap()
                .frame_index(),
            1
        );
    }

    #[test]
    fn render_request_crossfades_scenes_from_the_previous_rust_frame() {
        let mut request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 1_000_000_000,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                settings: serde_json::Map::new(),
                effects: Vec::new(),
                base_intensity: 1.0,
                max_overlay_strength: 0.65,
                zone_count: 1,
                zone_pattern: "repeat".into(),
                transition: None,
                blackout: false,
                ..RenderOutput::default()
            }],
        };
        let mut frame = FrameSet::new(&[8]);
        let mut renderers = vec![None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        let source = frame.frames[0].clone();

        request.sequence = 2;
        request.timestamp_ns = 1_050_000_000;
        request.outputs[0].animation = "strobe".into();
        request.outputs[0].transition = Some(RenderTransition {
            started_at_ns: 1_000_000_000,
            duration_ns: 100_000_000,
            mode: "crossfade".into(),
        });
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();

        let input = RenderingInput {
            timestamp_ns: 1_050_000_000,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
        };
        let mut target = super::FrameBuffer::new(8).unwrap();
        AnimationInstance::new("strobe", &serde_json::Map::new())
            .unwrap()
            .render_next(input, &mut target)
            .unwrap();
        let source = super::FrameBuffer::from_bytes(source).unwrap();
        let mut expected = super::FrameBuffer::new(8).unwrap();
        apply_transition(
            &source,
            &target,
            &mut expected,
            TransitionMode::Crossfade,
            0.5,
        )
        .unwrap();
        assert_eq!(frame.frames[0], expected.as_bytes());
        assert!(renderers[0].as_ref().unwrap().active_transition.is_some());
    }

    #[test]
    fn render_scene_revision_restarts_same_animation_and_keeps_transition_source() {
        let mut request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 1_000_000_000,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                ..RenderOutput::default()
            }],
        };
        let mut frame = FrameSet::new(&[8]);
        let mut renderers = vec![None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        let source = frame.frames[0].clone();
        assert_eq!(
            renderers[0]
                .as_ref()
                .unwrap()
                .animation
                .as_ref()
                .unwrap()
                .frame_index(),
            1
        );

        request.sequence = 2;
        request.timestamp_ns += request.delta_ns;
        request.outputs[0].scene_revision = 1;
        request.outputs[0].brightness = Some(0.25);
        request.outputs[0].transition = Some(RenderTransition {
            started_at_ns: 1_000_000_000,
            duration_ns: 100_000_000,
            mode: "crossfade".into(),
        });
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();

        let state = renderers[0].as_ref().unwrap();
        assert_eq!(state.animation.as_ref().unwrap().frame_index(), 1);
        assert!(state.active_transition.is_some());
        assert_eq!(state.transition_source.as_ref().unwrap().as_bytes(), source);
        request.sequence = 3;
        request.timestamp_ns = 1_120_000_000;
        request.outputs[0].transition = None;
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert!(renderers[0].as_ref().unwrap().active_transition.is_none());
        assert!(frame.frames[0].chunks_exact(4).all(|pixel| pixel[3] <= 64));
    }

    #[test]
    fn render_request_sends_black_during_blackout_and_keeps_scene_state_warm() {
        let mut request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 1_000_000_000,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                settings: serde_json::Map::new(),
                effects: Vec::new(),
                base_intensity: 1.0,
                max_overlay_strength: 0.65,
                zone_count: 1,
                zone_pattern: "repeat".into(),
                transition: None,
                blackout: false,
                ..RenderOutput::default()
            }],
        };
        let mut frame = FrameSet::new(&[8]);
        let mut renderers = vec![None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert!(frame.frames[0].chunks_exact(4).any(|pixel| pixel[0] != 0));

        request.sequence = 2;
        request.timestamp_ns += request.delta_ns;
        request.outputs[0].blackout = true;
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert!(frame.frames[0].iter().all(|channel| *channel == 0));
        assert!(
            renderers[0]
                .as_ref()
                .unwrap()
                .last_frame
                .as_ref()
                .unwrap()
                .as_bytes()
                .iter()
                .any(|channel| *channel != 0)
        );

        request.sequence = 3;
        request.timestamp_ns += request.delta_ns;
        request.outputs[0].blackout = false;
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert!(frame.frames[0].chunks_exact(4).any(|pixel| pixel[0] != 0));
    }

    #[test]
    fn render_request_composites_active_effects_without_replacing_output_buffers() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: Some(RenderAudioInput {
                rms: 0.5,
                bands: [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
                beat: true,
                beat_strength: 1.0,
                feature_timestamp_ns: None,
                feature_sequence: None,
                fallback_bpm: None,
                beat_events: Vec::new(),
                structure_events: Vec::new(),
                selector_features: None,
                effect_features: None,
                beat_event_stream: false,
            }),
            power: Default::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                settings: serde_json::Map::new(),
                effects: vec![RenderEffect {
                    name: "hard_beat".into(),
                    settings: serde_json::Map::new(),
                    intensity: 1.0,
                    trigger_beat: false,
                    trigger_beat_strength: 0.0,
                }],
                base_intensity: 0.0,
                max_overlay_strength: 1.0,
                zone_count: 1,
                zone_pattern: "repeat".into(),
                transition: None,
                blackout: false,
                ..RenderOutput::default()
            }],
        };
        let mut frame = FrameSet::new(&[32]);
        let frame_address = frame.frames[0].as_ptr();
        let mut renderers = vec![None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        let first_frame = frame.frames[0].clone();
        let base_frame_address = renderers[0]
            .as_ref()
            .unwrap()
            .base_frame
            .as_ref()
            .unwrap()
            .as_bytes()
            .as_ptr();
        assert!(
            frame.frames[0]
                .chunks_exact(4)
                .any(|pixel| pixel[0..3] != [0, 0, 0])
        );
        assert_eq!(frame.frames[0].as_ptr(), frame_address);
        assert_eq!(
            renderers[0]
                .as_ref()
                .unwrap()
                .base_frame
                .as_ref()
                .unwrap()
                .as_bytes()
                .as_ptr(),
            base_frame_address
        );

        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert_ne!(frame.frames[0], first_frame);
        assert_eq!(frame.frames[0].as_ptr(), frame_address);
        let active_effects = renderers[0]
            .as_ref()
            .unwrap()
            .effects
            .as_ref()
            .unwrap()
            .active_names()
            .collect::<Vec<_>>();
        assert_eq!(active_effects, ["hard_beat"]);
    }

    #[test]
    fn render_finalization_applies_color_correction_before_power_scaling() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: RenderPowerConfig {
                enabled: true,
                budget_watts: Some(0.05),
            },
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                color_correction: [128, 255, 64],
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: Some(0.04),
                ..RenderOutput::default()
            }],
        };
        let mut frames = vec![vec![255, 128, 64, 255]];
        let summary = finalize_render_frames(&request, &mut frames).unwrap();

        assert_eq!(frames[0], [128, 128, 16, 95]);
        assert!((summary.estimated_watts - 0.10666666666666667).abs() < 1e-12);
        assert!((summary.applied_scale - 0.375).abs() < 1e-12);
        assert_eq!(summary.preview_counts[0], 1);
        assert_eq!(&summary.preview_rgba[0][..4], &[128, 128, 16, 95]);
    }

    #[test]
    fn render_preview_sampling_includes_endpoints_and_reverses_output_order() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: RenderPowerConfig::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                reversed: true,
                ..RenderOutput::default()
            }],
        };
        let mut frames = vec![
            (0..129_u8)
                .flat_map(|red| [red, 0, 0, 255])
                .collect::<Vec<_>>(),
        ];
        let summary = finalize_render_frames(&request, &mut frames).unwrap();

        assert_eq!(summary.preview_counts[0], MAX_RENDER_PREVIEW_PIXELS);
        assert_eq!(summary.preview_rgba[0][..4], [128, 0, 0, 255]);
        assert_eq!(summary.preview_rgba[0][4..8], [127, 0, 0, 255]);
        assert_eq!(
            summary.preview_rgba[0][124 * 4..128 * 4],
            [4, 0, 0, 255, 3, 0, 0, 255, 2, 0, 0, 255, 0, 0, 0, 255,]
        );
        assert_eq!(frames[0].len(), 129 * 4);
    }

    #[test]
    fn render_finalization_keeps_blackout_out_of_the_power_estimate() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: RenderPowerConfig {
                enabled: true,
                budget_watts: Some(0.1),
            },
            outputs: vec![
                RenderOutput {
                    animation: "rainbow".into(),
                    ..RenderOutput::default()
                },
                RenderOutput {
                    animation: "rainbow".into(),
                    blackout: true,
                    ..RenderOutput::default()
                },
            ],
        };
        let mut frames = vec![vec![255; 4], vec![255; 4]];
        let summary = finalize_render_frames(&request, &mut frames).unwrap();

        assert_eq!(summary.estimated_watts, 0.3);
        assert!((summary.applied_scale - 1.0 / 3.0).abs() < 1e-12);
        assert_eq!(frames[0], [255, 255, 255, 85]);
        assert_eq!(frames[1], [0; 4]);
    }

    #[test]
    fn render_power_finalization_matches_python_golden_frames() {
        let fixtures: serde_json::Value =
            serde_json::from_str(include_str!("../golden_power_finalization.json"))
                .expect("Python power-finalization fixtures must be valid JSON");
        assert_eq!(fixtures["schema_version"], 1);
        let cases = fixtures["cases"].as_array().expect("fixture cases");
        assert_eq!(cases.len(), 2);

        for case in cases {
            let power = &case["power"];
            let outputs = case["outputs"].as_array().expect("output fixture array");
            let request = RenderFrameRequest {
                sequence: 1,
                timestamp_ns: 0,
                delta_ns: 16_000_000,
                brightness: 1.0,
                audio: None,
                power: RenderPowerConfig {
                    enabled: power["enabled"].as_bool().expect("power enabled"),
                    budget_watts: power["budget_watts"].as_f64(),
                },
                outputs: outputs
                    .iter()
                    .map(|output| RenderOutput {
                        animation: output["animation"]
                            .as_str()
                            .expect("animation name")
                            .to_owned(),
                        color_correction: output["color_correction"]
                            .as_array()
                            .expect("color correction")
                            .iter()
                            .map(|channel| channel.as_u64().expect("correction channel") as u8)
                            .collect::<Vec<_>>()
                            .try_into()
                            .expect("three correction channels"),
                        voltage_v: output["voltage_v"].as_f64().expect("voltage"),
                        full_white_current_a: output["full_white_current_a"]
                            .as_f64()
                            .expect("full-white current"),
                        power_limit_watts: output["power_limit_watts"].as_f64(),
                        blackout: output["blackout"].as_bool().expect("blackout"),
                        ..RenderOutput::default()
                    })
                    .collect(),
            };
            let mut frames = case["input_frames"]
                .as_array()
                .expect("input frames")
                .iter()
                .map(|frame| {
                    frame
                        .as_array()
                        .expect("frame pixels")
                        .iter()
                        .flat_map(|pixel| {
                            pixel
                                .as_array()
                                .expect("RGBA pixel")
                                .iter()
                                .map(|channel| channel.as_u64().expect("pixel channel") as u8)
                        })
                        .collect::<Vec<_>>()
                })
                .collect::<Vec<_>>();

            let summary = finalize_render_frames(&request, &mut frames)
                .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
            let expected_watts = case["estimated_watts"].as_f64().expect("estimated watts");
            let expected_scale = case["applied_scale"].as_f64().expect("applied scale");
            assert!(
                (summary.estimated_watts - expected_watts).abs() <= 1e-12,
                "{} estimated watts: {} != {}",
                case["name"],
                summary.estimated_watts,
                expected_watts
            );
            assert!(
                (summary.applied_scale - expected_scale).abs() <= 1e-12,
                "{} applied scale: {} != {}",
                case["name"],
                summary.applied_scale,
                expected_scale
            );
            let expected_output_watts = case["output_watts"]
                .as_array()
                .expect("per-output power estimates")
                .iter()
                .map(|watts| watts.as_f64().expect("output watts"))
                .collect::<Vec<_>>();
            assert_eq!(summary.output_count, expected_output_watts.len());
            for (actual, expected) in summary.output_watts[..summary.output_count]
                .iter()
                .zip(expected_output_watts)
            {
                assert!((actual - expected).abs() <= 1e-12);
            }
            let expected_limiting_output_index = case["limiting_output_index"]
                .as_u64()
                .map(|index| index as usize);
            assert_eq!(
                summary.limiting_output_index, expected_limiting_output_index,
                "{} limiting output",
                case["name"]
            );
            let expected_frames =
                case["output_frames"]
                    .as_array()
                    .expect("expected output frames")
                    .iter()
                    .map(|frame| {
                        frame
                            .as_array()
                            .expect("expected frame pixels")
                            .iter()
                            .flat_map(|pixel| {
                                pixel.as_array().expect("expected RGBA pixel").iter().map(
                                    |channel| channel.as_u64().expect("expected channel") as u8,
                                )
                            })
                            .collect::<Vec<_>>()
                    })
                    .collect::<Vec<_>>();
            assert_eq!(frames, expected_frames, "{} output frames", case["name"]);
        }
    }

    #[test]
    fn multi_strip_render_layouts_match_python_golden_frames() {
        const RGB_TOLERANCE: u8 = 5;
        const ALPHA_TOLERANCE: u8 = 1;
        let fixtures: serde_json::Value =
            serde_json::from_str(include_str!("../golden_layouts.json"))
                .expect("Python multi-strip layout fixtures must be valid JSON");
        assert_eq!(fixtures["schema_version"], 1);
        let cases = fixtures["cases"].as_array().expect("layout fixture cases");
        assert_eq!(cases.len(), 3);

        for case in cases {
            let pixel_counts = case["pixel_counts"]
                .as_array()
                .expect("physical pixel counts")
                .iter()
                .map(|count| count.as_u64().expect("pixel count") as usize)
                .collect::<Vec<_>>();
            let zone_counts = case["zone_counts"].as_array().expect("zone counts");
            let zone_patterns = case["zone_patterns"].as_array().expect("zone patterns");
            let scene_offsets = case["scene_offsets"].as_array().expect("scene offsets");
            let animation = case["animation"].as_str().expect("animation name");
            let settings = case["settings"]
                .as_object()
                .expect("animation settings")
                .clone();
            let scene_pixel_count = case["scene_pixel_count"]
                .as_u64()
                .map(|count| count as usize);
            let scene_resample = case["scene_resample"]
                .as_bool()
                .expect("scene resampling flag");
            let brightness = case["brightness"].as_f64().expect("brightness");
            let mut frame = FrameSet::new(&pixel_counts);
            let mut renderers = (0..pixel_counts.len()).map(|_| None).collect::<Vec<_>>();
            let expected_frames = case["frames"].as_array().expect("golden frame sequence");

            for expected in expected_frames {
                let timestamp_ns = expected["timestamp_ns"].as_u64().expect("render timestamp");
                let delta_ns = expected["delta_ns"].as_u64().expect("frame delta");
                let audio = if expected["audio"].is_null() {
                    None
                } else {
                    Some(RenderAudioInput {
                        rms: expected["audio"]["rms"].as_f64().expect("audio rms"),
                        bands: expected["audio"]["bands"]
                            .as_array()
                            .expect("audio bands")
                            .iter()
                            .map(|band| band.as_f64().expect("audio band"))
                            .collect::<Vec<_>>()
                            .try_into()
                            .expect("eight audio bands"),
                        beat: expected["audio"]["beat"].as_bool().expect("audio beat"),
                        beat_strength: expected["audio"]["beat_strength"]
                            .as_f64()
                            .expect("beat strength"),
                        feature_timestamp_ns: expected["audio"]["feature_timestamp_ns"].as_u64(),
                        feature_sequence: None,
                        fallback_bpm: None,
                        beat_events: Vec::new(),
                        structure_events: Vec::new(),
                        selector_features: None,
                        effect_features: None,
                        beat_event_stream: false,
                    })
                };
                let request = RenderFrameRequest {
                    sequence: timestamp_ns / delta_ns + 1,
                    timestamp_ns,
                    delta_ns,
                    brightness: 1.0,
                    audio,
                    power: RenderPowerConfig::default(),
                    outputs: pixel_counts
                        .iter()
                        .enumerate()
                        .map(|(index, _)| RenderOutput {
                            animation: animation.to_owned(),
                            settings: settings.clone(),
                            brightness: Some(brightness),
                            scene_pixel_count,
                            scene_offset: scene_offsets[index].as_u64().expect("scene offset")
                                as usize,
                            scene_resample,
                            zone_count: zone_counts[index].as_u64().expect("zone count") as usize,
                            zone_pattern: zone_patterns[index]
                                .as_str()
                                .expect("zone pattern")
                                .to_owned(),
                            ..RenderOutput::default()
                        })
                        .collect(),
                };
                render_frame_request(&request, &mut frame, &mut renderers)
                    .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));

                let expected_outputs = expected["outputs_hex"].as_array().expect("output frames");
                assert_eq!(frame.frames.len(), expected_outputs.len());
                for (output_index, (actual, encoded)) in
                    frame.frames.iter().zip(expected_outputs).enumerate()
                {
                    let encoded = encoded.as_str().expect("hex output frame");
                    assert_eq!(encoded.len() % 2, 0, "{}", case["name"]);
                    let expected_bytes = encoded
                        .as_bytes()
                        .chunks_exact(2)
                        .map(|pair| {
                            u8::from_str_radix(
                                std::str::from_utf8(pair).expect("hex frame is UTF-8"),
                                16,
                            )
                            .expect("valid hex byte")
                        })
                        .collect::<Vec<_>>();
                    assert_eq!(actual.len(), expected_bytes.len(), "{}", case["name"]);
                    for (byte_index, (actual, expected)) in
                        actual.iter().zip(expected_bytes).enumerate()
                    {
                        let tolerance = if byte_index % 4 == 3 {
                            ALPHA_TOLERANCE
                        } else {
                            RGB_TOLERANCE
                        };
                        assert!(
                            actual.abs_diff(expected) <= tolerance,
                            "{} output {output_index} byte {byte_index}: {actual} != {expected} (tolerance {tolerance})",
                            case["name"]
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn render_request_composites_on_logical_canvas_then_maps_alternating_zones() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: Some(RenderAudioInput {
                rms: 0.5,
                bands: [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
                beat: true,
                beat_strength: 1.0,
                feature_timestamp_ns: None,
                feature_sequence: None,
                fallback_bpm: None,
                beat_events: Vec::new(),
                structure_events: Vec::new(),
                selector_features: None,
                effect_features: None,
                beat_event_stream: false,
            }),
            power: Default::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                settings: serde_json::Map::new(),
                effects: vec![RenderEffect {
                    name: "hard_beat".into(),
                    settings: serde_json::Map::new(),
                    intensity: 0.75,
                    trigger_beat: false,
                    trigger_beat_strength: 0.0,
                }],
                base_intensity: 0.4,
                max_overlay_strength: 0.6,
                zone_count: 3,
                zone_pattern: "alternate".into(),
                transition: None,
                blackout: false,
                ..RenderOutput::default()
            }],
        };
        let mut frame = FrameSet::new(&[10]);
        let output_address = frame.frames[0].as_ptr();
        let mut renderers = vec![None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();

        let state = renderers[0].as_ref().unwrap();
        assert_eq!(state.pixel_count, 4);
        assert_eq!(state.base_frame.as_ref().unwrap().pixel_count(), 4);
        let logical = state.logical_frame.as_ref().unwrap();
        let logical_address = logical.as_bytes().as_ptr();
        let mut expected = super::FrameBuffer::new(10).unwrap();
        super::ZoneMapping::new(10, 3, true)
            .unwrap()
            .map_frame(logical, &mut expected)
            .unwrap();
        assert_eq!(frame.frames[0], expected.as_bytes());

        render_frame_request(&request, &mut frame, &mut renderers).unwrap();
        assert_eq!(frame.frames[0].as_ptr(), output_address);
        assert_eq!(
            renderers[0]
                .as_ref()
                .unwrap()
                .logical_frame
                .as_ref()
                .unwrap()
                .as_bytes()
                .as_ptr(),
            logical_address
        );
    }

    #[test]
    fn render_request_slices_a_shared_scene_for_continuous_outputs() {
        let mut request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![
                RenderOutput {
                    animation: "rainbow".into(),
                    scene_pixel_count: Some(5),
                    scene_offset: 0,
                    zone_count: 2,
                    ..RenderOutput::default()
                },
                RenderOutput {
                    animation: "rainbow".into(),
                    scene_pixel_count: Some(5),
                    scene_offset: 2,
                    ..RenderOutput::default()
                },
            ],
        };
        let mut frame = FrameSet::new(&[4, 3]);
        let mut renderers = vec![None, None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();

        let reference_request = RenderFrameRequest {
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                ..RenderOutput::default()
            }],
            ..request.clone()
        };
        let mut reference_frame = FrameSet::new(&[5]);
        let mut reference_renderers = vec![None];
        render_frame_request(
            &reference_request,
            &mut reference_frame,
            &mut reference_renderers,
        )
        .unwrap();
        let scene = &reference_frame.frames[0];
        assert_eq!(&frame.frames[0][..8], &scene[..8]);
        assert_eq!(&frame.frames[0][8..], &scene[..8]);
        assert_eq!(frame.frames[1], scene[8..20]);
        assert_eq!(renderers[0].as_ref().unwrap().pixel_count, 5);
        assert_eq!(renderers[1].as_ref().unwrap().scene_offset, 2);

        request.outputs[1].scene_offset = 4;
        assert!(render_frame_request(&request, &mut frame, &mut renderers).is_err());
    }

    #[test]
    fn render_request_resamples_shared_scenes_for_mirrored_outputs() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![
                RenderOutput {
                    animation: "rainbow".into(),
                    scene_pixel_count: Some(4),
                    scene_resample: true,
                    ..RenderOutput::default()
                },
                RenderOutput {
                    animation: "rainbow".into(),
                    scene_pixel_count: Some(4),
                    scene_resample: true,
                    ..RenderOutput::default()
                },
            ],
        };
        let mut frame = FrameSet::new(&[4, 3]);
        let mut renderers = vec![None, None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();

        let reference_request = RenderFrameRequest {
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                ..RenderOutput::default()
            }],
            ..request
        };
        let mut reference_frame = FrameSet::new(&[4]);
        let mut reference_renderers = vec![None];
        render_frame_request(
            &reference_request,
            &mut reference_frame,
            &mut reference_renderers,
        )
        .unwrap();
        let scene = &reference_frame.frames[0];
        assert_eq!(frame.frames[0], *scene);
        assert_eq!(&frame.frames[1][..4], &scene[..4]);
        assert_eq!(&frame.frames[1][4..8], &scene[8..12]);
        assert_eq!(&frame.frames[1][8..12], &scene[12..16]);

        let mut invalid = reference_request;
        invalid.outputs[0].scene_pixel_count = Some(2);
        invalid.outputs[0].scene_resample = true;
        invalid.outputs[0].scene_offset = 1;
        let mut invalid_frame = FrameSet::new(&[3]);
        let mut invalid_renderers = vec![None];
        assert!(
            render_frame_request(&invalid, &mut invalid_frame, &mut invalid_renderers).is_err()
        );
    }

    #[test]
    fn render_request_applies_solid_color_brightness_and_zone_mapping() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![RenderOutput {
                animation: "rainbow".into(),
                brightness: Some(0.5),
                solid_color: Some([128, 64, 32]),
                zone_count: 2,
                ..RenderOutput::default()
            }],
        };
        let mut frame = FrameSet::new(&[4]);
        let mut renderers = vec![None];
        render_frame_request(&request, &mut frame, &mut renderers).unwrap();

        assert_eq!(frame.frames[0], [128, 64, 32, 127].repeat(4));
        assert!(renderers[0].as_ref().unwrap().active_transition.is_none());
    }

    #[test]
    fn invalid_layered_render_does_not_advance_other_outputs() {
        let request = RenderFrameRequest {
            sequence: 1,
            timestamp_ns: 0,
            delta_ns: 16_000_000,
            brightness: 1.0,
            audio: None,
            power: Default::default(),
            outputs: vec![
                RenderOutput {
                    animation: "rainbow".into(),
                    settings: serde_json::Map::new(),
                    effects: Vec::new(),
                    base_intensity: 1.0,
                    max_overlay_strength: 0.65,
                    zone_count: 1,
                    zone_pattern: "repeat".into(),
                    transition: None,
                    blackout: false,
                    ..RenderOutput::default()
                },
                RenderOutput {
                    animation: "strobe".into(),
                    settings: serde_json::Map::new(),
                    effects: vec![RenderEffect {
                        name: "unknown_effect".into(),
                        settings: serde_json::Map::new(),
                        intensity: 1.0,
                        trigger_beat: false,
                        trigger_beat_strength: 0.0,
                    }],
                    base_intensity: 1.0,
                    max_overlay_strength: 0.65,
                    zone_count: 1,
                    zone_pattern: "repeat".into(),
                    transition: None,
                    blackout: false,
                    ..RenderOutput::default()
                },
            ],
        };
        let mut frame = FrameSet::new(&[32, 24]);
        let original_frames = frame.frames.clone();
        let mut renderers = vec![None, None];
        assert!(render_frame_request(&request, &mut frame, &mut renderers).is_err());
        assert_eq!(frame.frames, original_frames);
        assert!(renderers.iter().all(Option::is_none));
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
    fn v2_clients_receive_v2_responses_for_existing_commands() {
        let server = ServerThread::start();
        let response =
            server.request_with_version(PREVIOUS_PROTOCOL_VERSION, RequestCommand::Health);
        assert_eq!(response.protocol_version, PREVIOUS_PROTOCOL_VERSION);
        assert!(matches!(response.result, ResponseResult::Health { .. }));
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
    fn oversized_control_requests_are_rejected_without_stopping_the_server() {
        let server = ServerThread::start();
        let mut stream = UnixStream::connect(&server.socket).unwrap();
        stream
            .write_all(&vec![b'x'; MAX_CONTROL_MESSAGE_BYTES + 1])
            .unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).unwrap();
        assert!(response.is_empty());

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
    fn frame_scheduler_advances_from_deadlines_and_skips_elapsed_ticks() {
        let origin = Instant::now();
        let period = Duration::from_millis(10);
        let (on_time, on_time_skipped) =
            advance_frame_deadline(origin + period, origin + Duration::from_millis(5), period);
        assert_eq!(on_time, origin + period);
        assert_eq!(on_time_skipped, 0);

        let (after_stall, skipped) =
            advance_frame_deadline(origin + period, origin + Duration::from_millis(35), period);
        assert_eq!(after_stall, origin + Duration::from_millis(40));
        assert_eq!(skipped, 3);
    }

    #[test]
    fn health_keeps_queue_wait_transmission_and_skew_separate() {
        let mut health = RuntimeHealth::new();
        health.record_frame(9, Instant::now(), 1.25, Some(2.5), Some(0.75), None);

        assert!(health.frame_latency_ms.is_some());
        assert_eq!(health.queue_latency_ms, Some(1.25));
        assert_eq!(health.transmission_ms, Some(2.5));
        assert_eq!(health.sequential_skew_ms, Some(0.75));
    }

    #[test]
    fn audio_feature_latency_includes_capture_to_render_and_output_time() {
        assert_eq!(
            audio_feature_to_led_latency_ms(
                1_050_000_000,
                1_000_000_000,
                Duration::from_millis(15),
            ),
            Some(65.0)
        );
        assert_eq!(
            audio_feature_to_led_latency_ms(
                1_000_000_000,
                1_000_000_001,
                Duration::from_millis(15),
            ),
            None
        );

        let mut health = RuntimeHealth::new();
        health.record_audio_feature_to_led_latency(Some(3.0));
        health.record_audio_feature_to_led_latency(Some(4.0));
        health.record_audio_feature_to_led_latency(Some(30.0));
        assert_eq!(health.audio_feature_to_led_latency_ms, Some(30.0));
        assert_eq!(health.audio_feature_to_led_percentile_ms(0.50), Some(4.0));
        assert_eq!(health.audio_feature_to_led_percentile_ms(0.95), Some(30.0));
        assert_eq!(health.audio_feature_to_led_percentile_ms(0.99), Some(30.0));
        health.record_audio_feature_to_led_latency(None);
        assert_eq!(health.audio_feature_to_led_percentile_ms(0.50), None);
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
