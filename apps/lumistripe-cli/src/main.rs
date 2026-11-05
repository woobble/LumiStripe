mod audio_debug;
mod encoder;
mod mic_profile;

use clap::{Parser, ValueEnum};
use encoder::{EncoderBackend, EncoderEvent, EncoderPins};
use lumistripe_engine_animation_definitions::{AnimationDefinition, catalog};
use lumistripe_engine_audio::{
    AudioCalibrationRecommendation, AudioConfig, InputAudioDevice, recommend_audio_calibration,
};
use lumistripe_engine_client::{EngineClient, EngineClientError};
use lumistripe_engine_config::{EngineConfig, OutputBackend, OutputConfig};
use lumistripe_engine_protocol::{
    RenderAnimationChoice, RenderCycleOrder, RenderCycleTiming, RenderCycling, RenderDynamicIdle,
    RenderDynamicIdleMode, RenderDynamicSelector, RenderEffectChoice, RenderEffectSchedulerConfig,
    RenderMusicActivityConfig, RenderOutput, RenderPowerConfig, RenderSceneUpdate,
    RenderSchedulerConfig, RenderSwitchQuantization, RequestCommand, ResponseResult,
};
use mic_profile::MicProfile;
use serde_json::{Map, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;
use tempfile::TempDir;
use tokio::process::{Child, Command};
use tokio::time::{Instant, sleep};

#[derive(Debug, Parser)]
#[command(
    name = "lumistripe",
    about = "Control LumiStripe through its Rust real-time engine"
)]
struct Args {
    #[arg(long, env = "LUMISTRIPE_ENGINE_BINARY")]
    engine_binary: Option<PathBuf>,
    #[command(subcommand)]
    command: Option<CommandArgs>,
    #[command(flatten)]
    legacy_run: RunArgs,
    /// Analyze one or more JSONL files produced by --audio-debug.
    #[arg(long, value_name = "PATH", num_args = 1..)]
    analyze_audio_debug: Vec<PathBuf>,
    /// List available audio input devices and exit.
    #[arg(long)]
    list_audio_devices: bool,
    /// Measure the microphone and print tuning recommendations, then exit.
    #[arg(long, value_name = "SECONDS")]
    calibrate_audio: Option<f64>,
    /// Run audio-only microphone diagnostics without opening LED output.
    #[arg(long)]
    audio_debug: bool,
    /// Include selector internals in legacy --audio-debug output.
    #[arg(long)]
    audio_debug_verbose: bool,
    /// Record legacy --audio-debug rows as JSONL.
    #[arg(long, value_name = "PATH")]
    audio_debug_record: Option<PathBuf>,
    /// Stop legacy --audio-debug after this duration.
    #[arg(long, value_name = "SECONDS")]
    audio_debug_duration: Option<f64>,
    /// Label the legacy --audio-debug recording.
    #[arg(long)]
    audio_debug_label: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LegacyAction {
    AnalyzeAudioDebug,
    ListAudioDevices,
    CalibrateAudio,
    WriteMicProfile,
    AudioDebug,
    Playback,
}

fn legacy_action(args: &Args) -> LegacyAction {
    if !args.analyze_audio_debug.is_empty() {
        LegacyAction::AnalyzeAudioDebug
    } else if args.list_audio_devices {
        LegacyAction::ListAudioDevices
    } else if args.calibrate_audio.is_some() {
        LegacyAction::CalibrateAudio
    } else if args.legacy_run.write_mic_profile.is_some() {
        LegacyAction::WriteMicProfile
    } else if args.audio_debug {
        LegacyAction::AudioDebug
    } else {
        LegacyAction::Playback
    }
}

fn legacy_audio_debug_label<'label>(
    record_path: Option<&Path>,
    label: Option<&'label str>,
) -> Option<&'label str> {
    record_path.and(label)
}

#[derive(Debug, clap::Subcommand)]
enum CommandArgs {
    /// Run Rust-owned animation playback on one or two LED outputs.
    Run(Box<RunArgs>),
    /// Validate an engine output configuration without opening hardware.
    ValidateOutputs { config: PathBuf },
    /// List the animations supported by this Rust engine build.
    ListAnimations,
    /// Report the engine's output and scheduler health.
    Health,
    /// List available microphone devices.
    AudioDevices,
    /// Report current engine audio capture status.
    AudioStatus,
    /// Measure microphone input and print recommended engine audio tuning.
    CalibrateAudio {
        #[arg(long, default_value_t = 5.0)]
        duration_seconds: f64,
        #[arg(long)]
        audio_device: Option<String>,
    },
    /// Stream microphone/DSP diagnostics and optionally record rows as JSONL.
    AudioDebug {
        #[arg(long)]
        duration_seconds: Option<f64>,
        #[arg(long)]
        audio_device: Option<String>,
        #[arg(long)]
        record: Option<PathBuf>,
        #[arg(long)]
        label: Option<String>,
        #[arg(long, visible_alias = "audio-debug-verbose")]
        verbose: bool,
    },
    /// Analyze one or more JSONL files produced by audio-debug.
    AnalyzeAudioDebug {
        #[arg(required = true, num_args = 1..)]
        paths: Vec<PathBuf>,
    },
    /// List renderer capabilities exposed by the engine.
    Capabilities,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum BackendArg {
    Mock,
    MockSpi,
    Spi,
    Gpio,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum ModeArg {
    Static,
    Cycling,
    Dynamic,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum AudioSourceArg {
    Off,
    Mic,
    Demo,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, ValueEnum)]
#[value(rename_all = "kebab-case")]
enum CycleOrderArg {
    #[default]
    Sequential,
    Shuffle,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, ValueEnum)]
#[value(rename_all = "kebab-case")]
enum CycleTimingArg {
    #[default]
    PerAnimation,
    Fixed,
}

#[derive(Debug, clap::Args)]
struct RunArgs {
    #[arg(long = "backend", visible_alias = "output-backend", value_enum, default_value_t = BackendArg::Spi)]
    backend: BackendArg,
    #[arg(long, default_value_t = 80)]
    pixels: usize,
    #[arg(long)]
    pixels_2: Option<usize>,
    #[arg(long, default_value = "/dev/spidev0.0")]
    spi_device: String,
    #[arg(
        long = "spi-speed",
        visible_alias = "spi-speed-hz",
        default_value_t = 1_000_000
    )]
    spi_speed_hz: u32,
    #[arg(long)]
    spi_device_2: Option<String>,
    #[arg(long = "spi-speed-hz-2", visible_alias = "spi-speed-2")]
    spi_speed_hz_2: Option<u32>,
    #[arg(
        long = "gpio-chip",
        visible_alias = "chip",
        default_value = "/dev/gpiochip0"
    )]
    gpio_chip: String,
    #[arg(long, default_value_t = 14)]
    data_pin: u32,
    #[arg(long, default_value_t = 15)]
    clock_pin: u32,
    #[arg(long = "gpio-chip-2", visible_alias = "chip-2")]
    gpio_chip_2: Option<String>,
    #[arg(long)]
    data_pin_2: Option<u32>,
    #[arg(long)]
    clock_pin_2: Option<u32>,
    #[arg(long, value_enum, default_value_t = ModeArg::Static)]
    mode: ModeArg,
    #[arg(long, default_value = "rainbow_cycle")]
    animation: String,
    /// Animation setting values as a JSON object, for example '{"speed": 1.5}'.
    #[arg(long)]
    settings: Option<String>,
    #[arg(long, default_value_t = 1.0)]
    brightness: f64,
    #[arg(long, default_value_t = 60)]
    fps: u16,
    #[arg(
        long = "cycle-interval",
        visible_alias = "cycle-interval-seconds",
        default_value_t = 30.0
    )]
    cycle_interval_seconds: f64,
    #[arg(long, value_enum, default_value_t = CycleOrderArg::Sequential)]
    cycle_order: CycleOrderArg,
    #[arg(long, value_enum, default_value_t = CycleTimingArg::PerAnimation)]
    cycle_timing: CycleTimingArg,
    #[arg(long)]
    power_budget_watts: Option<f64>,
    #[arg(long)]
    audio_device: Option<String>,
    /// Audio source for engine capture. Dynamic mode defaults to mic; other modes default to off.
    #[arg(long, value_enum)]
    audio_source: Option<AudioSourceArg>,
    /// JSON object containing fields from the engine's shared AudioConfig.
    #[arg(long)]
    audio_config: Option<String>,
    /// Legacy-compatible microphone normalization target.
    #[arg(long)]
    mic_target_level: Option<f64>,
    /// Legacy-compatible microphone smoothing noise floor.
    #[arg(long)]
    mic_noise_floor: Option<f64>,
    /// Load a named built-in profile, JSON profile file, or match a built-in profile with "auto".
    #[arg(long)]
    mic_profile: Option<String>,
    /// Write the effective microphone tuning as a JSON profile and exit.
    #[arg(long)]
    write_mic_profile: Option<PathBuf>,
    /// Measure microphone input before playback and apply the recommended tuning.
    #[arg(
        long = "auto-calibrate-seconds",
        visible_alias = "auto-calibrate-audio"
    )]
    auto_calibrate_seconds: Option<f64>,
    /// Scale Dynamic's idle activity thresholds.
    #[arg(long, allow_hyphen_values = true)]
    idle_threshold_scale: Option<f64>,
    #[arg(long, default_value_t = 60)]
    idle_enter_frames: u32,
    #[arg(
        long = "music-activation-delay",
        visible_alias = "music-activation-delay-seconds",
        default_value_t = 0.75
    )]
    music_activation_delay_seconds: f64,
    #[arg(long = "dynamic-idle-brightness", default_value_t = 1.0)]
    dynamic_idle_brightness: f64,
    #[arg(long = "dynamic-min-duration", default_value_t = 12.0)]
    dynamic_min_duration_seconds: f64,
    #[arg(long = "dynamic-max-duration", default_value_t = 45.0)]
    dynamic_max_duration_seconds: f64,
    #[arg(long = "dynamic-switch-cooldown", default_value_t = 8.0)]
    dynamic_switch_cooldown_seconds: f64,
    #[arg(long = "dynamic-drop-cooldown", default_value_t = 15.0)]
    dynamic_drop_cooldown_seconds: f64,
    #[arg(long, default_value_t = 0.15)]
    dynamic_randomness: f64,
    #[arg(long, default_value_t = 5)]
    dynamic_history_size: usize,
    #[arg(long)]
    dynamic_seed: Option<i64>,
    #[arg(long)]
    quiet: bool,
    /// Print the engine's selector, music-gate, beat-clock, and effect status.
    #[arg(long)]
    debug_selector: bool,
    /// Print engine frame, latency, and power statistics while playback runs.
    #[arg(long)]
    stats: bool,
    /// Interval between --stats reports, in milliseconds.
    #[arg(long, default_value_t = 1000)]
    stats_interval_ms: u64,
    /// GPIO chip for rotary encoder inputs; defaults to --gpio-chip.
    #[arg(long)]
    encoder_chip: Option<String>,
    #[arg(long)]
    encoder1_a: Option<u32>,
    #[arg(long)]
    encoder1_b: Option<u32>,
    #[arg(long)]
    encoder1_button: Option<u32>,
    #[arg(long)]
    encoder2_a: Option<u32>,
    #[arg(long)]
    encoder2_b: Option<u32>,
    #[arg(long)]
    encoder2_button: Option<u32>,
    /// Stop after this many seconds; otherwise run until interrupted.
    #[arg(long)]
    duration_seconds: Option<u64>,
}

