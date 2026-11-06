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
    /// Run the interactive dashboard against mock LED outputs on localhost.
    #[arg(long)]
    pub simulate: bool,
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
    #[arg(long, value_enum, env = "LUMI_SPOTIFY_BACKEND")]
    pub spotify_backend: Option<SpotifyBackend>,
    #[arg(long = "spotify-data-dir", env = "LUMI_SPOTIFY_LIBRESPOT_DATA_DIR")]
    pub spotify_data_dir: Option<PathBuf>,
    #[arg(long, env = "LUMISTRIPE_AUDIO_DEVICE")]
    pub audio_device: Option<String>,
    #[arg(
        long = "spotify-ws",
        env = "LUMI_SPOTIFY_WS",
        default_value = "127.0.0.1:9090"
    )]
    pub spotify_ws_uri: String,
    #[arg(
        long = "enable-spotify",
        env = "LUMISTRIPE_ENABLE_SPOTIFY",
        default_value_t = false
    )]
    pub spotify_provider_enabled: bool,
    #[arg(long, env = "LUMI_BLUETOOTH_MAX_INPUTS", default_value_t = 1)]
    pub bluetooth_max_inputs: usize,
    #[arg(long, env = "LUMI_BLUETOOTH_MAX_OUTPUTS", default_value_t = 1)]
    pub bluetooth_max_outputs: usize,
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

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum SpotifyBackend {
    Librespot,
    Soloist,
    Disabled,
}

impl SpotifyBackend {
    pub fn is_enabled(self) -> bool {
        self != Self::Disabled
    }
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
    pub spotify_ws_uri: String,
    pub spotify_backend: SpotifyBackend,
    pub spotify_data_dir: PathBuf,
    pub bluetooth_max_inputs: usize,
    pub bluetooth_max_outputs: usize,
    pub secure_cookie: bool,
    pub static_dir: Option<PathBuf>,
    pub settings_file: PathBuf,
}

