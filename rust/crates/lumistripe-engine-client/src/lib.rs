//! Cancellation-safe asynchronous client for the versioned local engine IPC.

use lumistripe_engine_config::EngineConfig;
use lumistripe_engine_protocol::{
    EngineErrorCode, MAX_CONTROL_MESSAGE_BYTES, PROTOCOL_VERSION, RenderSceneUpdate, Request,
    RequestCommand, Response, ResponseResult,
};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::sync::Semaphore;
use tokio::time::timeout;

const MAX_IN_FLIGHT_REQUESTS: usize = 8;

#[derive(Clone)]
pub struct EngineClient {
    socket_path: Arc<PathBuf>,
    request_timeout: Duration,
    next_request_id: Arc<AtomicU64>,
    in_flight: Arc<Semaphore>,
}

impl EngineClient {
    pub fn new(socket_path: impl AsRef<Path>, request_timeout: Duration) -> Self {
        Self {
            socket_path: Arc::new(socket_path.as_ref().to_path_buf()),
            request_timeout,
            next_request_id: Arc::new(AtomicU64::new(1)),
            in_flight: Arc::new(Semaphore::new(MAX_IN_FLIGHT_REQUESTS)),
        }
    }

    pub fn socket_path(&self) -> &Path {
        self.socket_path.as_path()
    }

    /// Send one request over a fresh connection. The engine handles one command
    /// per connection; dropping this future closes only its own socket.
    pub async fn request(
        &self,
        command: RequestCommand,
    ) -> Result<ResponseResult, EngineClientError> {
        let request_id = format!(
            "web-{}",
            self.next_request_id.fetch_add(1, Ordering::Relaxed)
        );
        let request = Request::new(request_id.clone(), command);
        let encoded = serde_json::to_vec(&request)?;
        if encoded.len() > MAX_CONTROL_MESSAGE_BYTES {
            return Err(EngineClientError::RequestTooLarge);
        }

        let response_bytes = timeout(self.request_timeout, async {
            let _permit = self.in_flight.clone().acquire_owned().await?;
            let mut stream = UnixStream::connect(self.socket_path.as_path()).await?;
            stream.write_all(&encoded).await?;
            stream.write_all(b"\n").await?;
            stream.flush().await?;

            let mut reader = BufReader::new(stream);
            let mut line = Vec::with_capacity(1024);
            let read = (&mut reader)
                .take((MAX_CONTROL_MESSAGE_BYTES + 2) as u64)
                .read_until(b'\n', &mut line)
                .await?;
            if read == 0 {
                return Err(EngineClientError::ConnectionClosed);
            }
            if line.len() > MAX_CONTROL_MESSAGE_BYTES + 1 {
                return Err(EngineClientError::ResponseTooLarge);
            }
            if line.last() != Some(&b'\n') {
                return Err(EngineClientError::IncompleteResponse);
            }
            line.pop();
            Ok::<_, EngineClientError>(line)
        })
        .await
        .map_err(|_| EngineClientError::DeadlineExceeded)??;

        let response: Response = serde_json::from_slice(&response_bytes)?;
        if response.protocol_version != PROTOCOL_VERSION {
            return Err(EngineClientError::ProtocolVersion {
                expected: PROTOCOL_VERSION,
                received: response.protocol_version,
            });
        }
        if response.request_id != request_id {
            return Err(EngineClientError::RequestIdMismatch);
        }
        match response.result {
            ResponseResult::Error { code, message } => {
                Err(EngineClientError::Engine { code, message })
            }
            result => Ok(result),
        }
    }

    pub async fn health(&self) -> Result<ResponseResult, EngineClientError> {
        self.request(RequestCommand::Health).await
    }

    pub async fn configure(
        &self,
        config: EngineConfig,
    ) -> Result<ResponseResult, EngineClientError> {
        self.request(RequestCommand::Configure { config }).await
    }

    pub async fn render_capabilities(&self) -> Result<ResponseResult, EngineClientError> {
        self.request(RequestCommand::RenderCapabilities).await
    }

