use crate::config::{AppConfig, EngineOutputOptions};
use crate::settings::{
    AudioTuningProfile, ColorCorrection, DashboardSettings, MusicIdleMode, MusicIdleSettings,
    PersistedPlaybackMode, StripeLayout, StripeOutputSettings, TransitionEffect, ZonePattern,
};
use lumistripe_engine_animation_definitions::{AnimationDefinition, catalog};
use lumistripe_engine_config::OutputBackend;
use lumistripe_engine_protocol::{
    RenderAnimationChoice, RenderAnimationSequence, RenderAnimationSequenceStep, RenderCycleOrder,
    RenderCycleTiming, RenderCycling, RenderDynamicIdle, RenderDynamicIdleMode,
    RenderDynamicSelector, RenderMusicActivityConfig, RenderOutput, RenderPowerConfig,
    RenderSceneUpdate, RenderSchedulerConfig, RenderSwitchQuantization, RenderTransition,
    ResponseResult,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::time::Instant;

#[derive(Clone, Debug)]
pub(crate) struct EngineDesiredState {
    pub config: lumistripe_engine_config::EngineConfig,
    pub scene: RenderSceneUpdate,
    pub audio: Option<EngineDesiredAudio>,
}

#[derive(Clone, Debug)]
pub(crate) struct EngineDesiredAudio {
    pub selector: Option<String>,
    pub config: lumistripe_engine_audio::AudioConfig,
}

impl EngineDesiredState {
    pub(crate) fn for_model(
        model: &ControlModel,
        config: lumistripe_engine_config::EngineConfig,
        scene: RenderSceneUpdate,
    ) -> Self {
        Self {
            config,
            scene,
            audio: model.desired_audio(),
        }
    }
}

pub(crate) const COLOR_CALIBRATION_TIMEOUT: std::time::Duration =
    std::time::Duration::from_secs(300);

#[derive(Clone, Debug)]
pub(crate) struct ActiveColorCalibration {
    pub session_id: String,
    pub output_index: usize,
    pub pattern: String,
    pub last_activity: Instant,
    pub original_corrections: std::collections::BTreeMap<String, ColorCorrection>,
}

pub(crate) const AUDIO_CALIBRATION_TIMEOUT: std::time::Duration =
    std::time::Duration::from_secs(120);

#[derive(Clone, Debug)]
pub(crate) struct AudioCalibrationResult {
    pub duration_seconds: f64,
    pub samples: usize,
    pub measured_floor: f64,
    pub measured_peak: f64,
    pub recommended_noise_floor: f64,
    pub recommended_target_level: f64,
    pub recommended_hardware_gain: Option<f64>,
    pub recommended_idle_threshold_scale: f64,
    pub recommended_input_noise_floor: f64,
    pub measured_music_level: f64,
    pub clipping_fraction: f64,
}

#[derive(Clone, Debug)]
pub(crate) struct ActiveAudioCalibration {
    pub session_id: String,
    pub device_name: String,
    pub selector: String,
    pub started_at: Instant,
    pub last_activity: Instant,
    pub duration_seconds: f64,
    pub two_step: bool,
    pub phase: String,
    pub last_sequence: u64,
    pub frame_rms: Vec<f64>,
    pub energy: Vec<f64>,
    pub quiet_input_rms: Vec<f64>,
    pub quiet_input_peaks: Vec<f64>,
    pub quiet_clipping: Vec<f64>,
    pub music_input_rms: Vec<f64>,
    pub music_input_peaks: Vec<f64>,
    pub music_clipping: Vec<f64>,
    pub result: Option<AudioCalibrationResult>,
    pub error: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum PlaybackMode {
    Solid,
    Static,
    Cycling,
    Dynamic,
}

#[derive(Clone, Debug)]
pub struct OutputControl {
    pub stripe_id: String,
    pub mode: PlaybackMode,
    pub solid_color: String,
    pub animation: String,
    pub brightness: f64,
    pub blackout: bool,
    pub music_recognition_enabled: bool,
}

#[derive(Clone, Debug)]
pub struct ControlModel {
    pub config: AppConfig,
    pub settings: DashboardSettings,
    pub outputs: Vec<OutputControl>,
    pub revision: u64,
    pub spotify_restart_requested: bool,
    pub started_at: Instant,
    pub engine_health: Option<ResponseResult>,
    pub engine_error: Option<String>,
    pub audio_status: Option<lumistripe_engine_protocol::EngineAudioStatus>,
    pub audio_started: bool,
    pub audio_source: String,
    pub active_audio_source: String,
    pub(crate) audio_calibration: Option<ActiveAudioCalibration>,
    pub(crate) calibration: Option<ActiveColorCalibration>,
}

impl ControlModel {
    pub fn new(config: AppConfig, settings: DashboardSettings) -> Self {
        let remembered = &settings.remembered_playback;
        let restore = settings.restore_last_state;
        let remembered_animation = if catalog().animation(&remembered.animation).is_some() {
            remembered.animation.clone()
        } else {
            "rainbow_cycle".to_owned()
        };
        let outputs = config
            .outputs
            .iter()
            .map(|output| OutputControl {
                stripe_id: output.id.clone(),
                mode: if restore {
                    playback_mode(remembered.mode)
                } else {
                    PlaybackMode::Static
                },
                solid_color: if restore {
                    remembered.solid_color.clone()
                } else {
                    "#7C3AED".to_owned()
                },
                animation: if restore {
                    remembered_animation.clone()
                } else {
                    "rainbow_cycle".to_owned()
                },
                brightness: if restore { remembered.brightness } else { 1.0 },
                blackout: restore && remembered.blackout,
                music_recognition_enabled: true,
            })
            .collect();
        Self {
            config,
            settings,
            outputs,
            revision: 1,
            spotify_restart_requested: false,
            started_at: Instant::now(),
            engine_health: None,
            engine_error: None,
            audio_status: None,
            audio_started: false,
            audio_source: "auto".to_owned(),
            active_audio_source: "off".to_owned(),
            audio_calibration: None,
            calibration: None,
        }
    }

    pub fn initial_scene(&self) -> RenderSceneUpdate {
        self.render_scene(self.revision)
    }

    pub fn render_scene(&self, revision: u64) -> RenderSceneUpdate {
        self.render_scene_with_transition(revision, false)
    }

    pub fn render_scene_with_transition(
        &self,
        revision: u64,
        transition_requested: bool,
    ) -> RenderSceneUpdate {
        let timestamp_ns = monotonic_time_ns();
        let outputs = self
            .outputs
            .iter()
            .enumerate()
            .map(|(index, control)| {
                let calibration = self.calibration.as_ref();
                let in_calibration = calibration.is_some();
                let selected_for_calibration =
                    calibration.is_some_and(|session| session.output_index == index);
                let definition = catalog()
                    .animation(&control.animation)
                    .expect("control state only stores known catalog animations");
                let config = &self.config.outputs[index];
                let mut cycling = if in_calibration {
                    None
                } else {
                    match control.mode {
                        PlaybackMode::Cycling => Some(cycling_config(false)),
                        PlaybackMode::Dynamic => Some(cycling_config(true)),
                        PlaybackMode::Solid | PlaybackMode::Static => None,
                    }
                };
                if let Some(cycling) = &mut cycling {
                    apply_sequencer_settings(cycling, &self.settings);
                    apply_transition_settings(cycling, &self.settings);
                    if control.mode == PlaybackMode::Dynamic {
                        cycling.sequence = active_sequence(&self.settings);
                        apply_audio_profile(
                            cycling,
                            &self.settings,
                            self.audio_status.as_ref(),
                            &self.audio_source,
                        );
                    }
                    for choice in &mut cycling.choices {
                        if let Some(values) = animation_settings_for(
                            &self.settings,
                            &control.stripe_id,
                            &choice.animation,
                        ) {
                            choice.settings = values;
                        }
                    }
                    if control.mode == PlaybackMode::Dynamic {
                        apply_music_idle(cycling, &self.settings, &control.stripe_id);
                    }
                }
                let mut output_settings = default_settings(definition);
                if let Some(values) =
                    animation_settings_for(&self.settings, &control.stripe_id, &control.animation)
                {
                    output_settings = values;
                }
                let correction_name = if index == 0 { "primary" } else { "secondary" };
                let correction = self
                    .settings
                    .color_corrections
                    .get(&control.stripe_id)
                    .or_else(|| self.settings.color_corrections.get(correction_name))
                    .cloned()
                    .unwrap_or_default();
                let (scene_pixel_count, scene_offset, scene_resample) = scene_geometry(
                    &self.config.outputs,
                    index,
                    self.settings
                        .stripe_topology
                        .as_ref()
                        .map_or(StripeLayout::Mirrored, |topology| topology.layout),
                );
                RenderOutput {
                    animation: control.animation.clone(),
                    settings: output_settings,
                    brightness: Some(if selected_for_calibration {
                        1.0
                    } else {
                        control.brightness
                    }),
                    solid_color: if let Some(session) =
                        calibration.filter(|_| selected_for_calibration)
                    {
                        calibration_pattern_color(&session.pattern)
                    } else if control.mode == PlaybackMode::Solid && !in_calibration {
                        parse_color(&control.solid_color)
                    } else {
                        None
                    },
                    blackout: if in_calibration {
                        !selected_for_calibration
                    } else {
                        control.blackout
                    },
                    cycling,
                    scene_revision: revision,
                    scene_pixel_count,
                    scene_offset,
                    scene_resample,
                    reversed: config.reversed,
                    zone_count: config.zone_count,
                    zone_pattern: match config.zone_pattern {
                        ZonePattern::Repeat => "repeat".to_owned(),
                        ZonePattern::Alternate => "alternate".to_owned(),
                    },
                    color_correction: [correction.red, correction.green, correction.blue],
                    voltage_v: config.voltage_v,
                    full_white_current_a: config.full_white_current_a,
                    power_limit_watts: config.power_limit_watts,
                    transition: (!in_calibration
                        && transition_requested
                        && self.settings.transition_settings.enabled)
                        .then(|| RenderTransition {
                            started_at_ns: timestamp_ns,
                            duration_ns: u64::from(self.settings.transition_settings.duration_ms)
                                * 1_000_000,
                            mode: transition_effect_name(&self.settings.transition_settings.effect)
                                .to_owned(),
                        }),
                    ..RenderOutput::default()
                }
            })
            .collect();
        let topology = self.settings.stripe_topology.as_ref();
        RenderSceneUpdate {
            revision,
            timestamp_ns,
            brightness: 1.0,
            power: RenderPowerConfig {
                enabled: topology.is_some_and(|topology| {
                    topology.power_budget_enabled || topology.shared_supply_enabled
                }),
                budget_watts: topology.and_then(|topology| topology.effective_power_budget_watts()),
            },
            outputs,
            scheduler: RenderSchedulerConfig { max_fps: 60 },
        }
    }

    pub fn snapshot(&self, command_queue_depth: usize) -> Value {
        let primary = &self.outputs[0];
        let (running, engine_error, health_fields) = match self.engine_health.as_ref() {
            Some(ResponseResult::Health {
                configured,
                output_count,
                pixel_counts,
                frames_rendered,
                dropped_frames,
                failed_frames,
                scheduler_missed_deadlines,
                last_error,
                output_rate,
                frame_latency_ms,
                queue_latency_ms,
                beat_clocks,
                transmission_ms,
                frame_latency_p50_ms,
                frame_latency_p95_ms,
                frame_latency_p99_ms,
                sequential_skew_ms,
                last_successful_update_epoch_ms,
                render_time_ms,
                estimated_power_watts,
                applied_power_scale,
                output_power_watts,
                limiting_output_index,
                selected_animations,
                dynamic_gate_states,
                selector_diagnostics,
                effect_schedulers,
                ..
            }) => {
                let issue = self.engine_error.clone().or_else(|| last_error.clone());
                let valid = *configured
                    && *output_count == self.config.outputs.len()
                    && pixel_counts.len() == self.config.outputs.len();
                (
                    valid,
                    issue,
                    json!({
                        "frames_rendered": frames_rendered,
                        "dropped_frames": dropped_frames,
                        "failed_frames": failed_frames,
                        "missed_deadlines": scheduler_missed_deadlines,
                        "output_rate": output_rate,
                        "frame_latency_ms": frame_latency_ms,
                        "queue_latency_ms": queue_latency_ms,
                        "transmission_ms": transmission_ms,
                        "frame_latency_p50_ms": frame_latency_p50_ms,
                        "frame_latency_p95_ms": frame_latency_p95_ms,
                        "frame_latency_p99_ms": frame_latency_p99_ms,
                        "sequential_skew_ms": sequential_skew_ms,
                        "last_successful_update_epoch_ms": last_successful_update_epoch_ms,
                        "render_time_ms": render_time_ms,
                        "estimated_power_watts": estimated_power_watts,
                        "applied_power_scale": applied_power_scale,
                        "output_power_watts": output_power_watts,
                        "limiting_output_index": limiting_output_index,
                        "selected_animations": selected_animations,
                        "dynamic_gate_states": dynamic_gate_states,
                        "selector_diagnostics": selector_diagnostics,
                        "effect_schedulers": effect_schedulers,
                        "beat_clocks": beat_clocks,
                    }),
                )
            }
            _ => (
                false,
                self.engine_error
                    .clone()
                    .or_else(|| Some("engine is not ready".to_owned())),
                Value::Null,
            ),
        };
        let audio = self.audio_status.as_ref();
        let audio_active = audio.is_some_and(|status| status.active);
        let audio_monitoring = audio_active && self.active_audio_source != "demo";
        let audio_error = audio.and_then(|status| status.last_error.clone());
        let output_stats = &health_fields;
        let gate = output_stats
            .get("dynamic_gate_states")
            .and_then(Value::as_array)
            .and_then(|items| items.first())
            .and_then(Value::as_str)
            .unwrap_or("calm");
        let active_effects = output_stats
            .get("effect_schedulers")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|scheduler| scheduler.get("active").and_then(Value::as_array))
            .flatten()
            .filter_map(|effect| effect.get("name").and_then(Value::as_str))
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let last_output_at = output_stats
            .get("last_successful_update_epoch_ms")
            .and_then(Value::as_u64)
            .and_then(epoch_millis_to_rfc3339);
        let output_configs = self.settings.stripe_topology.as_ref().map_or_else(
            || {
                self.config
                    .outputs
                    .iter()
                    .map(|output| output_topology_value(output, last_output_at.as_deref()))
                    .collect::<Vec<_>>()
            },
            |topology| {
                topology
                    .outputs
                    .iter()
                    .map(|output| stripe_output_topology_value(output, last_output_at.as_deref()))
                    .collect::<Vec<_>>()
            },
        );
        let playback = self
            .outputs
            .iter()
            .map(|output| {
                let idle = music_idle_for(&self.settings, &output.stripe_id);
                stripe_playback_value(output, output_stats, audio_active, &idle)
            })
            .collect::<Vec<_>>();
        let issues = if running {
            Vec::new()
        } else {
            vec![json!({
                "severity": "error",
                "title": "Rust engine unavailable",
                "message": engine_error.as_deref().unwrap_or("engine is not ready"),
                "action": "Restart the LumiStripe web service.",
            })]
        };
        let output_latency =
            |name: &str| -> Value { output_stats.get(name).cloned().unwrap_or(Value::Null) };
        let bpm = audio
            .map(|status| f64::from(status.features.bpm.max(0.0)))
            .unwrap_or(0.0);
        let idle = self
            .settings
            .music_idle
            .get("shared")
            .cloned()
            .unwrap_or_default();
        let topology = self.settings.stripe_topology.as_ref();
        let power_budget_enabled =
            topology.is_some_and(|item| item.power_budget_enabled || item.shared_supply_enabled);
        let power_budget_watts = topology.and_then(|item| item.effective_power_budget_watts());
        let color_corrections = self
            .config
            .outputs
            .iter()
            .enumerate()
            .map(|(index, output)| {
                let fallback_name = if index == 0 { "primary" } else { "secondary" };
                let correction = self
                    .settings
                    .color_corrections
                    .get(&output.id)
                    .or_else(|| self.settings.color_corrections.get(fallback_name))
                    .cloned()
                    .unwrap_or_default();
                json!({
                    "output_index": index,
                    "name": output.name,
                    "device": self.config.output_devices.get(index).cloned().unwrap_or_default(),
                    "red": correction.red,
                    "green": correction.green,
                    "blue": correction.blue,
                })
            })
            .collect::<Vec<_>>();
        let layout = topology.map_or("mirrored", |item| match item.layout {
            StripeLayout::Mirrored => "mirrored",
            StripeLayout::Continuous => "continuous",
            StripeLayout::Independent => "independent",
        });
        let sequence_values = self
            .settings
            .animation_sequences
            .iter()
            .map(|sequence| {
                json!({
                    "id": sequence.id,
                    "name": sequence.name,
                })
            })
            .collect::<Vec<_>>();
        json!({
            "revision": self.revision,
            "runtime": self.config.runtime_kind,
            "output_backend": self.config.output_backend,
            "output_devices": self.config.output_devices,
            "spi_speed_hz": self.config.spi_speed_hz,
            "running": running,
            "mode": primary.mode,
            "solid_color": primary.solid_color,
            "animation": primary.animation,
            "brightness": primary.brightness,
            "blackout": primary.blackout,
            "music_active": gate == "active",
            "music_recognition_enabled": primary.music_recognition_enabled,
            "music_idle": music_idle_value(&idle),
            "music_gate": gate,
            "bpm": bpm,
            "audio_status": audio_error.clone().unwrap_or_else(|| {
                if self.active_audio_source == "demo" && audio_active {
                    "Using the internal demo beat.".to_owned()
                } else if audio_active && self.active_audio_source == "bluetooth" {
                    audio.and_then(|status| status.device.as_ref())
                        .map(|device| format!("Bluetooth: {}", device.name))
                        .unwrap_or_else(|| "Bluetooth audio capture active.".to_owned())
                } else if audio_active && self.active_audio_source == "spotify" {
                    "Spotify: waiting for playback".to_owned()
                } else if audio_active {
                    "Rust audio capture active.".to_owned()
                } else {
                    "No audio source active.".to_owned()
                }
            }),
            "configured_audio_source": self.audio_source,
            "active_audio_source": self.active_audio_source,
            "audio_monitoring": audio_monitoring,
            "active_effects": active_effects,
            "selector_diagnostics": output_stats.get("selector_diagnostics").cloned().unwrap_or_else(|| json!([])),
            "uptime_seconds": self.started_at.elapsed().as_secs_f64(),
            "frame_rate": output_stats.get("output_rate").and_then(Value::as_f64).unwrap_or(0.0),
            "render_time_ms": output_stats.get("render_time_ms").and_then(Value::as_f64).unwrap_or(0.0),
            "scheduler_time_ms": Value::Null,
            "worker_heartbeat_age_seconds": Value::Null,
            "missed_frame_count": output_stats.get("missed_deadlines").and_then(Value::as_u64).unwrap_or(0),
            "recent_frame_miss_rate": 0.0,
            "output_frame_rate": output_stats.get("output_rate").and_then(Value::as_f64).unwrap_or(0.0),
            "output_frame_latency_ms": output_latency("frame_latency_ms"),
            "output_queue_latency_ms": output_latency("queue_latency_ms"),
            "output_transmission_ms": output_latency("transmission_ms"),
            "output_frame_latency_p50_ms": output_latency("frame_latency_p50_ms"),
            "output_frame_latency_p95_ms": output_latency("frame_latency_p95_ms"),
            "output_frame_latency_p99_ms": output_latency("frame_latency_p99_ms"),
            "output_ipc_latency_ms": output_latency("queue_latency_ms"),
            "output_sequential_skew_ms": output_latency("sequential_skew_ms"),
            "output_heartbeat_age_seconds": output_age_seconds(output_stats.get("last_successful_update_epoch_ms")),
            "output_dropped_frame_count": output_stats.get("dropped_frames").and_then(Value::as_u64).unwrap_or(0),
            "output_failure_count": output_stats.get("failed_frames").and_then(Value::as_u64).unwrap_or(0),
            "output_error": engine_error,
            "command_queue_depth": command_queue_depth,
            "audio_health": if audio_active { "active" } else if audio_error.is_some() { "error" } else { "inactive" },
            "audio_callback_age_seconds": audio.and_then(|status| status.capture.last_callback_ns).map(|stamp| monotonic_time_ns().saturating_sub(stamp) as f64 / 1_000_000_000.0),
            "audio_frame_age_seconds": audio.and_then(|status| status.last_frame_ns).map(|stamp| monotonic_time_ns().saturating_sub(stamp) as f64 / 1_000_000_000.0),
            "last_output_at": last_output_at,
            "last_output_age_seconds": output_age_seconds(output_stats.get("last_successful_update_epoch_ms")),
            "application_version": env!("CARGO_PKG_VERSION"),
            "application_name": self.settings.application_name,
            "color_corrections": color_corrections,
            "calibration": self.calibration.as_ref().map_or_else(
                || json!({"active": false, "output_index": Value::Null, "pattern": Value::Null, "expires_in_seconds": Value::Null}),
                |session| json!({
                    "active": true,
                    "output_index": session.output_index,
                    "pattern": session.pattern,
                    "expires_in_seconds": COLOR_CALIBRATION_TIMEOUT
                        .saturating_sub(session.last_activity.elapsed())
                        .as_secs_f64(),
                }),
            ),
            "stripe_topology": {
                "layout": layout,
                "outputs": output_configs,
                "power_budget_enabled": topology.is_some_and(|item| item.power_budget_enabled),
                "power_budget_watts": topology.and_then(|item| item.power_budget_watts),
                "shared_supply_enabled": topology.is_some_and(|item| item.shared_supply_enabled),
                "supply_capacity_watts": topology.map_or(200.0, |item| item.supply_capacity_watts),
                "pi_reserve_watts": topology.map_or(15.0, |item| item.pi_reserve_watts),
            },
            "power_budget": {
                "enabled": power_budget_enabled,
                "budget_watts": power_budget_watts,
                "estimated_watts": output_stats.get("estimated_power_watts").and_then(Value::as_f64).unwrap_or(0.0),
                "applied_scale": output_stats.get("applied_power_scale").and_then(Value::as_f64).unwrap_or(1.0),
                "limiting_output_id": output_stats.get("limiting_output_index").and_then(Value::as_u64).and_then(|index| self.config.outputs.get(index as usize)).map(|item| item.id.clone()),
                "outputs": power_outputs(&self.config.outputs, output_stats),
                "shared_supply_enabled": topology.is_some_and(|item| item.shared_supply_enabled),
                "supply_capacity_watts": topology.map_or(Value::Null, |item| json!(item.supply_capacity_watts)),
                "pi_reserve_watts": topology.map_or(Value::Null, |item| json!(item.pi_reserve_watts)),
                "available_led_watts": topology.filter(|item| item.shared_supply_enabled).map_or(Value::Null, |item| json!(item.available_led_watts())),
            },
            "sequencer_settings": {
                "switch_quantization": switch_quantization_name(self.settings.switch_quantization),
                "bars_per_phrase": self.settings.bars_per_phrase,
            },
            "transition_settings": {
                "enabled": self.settings.transition_settings.enabled,
                "duration_ms": self.settings.transition_settings.duration_ms,
                "effect": transition_effect_name(&self.settings.transition_settings.effect),
            },
            "sequence_library": {
                "sequences": sequence_values,
                "active_sequence_id": self.settings.active_sequence_id,
            },
            "stripe_playback": playback,
            "diagnostic_issues": issues,
            "error": engine_error,
        })
    }

    pub fn set_engine_health(&mut self, response: Result<ResponseResult, String>) {
        match response {
            Ok(health @ ResponseResult::Health { .. }) => {
                self.engine_health = Some(health);
                self.engine_error = None;
            }
            Ok(_) => {
                self.engine_health = None;
                self.engine_error =
                    Some("Rust engine returned an unexpected health response".to_owned());
            }
            Err(error) => {
                self.engine_health = None;
                self.engine_error = Some(error);
            }
        }
    }

    pub fn set_audio_status(
        &mut self,
        status: Result<lumistripe_engine_protocol::EngineAudioStatus, String>,
    ) {
        match status {
            Ok(status) => self.audio_status = Some(status),
            Err(error) => {
                if let Some(audio) = &mut self.audio_status {
                    audio.active = false;
                    audio.last_error = Some(error);
                }
            }
        }
    }

    pub(crate) fn desired_audio(&self) -> Option<EngineDesiredAudio> {
        let status = self.audio_status.as_ref().filter(|status| status.active);
        if !self.audio_started && status.is_none() {
            return None;
        }

        let device = status.and_then(|status| status.device.as_ref());
        let device_name = device
            .map(|device| device.name.as_str())
            .or(self.settings.selected_audio_device.as_deref());
        let profile_name = match self.active_audio_source.as_str() {
            "bluetooth" | "spotify" => Some(self.active_audio_source.as_str()),
            _ => device_name,
        };
        let profile = profile_name
            .and_then(|name| self.settings.audio_profiles.get(name))
            .cloned()
            .unwrap_or_default();
        let is_demo = self.active_audio_source == "demo";
        let microphone = matches!(self.active_audio_source.as_str(), "mic" | "auto");
        let selector = if is_demo {
            Some("demo".to_owned())
        } else {
            device
                .map(|device| device.selector.clone())
                .or_else(|| self.config.audio_device.clone())
                .or_else(|| self.settings.selected_audio_device.clone())
        };

        Some(EngineDesiredAudio {
            selector,
            config: crate::audio::engine_config(&profile, microphone && !is_demo),
        })
    }

    pub fn set_revision(&mut self, revision: u64) {
        self.revision = revision;
    }
}

pub fn animation_list() -> Value {
    let items = catalog()
        .animations
        .iter()
        .map(|definition| {
            let metadata = &definition.dynamic_metadata;
            let mood = metadata
                .get("mood")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            let role = metadata
                .get("role")
                .and_then(Value::as_str)
                .unwrap_or(&definition.role);
            let dynamic_safe = metadata
                .get("dynamic_safe")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let sequence_eligible = definition.automatic
                && definition.role == "base"
                && role == "base"
                && dynamic_safe
                && definition.effect_metadata.is_none();
            let description = metadata
                .get("description")
                .and_then(Value::as_str)
                .map(str::to_owned)
                .unwrap_or_else(|| format!("{} lighting effect.", mood.replace('_', " ")));
            json!({
                "name": definition.name,
                "display_name": definition.display_name,
                "description": description,
                "role": role,
                "mood": mood,
                "dynamic_safe": dynamic_safe,
                "sequence_eligible": sequence_eligible,
                "settings": definition.settings.iter().map(|setting| json!({
                    "id": setting.id,
                    "label": setting.label,
                    "type": setting.setting_type,
                    "default": setting.default,
                    "description": setting.description,
                    "minimum": setting.minimum,
                    "maximum": setting.maximum,
                    "step": setting.step,
                    "unit": setting.unit,
                    "choices": setting.choices,
                    "visible_when_option": setting.visible_when_option,
                    "visible_when_value": setting.visible_when_value,
                })).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>();
    json!({"items": items})
}

pub fn monotonic_time_ns() -> u64 {
    let mut value = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    let result = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut value) };
    if result != 0 {
        return 0;
    }
    (value.tv_sec as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(value.tv_nsec as u64)
}

fn cycling_config(dynamic: bool) -> RenderCycling {
    let choices = catalog()
        .animations
        .iter()
        .filter(|definition| definition.automatic)
        .map(|definition| RenderAnimationChoice {
            animation: definition.name.clone(),
            settings: default_settings(definition),
        })
        .collect();
    RenderCycling {
        order: RenderCycleOrder::Sequential,
        timing: if dynamic {
            RenderCycleTiming::Fixed
        } else {
            RenderCycleTiming::PerAnimation
        },
        interval_ns: if dynamic { 1 } else { 30_000_000_000 },
        seed: None,
        choices,
        transition_duration_ns: 300_000_000,
        transition_mode: "crossfade".to_owned(),
        selector: dynamic.then_some(RenderDynamicSelector {
            min_duration_ns: 12_000_000_000,
            max_duration_ns: 45_000_000_000,
            switch_cooldown_ns: 8_000_000_000,
            drop_cooldown_ns: 15_000_000_000,
            randomness: 0.15,
            history_size: 5,
            seed: None,
            switch_margin: 0.12,
            switch_quantization: RenderSwitchQuantization::Off,
            bars_per_phrase: 8,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: Some(Default::default()),
        }),
        dynamic_idle: dynamic.then_some(RenderDynamicIdle {
            mode: RenderDynamicIdleMode::Off,
            animation: None,
            settings: Map::new(),
            color: [32, 96, 255],
            brightness: 1.0,
        }),
        sequence: None,
        effect_scheduler: None,
    }
}

fn playback_mode(mode: PersistedPlaybackMode) -> PlaybackMode {
    match mode {
        PersistedPlaybackMode::Solid => PlaybackMode::Solid,
        PersistedPlaybackMode::Static => PlaybackMode::Static,
        PersistedPlaybackMode::Cycling => PlaybackMode::Cycling,
        PersistedPlaybackMode::Dynamic => PlaybackMode::Dynamic,
    }
}

fn switch_quantization_name(value: crate::settings::SwitchQuantization) -> &'static str {
    match value {
        crate::settings::SwitchQuantization::Off => "off",
        crate::settings::SwitchQuantization::Beat => "beat",
        crate::settings::SwitchQuantization::Bar => "bar",
        crate::settings::SwitchQuantization::Phrase => "phrase",
    }
}

fn transition_effect_name(value: &TransitionEffect) -> &'static str {
    match value {
        TransitionEffect::Crossfade => "crossfade",
        TransitionEffect::FadeBlack => "fade_black",
        TransitionEffect::Wipe => "wipe",
    }
}

fn music_idle_for(settings: &DashboardSettings, scope: &str) -> MusicIdleSettings {
    settings
        .music_idle
        .get(scope)
        .or_else(|| settings.music_idle.get("shared"))
        .cloned()
        .unwrap_or_default()
}

fn music_idle_value(settings: &MusicIdleSettings) -> Value {
    json!({
        "mode": match settings.mode {
            MusicIdleMode::Off => "off",
            MusicIdleMode::Solid => "solid",
            MusicIdleMode::Animation => "animation",
        },
        "color": settings.color,
        "animation": settings.animation,
        "brightness": settings.brightness,
    })
}

fn animation_settings_for(
    settings: &DashboardSettings,
    scope: &str,
    animation: &str,
) -> Option<Map<String, Value>> {
    let values = settings
        .animation_settings
        .get(scope)
        .and_then(|animations| animations.get(animation))
        .or_else(|| {
            settings
                .animation_settings
                .get("shared")
                .and_then(|animations| animations.get(animation))
        })?;
    let definition = catalog().animation(animation)?;
    definition
        .normalize_settings(values, true)
        .ok()
        .map(|values| values.into_iter().collect())
}

fn apply_sequencer_settings(cycling: &mut RenderCycling, settings: &DashboardSettings) {
    if let Some(selector) = &mut cycling.selector {
        selector.switch_quantization = match settings.switch_quantization {
            crate::settings::SwitchQuantization::Off => RenderSwitchQuantization::Off,
            crate::settings::SwitchQuantization::Beat => RenderSwitchQuantization::Beat,
            crate::settings::SwitchQuantization::Bar => RenderSwitchQuantization::Bar,
            crate::settings::SwitchQuantization::Phrase => RenderSwitchQuantization::Phrase,
        };
        selector.bars_per_phrase = usize::from(settings.bars_per_phrase);
    }
}

fn apply_audio_profile(
    cycling: &mut RenderCycling,
    settings: &DashboardSettings,
    status: Option<&lumistripe_engine_protocol::EngineAudioStatus>,
    configured_source: &str,
) {
    let Some(selector) = &mut cycling.selector else {
        return;
    };
    let device_name = status
        .filter(|status| status.active)
        .and_then(|status| status.device.as_ref())
        .map(|device| device.name.as_str())
        .or(settings.selected_audio_device.as_deref());
    let source = if configured_source == "demo" && status.is_some_and(|status| status.active) {
        "demo"
    } else if status.is_some_and(|status| status.active) {
        "mic"
    } else {
        "off"
    };
    let route = format!("{source}|{}|default", device_name.unwrap_or(source));
    selector.sync_offset_ns = settings
        .audio_sync_offsets_ms
        .get(&route)
        .copied()
        .unwrap_or_default()
        .mul_add(1_000_000.0, 0.0)
        .round()
        .clamp(-500_000_000.0, 500_000_000.0) as i64;

    let profile = device_name
        .and_then(|name| settings.audio_profiles.get(name))
        .cloned()
        .unwrap_or_else(AudioTuningProfile::default);
    selector.activity = Some(RenderMusicActivityConfig {
        energy_threshold: profile.energy_threshold,
        onset_threshold: profile.onset_threshold,
        beat_density_threshold: profile.beat_density_threshold,
        brightness_threshold: profile.brightness_threshold,
        spectral_balance_ratio: profile.spectral_balance_ratio,
        ..RenderMusicActivityConfig::default()
    });
}

fn active_sequence(settings: &DashboardSettings) -> Option<RenderAnimationSequence> {
    let id = settings.active_sequence_id.as_ref()?;
    let sequence = settings
        .animation_sequences
        .iter()
        .find(|sequence| &sequence.id == id)?;
    Some(RenderAnimationSequence {
        id: sequence.id.clone(),
        repeat: sequence.repeat,
        steps: sequence
            .steps
            .iter()
            .map(|step| RenderAnimationSequenceStep {
                animation: step.animation_name.clone(),
                duration_bars: u32::from(step.duration_bars),
            })
            .collect(),
    })
}

fn apply_transition_settings(cycling: &mut RenderCycling, settings: &DashboardSettings) {
    cycling.transition_duration_ns = if settings.transition_settings.enabled {
        u64::from(settings.transition_settings.duration_ms) * 1_000_000
    } else {
        0
    };
    cycling.transition_mode =
        transition_effect_name(&settings.transition_settings.effect).to_owned();
}

fn apply_music_idle(cycling: &mut RenderCycling, settings: &DashboardSettings, scope: &str) {
    let Some(idle) = &mut cycling.dynamic_idle else {
        return;
    };
    let configured = music_idle_for(settings, scope);
    idle.mode = match configured.mode {
        MusicIdleMode::Off => RenderDynamicIdleMode::Off,
        MusicIdleMode::Solid => RenderDynamicIdleMode::Solid,
        MusicIdleMode::Animation => RenderDynamicIdleMode::Animation,
    };
    idle.animation = (!configured.animation.is_empty()).then_some(configured.animation.clone());
    idle.settings = idle
        .animation
        .as_deref()
        .and_then(|animation| animation_settings_for(settings, scope, animation))
        .unwrap_or_default();
    idle.color = parse_color(&configured.color).unwrap_or([32, 96, 255]);
    idle.brightness = configured.brightness;
}

fn scene_geometry(
    outputs: &[EngineOutputOptions],
    index: usize,
    layout: StripeLayout,
) -> (Option<usize>, usize, bool) {
    let Some(output) = outputs.get(index) else {
        return (None, 0, false);
    };
    match layout {
        StripeLayout::Independent => (None, 0, false),
        StripeLayout::Continuous => {
            let offset = outputs[..index].iter().map(logical_pixel_count).sum();
            let total = outputs.iter().map(logical_pixel_count).sum();
            (Some(total), offset, false)
        }
        StripeLayout::Mirrored => {
            let canvas = outputs.first().map(logical_pixel_count).unwrap_or(0);
            let resample = outputs
                .iter()
                .any(|candidate| logical_pixel_count(candidate) != canvas);
            (
                resample.then_some(canvas),
                0,
                resample && logical_pixel_count(output) != canvas,
            )
        }
    }
}

fn logical_pixel_count(output: &EngineOutputOptions) -> usize {
    output.pixel_count.div_ceil(output.zone_count.max(1))
}

fn default_settings(definition: &AnimationDefinition) -> Map<String, Value> {
    definition
        .normalize_settings(&Map::new(), true)
        .unwrap_or_default()
        .into_iter()
        .collect()
}

fn parse_color(value: &str) -> Option<[u8; 3]> {
    if value.len() != 7 || !value.starts_with('#') {
        return None;
    }
    Some([
        u8::from_str_radix(&value[1..3], 16).ok()?,
        u8::from_str_radix(&value[3..5], 16).ok()?,
        u8::from_str_radix(&value[5..7], 16).ok()?,
    ])
}

fn calibration_pattern_color(pattern: &str) -> Option<[u8; 3]> {
    match pattern {
        "white" => Some([255, 255, 255]),
        "red" => Some([255, 0, 0]),
        "green" => Some([0, 255, 0]),
        "blue" => Some([0, 0, 255]),
        _ => None,
    }
}

fn output_topology_value(output: &EngineOutputOptions, last_output_at: Option<&str>) -> Value {
    let (backend, spi_device, spi_speed_hz, chip, data_pin, clock_pin) = match &output.backend {
        OutputBackend::Mock => ("spi", "/dev/spidev0.0", 1_000_000, "/dev/gpiochip0", 14, 15),
        OutputBackend::MockSpi => ("spi", "/dev/spidev0.0", 1_000_000, "/dev/gpiochip0", 14, 15),
        OutputBackend::Spi { device, speed_hz } => {
            ("spi", device.as_str(), *speed_hz, "/dev/gpiochip0", 14, 15)
        }
        OutputBackend::Gpio {
            chip,
            data_pin,
            clock_pin,
        } => (
            "gpio",
            "/dev/spidev0.0",
            1_000_000,
            chip.as_str(),
            *data_pin,
            *clock_pin,
        ),
    };
    json!({
        "id": output.id,
        "name": output.name,
        "pixels": output.pixel_count,
        "backend": backend,
        "reversed": output.reversed,
        "zone_count": output.zone_count,
        "zone_pattern": match output.zone_pattern {
            ZonePattern::Repeat => "repeat",
            ZonePattern::Alternate => "alternate",
        },
        "spi_device": spi_device,
        "spi_speed_hz": spi_speed_hz,
        "chip": chip,
        "data_pin": data_pin,
        "clock_pin": clock_pin,
        "voltage_v": output.voltage_v,
        "full_white_current_a": output.full_white_current_a,
        "power_limit_watts": output.power_limit_watts,
        "last_output_at": last_output_at,
        "error": Value::Null,
    })
}

fn stripe_output_topology_value(
    output: &StripeOutputSettings,
    last_output_at: Option<&str>,
) -> Value {
    json!({
        "id": output.id,
        "name": output.name,
        "pixels": output.pixels,
        "backend": match output.backend {
            crate::settings::StripeBackend::Spi => "spi",
            crate::settings::StripeBackend::Gpio => "gpio",
        },
        "reversed": output.reversed,
        "zone_count": output.zone_count,
        "zone_pattern": match output.zone_pattern {
            ZonePattern::Repeat => "repeat",
            ZonePattern::Alternate => "alternate",
        },
        "spi_device": output.spi_device,
        "spi_speed_hz": output.spi_speed_hz,
        "chip": output.chip,
        "data_pin": output.data_pin,
        "clock_pin": output.clock_pin,
        "voltage_v": output.voltage_v,
        "full_white_current_a": output.full_white_current_a,
        "power_limit_watts": output.power_limit_watts,
        "last_output_at": last_output_at,
        "error": Value::Null,
    })
}

fn stripe_playback_value(
    output: &OutputControl,
    stats: &Value,
    audio_active: bool,
    music_idle: &MusicIdleSettings,
) -> Value {
    let diagnostics = {
        let mut value = json!({
            "mode": "off",
            "beats_per_bar": 4,
            "bars_per_phrase": 8,
            "beat_index": Value::Null,
            "beat_in_bar": Value::Null,
            "bar_index": Value::Null,
            "bar_in_phrase": Value::Null,
            "phrase_index": Value::Null,
            "beat_in_phrase": Value::Null,
            "bpm": 0.0,
            "beat_clock_confidence": 0.0,
            "alignment_estimated": false,
            "pending_animation": Value::Null,
            "next_switch_in_s": Value::Null,
            "waiting_for_clock": false,
        });
        if let Some(clock) = stats
            .get("beat_clocks")
            .and_then(Value::as_array)
            .and_then(|clocks| clocks.first())
        {
            value["beat_index"] = clock.get("beat_index").cloned().unwrap_or(Value::Null);
            value["bpm"] = clock.get("bpm").cloned().unwrap_or(json!(0.0));
            value["beat_clock_confidence"] = clock.get("confidence").cloned().unwrap_or(json!(0.0));
            value["alignment_estimated"] = json!(true);
        }
        value
    };
    json!({
        "stripe_id": output.stripe_id,
        "mode": output.mode,
        "solid_color": output.solid_color,
        "animation": output.animation,
        "brightness": output.brightness,
        "blackout": output.blackout,
        "music_active": audio_active,
        "music_recognition_enabled": output.music_recognition_enabled,
            "music_idle": music_idle_value(music_idle),
        "sequencer_diagnostics": diagnostics,
        "sequence_playback": {
            "selected_sequence_id": Value::Null,
            "active": false,
            "current_step_index": Value::Null,
            "animation_name": Value::Null,
            "remaining_bars": Value::Null,
            "waiting_for_clock": false,
            "next_transition_in_s": Value::Null,
        },
    })
}

fn power_outputs(outputs: &[EngineOutputOptions], stats: &Value) -> Vec<Value> {
    outputs
        .iter()
        .enumerate()
        .map(|(index, output)| {
            let estimated = stats
                .get("output_power_watts")
                .and_then(Value::as_array)
                .and_then(|items| items.get(index))
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            json!({
                "output_id": output.id,
                "estimated_watts": estimated,
                "limit_watts": output.power_limit_watts,
                "applied_scale": stats.get("applied_power_scale").and_then(Value::as_f64).unwrap_or(1.0),
            })
        })
        .collect()
}

fn output_age_seconds(epoch_ms: Option<&Value>) -> Value {
    let Some(epoch_ms) = epoch_ms.and_then(Value::as_u64) else {
        return Value::Null;
    };
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    json!(now_ms.saturating_sub(epoch_ms) as f64 / 1000.0)
}

fn epoch_millis_to_rfc3339(epoch_ms: u64) -> Option<String> {
    let seconds = i64::try_from(epoch_ms / 1_000).ok()?;
    let days = seconds.div_euclid(86_400);
    let day_seconds = seconds.rem_euclid(86_400);
    let shifted_days = days.checked_add(719_468)?;
    let era_input = if shifted_days >= 0 {
        shifted_days
    } else {
        shifted_days.checked_sub(146_096)?
    };
    let era = era_input.div_euclid(146_097);
    let day_of_era = shifted_days - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += if month <= 2 { 1 } else { 0 };
    let hour = day_seconds / 3_600;
    let minute = day_seconds % 3_600 / 60;
    let second = day_seconds % 60;
    let millisecond = epoch_ms % 1_000;
    Some(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millisecond:03}Z"
    ))
}

