use crate::settings::{
    StripeBackend as SettingsStripeBackend, StripeTopologySettings, ZonePattern,
};
use clap::{Parser, ValueEnum};
use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
use std::path::PathBuf;

#[derive(Clone, Debug, Parser)]
#[command(name = "lumistripe-web", about = "LumiStripe native web dashboard")]
pub struct CliOptions {
    #[arg(long, default_value = "0.0.0.0")]
    pub host: String,
    #[arg(long, default_value_t = 8000)]
    pub port: u16,
    #[arg(long, env = "LUMISTRIPE_ENGINE_BINARY")]
    pub engine_binary: Option<PathBuf>,
    #[arg(long, default_value_t = 80)]
    pub pixels: usize,
    #[arg(long, value_enum, default_value_t = OutputBackendArg::Mock)]
    pub output_backend: OutputBackendArg,
    #[arg(long, default_value = "/dev/spidev0.0")]
    pub spi_device: String,
    #[arg(long, default_value_t = 1_000_000)]
    pub spi_speed_hz: u32,
    #[arg(long, default_value = "/dev/gpiochip0")]
    pub chip: String,
    #[arg(long, default_value_t = 14)]
    pub data_pin: u32,
    #[arg(long, default_value_t = 15)]
    pub clock_pin: u32,
    #[arg(long)]
    pub pixels_2: Option<usize>,
    #[arg(long)]
    pub spi_device_2: Option<String>,
    #[arg(long)]
    pub spi_speed_hz_2: Option<u32>,
    #[arg(long)]
    pub data_pin_2: Option<u32>,
    #[arg(long)]
    pub clock_pin_2: Option<u32>,
    #[arg(long, env = "LUMI_PAIRING_CODE")]
    pub pairing_code: Option<String>,
    #[arg(long, env = "LUMISTRIPE_WEB_STATIC_DIR")]
    pub static_dir: Option<PathBuf>,
    #[arg(long, env = "LUMISTRIPE_SETTINGS_FILE")]
    pub settings_file: Option<PathBuf>,
    #[arg(long, env = "LUMISTRIPE_AUDIO_DEVICE")]
    pub audio_device: Option<String>,
    #[arg(long, env = "LUMISTRIPE_SECURE_COOKIE", default_value_t = false)]
    pub secure_cookie: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum OutputBackendArg {
    Mock,
    MockSpi,
    Spi,
    Gpio,
}

#[derive(Clone, Debug)]
pub struct EngineOutputOptions {
    pub id: String,
    pub name: String,
    pub pixel_count: usize,
    pub backend: OutputBackend,
    pub reversed: bool,
    pub zone_count: usize,
    pub zone_pattern: ZonePattern,
    pub voltage_v: f64,
    pub full_white_current_a: f64,
    pub power_limit_watts: Option<f64>,
}

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub outputs: Vec<EngineOutputOptions>,
    pub runtime_kind: &'static str,
    pub output_backend: &'static str,
    pub output_devices: Vec<String>,
    pub spi_speed_hz: Option<u32>,
    pub audio_device: Option<String>,
    pub secure_cookie: bool,
    pub static_dir: Option<PathBuf>,
    pub settings_file: PathBuf,
}