    pub async fn update_scene(
        &self,
        update: RenderSceneUpdate,
    ) -> Result<ResponseResult, EngineClientError> {
        self.request(RequestCommand::SceneUpdate { update }).await
    }

    pub async fn audio_status(&self) -> Result<ResponseResult, EngineClientError> {
        self.request(RequestCommand::AudioStatus).await
    }

    pub async fn shutdown(&self) -> Result<ResponseResult, EngineClientError> {
        self.request(RequestCommand::Shutdown).await
    }
}

#[derive(Debug, Error)]
pub enum EngineClientError {
    #[error("engine IPC request deadline expired")]
    DeadlineExceeded,
    #[error("engine IPC connection closed before a response")]
    ConnectionClosed,
    #[error("engine IPC response ended before its newline")]
    IncompleteResponse,
    #[error("engine IPC response exceeded the {MAX_CONTROL_MESSAGE_BYTES}-byte limit")]
    ResponseTooLarge,
    #[error("engine IPC request exceeded the {MAX_CONTROL_MESSAGE_BYTES}-byte limit")]
    RequestTooLarge,
    #[error("engine IPC response used protocol {received}; expected {expected}")]
    ProtocolVersion { expected: u16, received: u16 },
    #[error("engine IPC response request ID did not match the request")]
    RequestIdMismatch,
    #[error("engine rejected the request ({code:?}): {message}")]
    Engine {
        code: EngineErrorCode,
        message: String,
    },
    #[error("engine IPC request failed: {0}")]
    Io(#[from] io::Error),
    #[error("engine IPC JSON encoding failed: {0}")]
    Json(#[from] serde_json::Error),
    #[error("engine IPC request semaphore was closed")]
    SemaphoreClosed(#[from] tokio::sync::AcquireError),
}

#[cfg(test)]
mod tests {
    use super::{EngineClient, EngineClientError};
    use lumistripe_engine_protocol::{
        PROTOCOL_VERSION, RenderSceneUpdate, Request, Response, ResponseResult,
    };
    use std::time::Duration;
    use tempfile::tempdir;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixListener;

    #[tokio::test]
    async fn round_trips_typed_engine_response_and_correlates_request() {
        let directory = tempdir().unwrap();
        let socket_path = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket_path).unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let (read, mut write) = stream.into_split();
            let mut line = String::new();
            BufReader::new(read).read_line(&mut line).await.unwrap();
            let request: Request = serde_json::from_str(line.trim()).unwrap();
            let response = Response {
                protocol_version: PROTOCOL_VERSION,
                request_id: request.request_id,
                result: ResponseResult::SceneUpdated {
                    revision: 9,
                    replaced: false,
                },
            };
            write
                .write_all(serde_json::to_string(&response).unwrap().as_bytes())
                .await
                .unwrap();
            write.write_all(b"\n").await.unwrap();
        });
        let client = EngineClient::new(&socket_path, Duration::from_secs(1));
        let result = client
            .update_scene(RenderSceneUpdate {
                revision: 9,
                timestamp_ns: 10,
                brightness: 1.0,
                power: Default::default(),
                outputs: Vec::new(),
                scheduler: lumistripe_engine_protocol::RenderSchedulerConfig { max_fps: 60 },
            })
            .await
            .unwrap();
        assert!(matches!(
            result,
            ResponseResult::SceneUpdated { revision: 9, .. }
        ));
        server.await.unwrap();
    }

    #[tokio::test]
    async fn reports_a_deadline_without_reusing_the_connection() {
        let directory = tempdir().unwrap();
        let socket_path = directory.path().join("engine.sock");
        let listener = UnixListener::bind(&socket_path).unwrap();
        let server = tokio::spawn(async move {
            let (_stream, _) = listener.accept().await.unwrap();
            tokio::time::sleep(Duration::from_millis(100)).await;
        });
        let client = EngineClient::new(&socket_path, Duration::from_millis(10));
        assert!(matches!(
            client.health().await,
            Err(EngineClientError::DeadlineExceeded)
        ));
        server.await.unwrap();
    }
}
