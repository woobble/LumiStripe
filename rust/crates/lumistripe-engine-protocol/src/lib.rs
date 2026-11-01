//! Versioned JSON-line messages shared by the Python adapter and Rust engine.

use lumistripe_engine_config::EngineConfig;
use serde::{Deserialize, Serialize};
use std::fmt;

pub const PROTOCOL_VERSION: u16 = 1;
pub const MAX_MESSAGE_BYTES: usize = 1_048_576;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(transparent)]
pub struct Rgb(pub [u8; 3]);

impl Rgb {
    pub const BLACK: Self = Self([0, 0, 0]);

    pub const fn new(red: u8, green: u8, blue: u8) -> Self {
        Self([red, green, blue])
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Request {
    pub protocol_version: u16,
    pub request_id: String,
    pub command: RequestCommand,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "type", content = "data", rename_all = "snake_case")]
pub enum RequestCommand {
    Health,
    Configure { config: EngineConfig },
    Render { pixels: Vec<Rgb> },
    Shutdown,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Response {
    pub protocol_version: u16,
    pub request_id: String,
    pub result: ResponseResult,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResponseResult {
    Health {
        status: EngineStatus,
        pixel_count: Option<usize>,
        frames_rendered: u64,
    },
    Configured {
        pixel_count: usize,
    },
    Rendered {
        frame_index: u64,
        pixel_count: usize,
    },
    Shutdown,
    Error {
        code: EngineErrorCode,
        message: String,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineStatus {
    Ready,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineErrorCode {
    UnsupportedVersion,
    InvalidConfig,
    InvalidFrame,
    NotConfigured,
}

impl Request {
    pub fn new(request_id: impl Into<String>, command: RequestCommand) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            request_id: request_id.into(),
            command,
        }
    }
}

impl Response {
    pub fn new(request_id: impl Into<String>, result: ResponseResult) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            request_id: request_id.into(),
            result,
        }
    }
}

#[derive(Debug)]
pub struct MessageTooLarge {
    pub actual_bytes: usize,
}

impl fmt::Display for MessageTooLarge {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "IPC message is {} bytes; maximum is {MAX_MESSAGE_BYTES}",
            self.actual_bytes
        )
    }
}

impl std::error::Error for MessageTooLarge {}

pub fn encode_request(request: &Request) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(request)
}

pub fn encode_response(response: &Response) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(response)
}

pub fn decode_request(bytes: &[u8]) -> Result<Request, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn decode_response(bytes: &[u8]) -> Result<Response, serde_json::Error> {
    serde_json::from_slice(bytes)
}

fn encode_line<T: Serialize>(value: &T) -> Result<Vec<u8>, serde_json::Error> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::{
        EngineErrorCode, EngineStatus, PROTOCOL_VERSION, Request, RequestCommand, Response,
        ResponseResult,
    };
    use lumistripe_engine_config::EngineConfig;
    use serde_json::Value;

    #[test]
    fn health_request_matches_shared_v1_fixture() {
        let request = Request::new("fixture-health-1", RequestCommand::Health);
        let actual: Value =
            serde_json::from_slice(&super::encode_request(&request).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../test-fixtures/protocol-v1/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
    }

    #[test]
    fn health_response_matches_shared_v1_fixture() {
        let response = Response::new(
            "fixture-health-1",
            ResponseResult::Health {
                status: EngineStatus::Ready,
                pixel_count: Some(3),
                frames_rendered: 7,
            },
        );
        let actual: Value =
            serde_json::from_slice(&super::encode_response(&response).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../test-fixtures/protocol-v1/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
    }

    #[test]
    fn config_and_frame_are_typed_wire_values() {
        let request = Request::new(
            "configure-1",
            RequestCommand::Configure {
                config: EngineConfig { pixel_count: 3 },
            },
        );
        let decoded = super::decode_request(&super::encode_request(&request).unwrap()).unwrap();
        assert_eq!(decoded, request);
        assert_eq!(request.protocol_version, PROTOCOL_VERSION);
        let _ = EngineErrorCode::InvalidFrame;
    }
}