impl CliOptions {
    pub fn into_config(self) -> Result<(AppConfig, PathBuf, String, u16), String> {
        if self.pairing_code.as_ref().is_some_and(|code| {
            code.len() != 4 || !code.is_ascii() || !code.bytes().all(|byte| byte.is_ascii_digit())
        }) {
            return Err("pairing code must contain exactly four ASCII digits".to_owned());
        }
        if (self.spi_speed_hz_2.is_some() && self.spi_device_2.is_none())
            || (self.spi_device_2.is_some() && self.output_backend != OutputBackendArg::Spi)
        {
            return Err("--spi-device-2 requires --output-backend spi".to_owned());
        }
        if (self.data_pin_2.is_some() || self.clock_pin_2.is_some())
            && (self.output_backend != OutputBackendArg::Gpio || self.pixels_2.is_none())
        {
            return Err("second GPIO pins require --output-backend gpio and --pixels-2".to_owned());
        }
        if self.pixels_2.is_some_and(|pixels| pixels == 0) {
            return Err("--pixels-2 must be greater than zero".to_owned());
        }
        if self.spi_speed_hz_2 == Some(0) {
            return Err("--spi-speed-hz-2 must be greater than zero".to_owned());
        }

        let (runtime_kind, output_backend, primary_backend, secondary_backend) = match self
            .output_backend
        {
            OutputBackendArg::Mock => ("simulation", "simulation", OutputBackend::Mock, None),
            OutputBackendArg::MockSpi => ("simulation", "mock_spi", OutputBackend::MockSpi, None),
            OutputBackendArg::Spi => (
                "hardware",
                "spi",
                OutputBackend::Spi {
                    device: self.spi_device.clone(),
                    speed_hz: self.spi_speed_hz,
                },
                self.pixels_2.map(|_| OutputBackend::Spi {
                    device: self
                        .spi_device_2
                        .clone()
                        .unwrap_or_else(|| "/dev/spidev0.1".to_owned()),
                    speed_hz: self.spi_speed_hz_2.unwrap_or(self.spi_speed_hz),
                }),
            ),
            OutputBackendArg::Gpio => {
                let primary = OutputBackend::Gpio {
                    chip: self.chip.clone(),
                    data_pin: self.data_pin,
                    clock_pin: self.clock_pin,
                };
                let secondary = self.pixels_2.map(|_| OutputBackend::Gpio {
                    chip: self.chip.clone(),
                    data_pin: self.data_pin_2.unwrap_or(17),
                    clock_pin: self.clock_pin_2.unwrap_or(18),
                });
                ("hardware", "gpio", primary, secondary)
            }
        };
        let mut outputs = vec![EngineOutputOptions {
            id: "primary".to_owned(),
            name: "Primary".to_owned(),
            pixel_count: self.pixels,
            backend: primary_backend,
            reversed: false,
            zone_count: 1,
            zone_pattern: ZonePattern::Repeat,
            voltage_v: 5.0,
            full_white_current_a: 0.06,
            power_limit_watts: None,
        }];
        if let (Some(pixel_count), Some(backend)) = (self.pixels_2, secondary_backend) {
            outputs.push(EngineOutputOptions {
                id: "secondary".to_owned(),
                name: "Secondary".to_owned(),
                pixel_count,
                backend,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            });
        }
        let devices = outputs.iter().map(output_device_name).collect();
        let spi_speed_hz = if output_backend == "spi" {
            Some(self.spi_speed_hz)
        } else {
            None
        };
        let engine_binary = self.engine_binary.unwrap_or_else(default_engine_binary);
        Ok((
            AppConfig {
                outputs,
                runtime_kind,
                output_backend,
                output_devices: devices,
                spi_speed_hz,
                audio_device: self.audio_device,
                secure_cookie: self.secure_cookie,
                static_dir: self.static_dir,
                settings_file: self.settings_file.unwrap_or_else(default_settings_file),
            },
            engine_binary,
            self.host,
            self.port,
        ))
    }
}

impl AppConfig {
    pub fn engine_config(&self) -> EngineConfig {
        EngineConfig::new(
            self.outputs
                .iter()
                .map(|output| OutputConfig {
                    id: output.id.clone(),
                    pixel_count: output.pixel_count,
                    reversed: output.reversed,
                    backend: output.backend.clone(),
                })
                .collect(),
        )
        .expect("CLI validation produces a valid engine output configuration")
    }

    pub fn apply_topology(&mut self, topology: &StripeTopologySettings) -> Result<(), String> {
        topology
            .validate()
            .map_err(|error| format!("saved stripe topology is invalid: {error}"))?;
        if topology.outputs.is_empty() {
            return Err("saved stripe topology has no physical LED outputs".to_owned());
        }
        let simulated_backend = match self.output_backend {
            "simulation" => Some(OutputBackend::Mock),
            "mock_spi" => Some(OutputBackend::MockSpi),
            _ => None,
        };
        let outputs = topology
            .outputs
            .iter()
            .map(|output| {
                let backend = simulated_backend
                    .clone()
                    .unwrap_or_else(|| match output.backend {
                        SettingsStripeBackend::Spi => OutputBackend::Spi {
                            device: output.spi_device.clone(),
                            speed_hz: output.spi_speed_hz,
                        },
                        SettingsStripeBackend::Gpio => OutputBackend::Gpio {
                            chip: output.chip.clone(),
                            data_pin: output.data_pin,
                            clock_pin: output.clock_pin,
                        },
                    });
                EngineOutputOptions {
                    id: output.id.clone(),
                    name: output.name.clone(),
                    pixel_count: output.pixels,
                    backend,
                    reversed: output.reversed,
                    zone_count: output.zone_count,
                    zone_pattern: output.zone_pattern,
                    voltage_v: output.voltage_v,
                    full_white_current_a: output.full_white_current_a,
                    power_limit_watts: output.power_limit_watts,
                }
            })
            .collect::<Vec<_>>();
        let engine_config = EngineConfig::new(
            outputs
                .iter()
                .map(|output| OutputConfig {
                    id: output.id.clone(),
                    pixel_count: output.pixel_count,
                    reversed: output.reversed,
                    backend: output.backend.clone(),
                })
                .collect(),
        )
        .map_err(|error| format!("saved stripe topology cannot configure the engine: {error}"))?;
        self.outputs = outputs;
        if simulated_backend.is_none() {
            self.runtime_kind = "hardware";
            self.output_backend = match self.outputs.first().map(|output| &output.backend) {
                Some(OutputBackend::Spi { .. })
                    if self
                        .outputs
                        .iter()
                        .all(|output| matches!(&output.backend, OutputBackend::Spi { .. })) =>
                {
                    "spi"
                }
                Some(OutputBackend::Gpio { .. })
                    if self
                        .outputs
                        .iter()
                        .all(|output| matches!(&output.backend, OutputBackend::Gpio { .. })) =>
                {
                    "gpio"
                }
                Some(_) => "mixed",
                None => "none",
            };
        }
        self.output_devices = self.outputs.iter().map(output_device_name).collect();
        self.spi_speed_hz = self
            .outputs
            .iter()
            .find_map(|output| match &output.backend {
                OutputBackend::Spi { speed_hz, .. } => Some(*speed_hz),
                _ => None,
            });
        let _ = engine_config;
        Ok(())
    }
}

