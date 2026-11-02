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
            id: "main".to_owned(),
            name: "Main stripe".to_owned(),
            pixel_count: self.pixels,
            backend: primary_backend,
        }];
        if let (Some(pixel_count), Some(backend)) = (self.pixels_2, secondary_backend) {
            outputs.push(EngineOutputOptions {
                id: "secondary".to_owned(),
                name: "Secondary stripe".to_owned(),
                pixel_count,
                backend,
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
                    reversed: false,
                    backend: output.backend.clone(),
                })
                .collect(),
        )
        .expect("CLI validation produces a valid engine output configuration")
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
