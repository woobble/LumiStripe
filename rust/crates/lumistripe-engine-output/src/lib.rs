//! Output abstraction and the first, hardware-free implementation.

use lumistripe_engine_config::{ConfigError, EngineConfig};
use lumistripe_engine_protocol::Rgb;
use std::fmt;

pub trait LedOutput {
    fn configure(&mut self, config: EngineConfig) -> Result<(), OutputError>;
    fn render(&mut self, pixels: Vec<Rgb>) -> Result<u64, OutputError>;
    fn pixel_count(&self) -> Option<usize>;
    fn frames_rendered(&self) -> u64;
}

#[derive(Clone, Debug, Default)]
pub struct SimulatedLedOutput {
    config: Option<EngineConfig>,
    pixels: Vec<Rgb>,
    frames_rendered: u64,
}

impl SimulatedLedOutput {
    pub fn last_frame(&self) -> &[Rgb] {
        &self.pixels
    }
}

impl LedOutput for SimulatedLedOutput {
    fn configure(&mut self, config: EngineConfig) -> Result<(), OutputError> {
        config.validate().map_err(OutputError::InvalidConfig)?;
        self.pixels = vec![Rgb::BLACK; config.pixel_count];
        self.config = Some(config);
        self.frames_rendered = 0;
        Ok(())
    }

    fn render(&mut self, pixels: Vec<Rgb>) -> Result<u64, OutputError> {
        let config = self.config.ok_or(OutputError::NotConfigured)?;
        if pixels.len() != config.pixel_count {
            return Err(OutputError::InvalidFrame {
                expected: config.pixel_count,
                actual: pixels.len(),
            });
        }
        self.pixels = pixels;
        self.frames_rendered = self.frames_rendered.saturating_add(1);
        Ok(self.frames_rendered)
    }

    fn pixel_count(&self) -> Option<usize> {
        self.config.map(|config| config.pixel_count)
    }

    fn frames_rendered(&self) -> u64 {
        self.frames_rendered
    }
}

#[derive(Debug)]
pub enum OutputError {
    InvalidConfig(ConfigError),
    InvalidFrame { expected: usize, actual: usize },
    NotConfigured,
}

impl fmt::Display for OutputError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidConfig(error) => write!(f, "{error}"),
            Self::InvalidFrame { expected, actual } => {
                write!(f, "frame has {actual} pixels; expected {expected}")
            }
            Self::NotConfigured => write!(f, "simulated output is not configured"),
        }
    }
}

impl std::error::Error for OutputError {}

#[cfg(test)]
mod tests {
    use super::{LedOutput, OutputError, SimulatedLedOutput};
    use lumistripe_engine_config::EngineConfig;
    use lumistripe_engine_protocol::Rgb;

    #[test]
    fn renders_and_keeps_the_latest_frame() {
        let mut output = SimulatedLedOutput::default();
        output.configure(EngineConfig { pixel_count: 2 }).unwrap();
        let frame = vec![Rgb::new(1, 2, 3), Rgb::new(4, 5, 6)];
        assert_eq!(output.render(frame.clone()).unwrap(), 1);
        assert_eq!(output.last_frame(), frame);
        assert_eq!(output.frames_rendered(), 1);
    }

    #[test]
    fn rejects_frames_with_the_wrong_length() {
        let mut output = SimulatedLedOutput::default();
        output.configure(EngineConfig { pixel_count: 2 }).unwrap();
        assert!(matches!(
            output.render(vec![Rgb::BLACK]),
            Err(OutputError::InvalidFrame {
                expected: 2,
                actual: 1
            })
        ));
    }

    #[test]
    fn requires_configuration_before_rendering() {
        let mut output = SimulatedLedOutput::default();
        assert!(matches!(
            output.render(vec![Rgb::BLACK]),
            Err(OutputError::NotConfigured)
        ));
    }
}