impl CliOptions {
    pub fn into_config(mut self) -> Result<(AppConfig, String, u16), String> {
        if self.bluetooth_max_inputs == 0 || self.bluetooth_max_outputs == 0 {
            return Err("Bluetooth connection limits must be at least one".to_owned());
        }
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
        if self.simulate && self.output_backend != OutputBackendArg::Mock {
            return Err("--simulate always uses mock outputs; remove --output-backend".to_owned());
        }

        let host = if self.simulate {
            "127.0.0.1".to_owned()
        } else {
            self.host.clone()
        };
        let settings_file = self.settings_file.take().unwrap_or_else(|| {
            if self.simulate {
                default_simulator_settings_file()
            } else {
                default_settings_file()
            }
        });

        let (runtime_kind, output_backend, primary_backend, secondary_backend) =
            match self.output_backend {
                OutputBackendArg::Mock => (
                    "simulation",
                    "simulation",
                    OutputBackend::Mock,
                    self.pixels_2.map(|_| OutputBackend::Mock),
                ),
                OutputBackendArg::MockSpi => (
                    "simulation",
                    "mock_spi",
                    OutputBackend::MockSpi,
                    self.pixels_2.map(|_| OutputBackend::MockSpi),
                ),
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
        let spotify_backend = match self.spotify_backend {
            Some(backend) => backend,
            None if self.spotify_provider_enabled || runtime_kind == "hardware" => {
                SpotifyBackend::Soloist
            }
            None => SpotifyBackend::Disabled,
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
        Ok((
            AppConfig {
                outputs,
                runtime_kind,
                output_backend,
                output_devices: devices,
                spi_speed_hz,
                audio_device: self.audio_device,
                spotify_ws_uri: self.spotify_ws_uri,
                spotify_backend,
                spotify_data_dir: self
                    .spotify_data_dir
                    .unwrap_or_else(default_spotify_data_dir),
                bluetooth_max_inputs: self.bluetooth_max_inputs,
                bluetooth_max_outputs: self.bluetooth_max_outputs,
                secure_cookie: self.secure_cookie,
                static_dir: self.static_dir,
                settings_file,
            },
            host,
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

fn default_settings_file() -> PathBuf {
    if let Some(path) = std::env::var_os("HOME").filter(|path| !path.is_empty()) {
        return PathBuf::from(path).join(".config/lumistripe/settings.json");
    }
    PathBuf::from(".config/lumistripe/settings.json")
}

fn default_spotify_data_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("HOME").filter(|path| !path.is_empty()) {
        return PathBuf::from(path).join(".local/share/lumistripe/spotify");
    }
    PathBuf::from(".local/share/lumistripe/spotify")
}

fn default_simulator_settings_file() -> PathBuf {
    if let Some(path) = std::env::var_os("XDG_CONFIG_HOME").filter(|path| !path.is_empty()) {
        return PathBuf::from(path).join("lumistripe/simulator-settings.json");
    }
    if let Some(path) = std::env::var_os("HOME").filter(|path| !path.is_empty()) {
        return PathBuf::from(path).join(".config/lumistripe/simulator-settings.json");
    }
    PathBuf::from(".config/lumistripe/simulator-settings.json")
}

#[cfg(test)]
mod tests {
    use super::{AppConfig, CliOptions, EngineOutputOptions, SpotifyBackend};
    use crate::settings::{
        StripeBackend, StripeOutputSettings, StripeTopologySettings, ZonePattern,
    };
    use clap::Parser;
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
            spotify_ws_uri: "127.0.0.1:9090".to_owned(),
            spotify_backend: SpotifyBackend::Disabled,
            spotify_data_dir: PathBuf::new(),
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
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

    #[test]
    fn bluetooth_connection_limits_are_configurable_and_must_be_positive() {
        let options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--bluetooth-max-inputs",
            "3",
            "--bluetooth-max-outputs",
            "2",
        ])
        .unwrap();
        let (config, _, _) = options.into_config().unwrap();
        assert_eq!(config.bluetooth_max_inputs, 3);
        assert_eq!(config.bluetooth_max_outputs, 2);

        let mut options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--bluetooth-max-inputs",
            "1",
            "--bluetooth-max-outputs",
            "1",
        ])
        .unwrap();
        options.bluetooth_max_inputs = 0;
        assert!(options.into_config().is_err());
    }

    #[test]
    fn soloist_endpoint_and_simulation_opt_in_are_configurable() {
        let options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--spotify-ws",
            "ws://127.0.0.1:9123",
            "--enable-spotify",
        ])
        .unwrap();
        let (config, _, _) = options.into_config().unwrap();
        assert_eq!(config.spotify_ws_uri, "ws://127.0.0.1:9123");
        assert_eq!(config.spotify_backend, SpotifyBackend::Soloist);
    }

    #[test]
    fn spotify_backend_selection_is_explicit_and_preserves_hardware_migration_default() {
        let options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--spotify-backend",
            "librespot",
            "--spotify-data-dir",
            "/tmp/lumistripe-spotify",
        ])
        .unwrap();
        let (config, _, _) = options.into_config().unwrap();
        assert_eq!(config.spotify_backend, SpotifyBackend::Librespot);
        assert_eq!(
            config.spotify_data_dir,
            PathBuf::from("/tmp/lumistripe-spotify")
        );

        let options =
            CliOptions::try_parse_from(["lumistripe-web", "--output-backend", "spi"]).unwrap();
        let (config, _, _) = options.into_config().unwrap();
        assert_eq!(config.spotify_backend, SpotifyBackend::Soloist);

        let options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--output-backend",
            "spi",
            "--spotify-backend",
            "disabled",
        ])
        .unwrap();
        let (config, _, _) = options.into_config().unwrap();
        assert_eq!(config.spotify_backend, SpotifyBackend::Disabled);
    }

    #[test]
    fn explicit_spotify_backend_takes_precedence_over_legacy_enable_flag() {
        let options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--spotify-backend",
            "librespot",
            "--enable-spotify",
        ])
        .unwrap();
        let (config, _, _) = options.into_config().unwrap();
        assert_eq!(config.spotify_backend, SpotifyBackend::Librespot);
    }

    #[test]
    fn simulator_mode_is_loopback_mock_and_keeps_settings_separate() {
        let options = CliOptions::try_parse_from([
            "lumistripe-web",
            "--simulate",
            "--host",
            "0.0.0.0",
            "--pixels",
            "80",
            "--pixels-2",
            "24",
        ])
        .unwrap();
        let (config, host, _) = options.into_config().unwrap();

        assert_eq!(host, "127.0.0.1");
        assert_eq!(config.runtime_kind, "simulation");
        assert_eq!(config.output_backend, "simulation");
        assert_eq!(config.engine_config().pixel_counts(), vec![80, 24]);
        assert!(
            config
                .engine_config()
                .outputs
                .iter()
                .all(|output| matches!(output.backend, OutputBackend::Mock))
        );
        assert!(
            config
                .settings_file
                .ends_with("lumistripe/simulator-settings.json")
        );
    }

    #[test]
    fn simulator_mode_rejects_hardware_output_selection() {
        let options =
            CliOptions::try_parse_from(["lumistripe-web", "--simulate", "--output-backend", "spi"])
                .unwrap();
        assert!(
            options
                .into_config()
                .unwrap_err()
                .contains("always uses mock outputs")
        );
    }

    #[test]
    fn web_cli_does_not_accept_a_standalone_engine_binary() {
        assert!(
            CliOptions::try_parse_from([
                "lumistripe-web",
                "--engine-binary",
                "/usr/local/bin/lumistripe-engine",
            ])
            .is_err()
        );
    }
}