impl Default for RunArgs {
    fn default() -> Self {
        Self {
            backend: BackendArg::Spi,
            pixels: 80,
            pixels_2: None,
            spi_device: "/dev/spidev0.0".to_owned(),
            spi_speed_hz: 1_000_000,
            spi_device_2: None,
            spi_speed_hz_2: None,
            gpio_chip: "/dev/gpiochip0".to_owned(),
            data_pin: 14,
            clock_pin: 15,
            gpio_chip_2: None,
            data_pin_2: None,
            clock_pin_2: None,
            mode: ModeArg::Static,
            animation: "rainbow_cycle".to_owned(),
            settings: None,
            brightness: 1.0,
            fps: 60,
            cycle_interval_seconds: 30.0,
            cycle_order: CycleOrderArg::Sequential,
            cycle_timing: CycleTimingArg::PerAnimation,
            power_budget_watts: None,
            audio_device: None,
            audio_source: None,
            audio_config: None,
            mic_target_level: None,
            mic_noise_floor: None,
            mic_profile: None,
            write_mic_profile: None,
            auto_calibrate_seconds: None,
            idle_threshold_scale: None,
            idle_enter_frames: 60,
            music_activation_delay_seconds: 0.75,
            dynamic_idle_brightness: 1.0,
            dynamic_min_duration_seconds: 12.0,
            dynamic_max_duration_seconds: 45.0,
            dynamic_switch_cooldown_seconds: 8.0,
            dynamic_drop_cooldown_seconds: 15.0,
            dynamic_randomness: 0.15,
            dynamic_history_size: 5,
            dynamic_seed: None,
            quiet: false,
            debug_selector: false,
            stats: false,
            stats_interval_ms: 1000,
            encoder_chip: None,
            encoder1_a: None,
            encoder1_b: None,
            encoder1_button: None,
            encoder2_a: None,
            encoder2_b: None,
            encoder2_button: None,
            duration_seconds: None,
        }
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    let args = Args::parse();
    let legacy_action = legacy_action(&args);
    let result = match args.command {
        None => match legacy_action {
            LegacyAction::AnalyzeAudioDebug => {
                audio_debug::analyze(&args.analyze_audio_debug).map(|summary| println!("{summary}"))
            }
            LegacyAction::ListAudioDevices => {
                run_query(args.engine_binary, RequestCommand::AudioDevices).await
            }
            LegacyAction::CalibrateAudio => {
                let audio_device = args.legacy_run.audio_device.clone();
                calibrate_audio(
                    args.engine_binary,
                    args.calibrate_audio.expect("selected calibration action"),
                    audio_device.as_deref(),
                )
                .await
            }
            LegacyAction::WriteMicProfile | LegacyAction::Playback => {
                run_playback(args.engine_binary, args.legacy_run).await
            }
            LegacyAction::AudioDebug => {
                let audio_device = args.legacy_run.audio_device.clone();
                let label = legacy_audio_debug_label(
                    args.audio_debug_record.as_deref(),
                    args.audio_debug_label.as_deref(),
                );
                audio_debug(
                    args.engine_binary,
                    args.audio_debug_duration,
                    audio_device.as_deref(),
                    args.audio_debug_record.as_deref(),
                    label,
                    args.audio_debug_verbose,
                    args.legacy_run,
                )
                .await
            }
        },
        Some(CommandArgs::Run(run)) => run_playback(args.engine_binary, *run).await,
        Some(CommandArgs::ValidateOutputs { config }) => validate_outputs(&config),
        Some(CommandArgs::ListAnimations) => list_animations(),
        Some(CommandArgs::Health) => run_query(args.engine_binary, RequestCommand::Health).await,
        Some(CommandArgs::AudioDevices) => {
            run_query(args.engine_binary, RequestCommand::AudioDevices).await
        }
        Some(CommandArgs::AudioStatus) => {
            run_query(args.engine_binary, RequestCommand::AudioStatus).await
        }
        Some(CommandArgs::CalibrateAudio {
            duration_seconds,
            audio_device,
        }) => {
            calibrate_audio(
                args.engine_binary,
                duration_seconds,
                audio_device.as_deref(),
            )
            .await
        }
        Some(CommandArgs::AudioDebug {
            duration_seconds,
            audio_device,
            record,
            label,
            verbose,
        }) => {
            audio_debug(
                args.engine_binary,
                duration_seconds,
                audio_device.as_deref(),
                record.as_deref(),
                label.as_deref(),
                verbose,
                RunArgs::default(),
            )
            .await
        }
        Some(CommandArgs::AnalyzeAudioDebug { paths }) => {
            audio_debug::analyze(&paths).map(|summary| println!("{summary}"))
        }
        Some(CommandArgs::Capabilities) => {
            run_query(args.engine_binary, RequestCommand::RenderCapabilities).await
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("lumistripe-cli: {error}");
            ExitCode::FAILURE
        }
    }
}

fn list_animations() -> Result<(), String> {
    use std::io::{ErrorKind, Write};

    let mut output = String::new();
    for animation in &catalog().animations {
        use std::fmt::Write as _;
        writeln!(output, "{}\t{}", animation.name, animation.display_name)
            .expect("writing animation names to a String cannot fail");
    }
    match std::io::stdout().lock().write_all(output.as_bytes()) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::BrokenPipe => Ok(()),
        Err(error) => Err(format!("write animation list: {error}")),
    }
}

fn validate_outputs(path: &Path) -> Result<(), String> {
    const MAX_CONFIG_BYTES: u64 = 1_048_576;
    use std::io::Read;

    let file = std::fs::File::open(path).map_err(|error| {
        format!(
            "could not open output configuration {}: {error}",
            path.display()
        )
    })?;
    let mut contents = Vec::new();
    file.take(MAX_CONFIG_BYTES + 1)
        .read_to_end(&mut contents)
        .map_err(|error| format!("could not read output configuration: {error}"))?;
    if contents.len() as u64 > MAX_CONFIG_BYTES {
        return Err(format!(
            "output configuration exceeds the {MAX_CONFIG_BYTES}-byte limit"
        ));
    }
    let config: EngineConfig = serde_json::from_slice(&contents)
        .map_err(|error| format!("invalid output configuration JSON: {error}"))?;
    config
        .validate()
        .map_err(|error| format!("invalid output configuration: {error}"))?;
    println!(
        "Valid: {} output(s), {} total pixels",
        config.outputs.len(),
        config.total_pixels()
    );
    Ok(())
}

async fn run_playback(engine_binary: Option<PathBuf>, args: RunArgs) -> Result<(), String> {
    validate_run_args(&args)?;
    let audio_source = resolved_audio_source(&args);
    let engine_config = build_engine_config(&args)?;
    let settings = parse_settings(args.settings.as_deref())?;
    let engine = OwnedEngine::start(engine_binary).await?;
    let client = engine.client.clone();

    let operation = async {
        let profile = resolve_mic_profile(
            &client,
            args.mic_profile.as_deref(),
            args.audio_device.as_deref(),
        )
        .await?;
        let mut audio_config = build_profile_audio_config(
            args.audio_config.as_deref(),
            audio_source,
            profile.as_ref(),
        )?;
        if let Some(target_level) = args.mic_target_level {
            audio_config.target_level = target_level as f32;
        }
        if let Some(noise_floor) = args.mic_noise_floor {
            audio_config.noise_floor = noise_floor as f32;
        }
        audio_config
            .validate()
            .map_err(|error| format!("invalid audio configuration: {error}"))?;
        let mut idle_threshold_scale = args
            .idle_threshold_scale
            .or_else(|| {
                profile
                    .as_ref()
                    .and_then(|profile| profile.idle_threshold_scale)
            })
            .unwrap_or(1.0);

        let calibrate_audio =
            args.auto_calibrate_seconds.is_some() && audio_source == AudioSourceArg::Mic;
        let capture_audio = args.write_mic_profile.is_none() || calibrate_audio;
        if audio_source == AudioSourceArg::Mic && capture_audio {
            start_audio(
                &client,
                audio_source,
                args.audio_device.as_deref(),
                audio_config,
            )
            .await?;
        }
        if let Some(duration) = args.auto_calibrate_seconds.filter(|_| calibrate_audio) {
            let recommendation = collect_calibration(&client, duration).await?;
            audio_config.noise_floor = recommendation.recommended_noise_floor as f32;
            audio_config.target_level = recommendation.recommended_target_level as f32;
            idle_threshold_scale = recommendation.recommended_idle_threshold_scale;
            match client
                .request(RequestCommand::AudioConfigure {
                    config: audio_config,
                })
                .await
                .map_err(engine_error)?
            {
                ResponseResult::AudioConfigured { active: true } => {}
                result => {
                    return Err(format!(
                        "engine returned unexpected audio reconfiguration response: {result:?}"
                    ));
                }
            }
            print_calibration_recommendation(&recommendation);
        } else if args.write_mic_profile.is_none()
            && audio_source != AudioSourceArg::Off
            && audio_source != AudioSourceArg::Mic
        {
            start_audio(&client, audio_source, None, audio_config).await?;
        }

        if let Some(path) = args.write_mic_profile.as_deref() {
            let active_device = if capture_audio {
                match client
                    .request(RequestCommand::AudioStatus)
                    .await
                    .map_err(engine_error)?
                {
                    ResponseResult::AudioStatus { status } => {
                        status.device.map(|device| device.name)
                    }
                    result => {
                        return Err(format!(
                            "engine returned unexpected audio status response: {result:?}"
                        ));
                    }
                }
            } else {
                match list_audio_devices(&client).await {
                    Ok(devices) => select_audio_device(&devices, args.audio_device.as_deref())
                        .map(|device| device.name.clone()),
                    Err(_) => None,
                }
            };
            let profile = MicProfile::from_effective_config(
                &audio_config,
                idle_threshold_scale,
                active_device.as_deref(),
            );
            profile.write(path)?;
            println!("Wrote mic profile: {}", path.display());
            return Ok(false);
        }

        match client
            .configure(engine_config.clone())
            .await
            .map_err(engine_error)?
        {
            ResponseResult::Configured { .. } => {}
            result => {
                return Err(format!(
                    "engine returned unexpected configure response: {result:?}"
                ));
            }
        }
        let scene = build_scene(
            &args,
            &engine_config,
            settings.clone(),
            idle_threshold_scale,
        )?;
        match client
            .update_scene(scene.clone())
            .await
            .map_err(engine_error)?
        {
            ResponseResult::SceneUpdated { .. } => {}
            result => {
                return Err(format!(
                    "engine returned unexpected scene response: {result:?}"
                ));
            }
        }
        let encoder_chip = args
            .encoder_chip
            .as_deref()
            .unwrap_or(args.gpio_chip.as_str());
        let encoder =
            EncoderBackend::open(encoder_chip, encoder1_pins(&args), encoder2_pins(&args))?;
        let mut playback = LivePlayback {
            args,
            engine_config,
            settings,
            scene,
            audio_config,
            audio_source,
            idle_threshold_scale,
            encoder,
        };
        if !playback.args.quiet {
            println!("Playback started; press Ctrl-C to stop.");
        }
        let duration_seconds = playback.args.duration_seconds;
        let report_interval = (playback.args.stats || playback.args.debug_selector)
            .then_some(Duration::from_millis(playback.args.stats_interval_ms));
        wait_for_playback(&client, &mut playback, duration_seconds, report_interval).await?;
        Ok(true)
    }
    .await;

    let _ = client.request(RequestCommand::AudioStop).await;
    let shutdown_result = engine.shutdown().await;
    let _played = operation?;
    shutdown_result?;
    Ok(())
}