#[cfg(test)]
mod tests {
    use super::{
        ControlModel, PlaybackMode, ResponseResult, animation_list, epoch_millis_to_rfc3339,
    };
    use crate::config::{AppConfig, EngineOutputOptions};
    use crate::settings::{
        ColorCorrection, DashboardSettings, MusicIdleMode, MusicIdleSettings,
        PersistedPlaybackMode, SavedAnimationSequence, SavedAnimationSequenceStep,
        StartupPlaybackSettings, StripeLayout, StripeTopologySettings, ZonePattern,
    };
    use lumistripe_engine_config::OutputBackend;
    use lumistripe_engine_protocol::{RenderAnimationSequence, RenderAnimationSequenceStep};

    fn test_config() -> AppConfig {
        AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
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
            spotify_provider_enabled: false,
            bluetooth_max_inputs: 1,
            bluetooth_max_outputs: 1,
            secure_cookie: false,
            static_dir: None,
            settings_file: std::path::PathBuf::new(),
        }
    }

    fn test_model() -> ControlModel {
        ControlModel::new(test_config(), DashboardSettings::default())
    }

    #[test]
    fn converts_engine_output_epoch_to_the_api_timestamp_format() {
        assert_eq!(
            epoch_millis_to_rfc3339(0).as_deref(),
            Some("1970-01-01T00:00:00.000Z")
        );
        assert_eq!(
            epoch_millis_to_rfc3339(951_782_400_000).as_deref(),
            Some("2000-02-29T00:00:00.000Z")
        );
    }

    #[test]
    fn initial_snapshot_matches_the_strict_dashboard_contract_shape() {
        let model = test_model();
        let state = model.snapshot(0);
        for key in [
            "revision",
            "runtime",
            "output_backend",
            "output_devices",
            "running",
            "mode",
            "solid_color",
            "animation",
            "brightness",
            "blackout",
            "music_active",
            "music_recognition_enabled",
            "music_idle",
            "music_gate",
            "bpm",
            "audio_status",
            "selector_diagnostics",
            "active_effects",
            "uptime_seconds",
            "frame_rate",
            "render_time_ms",
            "missed_frame_count",
            "recent_frame_miss_rate",
            "command_queue_depth",
            "audio_health",
            "application_version",
            "application_name",
            "color_corrections",
            "calibration",
            "stripe_topology",
            "power_budget",
            "sequencer_settings",
            "transition_settings",
            "sequence_library",
            "stripe_playback",
            "diagnostic_issues",
            "error",
        ] {
            assert!(state.get(key).is_some(), "missing dashboard key {key}");
        }
        assert_eq!(state["mode"], "static");
        assert_eq!(model.outputs[0].mode, PlaybackMode::Static);
    }

    #[test]
    fn snapshot_preserves_engine_dynamic_selector_diagnostics() {
        use lumistripe_engine_protocol::{
            EngineStatus, RenderSelectorDiagnostics, RenderSelectorScore,
        };

        let mut model = test_model();
        model.engine_health = Some(ResponseResult::Health {
            status: EngineStatus::Ready,
            configured: true,
            output_count: 1,
            pixel_counts: vec![8],
            frames_rendered: 12,
            last_sequence: Some(12),
            dropped_frames: 0,
            failed_frames: 0,
            scheduler_missed_deadlines: 0,
            selected_animations: vec!["aurora_wave".to_owned()],
            scene_revisions: vec![1],
            structure_event_sequences: vec![0],
            cycling_active: vec![false],
            dynamic_gate_states: vec!["music".to_owned()],
            selector_diagnostics: vec![RenderSelectorDiagnostics {
                output_index: 0,
                current_animation: "aurora_wave".to_owned(),
                candidate_animation: Some("bass_pulse".to_owned()),
                selected_animation: "aurora_wave".to_owned(),
                should_switch: false,
                reason: "minimum_duration".to_owned(),
                scores: vec![RenderSelectorScore {
                    animation: "bass_pulse".to_owned(),
                    score: 0.87,
                    reasons: vec!["bass_energy".to_owned()],
                }],
                elapsed_ns: 2_000_000_000,
                min_duration_remaining_ns: 500_000_000,
                max_duration_remaining_ns: 8_000_000_000,
                switch_cooldown_remaining_ns: 0,
                drop_cooldown_remaining_ns: 0,
                recent_names: vec!["aurora_wave".to_owned(), "color_waves".to_owned()],
            }],
            beat_clocks: vec![],
            sequence_playback: vec![],
            effect_schedulers: vec![],
            last_error: None,
            output_rate: 60.0,
            frame_latency_ms: None,
            queue_latency_ms: None,
            transmission_ms: None,
            frame_latency_p50_ms: None,
            frame_latency_p95_ms: None,
            frame_latency_p99_ms: None,
            audio_feature_to_led_latency_ms: None,
            audio_feature_to_led_p50_ms: None,
            audio_feature_to_led_p95_ms: None,
            audio_feature_to_led_p99_ms: None,
            sequential_skew_ms: None,
            last_successful_update_epoch_ms: None,
            render_time_ms: None,
            scene_control_revision: Some(1),
            audio_update_sequence: None,
            estimated_power_watts: None,
            applied_power_scale: None,
            output_power_watts: None,
            limiting_output_index: None,
            preview_outputs: None,
        });

        let snapshot = model.snapshot(0);
        assert_eq!(
            snapshot["selector_diagnostics"][0]["reason"],
            "minimum_duration"
        );
        assert_eq!(
            snapshot["selector_diagnostics"][0]["scores"][0]["score"],
            0.87
        );
        assert_eq!(
            snapshot["selector_diagnostics"][0]["recent_names"][1],
            "color_waves"
        );
    }

    #[test]
    fn animation_contract_uses_the_shared_engine_catalog() {
        let list = animation_list();
        let items = list["items"].as_array().unwrap();
        assert_eq!(
            items.len(),
            lumistripe_engine_animation_definitions::catalog()
                .animations
                .len()
        );
        assert!(items.iter().any(|item| item["name"] == "rainbow_cycle"));
        assert!(items.iter().any(|item| item["role"] == "effect"));
    }

    #[test]
    fn startup_restoration_populates_the_engine_scene_from_persisted_settings() {
        let settings = DashboardSettings {
            restore_last_state: true,
            remembered_playback: StartupPlaybackSettings {
                mode: PersistedPlaybackMode::Dynamic,
                solid_color: "#123456".to_owned(),
                animation: "rainbow_cycle".to_owned(),
                brightness: 0.6,
                blackout: true,
                ..Default::default()
            },
            animation_sequences: vec![SavedAnimationSequence {
                id: "night".to_owned(),
                name: "Night".to_owned(),
                repeat: true,
                steps: vec![SavedAnimationSequenceStep {
                    animation_name: "rainbow_cycle".to_owned(),
                    duration_bars: 2,
                }],
            }],
            active_sequence_id: Some("night".to_owned()),
            music_idle: std::collections::BTreeMap::from([(
                "shared".to_owned(),
                MusicIdleSettings {
                    mode: MusicIdleMode::Solid,
                    color: "#ABCDEF".to_owned(),
                    ..Default::default()
                },
            )]),
            color_corrections: std::collections::BTreeMap::from([
                (
                    "primary".to_owned(),
                    ColorCorrection {
                        red: 120,
                        ..Default::default()
                    },
                ),
                ("secondary".to_owned(), ColorCorrection::default()),
            ]),
            stripe_topology: Some(StripeTopologySettings {
                power_budget_enabled: true,
                power_budget_watts: Some(42.0),
                ..Default::default()
            }),
            ..Default::default()
        };

        let model = ControlModel::new(test_config(), settings);
        let scene = model.initial_scene();
        assert_eq!(model.outputs[0].mode, PlaybackMode::Dynamic);
        assert_eq!(model.outputs[0].brightness, 0.6);
        assert!(model.outputs[0].blackout);
        assert_eq!(scene.outputs[0].color_correction, [120, 255, 255]);
        assert_eq!(scene.power.budget_watts, Some(42.0));
        assert_eq!(
            scene.outputs[0]
                .cycling
                .as_ref()
                .unwrap()
                .dynamic_idle
                .as_ref()
                .unwrap()
                .color,
            [171, 205, 239]
        );
        assert_eq!(
            scene.outputs[0].cycling.as_ref().unwrap().sequence,
            Some(RenderAnimationSequence {
                id: "night".to_owned(),
                repeat: true,
                steps: vec![RenderAnimationSequenceStep {
                    animation: "rainbow_cycle".to_owned(),
                    duration_bars: 2,
                }],
            })
        );
        assert_eq!(model.snapshot(0)["music_idle"]["mode"], "solid");
    }

    #[test]
    fn scene_geometry_uses_logical_zone_lengths_for_continuous_and_mirrored_layouts() {
        let mut config = test_config();
        config.outputs[0].pixel_count = 80;
        config.outputs[0].zone_count = 2;
        let mut second = config.outputs[0].clone();
        second.id = "secondary".to_owned();
        second.name = "Secondary".to_owned();
        second.pixel_count = 60;
        second.zone_count = 3;
        config.outputs.push(second);
        let mut settings = DashboardSettings::default();
        let mut topology = StripeTopologySettings {
            layout: StripeLayout::Continuous,
            ..Default::default()
        };
        settings.stripe_topology = Some(topology.clone());

        let continuous = ControlModel::new(config.clone(), settings.clone()).initial_scene();
        assert_eq!(continuous.outputs[0].scene_pixel_count, Some(60));
        assert_eq!(continuous.outputs[0].scene_offset, 0);
        assert_eq!(continuous.outputs[1].scene_pixel_count, Some(60));
        assert_eq!(continuous.outputs[1].scene_offset, 40);
        assert!(!continuous.outputs[1].scene_resample);

        topology.layout = StripeLayout::Mirrored;
        settings.stripe_topology = Some(topology);
        let mirrored = ControlModel::new(config, settings).initial_scene();
        assert_eq!(mirrored.outputs[0].scene_pixel_count, Some(40));
        assert!(!mirrored.outputs[0].scene_resample);
        assert_eq!(mirrored.outputs[1].scene_pixel_count, Some(40));
        assert!(mirrored.outputs[1].scene_resample);
    }
}
