//! Single-process output ownership, bounded frame queue, IPC, and lifecycle.

use lumistripe_engine_animation_state::{
    AnimationInstance, RenderingInput, supported_animations, supports_animation,
};
use lumistripe_engine_compositing::{TransitionMode, apply_transition, screen_composite};
use lumistripe_engine_config::{
    EngineConfig, MAX_OUTPUT_COUNT, MAX_PIXEL_COUNT, OutputBackend, OutputConfig,
};
use lumistripe_engine_effects::EffectStack;
use lumistripe_engine_output::{OutputError, OutputSet};
use lumistripe_engine_protocol::{
    EngineErrorCode, EngineStatus, FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus,
    LEGACY_PROTOCOL_VERSION, LegacyErrorCode, LegacyRequest, LegacyRequestCommand, LegacyResponse,
    LegacyResponseResult, MAX_CONTROL_MESSAGE_BYTES, MAX_FRAME_BYTES, PROTOCOL_VERSION,
    RenderEffect, RenderFrameRequest, RenderOutput, RenderPowerConfig, RenderTransition, Request,
    RequestCommand, Response, ResponseResult, decode_frame_header, decode_legacy_request,
    decode_request, encode_frame_ack, encode_legacy_response, encode_response,
};
use lumistripe_engine_render_primitives::{AudioInput, FrameBuffer, Rgba, ZoneMapping, alpha_byte};
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
        RequestCommand::Render { frame } => {
            let sequence = frame.sequence;
            match server.output.submit_render(frame) {
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

fn render_frame_request(
    request: &RenderFrameRequest,
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
        timestamp_ns: request.timestamp_ns,
        delta_ns: request.delta_ns,
        brightness: request.brightness,
        audio: request.audio.map(|audio| AudioInput {
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
    pending_render: Option<PendingRender>,
    command_pending: bool,
    closed: bool,
}

struct PendingRender {
    request: RenderFrameRequest,
    queued_at: Instant,
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

    fn enqueue_latest_render(&mut self, request: RenderFrameRequest) -> Result<bool, ()> {
        if self.closed {
            return Err(());
        }
        if self
            .pending_render
            .as_ref()
            .is_some_and(|pending| pending.request.sequence >= request.sequence)
        {
            return Err(());
        }
        let replaced = self.pending_render.is_some();
        self.pending_render = Some(PendingRender {
            request,
            queued_at: Instant::now(),
        });
        Ok(replaced)
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
            if queue.pending_render.take().is_some() {
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

    fn submit_render(&self, request: RenderFrameRequest) -> Result<bool, FrameAckStatus> {
        let mut queue = self.shared.queue.lock().expect("output queue poisoned");
        if queue.closed {
            return Err(FrameAckStatus::ShuttingDown);
        }
        let mut health = self.shared.health.lock().expect("output health poisoned");
        if !health.configured {
            return Err(FrameAckStatus::NotConfigured);
        }
        let valid_audio = request.audio.is_none_or(|audio| {
            audio.rms.is_finite()
                && audio.beat_strength.is_finite()
                && audio.bands.iter().all(|band| band.is_finite())
        });
        if request.outputs.len() != health.pixel_counts.len()
            || !request.brightness.is_finite()
            || !(0.0..=1.0).contains(&request.brightness)
            || !valid_audio
            || health
                .last_sequence
                .is_some_and(|last_sequence| request.sequence <= last_sequence)
            || request
                .outputs
                .iter()
                .zip(&health.pixel_counts)
                .any(|(output, pixel_count)| output.zone_count > *pixel_count)
            || request.outputs.iter().any(|output| {
                !supports_animation(&output.animation)
                    || AnimationInstance::validate(&output.animation, &output.settings).is_err()
                    || !valid_render_output_parameters(output)
            })
        {
            return Err(FrameAckStatus::InvalidFrame);
        }
        let replaced = queue
            .enqueue_latest_render(request)
            .map_err(|()| FrameAckStatus::InvalidFrame)?;
        if replaced {
            health.dropped_frames = health.dropped_frames.saturating_add(1);
        }
        drop(health);
        drop(queue);
        self.shared.ready.notify_one();
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
            render_time_ms: health.render_time_ms,
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
                queue = shared.ready.wait(queue).expect("output queue poisoned");
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
                let render_started_at = Instant::now();
                let rendered = render_frame_request(&request.request, &mut frame, &mut renderers)
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

#[cfg(test)]
mod tests {
    use super::{
        AnimationInstance, EngineServer, FrameSet, MAX_RENDER_PREVIEW_PIXELS, RenderingInput,
        RuntimeHealth, TransitionMode, WorkQueue, apply_transition, finalize_render_frames,
        remove_stale_socket, render_frame_request, serve,
    };
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use lumistripe_engine_protocol::{
        FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus, FrameHeader, LegacyConfig,
        LegacyRequest, LegacyRequestCommand, LegacyResponseResult, PROTOCOL_VERSION,
        RenderAudioInput, RenderEffect, RenderFrameRequest, RenderOutput, RenderPowerConfig,
        RenderTransition, Request, RequestCommand, Response, ResponseResult, decode_frame_ack,
        decode_legacy_response, decode_response, encode_frame_header, encode_legacy_request,
        encode_request,
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