async fn run_query(engine_binary: Option<PathBuf>, command: RequestCommand) -> Result<(), String> {
    let engine = OwnedEngine::start(engine_binary).await?;
    let response = engine.client.request(command).await.map_err(engine_error);
    let shutdown = engine.shutdown().await;
    let response = response?;
    shutdown?;
    let encoded = serde_json::to_string_pretty(&response)
        .map_err(|error| format!("encode engine response: {error}"))?;
    println!("{encoded}");
    Ok(())
}

struct LivePlayback {
    args: RunArgs,
    engine_config: EngineConfig,
    settings: Map<String, Value>,
    scene: RenderSceneUpdate,
    audio_config: AudioConfig,
    audio_source: AudioSourceArg,
    idle_threshold_scale: f64,
    encoder: EncoderBackend,
}

async fn wait_for_playback(
    client: &EngineClient,
    playback: &mut LivePlayback,
    duration_seconds: Option<u64>,
    report_interval: Option<Duration>,
) -> Result<(), String> {
    let deadline = duration_seconds.map(|seconds| Instant::now() + Duration::from_secs(seconds));
    let mut next_stats = report_interval.map(|interval| Instant::now() + interval);
    let signal = tokio::signal::ctrl_c();
    tokio::pin!(signal);
    loop {
        let now = Instant::now();
        if deadline.is_some_and(|deadline| now >= deadline) {
            return Ok(());
        }
        let mut waits = Vec::with_capacity(3);
        if let Some(deadline) = deadline {
            waits.push(deadline.saturating_duration_since(now));
        }
        if playback.encoder.is_enabled() {
            waits.push(Duration::from_millis(20));
        }
        if let Some(next_stats) = next_stats {
            waits.push(next_stats.saturating_duration_since(now));
        }
        if let Some(wait) = waits.into_iter().min() {
            tokio::select! {
                result = &mut signal => {
                    result.map_err(|error| format!("wait for Ctrl-C: {error}"))?;
                    return Ok(());
                }
                _ = sleep(wait) => {}
            }
        } else {
            signal
                .await
                .map_err(|error| format!("wait for Ctrl-C: {error}"))?;
            return Ok(());
        }

        if playback.encoder.is_enabled() {
            let events = playback.encoder.read_events()?;
            let mut changed = false;
            let animation_before = playback.args.animation.clone();
            for event in events {
                if apply_encoder_event(&mut playback.args, event) {
                    changed = true;
                }
            }
            if changed {
                if playback.args.animation != animation_before {
                    playback.settings.clear();
                }
                let desired_source = resolved_audio_source(&playback.args);
                if desired_source != playback.audio_source {
                    set_playback_audio_source(client, playback, desired_source).await?;
                }
                let revision = playback.scene.revision.saturating_add(1);
                let mut scene = build_scene(
                    &playback.args,
                    &playback.engine_config,
                    playback.settings.clone(),
                    playback.idle_threshold_scale,
                )?;
                scene.revision = revision;
                scene.timestamp_ns = monotonic_now_ns()?;
                match client
                    .update_scene(scene.clone())
                    .await
                    .map_err(engine_error)?
                {
                    ResponseResult::SceneUpdated { .. } => playback.scene = scene,
                    result => {
                        return Err(format!(
                            "engine returned unexpected encoder scene response: {result:?}"
                        ));
                    }
                }
                if !playback.args.quiet {
                    println!(
                        "Encoder: mode={:?} animation={} brightness={:.2}",
                        playback.args.mode, playback.args.animation, playback.args.brightness
                    );
                }
            }
        }

        if let Some(interval) = report_interval
            && let Some(next_report) = next_stats
            && Instant::now() >= next_report
        {
            if playback.args.stats {
                report_performance(client).await?;
            }
            if playback.args.debug_selector {
                report_selector_debug(client).await?;
            }
            next_stats = Some(Instant::now() + interval);
        }
    }
}

async fn set_playback_audio_source(
    client: &EngineClient,
    playback: &mut LivePlayback,
    source: AudioSourceArg,
) -> Result<(), String> {
    if playback.audio_source != AudioSourceArg::Off {
        let _ = client.request(RequestCommand::AudioStop).await;
    }
    playback.audio_config.microphone_enabled = source == AudioSourceArg::Mic;
    if source != AudioSourceArg::Off {
        start_audio(
            client,
            source,
            playback.args.audio_device.as_deref(),
            playback.audio_config,
        )
        .await?;
    }
    playback.audio_source = source;
    Ok(())
}

fn apply_encoder_event(args: &mut RunArgs, event: EncoderEvent) -> bool {
    match event {
        EncoderEvent::Rotate { encoder: 0, step } => {
            let animations = &catalog().animations;
            if animations.is_empty() {
                return false;
            }
            let index = animations
                .iter()
                .position(|animation| animation.name == args.animation)
                .unwrap_or(0);
            let next = if step >= 0 {
                (index + 1) % animations.len()
            } else {
                (index + animations.len() - 1) % animations.len()
            };
            let changed = args.mode != ModeArg::Static || args.animation != animations[next].name;
            args.mode = ModeArg::Static;
            args.animation.clone_from(&animations[next].name);
            changed
        }
        EncoderEvent::Rotate { encoder: 1, step } => {
            let brightness = (args.brightness + 0.05 * f64::from(step)).clamp(0.0, 1.0);
            if brightness == args.brightness {
                return false;
            }
            args.brightness = brightness;
            true
        }
        EncoderEvent::Press { encoder: 0 } => {
            let next_mode = match args.mode {
                ModeArg::Static => ModeArg::Cycling,
                ModeArg::Cycling => ModeArg::Dynamic,
                ModeArg::Dynamic => ModeArg::Static,
            };
            if next_mode != ModeArg::Static
                && !catalog()
                    .animation(&args.animation)
                    .is_some_and(|animation| animation.automatic)
                && let Some(animation) = catalog()
                    .animations
                    .iter()
                    .find(|animation| animation.automatic)
            {
                args.animation.clone_from(&animation.name);
            }
            let next_source = args
                .audio_source
                .unwrap_or(if next_mode == ModeArg::Dynamic {
                    AudioSourceArg::Mic
                } else {
                    AudioSourceArg::Off
                });
            if next_mode == ModeArg::Dynamic && next_source == AudioSourceArg::Off {
                eprintln!(
                    "Encoder: Dynamic mode requires microphone or demo audio; mode change ignored."
                );
                return false;
            }
            args.mode = next_mode;
            true
        }
        EncoderEvent::Press { .. } | EncoderEvent::Rotate { .. } => false,
    }
}

async fn report_performance(client: &EngineClient) -> Result<(), String> {
    match client.health().await.map_err(engine_error)? {
        ResponseResult::Health {
            output_rate,
            frames_rendered,
            dropped_frames,
            failed_frames,
            scheduler_missed_deadlines,
            frame_latency_p50_ms,
            frame_latency_p95_ms,
            frame_latency_p99_ms,
            queue_latency_ms,
            transmission_ms,
            render_time_ms,
            audio_feature_to_led_p95_ms,
            estimated_power_watts,
            applied_power_scale,
            ..
        } => {
            println!(
                "PERF fps={output_rate:.1} frames={frames_rendered} dropped={dropped_frames} failed={failed_frames} missed_deadlines={scheduler_missed_deadlines} frame_ms[p50/p95/p99]={}/{}/{} queue_ms={} tx_ms={} render_ms={} audio_to_led_p95_ms={} power_w={} power_scale={}",
                metric(frame_latency_p50_ms),
                metric(frame_latency_p95_ms),
                metric(frame_latency_p99_ms),
                metric(queue_latency_ms),
                metric(transmission_ms),
                metric(render_time_ms),
                metric(audio_feature_to_led_p95_ms),
                metric(estimated_power_watts),
                metric(applied_power_scale),
            );
            Ok(())
        }
        result => Err(format!(
            "engine returned unexpected performance response: {result:?}"
        )),
    }
}

async fn report_selector_debug(client: &EngineClient) -> Result<(), String> {
    match client.health().await.map_err(engine_error)? {
        ResponseResult::Health {
            selected_animations,
            cycling_active,
            dynamic_gate_states,
            selector_diagnostics,
            beat_clocks,
            effect_schedulers,
            ..
        } => {
            println!(
                "SELECT animation={selected_animations:?} cycling={cycling_active:?} gate={dynamic_gate_states:?} selector={selector_diagnostics:?} beat_clocks={beat_clocks:?} effects={effect_schedulers:?}"
            );
            Ok(())
        }
        result => Err(format!(
            "engine returned unexpected selector response: {result:?}"
        )),
    }
}

fn metric(value: Option<f64>) -> String {
    value.map_or_else(|| "-".to_owned(), |value| format!("{value:.2}"))
}

