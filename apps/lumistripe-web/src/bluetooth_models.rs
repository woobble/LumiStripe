//! Typed BlueZ, PipeWire, and HTTP contract models.

use crate::request_validation::deserialize_pydantic_bool;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum BluetoothOperationState {
    #[default]
    Idle,
    Running,
    Complete,
    Failed,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum BluetoothOperation {
    Power,
    Rename,
    Scan,
    Pair,
    Connect,
    Disconnect,
    Forget,
    OutputSelect,
    OutputVolume,
    OutputMute,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum BluetoothDeviceRole {
    Input,
    Output,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(default)]
pub(crate) struct BluetoothCapabilities {
    #[schema(value_type = Vec<BluetoothOperation>, default = json!([]))]
    pub operations: Vec<String>,
    #[schema(minimum = 1, default = 1)]
    pub max_inputs: usize,
    #[schema(minimum = 1, default = 1)]
    pub max_outputs: usize,
}

impl Default for BluetoothCapabilities {
    fn default() -> Self {
        Self {
            operations: Vec::new(),
            max_inputs: 1,
            max_outputs: 1,
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[schema(as = BluetoothDeviceInfo)]
#[serde(default)]
pub(crate) struct BluetoothDevice {
    #[schema(required = true)]
    pub address: String,
    #[schema(required = true)]
    pub name: String,
    #[schema(default = false)]
    pub paired: bool,
    #[schema(default = false)]
    pub connected: bool,
    #[schema(value_type = Vec<BluetoothDeviceRole>, default = json!([]))]
    pub roles: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[schema(as = AudioOutputDeviceInfo)]
#[serde(default)]
pub(crate) struct AudioOutputDevice {
    #[schema(required = true)]
    pub selector: String,
    #[schema(required = true)]
    pub name: String,
    #[schema(minimum = 0.0, maximum = 1.0)]
    pub volume: Option<f64>,
    #[schema(default = false)]
    pub muted: bool,
    #[schema(default = false)]
    pub bluetooth: bool,
    #[schema(default = true)]
    pub connected: bool,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[schema(as = BluetoothStatusResponse)]
#[serde(default)]
pub(crate) struct BluetoothStatus {
    pub available: bool,
    pub powered: bool,
    pub adapter_alias: Option<String>,
    pub scanning: bool,
    pub streaming: bool,
    pub devices: Vec<BluetoothDevice>,
    pub connected_inputs: Vec<BluetoothDevice>,
    pub connected_outputs: Vec<BluetoothDevice>,
    pub connected_device: Option<BluetoothDevice>,
    pub input_source: Option<String>,
    pub output_devices: Vec<AudioOutputDevice>,
    pub default_sink: Option<String>,
    pub output_volume: Option<f64>,
    pub output_muted: bool,
    pub output_ready: bool,
    pub capabilities: BluetoothCapabilities,
    pub operation: Option<String>,
    pub operation_id: Option<String>,
    pub operation_state: BluetoothOperationState,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
pub(crate) struct BluetoothPowerRequest {
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    pub powered: bool,
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
pub(crate) struct BluetoothAliasRequest {
    #[schema(min_length = 1, max_length = 64)]
    pub alias: String,
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
#[schema(as = BluetoothDeviceRequest)]
pub(crate) struct BluetoothDeviceRequest {
    #[schema(min_length = 17, max_length = 17)]
    pub address: String,
    #[serde(default)]
    pub role: Option<BluetoothRole>,
}

#[derive(Clone, Copy, Debug, Deserialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub(crate) enum BluetoothRole {
    Input,
    Output,
}

impl BluetoothRole {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Input => "input",
            Self::Output => "output",
        }
    }
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
pub(crate) struct AudioOutputSelectionRequest {
    #[schema(min_length = 1, max_length = 256)]
    pub selector: String,
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
pub(crate) struct AudioOutputVolumeRequest {
    #[schema(min_length = 1, max_length = 256)]
    pub selector: String,
    #[schema(minimum = 0.0, maximum = 1.0)]
    pub volume: f64,
}

#[derive(Clone, Debug, Deserialize, utoipa::ToSchema)]
pub(crate) struct AudioOutputMuteRequest {
    #[schema(min_length = 1, max_length = 256)]
    pub selector: String,
    #[serde(deserialize_with = "deserialize_pydantic_bool")]
    pub muted: bool,
}

impl BluetoothStatus {
    pub fn streaming(&self) -> bool {
        self.input_source.is_some()
            && (!self.connected_inputs.is_empty() || self.connected_device.is_some())
    }
}
