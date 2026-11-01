//! Versioned control messages and compact binary frame-set packets.

use lumistripe_engine_config::EngineConfig;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::fmt;

pub const PROTOCOL_VERSION: u16 = 2;
pub const LEGACY_PROTOCOL_VERSION: u16 = 1;
pub const MAX_CONTROL_MESSAGE_BYTES: usize = 1_048_576;
pub const FRAME_MAGIC: [u8; 4] = *b"LSF2";
pub const FRAME_ACK_MAGIC: [u8; 4] = *b"LSA2";
pub const FRAME_HEADER_BYTES: usize = 20;
pub const FRAME_ACK_BYTES: usize = 24;
pub const FRAME_VERSION: u16 = 2;
pub const MAX_FRAME_BYTES: usize = 2 * 16_384 * 4;

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
    RenderCapabilities,
    Configure { config: EngineConfig },
    Render { frame: RenderFrameRequest },
    Shutdown,
}

/// Pixel-free scene input for one direct Rust render/output iteration.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderFrameRequest {
    pub sequence: u64,
    pub timestamp_ns: u64,
    pub delta_ns: u64,
    pub brightness: f64,
    pub audio: Option<RenderAudioInput>,
    #[serde(default, skip_serializing_if = "RenderPowerConfig::is_default")]
    pub power: RenderPowerConfig,
    pub outputs: Vec<RenderOutput>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct RenderPowerConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub budget_watts: Option<f64>,
}

impl RenderPowerConfig {
    fn is_default(&self) -> bool {
        self == &Self::default()
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderAudioInput {
    pub rms: f64,
    pub bands: [f64; 8],
    pub beat: bool,
    pub beat_strength: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderOutput {
    pub animation: String,
    #[serde(default)]
    pub settings: Map<String, Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub effects: Vec<RenderEffect>,
    #[serde(default = "default_render_intensity", skip_serializing_if = "is_one")]
    pub base_intensity: f64,
    #[serde(
        default = "default_overlay_strength",
        skip_serializing_if = "is_default_overlay_strength"
    )]
    pub max_overlay_strength: f64,
    #[serde(default, skip_serializing_if = "is_zero_u64")]
    pub scene_revision: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scene_pixel_count: Option<usize>,
    #[serde(default, skip_serializing_if = "is_zero_usize")]
    pub scene_offset: usize,
    #[serde(default, skip_serializing_if = "is_false")]
    pub scene_resample: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub brightness: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub solid_color: Option<[u8; 3]>,
    #[serde(default = "default_zone_count", skip_serializing_if = "is_one_zone")]
    pub zone_count: usize,
    #[serde(
        default = "default_zone_pattern",
        skip_serializing_if = "is_repeat_zone_pattern"
    )]
    pub zone_pattern: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transition: Option<RenderTransition>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub blackout: bool,
    #[serde(default, skip_serializing_if = "is_false")]
    pub reversed: bool,
    #[serde(
        default = "default_color_correction",
        skip_serializing_if = "is_identity_correction"
    )]
    pub color_correction: [u8; 3],
    #[serde(
        default = "default_voltage_v",
        skip_serializing_if = "is_default_voltage_v"
    )]
    pub voltage_v: f64,
    #[serde(
        default = "default_full_white_current_a",
        skip_serializing_if = "is_default_full_white_current_a"
    )]
    pub full_white_current_a: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub power_limit_watts: Option<f64>,
}