async fn calibrate_audio(
    engine_binary: Option<PathBuf>,
    duration_seconds: f64,
    device: Option<&str>,
) -> Result<(), String> {
    if !duration_seconds.is_finite() || !(0.1..=600.0).contains(&duration_seconds) {
        return Err("--duration-seconds must be between 0.1 and 600".to_owned());
    }
    if device.is_some_and(str::is_empty) {
        return Err("--audio-device cannot be empty".to_owned());
    }

    let engine = OwnedEngine::start(engine_binary).await?;
    let client = engine.client.clone();
    let operation = async {
        let config = build_audio_config(None, AudioSourceArg::Mic)?;
        start_audio(&client, AudioSourceArg::Mic, device, config).await?;

        let started = Instant::now();
        let deadline = started + Duration::from_secs_f64(duration_seconds);
        let mut last_sequence = 0;
        let mut rms = Vec::new();
        let mut energy = Vec::new();
        while Instant::now() < deadline {
            match client
                .request(RequestCommand::AudioStatus)
                .await
                .map_err(engine_error)?
            {
                ResponseResult::AudioStatus { status } => {
                    if status.frame.fresh && status.frame.sequence > last_sequence {
                        last_sequence = status.frame.sequence;
                        rms.push(f64::from(status.frame.rms));
                        energy.push(f64::from(status.features.energy));
                    }
                }
                ResponseResult::Error { message, .. } => return Err(message),
                result => {
                    return Err(format!(
                        "engine returned unexpected audio status response: {result:?}"
                    ));
                }
            }
            sleep(Duration::from_millis(20)).await;
        }

        if rms.len() < 10 {
            return Err("Not enough fresh input; check the microphone and retry.".to_owned());
        }
        let recommendation =
            recommend_audio_calibration(&rms, &energy, started.elapsed().as_secs_f64())
                .map_err(|error| format!("audio calibration failed: {error}"))?;
        println!("Audio calibration complete.");
        println!("  samples: {}", recommendation.samples);
        println!("  measured floor: {:0.4}", recommendation.measured_floor);
        println!("  measured peak: {:0.4}", recommendation.measured_peak);
        println!("Recommended shared AudioConfig values:");
        println!(
            "  --audio-config '{{\"noise_floor\":{:0.4},\"target_level\":{:0.3}}}'",
            recommendation.recommended_noise_floor, recommendation.recommended_target_level
        );
        println!(
            "  idle threshold scale: {:0.2}",
            recommendation.recommended_idle_threshold_scale
        );
        Ok(())
    }
    .await;

    let _ = client.request(RequestCommand::AudioStop).await;
    let shutdown_result = engine.shutdown().await;
    operation?;
    shutdown_result?;
    Ok(())
}

async fn collect_calibration(
    client: &EngineClient,
    duration_seconds: f64,
) -> Result<AudioCalibrationRecommendation, String> {
    let started = Instant::now();
    let deadline = started + Duration::from_secs_f64(duration_seconds);
    let mut last_sequence = 0;
    let mut rms = Vec::new();
    let mut energy = Vec::new();
    while Instant::now() < deadline {
        match client
            .request(RequestCommand::AudioStatus)
            .await
            .map_err(engine_error)?
        {
            ResponseResult::AudioStatus { status } => {
                if status.frame.fresh && status.frame.sequence > last_sequence {
                    last_sequence = status.frame.sequence;
                    rms.push(f64::from(status.frame.rms));
                    energy.push(f64::from(status.features.energy));
                }
            }
            ResponseResult::Error { message, .. } => return Err(message),
            result => {
                return Err(format!(
                    "engine returned unexpected audio status response: {result:?}"
                ));
            }
        }
        sleep(Duration::from_millis(20)).await;
    }
    if rms.len() < 10 {
        return Err("Not enough fresh input; check the microphone and retry.".to_owned());
    }
    recommend_audio_calibration(&rms, &energy, started.elapsed().as_secs_f64())
        .map_err(|error| format!("audio calibration failed: {error}"))
}

fn print_calibration_recommendation(recommendation: &AudioCalibrationRecommendation) {
    println!("Automatic microphone calibration complete.");
    println!("  samples: {}", recommendation.samples);
    println!("  measured floor: {:0.4}", recommendation.measured_floor);
    println!("  measured peak: {:0.4}", recommendation.measured_peak);
    println!(
        "  recommended noise floor: {:0.4}",
        recommendation.recommended_noise_floor
    );
    println!(
        "  recommended target level: {:0.3}",
        recommendation.recommended_target_level
    );
    println!(
        "  recommended idle threshold scale: {:0.2}",
        recommendation.recommended_idle_threshold_scale
    );
}

async fn resolve_mic_profile(
    client: &EngineClient,
    identifier: Option<&str>,
    device_selector: Option<&str>,
) -> Result<Option<MicProfile>, String> {
    let Some(identifier) = identifier else {
        return Ok(None);
    };
    if identifier != "auto" {
        return MicProfile::load(identifier).map(Some);
    }
    let devices = list_audio_devices(client).await?;
    let selected = select_audio_device(&devices, device_selector)
        .ok_or_else(|| "no audio input device matched --mic-profile auto".to_owned())?;
    MicProfile::matching_device(&selected.name)
        .map(Some)
        .ok_or_else(|| {
            format!(
                "no built-in mic profile matched audio device {}",
                selected.name
            )
        })
}

async fn list_audio_devices(client: &EngineClient) -> Result<Vec<InputAudioDevice>, String> {
    match client
        .request(RequestCommand::AudioDevices)
        .await
        .map_err(engine_error)?
    {
        ResponseResult::AudioDevices { devices } => Ok(devices),
        ResponseResult::Error { message, .. } => Err(message),
        result => Err(format!(
            "engine returned unexpected audio device response: {result:?}"
        )),
    }
}

fn select_audio_device<'a>(
    devices: &'a [InputAudioDevice],
    selector: Option<&str>,
) -> Option<&'a InputAudioDevice> {
    if let Some(selector) = selector {
        let normalized = selector.to_lowercase();
        devices.iter().find(|device| {
            device.selector == selector
                || device.name.eq_ignore_ascii_case(selector)
                || device.name.to_lowercase().contains(&normalized)
        })
    } else {
        devices
            .iter()
            .find(|device| device.is_default)
            .or_else(|| devices.first())
    }
}

async fn audio_debug(
    engine_binary: Option<PathBuf>,
    duration_seconds: Option<f64>,
    device: Option<&str>,
    record_path: Option<&Path>,
    label: Option<&str>,
    verbose: bool,
    mut debug_args: RunArgs,
) -> Result<(), String> {
    if duration_seconds
        .is_some_and(|duration| !duration.is_finite() || !(0.1..=3600.0).contains(&duration))
    {
        return Err("--duration-seconds must be between 0.1 and 3600".to_owned());
    }
    if device.is_some_and(str::is_empty) {
        return Err("--audio-device cannot be empty".to_owned());
    }
    if label.is_some() && record_path.is_none() {
        return Err("--label requires --record".to_owned());
    }
    let mut recorder = record_path
        .map(|path| audio_debug::Recorder::create(path, label))
        .transpose()?;

    let engine = OwnedEngine::start(engine_binary).await?;
    let client = engine.client.clone();
    let operation = async {
        debug_args.backend = BackendArg::Mock;
        debug_args.mode = ModeArg::Dynamic;
        debug_args.audio_source = Some(AudioSourceArg::Mic);
        if let Some(device) = device {
            debug_args.audio_device = Some(device.to_owned());
        }
        validate_run_args(&debug_args)?;
        let engine_config = build_engine_config(&debug_args)?;
        match client
            .configure(engine_config.clone())
            .await
            .map_err(engine_error)?
        {
            ResponseResult::Configured { .. } => {}
            result => {
                return Err(format!(
                    "engine returned unexpected audio-debug configure response: {result:?}"
                ));
            }
        }
        let profile = resolve_mic_profile(
            &client,
            debug_args.mic_profile.as_deref(),
            debug_args.audio_device.as_deref(),
        )
        .await?;
        let mut audio_config = build_profile_audio_config(
            debug_args.audio_config.as_deref(),
            AudioSourceArg::Mic,
            profile.as_ref(),
        )?;
        if let Some(target_level) = debug_args.mic_target_level {
            audio_config.target_level = target_level as f32;
        }
        if let Some(noise_floor) = debug_args.mic_noise_floor {
            audio_config.noise_floor = noise_floor as f32;
        }
        audio_config
            .validate()
            .map_err(|error| format!("invalid audio configuration: {error}"))?;
        let mut idle_threshold_scale = debug_args
            .idle_threshold_scale
            .or_else(|| profile.as_ref().and_then(|profile| profile.idle_threshold_scale))
            .unwrap_or(1.0);
        start_audio(
            &client,
            AudioSourceArg::Mic,
            debug_args.audio_device.as_deref(),
            audio_config.clone(),
        )
        .await?;
        if let Some(duration) = debug_args.auto_calibrate_seconds {
            let recommendation = collect_calibration(&client, duration).await?;
            audio_config.noise_floor = recommendation.recommended_noise_floor as f32;
            audio_config.target_level = recommendation.recommended_target_level as f32;
            idle_threshold_scale = recommendation.recommended_idle_threshold_scale;
            match client
                .request(RequestCommand::AudioConfigure {
                    config: audio_config,
                })
                .await
                .map_err(engine_error)?
            {
                ResponseResult::AudioConfigured { active: true } => {}
                result => {
                    return Err(format!(
                        "engine returned unexpected audio reconfiguration response: {result:?}"
                    ));
                }
            }
        }
        debug_args.idle_threshold_scale = Some(idle_threshold_scale);
        let settings = parse_settings(debug_args.settings.as_deref())?;
        let scene = build_scene(&debug_args, &engine_config, settings, debug_args.brightness)?;
        match client.update_scene(scene).await.map_err(engine_error)? {
            ResponseResult::SceneUpdated { .. } => {}
            result => {
                return Err(format!(
                    "engine returned unexpected audio-debug scene response: {result:?}"
                ));
            }
        }
        let started = Instant::now();
        let mut last_sequence = 0;
        let mut last_error = None;
        loop {
            let elapsed = started.elapsed();
            if duration_seconds.is_some_and(|duration| elapsed.as_secs_f64() >= duration) {
                break;
            }
            let status = match client
                .request(RequestCommand::AudioStatus)
                .await
                .map_err(engine_error)?
            {
                ResponseResult::AudioStatus { status } => status,
                ResponseResult::Error { message, .. } => return Err(message),
                result => {
                    return Err(format!(
                        "engine returned unexpected audio status response: {result:?}"
                    ));
                }
            };
            if status.last_error != last_error {
                if let Some(error) = status.last_error.as_deref() {
                    eprintln!("audio capture: {error}");
                }
                last_error.clone_from(&status.last_error);
            }
            if status.frame.fresh && status.frame.sequence > last_sequence {
                last_sequence = status.frame.sequence;
                let health = client.health().await.map_err(engine_error)?;
                let source = status
                    .device
                    .as_ref()
                    .map(|device| device.name.as_str())
                    .or(device)
                    .unwrap_or("default microphone");
                let row = audio_debug::debug_row(
                    &status,
                    &health,
                    elapsed.as_secs_f64(),
                    source,
                    monotonic_now_ns()?,
                );
                if verbose {
                    let encoded = serde_json::to_string(&row)
                        .map_err(|error| format!("encode verbose audio debug row: {error}"))?;
                    println!("{encoded}");
                } else {
                    println!(
                        "t={:0.2}s RMS={:0.3} ENERGY={:0.3} BPM={:0.0} BEAT={} SILENCE={} GATE={} ANIMATION={}",
                        elapsed.as_secs_f64(),
                        status.frame.rms,
                        status.features.energy,
                        status.features.bpm,
                        status.frame.beat,
                        status.features.silence,
                        match &health {
                            ResponseResult::Health {
                                dynamic_gate_states,
                                ..
                            } => dynamic_gate_states
                                .first()
                                .map(String::as_str)
                                .unwrap_or("unknown"),
                            _ => "unknown",
                        },
                        match &health {
                            ResponseResult::Health {
                                selected_animations,
                                ..
                            } => selected_animations
                                .first()
                                .map(String::as_str)
                                .unwrap_or("-"),
                            _ => "-",
                        },
                    );
                }
                if let Some(recorder) = recorder.as_mut() {
                    recorder.write(row)?;
                }
            }
            tokio::select! {
                result = tokio::signal::ctrl_c() => {
                    result.map_err(|error| format!("wait for Ctrl-C: {error}"))?;
                    break;
                }
                _ = sleep(Duration::from_millis(100)) => {}
            }
        }
        Ok(())
    }
    .await;

    let _ = client.request(RequestCommand::AudioStop).await;
    let shutdown_result = engine.shutdown().await;
    operation?;
    shutdown_result?;
    Ok(())
}

