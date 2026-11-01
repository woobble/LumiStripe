//! Unix-domain-socket server and process lifecycle for `lumistripe-engine`.

use lumistripe_engine_output::{LedOutput, OutputError, SimulatedLedOutput};
use lumistripe_engine_protocol::{
    EngineErrorCode, EngineStatus, MAX_MESSAGE_BYTES, PROTOCOL_VERSION, Request, RequestCommand,
    Response, ResponseResult, decode_request, encode_response,
};
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::fs::{FileTypeExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};

pub fn serve(socket_path: impl AsRef<Path>) -> io::Result<()> {
    let socket_path = socket_path.as_ref();
    remove_stale_socket(socket_path)?;
    let listener = UnixListener::bind(socket_path)?;
    let _socket_guard = SocketPathGuard(socket_path.to_path_buf());
    fs::set_permissions(socket_path, fs::Permissions::from_mode(0o600))?;
    let mut output = SimulatedLedOutput::default();

    for incoming in listener.incoming() {
        match incoming {
            Ok(stream) => match handle_connection(stream, &mut output) {
                Ok(true) => break,
                Ok(false) => {}
                Err(error) => eprintln!("engine IPC request failed: {error}"),
            },
            Err(error) => return Err(error),
        }
    }
    drop(listener);
    Ok(())
}

fn handle_connection(mut stream: UnixStream, output: &mut SimulatedLedOutput) -> io::Result<bool> {
    let request = match read_request(&mut stream) {
        Ok(request) => request,
        Err(error) => return Err(error),
    };
    let (result, should_stop) = dispatch(request, output);
    let response = encode_response(&result)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    stream.write_all(&response)?;
    stream.flush()?;
    Ok(should_stop)
}

fn read_request(stream: &mut UnixStream) -> io::Result<Request> {
    let mut bytes = Vec::with_capacity(1024);
    let mut bounded = BufReader::new(&mut *stream).take((MAX_MESSAGE_BYTES + 1) as u64);
    let read = bounded.read_until(b'\n', &mut bytes)?;
    if read == 0 || !bytes.ends_with(b"\n") {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "IPC request must end with a newline",
        ));
    }
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("IPC request exceeds {MAX_MESSAGE_BYTES} bytes"),
        ));
    }
    bytes.pop();
    decode_request(&bytes).map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn dispatch(request: Request, output: &mut SimulatedLedOutput) -> (Response, bool) {
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

    let (result, should_stop) = match request.command {
        RequestCommand::Health => (
            ResponseResult::Health {
                status: EngineStatus::Ready,
                pixel_count: output.pixel_count(),
                frames_rendered: output.frames_rendered(),
            },
            false,
        ),
        RequestCommand::Configure { config } => match output.configure(config) {
            Ok(()) => (
                ResponseResult::Configured {
                    pixel_count: config.pixel_count,
                },
                false,
            ),
            Err(error) => (output_error(error), false),
        },
        RequestCommand::Render { pixels } => match output.render(pixels) {
            Ok(frame_index) => (
                ResponseResult::Rendered {
                    frame_index,
                    pixel_count: output.pixel_count().unwrap_or_default(),
                },
                false,
            ),
            Err(error) => (output_error(error), false),
        },
        RequestCommand::Shutdown => (ResponseResult::Shutdown, true),
    };
    (Response::new(request_id, result), should_stop)
}

fn output_error(error: OutputError) -> ResponseResult {
    let code = match &error {
        OutputError::InvalidConfig(_) => EngineErrorCode::InvalidConfig,
        OutputError::InvalidFrame { .. } => EngineErrorCode::InvalidFrame,
        OutputError::NotConfigured => EngineErrorCode::NotConfigured,
    };
    ResponseResult::Error {
        code,
        message: error.to_string(),
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
    use super::serve;
    use lumistripe_engine_protocol::{MAX_MESSAGE_BYTES, PROTOCOL_VERSION};
    use serde_json::{Value, json};
    use std::fs;
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::net::UnixListener;
    use std::os::unix::net::UnixStream;
    use std::path::PathBuf;
    use std::thread;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

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

        fn request(&self, value: Value) -> Value {
            let mut stream = UnixStream::connect(&self.socket).unwrap();
            serde_json::to_writer(&mut stream, &value).unwrap();
            stream.write_all(b"\n").unwrap();
            let mut response = String::new();
            BufReader::new(stream).read_line(&mut response).unwrap();
            serde_json::from_str(&response).unwrap()
        }

        fn shutdown(mut self) {
            let response = self.request(json!({
                "protocol_version": PROTOCOL_VERSION,
                "request_id": "shutdown-test",
                "command": { "type": "shutdown" }
            }));
            assert_eq!(response["result"]["type"], "shutdown");
            self.thread.take().unwrap().join().unwrap();
            assert!(!self.socket.exists());
        }
    }

    #[test]
    fn unix_socket_health_configure_render_and_shutdown_round_trip() {
        let server = ServerThread::start();
        let health = server.request(json!({
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "health-1",
            "command": { "type": "health" }
        }));
        assert_eq!(health["result"]["type"], "health");
        assert_eq!(health["result"]["pixel_count"], Value::Null);

        let configured = server.request(json!({
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "configure-1",
            "command": { "type": "configure", "data": { "config": { "pixel_count": 2 } } }
        }));
        assert_eq!(configured["result"]["type"], "configured");

        let rendered = server.request(json!({
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "render-1",
            "command": { "type": "render", "data": { "pixels": [[1, 2, 3], [4, 5, 6]] } }
        }));
        assert_eq!(rendered["result"]["frame_index"], 1);
        server.shutdown();
    }

    #[test]
    fn rejects_unsupported_versions_and_invalid_configuration() {
        let server = ServerThread::start();
        let unsupported = server.request(json!({
            "protocol_version": 99,
            "request_id": "old-client",
            "command": { "type": "health" }
        }));
        assert_eq!(unsupported["result"]["code"], "unsupported_version");
        let invalid = server.request(json!({
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "bad-config",
            "command": { "type": "configure", "data": { "config": { "pixel_count": 0 } } }
        }));
        assert_eq!(invalid["result"]["code"], "invalid_config");
        assert!(MAX_MESSAGE_BYTES > 0);
        server.shutdown();
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
        let error = super::remove_stale_socket(&path).unwrap_err();
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
        let error = super::remove_stale_socket(&path).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::AddrInUse);
        assert!(path.exists());
    }
}