fn output_device_name(output: &EngineOutputOptions) -> String {
    match &output.backend {
        OutputBackend::Mock => "simulation".to_owned(),
        OutputBackend::MockSpi => "mock-spi".to_owned(),
        OutputBackend::Spi { device, .. } => device.clone(),
        OutputBackend::Gpio { chip, .. } => chip.clone(),
    }
}

fn default_engine_binary() -> PathBuf {
    if let Some(path) = option_env!("LUMISTRIPE_ENGINE_BINARY") {
        return PathBuf::from(path);
    }
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join("lumistripe-engine")))
        .unwrap_or_else(|| PathBuf::from("lumistripe-engine"))
}

fn default_settings_file() -> PathBuf {
    if let Some(path) = std::env::var_os("HOME").filter(|path| !path.is_empty()) {
        return PathBuf::from(path).join(".config/lumistripe/settings.json");
    }
    PathBuf::from(".config/lumistripe/settings.json")
}

#[cfg(test)]
mod tests {
    use super::{AppConfig, EngineOutputOptions};
    use crate::settings::{
        StripeBackend, StripeOutputSettings, StripeTopologySettings, ZonePattern,
    };
    use lumistripe_engine_config::OutputBackend;
    use std::path::PathBuf;

    #[test]
    fn persisted_topology_preserves_simulation_and_applies_hardware_backends() {
        let mut config = AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "cli".to_owned(),
                name: "CLI output".to_owned(),
                pixel_count: 8,
                backend: OutputBackend::Mock,
                reversed: false,
                zone_count: 1,
                zone_pattern: ZonePattern::Repeat,
                voltage_v: 5.0,
                full_white_current_a: 0.06,
                power_limit_watts: None,
            }],
            runtime_kind: "simulation",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            secure_cookie: false,
            static_dir: None,
            settings_file: PathBuf::new(),
        };
        let topology = StripeTopologySettings {
            layout: crate::settings::StripeLayout::Continuous,
            outputs: vec![StripeOutputSettings {
                id: "left".to_owned(),
                name: "Left".to_owned(),
                pixels: 100,
                backend: StripeBackend::Gpio,
                reversed: true,
                zone_count: 4,
                zone_pattern: ZonePattern::Alternate,
                ..Default::default()
            }],
            ..Default::default()
        };

        let mut hardware_config = config.clone();
        hardware_config.runtime_kind = "hardware";
        hardware_config.output_backend = "spi";
        hardware_config.apply_topology(&topology).unwrap();

        assert_eq!(hardware_config.runtime_kind, "hardware");
        assert_eq!(hardware_config.output_backend, "gpio");
        assert!(matches!(
            hardware_config.outputs[0].backend,
            OutputBackend::Gpio { .. }
        ));

        config.apply_topology(&topology).unwrap();

        assert_eq!(config.runtime_kind, "simulation");
        assert_eq!(config.output_backend, "simulation");
        assert_eq!(config.outputs[0].id, "left");
        assert_eq!(config.outputs[0].zone_count, 4);
        assert_eq!(config.outputs[0].zone_pattern, ZonePattern::Alternate);
        assert_eq!(config.engine_config().pixel_counts(), vec![100]);
        assert!(config.engine_config().outputs[0].reversed);
        assert!(matches!(config.outputs[0].backend, OutputBackend::Mock));
    }
}