struct OwnedEngine {
    _directory: TempDir,
    client: EngineClient,
    child: Child,
}

impl OwnedEngine {
    async fn start(engine_binary: Option<PathBuf>) -> Result<Self, String> {
        let binary = engine_binary.unwrap_or_else(default_engine_binary);
        if !binary.is_file() {
            return Err(format!(
                "Rust engine binary was not found at {}; pass --engine-binary or set LUMISTRIPE_ENGINE_BINARY",
                binary.display()
            ));
        }
        let directory =
            TempDir::new().map_err(|error| format!("create engine socket directory: {error}"))?;
        let socket = directory.path().join("engine.sock");
        let client = EngineClient::new(&socket, Duration::from_secs(2));
        let mut child = Command::new(&binary)
            .arg("--socket")
            .arg(&socket)
            .arg("--owner-pid")
            .arg(std::process::id().to_string())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| format!("start Rust engine {}: {error}", binary.display()))?;
        wait_for_engine(&client, &mut child).await?;
        Ok(Self {
            _directory: directory,
            client,
            child,
        })
    }

    async fn shutdown(mut self) -> Result<(), String> {
        let shutdown_response = self.client.shutdown().await;
        if !matches!(shutdown_response, Ok(ResponseResult::Shutdown))
            && self
                .child
                .try_wait()
                .map_err(|error| format!("check Rust engine shutdown: {error}"))?
                .is_none()
        {
            let _ = self.child.start_kill();
        }
        match tokio::time::timeout(Duration::from_secs(5), self.child.wait()).await {
            Ok(Ok(status)) if status.success() => Ok(()),
            Ok(Ok(status)) => Err(format!("Rust engine exited during shutdown with {status}")),
            Ok(Err(error)) => Err(format!("wait for Rust engine shutdown: {error}")),
            Err(_) => {
                let _ = self.child.start_kill();
                let _ = self.child.wait().await;
                Err("Rust engine did not stop within five seconds".to_owned())
            }
        }
    }
}

async fn wait_for_engine(client: &EngineClient, child: &mut Child) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("check Rust engine startup: {error}"))?
        {
            return Err(format!("Rust engine exited during startup with {status}"));
        }
        if matches!(client.health().await, Ok(ResponseResult::Health { .. })) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("Rust engine did not become ready within five seconds".to_owned());
        }
        sleep(Duration::from_millis(50)).await;
    }
}

async fn start_audio(
    client: &EngineClient,
    source: AudioSourceArg,
    device: Option<&str>,
    config: AudioConfig,
) -> Result<(), String> {
    let selector = match source {
        AudioSourceArg::Off => return Ok(()),
        AudioSourceArg::Mic => device,
        AudioSourceArg::Demo => Some("demo"),
    };
    match client
        .request(RequestCommand::AudioStart {
            selector: selector.map(ToOwned::to_owned),
            config,
        })
        .await
        .map_err(engine_error)?
    {
        ResponseResult::AudioConfigured { active: true } => Ok(()),
        ResponseResult::AudioConfigured { active: false } => {
            Err("engine did not activate the microphone".to_owned())
        }
        result => Err(format!(
            "engine returned unexpected audio response: {result:?}"
        )),
    }
}

fn resolved_audio_source(args: &RunArgs) -> AudioSourceArg {
    args.audio_source
        .unwrap_or(if args.mode == ModeArg::Dynamic {
            AudioSourceArg::Mic
        } else {
            AudioSourceArg::Off
        })
}

fn build_audio_config(
    encoded: Option<&str>,
    source: AudioSourceArg,
) -> Result<AudioConfig, String> {
    build_profile_audio_config(encoded, source, None)
}

fn build_profile_audio_config(
    encoded: Option<&str>,
    source: AudioSourceArg,
    profile: Option<&MicProfile>,
) -> Result<AudioConfig, String> {
    const MAX_AUDIO_CONFIG_BYTES: usize = 65_536;
    let mut config = AudioConfig::default();
    if let Some(profile) = profile {
        profile.apply(&mut config);
    }
    config = match encoded {
        Some(encoded) if encoded.len() > MAX_AUDIO_CONFIG_BYTES => {
            return Err(format!(
                "--audio-config exceeds the {MAX_AUDIO_CONFIG_BYTES}-byte limit"
            ));
        }
        Some(encoded) => {
            let value = serde_json::from_str::<Value>(encoded)
                .map_err(|error| format!("--audio-config must be valid JSON: {error}"))?;
            let overrides = value
                .as_object()
                .ok_or_else(|| "--audio-config must be a JSON object".to_owned())?;
            let defaults = serde_json::to_value(config)
                .map_err(|error| format!("encode default audio configuration: {error}"))?;
            let known_fields = defaults
                .as_object()
                .expect("AudioConfig serializes as an object");
            if let Some(unknown) = overrides
                .keys()
                .find(|key| !known_fields.contains_key(key.as_str()))
            {
                return Err(format!("unknown --audio-config field: {unknown}"));
            }
            let mut merged = known_fields.clone();
            merged.extend(overrides.clone());
            serde_json::from_value::<AudioConfig>(Value::Object(merged)).map_err(|error| {
                format!("--audio-config contains an invalid AudioConfig value: {error}")
            })?
        }
        None => config,
    };
    config.microphone_enabled = source == AudioSourceArg::Mic;
    config
        .validate()
        .map_err(|error| format!("invalid audio configuration: {error}"))?;
    Ok(config)
}

fn validate_run_args(args: &RunArgs) -> Result<(), String> {
    if !(1..=16_384).contains(&args.pixels) {
        return Err("--pixels must be between 1 and 16384".to_owned());
    }
    if args
        .pixels_2
        .is_some_and(|pixels| !(1..=16_384).contains(&pixels))
    {
        return Err("--pixels-2 must be between 1 and 16384".to_owned());
    }
    if !(1..=240).contains(&args.fps) {
        return Err("--fps must be between 1 and 240".to_owned());
    }
    if !(100..=60_000).contains(&args.stats_interval_ms) {
        return Err("--stats-interval-ms must be between 100 and 60000".to_owned());
    }
    if !args.brightness.is_finite() || !(0.0..=1.0).contains(&args.brightness) {
        return Err("--brightness must be between 0 and 1".to_owned());
    }
    if !args.cycle_interval_seconds.is_finite() || args.cycle_interval_seconds <= 0.0 {
        return Err("--cycle-interval-seconds must be positive and finite".to_owned());
    }
    if args
        .mic_target_level
        .is_some_and(|value| !value.is_finite() || value <= 0.0)
    {
        return Err("--mic-target-level must be positive and finite".to_owned());
    }
    if args
        .mic_noise_floor
        .is_some_and(|value| !value.is_finite() || value < 0.0)
    {
        return Err("--mic-noise-floor must be nonnegative and finite".to_owned());
    }
    if args.idle_enter_frames == 0 {
        return Err("--idle-enter-frames must be positive".to_owned());
    }
    if !args.music_activation_delay_seconds.is_finite() || args.music_activation_delay_seconds < 0.0
    {
        return Err("--music-activation-delay-seconds must be nonnegative and finite".to_owned());
    }
    if !args.dynamic_idle_brightness.is_finite()
        || !(0.0..=1.0).contains(&args.dynamic_idle_brightness)
    {
        return Err("--dynamic-idle-brightness must be between 0 and 1".to_owned());
    }
    if !args.dynamic_min_duration_seconds.is_finite()
        || args.dynamic_min_duration_seconds <= 0.0
        || !args.dynamic_max_duration_seconds.is_finite()
        || args.dynamic_max_duration_seconds < args.dynamic_min_duration_seconds
    {
        return Err("Dynamic duration limits must be finite, positive, and max >= min".to_owned());
    }
    if !args.dynamic_switch_cooldown_seconds.is_finite()
        || args.dynamic_switch_cooldown_seconds <= 0.0
        || !args.dynamic_drop_cooldown_seconds.is_finite()
        || args.dynamic_drop_cooldown_seconds <= 0.0
    {
        return Err("Dynamic cooldowns must be positive and finite".to_owned());
    }
    if !args.dynamic_randomness.is_finite() || args.dynamic_randomness < 0.0 {
        return Err("--dynamic-randomness must be nonnegative and finite".to_owned());
    }
    if args.dynamic_history_size == 0 || args.dynamic_history_size > 256 {
        return Err("--dynamic-history-size must be between 1 and 256".to_owned());
    }
    if args
        .power_budget_watts
        .is_some_and(|watts| !watts.is_finite() || watts <= 0.0)
    {
        return Err("--power-budget-watts must be positive and finite".to_owned());
    }
    if args.audio_device.as_deref().is_some_and(str::is_empty) {
        return Err("--audio-device cannot be empty".to_owned());
    }
    let source = resolved_audio_source(args);
    let writing_mic_profile = args.write_mic_profile.is_some();
    if args.audio_device.is_some() && source != AudioSourceArg::Mic && !writing_mic_profile {
        return Err("--audio-device requires --audio-source mic".to_owned());
    }
    if args.mode == ModeArg::Dynamic && source == AudioSourceArg::Off {
        return Err("Dynamic mode requires --audio-source mic or demo".to_owned());
    }
    if args.mic_profile.is_some() && source != AudioSourceArg::Mic && !writing_mic_profile {
        return Err("--mic-profile requires --audio-source mic".to_owned());
    }
    if let Some(duration) = args.auto_calibrate_seconds {
        if !duration.is_finite() || !(0.1..=600.0).contains(&duration) {
            return Err("--auto-calibrate-seconds must be between 0.1 and 600".to_owned());
        }
        if source != AudioSourceArg::Mic && !writing_mic_profile {
            return Err("--auto-calibrate-seconds requires --audio-source mic".to_owned());
        }
    }
    if args
        .idle_threshold_scale
        .is_some_and(|scale| !scale.is_finite() || !(0.01..=10.0).contains(&scale))
    {
        return Err("--idle-threshold-scale must be between 0.01 and 10".to_owned());
    }
    if args.encoder_chip.as_deref().is_some_and(str::is_empty) {
        return Err("--encoder-chip cannot be empty".to_owned());
    }
    for (label, pins) in [
        (
            "encoder1",
            [args.encoder1_a, args.encoder1_b, args.encoder1_button],
        ),
        (
            "encoder2",
            [args.encoder2_a, args.encoder2_b, args.encoder2_button],
        ),
    ] {
        let configured = pins.iter().filter(|pin| pin.is_some()).count();
        if configured != 0 && configured != pins.len() {
            return Err(format!(
                "{label} requires --{label}-a, --{label}-b, and --{label}-button"
            ));
        }
    }
    let encoder_pins = [encoder1_pins(args), encoder2_pins(args)]
        .into_iter()
        .flatten()
        .flat_map(|pins| [pins.a, pins.b, pins.button])
        .collect::<Vec<_>>();
    if encoder_pins.iter().copied().collect::<HashSet<_>>().len() != encoder_pins.len() {
        return Err("GPIO encoder pins must be unique".to_owned());
    }
    Ok(())
}

