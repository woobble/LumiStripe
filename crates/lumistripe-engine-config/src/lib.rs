//! Validated output configuration shared by the engine and IPC protocol.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fmt;

pub const MIN_PIXEL_COUNT: usize = 1;
pub const MAX_PIXEL_COUNT: usize = 16_384;
pub const MAX_OUTPUT_COUNT: usize = 2;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EngineConfig {
    pub outputs: Vec<OutputConfig>,
}

impl EngineConfig {
    pub fn new(outputs: Vec<OutputConfig>) -> Result<Self, ConfigError> {
        let config = Self { outputs };
        config.validate()?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.outputs.len() > MAX_OUTPUT_COUNT {
            return Err(ConfigError::new(format!(
                "at most {MAX_OUTPUT_COUNT} outputs are supported"
            )));
        }
        let mut ids = HashSet::new();
        let mut spi_devices = HashSet::new();
        let mut gpio_pins = HashSet::new();
        for output in &self.outputs {
            output.validate()?;
            if !ids.insert(&output.id) {
                return Err(ConfigError::new("output ids must be unique"));
            }
            match &output.backend {
                OutputBackend::Spi { device, .. } if !spi_devices.insert(device) => {
                    return Err(ConfigError::new("SPI device paths must be unique"));
                }
                OutputBackend::Gpio {
                    chip,
                    data_pin,
                    clock_pin,
                } => {
                    if !gpio_pins.insert((chip, data_pin)) || !gpio_pins.insert((chip, clock_pin)) {
                        return Err(ConfigError::new("GPIO lines must be unique"));
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    pub fn pixel_counts(&self) -> Vec<usize> {
        self.outputs
            .iter()
            .map(|output| output.pixel_count)
            .collect()
    }

    pub fn total_pixels(&self) -> usize {
        self.outputs.iter().map(|output| output.pixel_count).sum()
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OutputConfig {
    pub id: String,
    pub pixel_count: usize,
    #[serde(default)]
    pub reversed: bool,
    pub backend: OutputBackend,
}

impl OutputConfig {
    fn validate(&self) -> Result<(), ConfigError> {
        if self.id.trim().is_empty() || self.id.len() > 128 {
            return Err(ConfigError::new("output id must contain 1–128 bytes"));
        }
        if !(MIN_PIXEL_COUNT..=MAX_PIXEL_COUNT).contains(&self.pixel_count) {
            return Err(ConfigError::new(format!(
                "pixel_count must be between {MIN_PIXEL_COUNT} and {MAX_PIXEL_COUNT}"
            )));
        }
        match &self.backend {
            OutputBackend::Mock | OutputBackend::MockSpi => Ok(()),
            OutputBackend::Spi { device, speed_hz } => {
                if device.trim().is_empty() || *speed_hz == 0 {
                    return Err(ConfigError::new("SPI device path and speed must be valid"));
                }
                Ok(())
            }
            OutputBackend::Gpio {
                chip,
                data_pin,
                clock_pin,
            } => {
                if chip.trim().is_empty() || data_pin == clock_pin {
                    return Err(ConfigError::new(
                        "GPIO chip path must be set and pins must differ",
                    ));
                }
                if *data_pin > 53 || *clock_pin > 53 {
                    return Err(ConfigError::new("GPIO pins must be between 0 and 53"));
                }
                Ok(())
            }
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum OutputBackend {
    Mock,
    MockSpi,
    Spi {
        device: String,
        speed_hz: u32,
    },
    Gpio {
        chip: String,
        data_pin: u32,
        clock_pin: u32,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfigError {
    pub message: String,
}

impl ConfigError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ConfigError {}

#[cfg(test)]
mod tests {
    use super::{EngineConfig, MAX_OUTPUT_COUNT, MAX_PIXEL_COUNT, OutputBackend, OutputConfig};

    fn mock(id: &str, pixel_count: usize) -> OutputConfig {
        OutputConfig {
            id: id.into(),
            pixel_count,
            reversed: false,
            backend: OutputBackend::Mock,
        }
    }

    #[test]
    fn accepts_empty_and_supported_output_sets() {
        assert!(EngineConfig::new(vec![]).is_ok());
        assert!(EngineConfig::new(vec![mock("main", 1)]).is_ok());
        let mut mock_spi = mock("mock-spi", 1);
        mock_spi.backend = OutputBackend::MockSpi;
        assert!(EngineConfig::new(vec![mock_spi]).is_ok());
        assert!(
            EngineConfig::new(vec![mock("main", MAX_PIXEL_COUNT), mock("secondary", 2)]).is_ok()
        );
    }

    #[test]
    fn rejects_oversized_and_duplicate_output_sets() {
        let too_many = (0..=MAX_OUTPUT_COUNT)
            .map(|index| mock(&format!("strip-{index}"), 1))
            .collect();
        assert!(EngineConfig::new(too_many).is_err());
        assert!(EngineConfig::new(vec![mock("same", 1), mock("same", 2)]).is_err());
        assert!(EngineConfig::new(vec![mock("main", 0)]).is_err());
        assert!(EngineConfig::new(vec![mock("main", MAX_PIXEL_COUNT + 1)]).is_err());
    }

    #[test]
    fn rejects_shared_spi_devices_and_gpio_lines() {
        let spi = |id: &str| OutputConfig {
            id: id.into(),
            pixel_count: 1,
            reversed: false,
            backend: OutputBackend::Spi {
                device: "/dev/spidev0.0".into(),
                speed_hz: 1_000_000,
            },
        };
        assert!(EngineConfig::new(vec![spi("a"), spi("b")]).is_err());
        let gpio = |id: &str, data_pin| OutputConfig {
            id: id.into(),
            pixel_count: 1,
            reversed: false,
            backend: OutputBackend::Gpio {
                chip: "/dev/gpiochip0".into(),
                data_pin,
                clock_pin: data_pin + 1,
            },
        };
        assert!(EngineConfig::new(vec![gpio("a", 10), gpio("b", 11)]).is_err());
    }

    #[test]
    fn validates_values_deserialized_from_the_wire() {
        let config: EngineConfig = serde_json::from_str(
            r#"{"outputs":[{"id":"main","pixel_count":0,"backend":{"type":"mock"}}]}"#,
        )
        .unwrap();
        assert!(config.validate().is_err());
    }
}
