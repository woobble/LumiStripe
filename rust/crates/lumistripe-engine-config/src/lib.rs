//! Validated configuration shared by the engine process and its protocol.

use serde::{Deserialize, Serialize};
use std::fmt;

pub const MIN_PIXEL_COUNT: usize = 1;
pub const MAX_PIXEL_COUNT: usize = 16_384;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EngineConfig {
    pub pixel_count: usize,
}

impl EngineConfig {
    pub fn new(pixel_count: usize) -> Result<Self, ConfigError> {
        let config = Self { pixel_count };
        config.validate()?;
        Ok(config)
    }

    pub fn validate(self) -> Result<(), ConfigError> {
        if !(MIN_PIXEL_COUNT..=MAX_PIXEL_COUNT).contains(&self.pixel_count) {
            return Err(ConfigError {
                pixel_count: self.pixel_count,
            });
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConfigError {
    pub pixel_count: usize,
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "pixel_count must be between {MIN_PIXEL_COUNT} and {MAX_PIXEL_COUNT}"
        )
    }
}

impl std::error::Error for ConfigError {}

#[cfg(test)]
mod tests {
    use super::{EngineConfig, MAX_PIXEL_COUNT, MIN_PIXEL_COUNT};

    #[test]
    fn accepts_supported_pixel_counts() {
        assert!(EngineConfig::new(MIN_PIXEL_COUNT).is_ok());
        assert!(EngineConfig::new(MAX_PIXEL_COUNT).is_ok());
    }

    #[test]
    fn rejects_zero_and_oversized_outputs() {
        assert!(EngineConfig::new(0).is_err());
        assert!(EngineConfig::new(MAX_PIXEL_COUNT + 1).is_err());
    }

    #[test]
    fn validates_values_deserialized_from_the_wire() {
        let config: EngineConfig = serde_json::from_str(r#"{"pixel_count":0}"#).unwrap();
        assert!(config.validate().is_err());
    }
}