fn encoder1_pins(args: &RunArgs) -> Option<EncoderPins> {
    Some(EncoderPins {
        a: args.encoder1_a?,
        b: args.encoder1_b?,
        button: args.encoder1_button?,
    })
}

fn encoder2_pins(args: &RunArgs) -> Option<EncoderPins> {
    Some(EncoderPins {
        a: args.encoder2_a?,
        b: args.encoder2_b?,
        button: args.encoder2_button?,
    })
}

fn build_engine_config(args: &RunArgs) -> Result<EngineConfig, String> {
    if args.backend != BackendArg::Spi
        && (args.spi_device_2.is_some() || args.spi_speed_hz_2.is_some())
    {
        return Err("secondary SPI options require --backend spi".to_owned());
    }
    if args.backend != BackendArg::Gpio
        && (args.gpio_chip_2.is_some() || args.data_pin_2.is_some() || args.clock_pin_2.is_some())
    {
        return Err("secondary GPIO options require --backend gpio".to_owned());
    }
    let backend = |index: usize| -> Result<OutputBackend, String> {
        Ok(match args.backend {
            BackendArg::Mock => OutputBackend::Mock,
            BackendArg::MockSpi => OutputBackend::MockSpi,
            BackendArg::Spi => {
                let device = if index == 0 {
                    args.spi_device.clone()
                } else {
                    args.spi_device_2.clone().ok_or_else(|| {
                        "--pixels-2 with --backend spi requires --spi-device-2".to_owned()
                    })?
                };
                let speed_hz = if index == 0 {
                    args.spi_speed_hz
                } else {
                    args.spi_speed_hz_2.unwrap_or(args.spi_speed_hz)
                };
                OutputBackend::Spi { device, speed_hz }
            }
            BackendArg::Gpio => {
                let (chip, data_pin, clock_pin) = if index == 0 {
                    (args.gpio_chip.clone(), args.data_pin, args.clock_pin)
                } else {
                    let data_pin = args.data_pin_2.ok_or_else(|| {
                        "--pixels-2 with --backend gpio requires --data-pin-2 and --clock-pin-2"
                            .to_owned()
                    })?;
                    let clock_pin = args.clock_pin_2.ok_or_else(|| {
                        "--pixels-2 with --backend gpio requires --data-pin-2 and --clock-pin-2"
                            .to_owned()
                    })?;
                    (
                        args.gpio_chip_2
                            .clone()
                            .unwrap_or_else(|| args.gpio_chip.clone()),
                        data_pin,
                        clock_pin,
                    )
                };
                OutputBackend::Gpio {
                    chip,
                    data_pin,
                    clock_pin,
                }
            }
        })
    };

    if args.spi_speed_hz_2.is_some() && args.spi_device_2.is_none() {
        return Err("--spi-speed-2 requires --spi-device-2".to_owned());
    }
    let secondary_requested = args.pixels_2.is_some()
        || (args.backend == BackendArg::Spi && args.spi_device_2.is_some())
        || (args.backend == BackendArg::Gpio
            && (args.data_pin_2.is_some()
                || args.clock_pin_2.is_some()
                || args.gpio_chip_2.is_some()));
    let mut outputs = vec![OutputConfig {
        id: "main".to_owned(),
        pixel_count: args.pixels,
        reversed: false,
        backend: backend(0)?,
    }];
    if secondary_requested {
        outputs.push(OutputConfig {
            id: "secondary".to_owned(),
            pixel_count: args.pixels_2.unwrap_or(args.pixels),
            reversed: false,
            backend: backend(1)?,
        });
    }
    EngineConfig::new(outputs).map_err(|error| format!("invalid output configuration: {error}"))
}

fn parse_settings(encoded: Option<&str>) -> Result<Map<String, Value>, String> {
    const MAX_SETTINGS_BYTES: usize = 65_536;
    let Some(encoded) = encoded else {
        return Ok(Map::new());
    };
    if encoded.len() > MAX_SETTINGS_BYTES {
        return Err(format!(
            "--settings exceeds the {MAX_SETTINGS_BYTES}-byte limit"
        ));
    }
    let value: Value = serde_json::from_str(encoded)
        .map_err(|error| format!("--settings must be a JSON object: {error}"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| "--settings must be a JSON object".to_owned())
}

fn build_scene(
    args: &RunArgs,
    config: &EngineConfig,
    settings: Map<String, Value>,
    idle_threshold_scale: f64,
) -> Result<RenderSceneUpdate, String> {
    let selected = catalog()
        .animation(&args.animation)
        .ok_or_else(|| format!("unknown animation: {}", args.animation))?;
    if args.mode != ModeArg::Static && !selected.automatic {
        return Err(format!(
            "animation {} is not available to automatic playback",
            args.animation
        ));
    }
    let selected_settings = normalized_settings(selected, &settings)?;
    let mut outputs = Vec::with_capacity(config.outputs.len());
    for _ in &config.outputs {
        let cycling = match args.mode {
            ModeArg::Static => None,
            ModeArg::Cycling | ModeArg::Dynamic => Some(build_cycling(
                args,
                selected,
                &selected_settings,
                idle_threshold_scale,
            )?),
        };
        outputs.push(RenderOutput {
            animation: selected.name.clone(),
            settings: selected_settings.clone(),
            brightness: Some(args.brightness),
            cycling,
            ..RenderOutput::default()
        });
    }
    Ok(RenderSceneUpdate {
        revision: 1,
        timestamp_ns: monotonic_now_ns()?,
        brightness: 1.0,
        power: RenderPowerConfig {
            enabled: args.power_budget_watts.is_some(),
            budget_watts: args.power_budget_watts,
        },
        outputs,
        scheduler: RenderSchedulerConfig { max_fps: args.fps },
    })
}

fn normalized_settings(
    animation: &AnimationDefinition,
    values: &Map<String, Value>,
) -> Result<Map<String, Value>, String> {
    let normalized = animation
        .normalize_settings(values, true)
        .map_err(|error| format!("invalid settings for {}: {error}", animation.name))?;
    Ok(normalized.into_iter().collect())
}

fn build_cycling(
    args: &RunArgs,
    selected: &AnimationDefinition,
    settings: &Map<String, Value>,
    idle_threshold_scale: f64,
) -> Result<RenderCycling, String> {
    let mut choices = Vec::new();
    for animation in catalog()
        .animations
        .iter()
        .filter(|animation| animation.automatic)
    {
        choices.push(RenderAnimationChoice {
            animation: animation.name.clone(),
            settings: if animation.name == selected.name {
                settings.clone()
            } else {
                normalized_settings(animation, &Map::new())?
            },
        });
    }
    choices.sort_by_key(|choice| choice.animation != selected.name);
    let dynamic = args.mode == ModeArg::Dynamic;
    let activity = if dynamic {
        let mut activity = RenderMusicActivityConfig::default();
        activity.energy_threshold *= idle_threshold_scale;
        activity.onset_threshold *= idle_threshold_scale;
        activity.beat_density_threshold *= idle_threshold_scale;
        activity.brightness_threshold *= idle_threshold_scale;
        Some(activity)
    } else {
        None
    };
    Ok(RenderCycling {
        order: match args.cycle_order {
            CycleOrderArg::Sequential => RenderCycleOrder::Sequential,
            CycleOrderArg::Shuffle => RenderCycleOrder::Shuffle,
        },
        timing: match args.cycle_timing {
            CycleTimingArg::PerAnimation => RenderCycleTiming::PerAnimation,
            CycleTimingArg::Fixed => RenderCycleTiming::Fixed,
        },
        interval_ns: (args.cycle_interval_seconds * 1_000_000_000.0).round() as u64,
        seed: None,
        choices,
        transition_duration_ns: 300_000_000,
        transition_mode: "crossfade".to_owned(),
        selector: dynamic.then_some(RenderDynamicSelector {
            min_duration_ns: (args.dynamic_min_duration_seconds * 1_000_000_000.0).round() as u64,
            max_duration_ns: (args.dynamic_max_duration_seconds * 1_000_000_000.0).round() as u64,
            switch_cooldown_ns: (args.dynamic_switch_cooldown_seconds * 1_000_000_000.0).round()
                as u64,
            drop_cooldown_ns: (args.dynamic_drop_cooldown_seconds * 1_000_000_000.0).round() as u64,
            randomness: args.dynamic_randomness,
            history_size: args.dynamic_history_size,
            seed: args.dynamic_seed.map(i64::unsigned_abs),
            switch_margin: 0.12,
            switch_quantization: RenderSwitchQuantization::Off,
            bars_per_phrase: 8,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: activity.map(|mut activity| {
                activity.idle_enter_frames = args.idle_enter_frames;
                activity.activation_delay_ns =
                    (args.music_activation_delay_seconds * 1_000_000_000.0).round() as u64;
                activity
            }),
        }),
        dynamic_idle: dynamic.then_some(RenderDynamicIdle {
            mode: RenderDynamicIdleMode::Solid,
            animation: None,
            settings: Map::new(),
            color: [32, 96, 255],
            brightness: args.dynamic_idle_brightness,
        }),
        sequence: None,
        effect_scheduler: dynamic.then(|| RenderEffectSchedulerConfig {
            beat_confidence_threshold: 0.55,
            onset_threshold: 0.65,
            rhythmic_cooldown_ns: 200_000_000,
            accent_cooldown_ns: 1_250_000_000,
            max_active_effects: 2,
            max_overlay_strength: 0.75,
            subdivisions_per_beat: 1,
            clock_confidence_threshold: 0.65,
            seed: None,
            sync_offset_ns: 0,
            dynamic_response: 0.65,
            choices: catalog()
                .effects
                .iter()
                .map(|effect| RenderEffectChoice {
                    name: effect.name.clone(),
                    settings: Map::new(),
                })
                .collect(),
        }),
    })
}

fn monotonic_now_ns() -> Result<u64, String> {
    let mut value = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // CLOCK_MONOTONIC is shared by the CLI and its child engine on supported Linux targets.
    let result = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut value) };
    if result != 0 || value.tv_sec < 0 || value.tv_nsec < 0 {
        return Err(format!(
            "could not read monotonic clock: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok((value.tv_sec as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(value.tv_nsec as u64))
}

fn default_engine_binary() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join("lumistripe-engine")))
        .unwrap_or_else(|| PathBuf::from("lumistripe-engine"))
}