impl Default for RenderOutput {
    fn default() -> Self {
        Self {
            animation: String::new(),
            settings: Map::new(),
            effects: Vec::new(),
            base_intensity: default_render_intensity(),
            max_overlay_strength: default_overlay_strength(),
            scene_revision: 0,
            scene_pixel_count: None,
            scene_offset: 0,
            scene_resample: false,
            brightness: None,
            solid_color: None,
            zone_count: default_zone_count(),
            zone_pattern: default_zone_pattern(),
            transition: None,
            blackout: false,
            reversed: false,
            color_correction: default_color_correction(),
            voltage_v: default_voltage_v(),
            full_white_current_a: default_full_white_current_a(),
            power_limit_watts: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderTransition {
    pub started_at_ns: u64,
    pub duration_ns: u64,
    pub mode: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RenderEffect {
    pub name: String,
    #[serde(default)]
    pub settings: Map<String, Value>,
    pub intensity: f64,
    #[serde(default, skip_serializing_if = "is_false")]
    pub trigger_beat: bool,
    #[serde(default, skip_serializing_if = "is_zero_f64")]
    pub trigger_beat_strength: f64,
}

fn default_render_intensity() -> f64 {
    1.0
}

fn default_overlay_strength() -> f64 {
    0.65
}

fn default_zone_count() -> usize {
    1
}

fn default_zone_pattern() -> String {
    "repeat".to_owned()
}

fn is_one(value: &f64) -> bool {
    *value == 1.0
}

fn is_zero_f64(value: &f64) -> bool {
    *value == 0.0
}

fn is_zero_u64(value: &u64) -> bool {
    *value == 0
}

fn is_zero_usize(value: &usize) -> bool {
    *value == 0
}

fn is_default_overlay_strength(value: &f64) -> bool {
    *value == 0.65
}

fn is_one_zone(value: &usize) -> bool {
    *value == 1
}

fn is_repeat_zone_pattern(value: &str) -> bool {
    value == "repeat"
}

fn is_false(value: &bool) -> bool {
    !*value
}

fn default_color_correction() -> [u8; 3] {
    [255; 3]
}

fn is_identity_correction(value: &[u8; 3]) -> bool {
    value == &[255; 3]
}

fn default_voltage_v() -> f64 {
    5.0
}

fn is_default_voltage_v(value: &f64) -> bool {
    *value == 5.0
}

fn default_full_white_current_a() -> f64 {
    0.06
}

fn is_default_full_white_current_a(value: &f64) -> bool {
    *value == 0.06
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct Response {
    pub protocol_version: u16,
    pub request_id: String,
    pub result: ResponseResult,
}

/// Version 1 remains available for existing single-output simulated clients.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LegacyRequest {
    pub protocol_version: u16,
    pub request_id: String,
    pub command: LegacyRequestCommand,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "data", rename_all = "snake_case")]
pub enum LegacyRequestCommand {
    Health,
    Configure { config: LegacyConfig },
    Render { pixels: Vec<[u8; 3]> },
    Shutdown,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LegacyConfig {
    pub pixel_count: usize,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LegacyResponse {
    pub protocol_version: u16,
    pub request_id: String,
    pub result: LegacyResponseResult,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum LegacyResponseResult {
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
        code: LegacyErrorCode,
        message: String,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LegacyErrorCode {
    UnsupportedVersion,
    InvalidConfig,
    InvalidFrame,
    NotConfigured,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResponseResult {
    Health {
        status: EngineStatus,
        configured: bool,
        output_count: usize,
        pixel_counts: Vec<usize>,
        frames_rendered: u64,
        last_sequence: Option<u64>,
        dropped_frames: u64,
        failed_frames: u64,
        last_error: Option<String>,
        output_rate: f64,
        frame_latency_ms: Option<f64>,
        queue_latency_ms: Option<f64>,
        transmission_ms: Option<f64>,
        frame_latency_p50_ms: Option<f64>,
        frame_latency_p95_ms: Option<f64>,
        frame_latency_p99_ms: Option<f64>,
        sequential_skew_ms: Option<f64>,
        last_successful_update_epoch_ms: Option<u64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        render_time_ms: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        estimated_power_watts: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        applied_power_scale: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        output_power_watts: Option<Vec<f64>>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        limiting_output_index: Option<usize>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        preview_outputs: Option<Vec<Vec<u8>>>,
    },
    Configured {
        output_count: usize,
        total_pixels: usize,
    },
    RenderCapabilities {
        animations: Vec<String>,
    },
    RenderQueued {
        sequence: u64,
        replaced: bool,
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
    InvalidRender,
    NotConfigured,
    OutputFailure,
    ShuttingDown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameHeader {
    pub output_count: u16,
    pub sequence: u64,
    pub payload_bytes: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameAck {
    pub status: FrameAckStatus,
    pub sequence: u64,
    pub dropped_frames: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u16)]
pub enum FrameAckStatus {
    Accepted = 0,
    NotConfigured = 1,
    ShuttingDown = 2,
    InvalidFrame = 3,
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

impl LegacyRequest {
    pub fn new(request_id: impl Into<String>, command: LegacyRequestCommand) -> Self {
        Self {
            protocol_version: LEGACY_PROTOCOL_VERSION,
            request_id: request_id.into(),
            command,
        }
    }
}

impl LegacyResponse {
    pub fn new(request_id: impl Into<String>, result: LegacyResponseResult) -> Self {
        Self {
            protocol_version: LEGACY_PROTOCOL_VERSION,
            request_id: request_id.into(),
            result,
        }
    }
}

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

pub fn encode_legacy_request(request: &LegacyRequest) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(request)
}

pub fn decode_legacy_response(bytes: &[u8]) -> Result<LegacyResponse, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn encode_legacy_response(response: &LegacyResponse) -> Result<Vec<u8>, serde_json::Error> {
    encode_line(response)
}

pub fn decode_legacy_request(bytes: &[u8]) -> Result<LegacyRequest, serde_json::Error> {
    serde_json::from_slice(bytes)
}

pub fn encode_frame_header(header: FrameHeader) -> Result<[u8; FRAME_HEADER_BYTES], ProtocolError> {
    if header.output_count == 0 || header.payload_bytes as usize > MAX_FRAME_BYTES {
        return Err(ProtocolError::InvalidFrameHeader);
    }
    let mut bytes = [0u8; FRAME_HEADER_BYTES];
    bytes[..4].copy_from_slice(&FRAME_MAGIC);
    bytes[4..6].copy_from_slice(&FRAME_VERSION.to_le_bytes());
    bytes[6..8].copy_from_slice(&header.output_count.to_le_bytes());
    bytes[8..16].copy_from_slice(&header.sequence.to_le_bytes());
    bytes[16..20].copy_from_slice(&header.payload_bytes.to_le_bytes());
    Ok(bytes)
}

pub fn decode_frame_header(bytes: &[u8; FRAME_HEADER_BYTES]) -> Result<FrameHeader, ProtocolError> {
    if bytes[..4] != FRAME_MAGIC {
        return Err(ProtocolError::InvalidFrameHeader);
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    let output_count = u16::from_le_bytes([bytes[6], bytes[7]]);
    let sequence = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
    let payload_bytes = u32::from_le_bytes(bytes[16..20].try_into().unwrap());
    if version != FRAME_VERSION || output_count == 0 || payload_bytes as usize > MAX_FRAME_BYTES {
        return Err(ProtocolError::InvalidFrameHeader);
    }
    Ok(FrameHeader {
        output_count,
        sequence,
        payload_bytes,
    })
}

pub fn encode_frame_ack(ack: FrameAck) -> [u8; FRAME_ACK_BYTES] {
    let mut bytes = [0u8; FRAME_ACK_BYTES];
    bytes[..4].copy_from_slice(&FRAME_ACK_MAGIC);
    bytes[4..6].copy_from_slice(&FRAME_VERSION.to_le_bytes());
    bytes[6..8].copy_from_slice(&(ack.status as u16).to_le_bytes());
    bytes[8..16].copy_from_slice(&ack.sequence.to_le_bytes());
    bytes[16..24].copy_from_slice(&ack.dropped_frames.to_le_bytes());
    bytes
}

pub fn decode_frame_ack(bytes: &[u8; FRAME_ACK_BYTES]) -> Result<FrameAck, ProtocolError> {
    if bytes[..4] != FRAME_ACK_MAGIC || u16::from_le_bytes([bytes[4], bytes[5]]) != FRAME_VERSION {
        return Err(ProtocolError::InvalidFrameAcknowledgement);
    }
    let status = match u16::from_le_bytes([bytes[6], bytes[7]]) {
        0 => FrameAckStatus::Accepted,
        1 => FrameAckStatus::NotConfigured,
        2 => FrameAckStatus::ShuttingDown,
        3 => FrameAckStatus::InvalidFrame,
        _ => return Err(ProtocolError::InvalidFrameAcknowledgement),
    };
    Ok(FrameAck {
        status,
        sequence: u64::from_le_bytes(bytes[8..16].try_into().unwrap()),
        dropped_frames: u64::from_le_bytes(bytes[16..24].try_into().unwrap()),
    })
}

fn encode_line<T: Serialize>(value: &T) -> Result<Vec<u8>, serde_json::Error> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProtocolError {
    InvalidFrameHeader,
    InvalidFrameAcknowledgement,
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidFrameHeader => f.write_str("invalid binary frame header"),
            Self::InvalidFrameAcknowledgement => {
                f.write_str("invalid binary frame acknowledgement")
            }
        }
    }
}

impl std::error::Error for ProtocolError {}

#[cfg(test)]
mod tests {
    use super::{
        FRAME_ACK_BYTES, FRAME_HEADER_BYTES, FrameAck, FrameAckStatus, FrameHeader,
        LEGACY_PROTOCOL_VERSION, LegacyConfig, LegacyRequest, LegacyRequestCommand, LegacyResponse,
        LegacyResponseResult, PROTOCOL_VERSION, RenderAudioInput, RenderEffect, RenderFrameRequest,
        RenderOutput, RenderPowerConfig, RenderTransition, Request, RequestCommand, Response,
        ResponseResult, decode_frame_ack, decode_frame_header, encode_frame_ack,
        encode_frame_header,
    };
    use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
    use serde_json::{Map, Value};

    #[test]
    fn health_request_matches_shared_v2_fixture() {
        let request = Request::new("fixture-health-2", RequestCommand::Health);
        let actual: Value =
            serde_json::from_slice(&super::encode_request(&request).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../test-fixtures/protocol-v2/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
        assert_eq!(request.protocol_version, PROTOCOL_VERSION);
    }

    #[test]
    fn health_response_matches_shared_v2_fixture() {
        let response = Response::new(
            "fixture-health-2",
            ResponseResult::Health {
                status: super::EngineStatus::Ready,
                configured: false,
                output_count: 0,
                pixel_counts: vec![],
                frames_rendered: 0,
                last_sequence: None,
                dropped_frames: 0,
                failed_frames: 0,
                last_error: None,
                output_rate: 0.0,
                frame_latency_ms: None,
                queue_latency_ms: None,
                transmission_ms: None,
                frame_latency_p50_ms: None,
                frame_latency_p95_ms: None,
                frame_latency_p99_ms: None,
                sequential_skew_ms: None,
                last_successful_update_epoch_ms: None,
                render_time_ms: None,
                estimated_power_watts: None,
                applied_power_scale: None,
                output_power_watts: None,
                limiting_output_index: None,
                preview_outputs: None,
            },
        );
        let actual: Value =
            serde_json::from_slice(&super::encode_response(&response).unwrap()).unwrap();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../test-fixtures/protocol-v2/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, fixture);
    }

    #[test]
    fn legacy_health_messages_match_the_shared_v1_fixtures() {
        let request = LegacyRequest::new("fixture-health-1", LegacyRequestCommand::Health);
        let actual: Value = serde_json::from_slice(&serde_json::to_vec(&request).unwrap()).unwrap();
        let request_fixture: Value = serde_json::from_str(include_str!(
            "../../../test-fixtures/protocol-v1/health-request.json"
        ))
        .unwrap();
        assert_eq!(actual, request_fixture);
        assert_eq!(request.protocol_version, LEGACY_PROTOCOL_VERSION);

        let response = LegacyResponse::new(
            "fixture-health-1",
            LegacyResponseResult::Health {
                status: super::EngineStatus::Ready,
                pixel_count: Some(3),
                frames_rendered: 7,
            },
        );
        let actual: Value =
            serde_json::from_slice(&super::encode_legacy_response(&response).unwrap()).unwrap();
        let response_fixture: Value = serde_json::from_str(include_str!(
            "../../../test-fixtures/protocol-v1/health-response.json"
        ))
        .unwrap();
        assert_eq!(actual, response_fixture);
    }

    #[test]
    fn legacy_configuration_and_rgb_frames_remain_typed() {
        let request = LegacyRequest::new(
            "configure-1",
            LegacyRequestCommand::Configure {
                config: LegacyConfig { pixel_count: 3 },
            },
        );
        assert_eq!(request.protocol_version, LEGACY_PROTOCOL_VERSION);
        let encoded = serde_json::to_vec(&request).unwrap();
        assert_eq!(super::decode_legacy_request(&encoded).unwrap(), request);

        let render = LegacyRequest::new(
            "render-1",
            LegacyRequestCommand::Render {
                pixels: vec![[1, 2, 3], [4, 5, 6]],
            },
        );
        let encoded = serde_json::to_vec(&render).unwrap();
        assert_eq!(super::decode_legacy_request(&encoded).unwrap(), render);
    }

    #[test]
    fn configure_control_message_is_typed() {
        let request = Request::new(
            "configure-1",
            RequestCommand::Configure {
                config: EngineConfig {
                    outputs: vec![OutputConfig {
                        id: "main".into(),
                        pixel_count: 2,
                        reversed: true,
                        backend: OutputBackend::Mock,
                    }],
                },
            },
        );
        let decoded = super::decode_request(&super::encode_request(&request).unwrap()).unwrap();
        assert_eq!(decoded, request);
    }

    #[test]
    fn render_control_message_carries_audio_and_settings_without_pixels() {
        let request = Request::new(
            "render-1",
            RequestCommand::Render {
                frame: RenderFrameRequest {
                    sequence: 8,
                    timestamp_ns: 1_000_000_000,
                    delta_ns: 16_000_000,
                    brightness: 0.8,
                    audio: Some(RenderAudioInput {
                        rms: 0.4,
                        bands: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
                        beat: true,
                        beat_strength: 0.9,
                    }),
                    power: RenderPowerConfig {
                        enabled: true,
                        budget_watts: Some(17.5),
                    },
                    outputs: vec![
                        RenderOutput {
                            animation: "rainbow".into(),
                            settings: Map::new(),
                            effects: vec![RenderEffect {
                                name: "hard_beat".into(),
                                settings: Map::new(),
                                intensity: 0.75,
                                trigger_beat: true,
                                trigger_beat_strength: 0.9,
                            }],
                            base_intensity: 0.8,
                            max_overlay_strength: 0.5,
                            scene_revision: 9,
                            brightness: Some(0.4),
                            zone_count: 3,
                            zone_pattern: "alternate".into(),
                            scene_pixel_count: Some(8),
                            scene_offset: 3,
                            transition: Some(RenderTransition {
                                started_at_ns: 900_000_000,
                                duration_ns: 200_000_000,
                                mode: "wipe".into(),
                            }),
                            blackout: false,
                            color_correction: [128, 240, 255],
                            voltage_v: 12.0,
                            full_white_current_a: 0.08,
                            power_limit_watts: Some(6.0),
                            ..RenderOutput::default()
                        },
                        RenderOutput {
                            animation: "rainbow".into(),
                            scene_pixel_count: Some(8),
                            scene_resample: true,
                            ..RenderOutput::default()
                        },
                    ],
                },
            },
        );
        let encoded = super::encode_request(&request).unwrap();
        assert!(
            encoded.len() < 1024,
            "render request should contain no pixel frame"
        );
        let decoded = super::decode_request(&encoded).unwrap();
        assert_eq!(decoded, request);
        let encoded_text = std::str::from_utf8(&encoded).unwrap();
        assert!(encoded_text.contains("\"zone_count\":3"));
        assert!(encoded_text.contains("\"zone_pattern\":\"alternate\""));
        assert!(encoded_text.contains("\"scene_pixel_count\":8"));
        assert!(encoded_text.contains("\"scene_offset\":3"));
        assert!(encoded_text.contains("\"scene_resample\":true"));
        assert!(encoded_text.contains("\"budget_watts\":17.5"));
        assert!(encoded_text.contains("\"color_correction\":[128,240,255]"));
    }

    #[test]
    fn render_control_message_defaults_zone_mapping_for_older_clients() {
        let request = br#"{"protocol_version":2,"request_id":"render-old","command":{"type":"render","data":{"frame":{"sequence":1,"timestamp_ns":0,"delta_ns":16000000,"brightness":1.0,"audio":null,"outputs":[{"animation":"rainbow"}]}}}}"#;
        let decoded = super::decode_request(request).unwrap();
        let super::RequestCommand::Render { frame } = decoded.command else {
            panic!("expected render request");
        };
        assert_eq!(frame.outputs[0].zone_count, 1);
        assert_eq!(frame.outputs[0].zone_pattern, "repeat");
        assert!(frame.outputs[0].transition.is_none());
        assert_eq!(frame.outputs[0].color_correction, [255; 3]);
        assert_eq!(frame.outputs[0].voltage_v, 5.0);
        assert_eq!(frame.outputs[0].full_white_current_a, 0.06);
        assert!(frame.outputs[0].power_limit_watts.is_none());
        assert!(!frame.power.enabled);
    }

    #[test]
    fn render_capability_query_is_a_typed_control_command() {
        let request = Request::new("render-capabilities", RequestCommand::RenderCapabilities);
        let encoded = super::encode_request(&request).unwrap();
        assert_eq!(super::decode_request(&encoded).unwrap(), request);
    }

    #[test]
    fn binary_frame_header_and_ack_are_fixed_size_and_little_endian() {
        let header = FrameHeader {
            output_count: 2,
            sequence: 0x0102_0304_0506_0708,
            payload_bytes: 24,
        };
        let encoded = encode_frame_header(header).unwrap();
        assert_eq!(encoded.len(), FRAME_HEADER_BYTES);
        assert_eq!(&encoded[8..16], &[8, 7, 6, 5, 4, 3, 2, 1]);
        assert_eq!(decode_frame_header(&encoded), Ok(header));

        let ack = FrameAck {
            status: FrameAckStatus::Accepted,
            sequence: header.sequence,
            dropped_frames: 9,
        };
        let encoded_ack = encode_frame_ack(ack);
        assert_eq!(encoded_ack.len(), FRAME_ACK_BYTES);
        assert_eq!(decode_frame_ack(&encoded_ack), Ok(ack));
    }

    #[test]
    fn rejects_bad_frame_headers_and_acknowledgements() {
        let mut header = [0u8; FRAME_HEADER_BYTES];
        assert!(decode_frame_header(&header).is_err());
        header[..4].copy_from_slice(b"LSF2");
        header[4..6].copy_from_slice(&99u16.to_le_bytes());
        assert!(decode_frame_header(&header).is_err());
        assert!(decode_frame_ack(&[0u8; FRAME_ACK_BYTES]).is_err());
    }
}