fn engine_error(error: EngineClientError) -> String {
    format!("engine request failed: {error}")
}

#[cfg(test)]
mod tests {
    use super::{
        Args, AudioSourceArg, BackendArg, CommandArgs, CycleOrderArg, CycleTimingArg, LegacyAction,
        ModeArg, RenderCycleOrder, RenderCycleTiming, RenderDynamicIdleMode, RunArgs,
        apply_encoder_event, build_audio_config, build_engine_config, build_profile_audio_config,
        build_scene, legacy_action, legacy_audio_debug_label, parse_settings,
        resolved_audio_source, validate_run_args,
    };
    use crate::encoder::EncoderEvent;
    use crate::mic_profile::MicProfile;
    use lumistripe_engine_config::OutputBackend;
    use std::path::PathBuf;

    fn args() -> RunArgs {
        RunArgs {
            backend: BackendArg::Mock,
            duration_seconds: Some(1),
            ..RunArgs::default()
        }
    }

    #[test]
    fn mock_configuration_supports_two_outputs() {
        let mut options = args();
        options.pixels_2 = Some(32);
        let config = build_engine_config(&options).unwrap();
        assert_eq!(config.pixel_counts(), [80, 32]);
        assert!(
            config
                .outputs
                .iter()
                .all(|output| output.backend == OutputBackend::Mock)
        );
    }

    #[test]
    fn legacy_secondary_spi_output_mirrors_primary_pixel_count() {
        let mut options = args();
        options.backend = BackendArg::Spi;
        options.spi_device_2 = Some("/dev/spidev0.1".to_owned());

        let config = build_engine_config(&options).unwrap();

        assert_eq!(config.pixel_counts(), [80, 80]);
        assert!(matches!(
            &config.outputs[1].backend,
            OutputBackend::Spi { device, speed_hz }
                if device == "/dev/spidev0.1" && *speed_hz == 1_000_000
        ));
    }

    #[test]
    fn legacy_secondary_gpio_output_mirrors_primary_pixel_count() {
        let mut options = args();
        options.backend = BackendArg::Gpio;
        options.data_pin_2 = Some(20);
        options.clock_pin_2 = Some(21);

        let config = build_engine_config(&options).unwrap();

        assert_eq!(config.pixel_counts(), [80, 80]);
        assert!(matches!(
            &config.outputs[1].backend,
            OutputBackend::Gpio { chip, data_pin, clock_pin }
                if chip == "/dev/gpiochip0" && *data_pin == 20 && *clock_pin == 21
        ));
    }

    #[test]
    fn clap_keeps_spi_default_and_accepts_legacy_output_and_cycle_flags() {
        use clap::Parser;

        let default = Args::try_parse_from(["lumistripe", "run"]).unwrap();
        let Some(CommandArgs::Run(default_run)) = default.command else {
            panic!("expected run command");
        };
        assert_eq!(default_run.backend, BackendArg::Spi);

        let legacy = Args::try_parse_from([
            "lumistripe",
            "--output-backend",
            "mock",
            "--cycle-order",
            "shuffle",
            "--cycle-timing",
            "fixed",
            "--cycle-interval",
            "12",
            "--chip",
            "/dev/gpiochip4",
        ])
        .unwrap();
        assert!(legacy.command.is_none());
        assert_eq!(legacy.legacy_run.backend, BackendArg::Mock);
        assert_eq!(legacy.legacy_run.cycle_order, CycleOrderArg::Shuffle);
        assert_eq!(legacy.legacy_run.cycle_timing, CycleTimingArg::Fixed);
        assert_eq!(legacy.legacy_run.cycle_interval_seconds, 12.0);
        assert_eq!(legacy.legacy_run.gpio_chip, "/dev/gpiochip4");
    }

    #[test]
    fn clap_accepts_legacy_audio_actions_and_secondary_spi_speed_alias() {
        use clap::Parser;

        let parsed = Args::try_parse_from([
            "lumistripe",
            "--analyze-audio-debug",
            "first.jsonl",
            "second.jsonl",
            "--list-audio-devices",
            "--calibrate-audio",
            "12",
            "--audio-debug",
            "--audio-debug-verbose",
            "--audio-debug-record",
            "capture.jsonl",
            "--audio-debug-duration",
            "6",
            "--audio-debug-label",
            "rehearsal",
            "--audio-device",
            "USB mic",
            "--spi-speed-2",
            "2000000",
        ])
        .unwrap();

        assert_eq!(parsed.analyze_audio_debug.len(), 2);
        assert_eq!(parsed.analyze_audio_debug[0], PathBuf::from("first.jsonl"));
        assert_eq!(parsed.analyze_audio_debug[1], PathBuf::from("second.jsonl"));
        assert!(parsed.list_audio_devices);
        assert_eq!(parsed.calibrate_audio, Some(12.0));
        assert!(parsed.audio_debug);
        assert!(parsed.audio_debug_verbose);
        assert_eq!(
            parsed.audio_debug_record,
            Some(PathBuf::from("capture.jsonl"))
        );
        assert_eq!(parsed.audio_debug_duration, Some(6.0));
        assert_eq!(parsed.audio_debug_label.as_deref(), Some("rehearsal"));
        assert_eq!(parsed.legacy_run.audio_device.as_deref(), Some("USB mic"));
        assert_eq!(parsed.legacy_run.spi_speed_hz_2, Some(2_000_000));
        assert_eq!(legacy_action(&parsed), LegacyAction::AnalyzeAudioDebug);
    }

    #[test]
    fn legacy_audio_action_precedence_matches_python_cli() {
        use clap::Parser;

        let parsed = Args::try_parse_from([
            "lumistripe",
            "--list-audio-devices",
            "--calibrate-audio",
            "12",
            "--audio-debug",
            "--audio-source",
            "mic",
            "--write-mic-profile",
            "profile.json",
        ])
        .unwrap();
        assert_eq!(legacy_action(&parsed), LegacyAction::ListAudioDevices);

        let parsed = Args::try_parse_from([
            "lumistripe",
            "--calibrate-audio",
            "12",
            "--audio-debug",
            "--audio-source",
            "mic",
            "--write-mic-profile",
            "profile.json",
        ])
        .unwrap();
        assert_eq!(legacy_action(&parsed), LegacyAction::CalibrateAudio);

        let parsed = Args::try_parse_from([
            "lumistripe",
            "--audio-debug",
            "--audio-source",
            "mic",
            "--write-mic-profile",
            "profile.json",
        ])
        .unwrap();
        assert_eq!(legacy_action(&parsed), LegacyAction::WriteMicProfile);

        let parsed = Args::try_parse_from(["lumistripe", "--audio-debug"]).unwrap();
        assert_eq!(legacy_action(&parsed), LegacyAction::AudioDebug);
        let parsed = Args::try_parse_from(["lumistripe"]).unwrap();
        assert_eq!(legacy_action(&parsed), LegacyAction::Playback);
    }

    #[test]
    fn legacy_audio_debug_ignores_label_without_recording_path() {
        use clap::Parser;

        let parsed = Args::try_parse_from([
            "lumistripe",
            "--audio-debug",
            "--audio-debug-label",
            "rehearsal",
        ])
        .unwrap();
        assert_eq!(
            legacy_audio_debug_label(
                parsed.audio_debug_record.as_deref(),
                parsed.audio_debug_label.as_deref()
            ),
            None
        );

        let parsed = Args::try_parse_from([
            "lumistripe",
            "--audio-debug",
            "--audio-debug-record",
            "capture.jsonl",
            "--audio-debug-label",
            "rehearsal",
        ])
        .unwrap();
        assert_eq!(
            legacy_audio_debug_label(
                parsed.audio_debug_record.as_deref(),
                parsed.audio_debug_label.as_deref()
            ),
            Some("rehearsal")
        );
    }

    #[test]
    fn hardware_configuration_requires_explicit_secondary_resources() {
        let mut options = args();
        options.backend = BackendArg::Spi;
        options.pixels_2 = Some(32);
        assert!(
            build_engine_config(&options)
                .unwrap_err()
                .contains("--spi-device-2")
        );
        options.spi_device_2 = Some("/dev/spidev0.1".to_owned());
        assert!(build_engine_config(&options).is_ok());
    }

    #[test]
    fn static_scene_normalizes_animation_settings_and_power_budget() {
        let mut options = args();
        options.power_budget_watts = Some(12.0);
        let settings = parse_settings(Some(r#"{"speed":1.5}"#)).unwrap();
        let scene = build_scene(
            &options,
            &build_engine_config(&options).unwrap(),
            settings,
            1.0,
        )
        .unwrap();
        assert_eq!(scene.outputs.len(), 1);
        assert_eq!(scene.outputs[0].settings.get("speed").unwrap(), &1.5);
        assert!(scene.power.enabled);
        assert_eq!(scene.power.budget_watts, Some(12.0));
    }

    #[test]
    fn cycling_and_dynamic_modes_use_engine_scheduling() {
        let mut options = args();
        options.mode = ModeArg::Cycling;
        let config = build_engine_config(&options).unwrap();
        let scene = build_scene(&options, &config, parse_settings(None).unwrap(), 1.0).unwrap();
        assert!(scene.outputs[0].cycling.as_ref().unwrap().choices.len() > 1);
        options.mode = ModeArg::Dynamic;
        let scene = build_scene(&options, &config, parse_settings(None).unwrap(), 1.0).unwrap();
        assert!(
            scene.outputs[0]
                .cycling
                .as_ref()
                .unwrap()
                .selector
                .is_some()
        );
        assert!(
            scene.outputs[0]
                .cycling
                .as_ref()
                .unwrap()
                .dynamic_idle
                .is_some()
        );
    }

    #[test]
    fn legacy_cycle_and_dynamic_tuning_reaches_the_engine_scene() {
        let mut options = args();
        options.mode = ModeArg::Dynamic;
        options.cycle_order = CycleOrderArg::Shuffle;
        options.cycle_timing = CycleTimingArg::Fixed;
        options.cycle_interval_seconds = 17.5;
        options.dynamic_min_duration_seconds = 9.0;
        options.dynamic_max_duration_seconds = 38.0;
        options.dynamic_switch_cooldown_seconds = 4.0;
        options.dynamic_drop_cooldown_seconds = 11.0;
        options.dynamic_randomness = 0.3;
        options.dynamic_history_size = 7;
        options.dynamic_seed = Some(-42);
        options.idle_enter_frames = 32;
        options.music_activation_delay_seconds = 1.25;
        options.dynamic_idle_brightness = 0.08;

        let scene = build_scene(
            &options,
            &build_engine_config(&options).unwrap(),
            Default::default(),
            1.0,
        )
        .unwrap();
        let cycling = scene.outputs[0].cycling.as_ref().unwrap();
        assert_eq!(cycling.order, RenderCycleOrder::Shuffle);
        assert_eq!(cycling.timing, RenderCycleTiming::Fixed);
        assert_eq!(cycling.interval_ns, 17_500_000_000);
        let selector = cycling.selector.unwrap();
        assert_eq!(selector.min_duration_ns, 9_000_000_000);
        assert_eq!(selector.max_duration_ns, 38_000_000_000);
        assert_eq!(selector.switch_cooldown_ns, 4_000_000_000);
        assert_eq!(selector.drop_cooldown_ns, 11_000_000_000);
        assert_eq!(selector.randomness, 0.3);
        assert_eq!(selector.history_size, 7);
        assert_eq!(selector.seed, Some(42));
        let activity = selector.activity.unwrap();
        assert_eq!(activity.idle_enter_frames, 32);
        assert_eq!(activity.activation_delay_ns, 1_250_000_000);
        let idle = cycling.dynamic_idle.as_ref().unwrap();
        assert_eq!(idle.mode, RenderDynamicIdleMode::Solid);
        assert_eq!(idle.brightness, 0.08);
        let effects = cycling.effect_scheduler.as_ref().unwrap();
        assert!(effects.is_valid());
        assert_eq!(effects.beat_confidence_threshold, 0.55);
        assert_eq!(effects.onset_threshold, 0.65);
        assert!(
            effects
                .choices
                .iter()
                .any(|choice| choice.name == "beat_wave")
        );
    }

    #[test]
    fn rejects_unknown_animations_and_non_object_settings() {
        let mut options = args();
        options.animation = "not-an-animation".to_owned();
        let config = build_engine_config(&options).unwrap();
        assert!(build_scene(&options, &config, Default::default(), 1.0).is_err());
        assert!(parse_settings(Some("[]")).is_err());
    }

    #[test]
    fn dynamic_audio_defaults_to_microphone_and_can_select_engine_demo() {
        let mut options = args();
        options.mode = ModeArg::Dynamic;
        assert_eq!(resolved_audio_source(&options), AudioSourceArg::Mic);
        options.audio_source = Some(AudioSourceArg::Demo);
        assert_eq!(resolved_audio_source(&options), AudioSourceArg::Demo);
        assert!(validate_run_args(&options).is_ok());

        let config = build_audio_config(None, AudioSourceArg::Demo).unwrap();
        assert!(!config.microphone_enabled);
    }

    #[test]
    fn mic_profile_can_be_written_without_active_microphone_capture() {
        let mut options = args();
        options.mic_profile = Some("pcm2902".to_owned());
        options.write_mic_profile = Some(PathBuf::from("profile.json"));
        options.audio_device = Some("USB mic".to_owned());

        assert!(validate_run_args(&options).is_ok());
    }

    #[test]
    fn cli_audio_config_uses_engine_defaults_and_validates_tuning() {
        let config = build_audio_config(
            Some(r#"{"target_level":0.48,"noise_floor":0.021}"#),
            AudioSourceArg::Mic,
        )
        .unwrap();
        assert!(config.microphone_enabled);
        assert_eq!(config.target_level, 0.48);
        assert_eq!(config.noise_floor, 0.021);
        assert!(build_audio_config(Some(r#"{"high_pass_hz":-1}"#), AudioSourceArg::Mic).is_err());
        assert!(build_audio_config(Some("[]"), AudioSourceArg::Mic).is_err());
        assert!(build_audio_config(Some(r#"{"unknown_field":1}"#), AudioSourceArg::Mic).is_err());
    }

    #[test]
    fn legacy_microphone_tuning_flags_override_profile_values() {
        let mut options = args();
        options.mic_profile = Some("pcm2902".to_owned());
        options.mic_target_level = Some(0.44);
        options.mic_noise_floor = Some(0.022);
        let profile = MicProfile::load("pcm2902").unwrap();
        let mut config =
            build_profile_audio_config(None, AudioSourceArg::Mic, Some(&profile)).unwrap();
        if let Some(target_level) = options.mic_target_level {
            config.target_level = target_level as f32;
        }
        if let Some(noise_floor) = options.mic_noise_floor {
            config.noise_floor = noise_floor as f32;
        }
        assert_eq!(config.target_level, 0.44);
        assert_eq!(config.noise_floor, 0.022);
    }

    #[test]
    fn mic_profile_tuning_is_overridden_by_explicit_audio_config() {
        let profile = MicProfile::load("pcm2902").unwrap();
        let config = build_profile_audio_config(
            Some(r#"{"target_level":0.5}"#),
            AudioSourceArg::Mic,
            Some(&profile),
        )
        .unwrap();
        assert_eq!(config.noise_floor, 0.0137);
        assert_eq!(config.target_level, 0.5);
    }

    #[test]
    fn dynamic_idle_threshold_scale_applies_to_activity_gate() {
        let mut options = args();
        options.mode = ModeArg::Dynamic;
        let scene = build_scene(
            &options,
            &build_engine_config(&options).unwrap(),
            Default::default(),
            2.0,
        )
        .unwrap();
        let activity = scene.outputs[0]
            .cycling
            .as_ref()
            .unwrap()
            .selector
            .unwrap()
            .activity
            .unwrap();
        assert_eq!(activity.energy_threshold, 0.06);
        assert_eq!(activity.onset_threshold, 0.05);
        assert_eq!(activity.beat_density_threshold, 0.1);
        assert_eq!(activity.brightness_threshold, 0.16);
    }

    #[test]
    fn runtime_stats_interval_is_bounded() {
        let mut options = args();
        options.stats_interval_ms = 99;
        assert!(validate_run_args(&options).is_err());
        options.stats_interval_ms = 100;
        assert!(validate_run_args(&options).is_ok());
    }

    #[test]
    fn encoder_events_match_legacy_animation_mode_and_brightness_controls() {
        let mut options = args();
        assert!(apply_encoder_event(
            &mut options,
            EncoderEvent::Press { encoder: 0 }
        ));
        assert_eq!(options.mode, ModeArg::Cycling);
        assert!(apply_encoder_event(
            &mut options,
            EncoderEvent::Press { encoder: 0 }
        ));
        assert_eq!(options.mode, ModeArg::Dynamic);
        assert_eq!(resolved_audio_source(&options), AudioSourceArg::Mic);

        assert!(apply_encoder_event(
            &mut options,
            EncoderEvent::Rotate {
                encoder: 0,
                step: 1,
            }
        ));
        assert_eq!(options.mode, ModeArg::Static);
        assert_ne!(options.animation, "rainbow_cycle");

        options.brightness = 0.99;
        assert!(apply_encoder_event(
            &mut options,
            EncoderEvent::Rotate {
                encoder: 1,
                step: 1,
            }
        ));
        assert_eq!(options.brightness, 1.0);
    }

    #[test]
    fn encoder_mode_change_recovers_an_invalid_animation_selection() {
        let mut options = args();
        options.animation = "unknown_animation".to_owned();
        assert!(apply_encoder_event(
            &mut options,
            EncoderEvent::Press { encoder: 0 }
        ));
        assert_eq!(options.mode, ModeArg::Cycling);
        let config = build_engine_config(&options).unwrap();
        assert!(build_scene(&options, &config, Default::default(), 1.0).is_ok());
    }

    #[test]
    fn encoder_input_configuration_requires_complete_unique_pin_groups() {
        let mut options = args();
        options.encoder1_a = Some(5);
        assert!(
            validate_run_args(&options)
                .unwrap_err()
                .contains("encoder1 requires")
        );

        options.encoder1_b = Some(6);
        options.encoder1_button = Some(13);
        options.encoder2_a = Some(5);
        options.encoder2_b = Some(20);
        options.encoder2_button = Some(21);
        assert!(validate_run_args(&options).unwrap_err().contains("unique"));
    }

    #[test]
    fn dynamic_mode_rejects_disabled_audio_and_devices_without_microphone_source() {
        let mut options = args();
        options.mode = ModeArg::Dynamic;
        options.audio_source = Some(AudioSourceArg::Off);
        assert!(validate_run_args(&options).is_err());

        options.mode = ModeArg::Static;
        options.audio_device = Some("USB mic".to_owned());
        assert!(validate_run_args(&options).is_err());

        options.audio_device = None;
        options.mic_profile = Some("pcm2902".to_owned());
        assert!(validate_run_args(&options).is_err());

        options.audio_source = Some(AudioSourceArg::Mic);
        options.auto_calibrate_seconds = Some(0.0);
        assert!(validate_run_args(&options).is_err());
    }
}
