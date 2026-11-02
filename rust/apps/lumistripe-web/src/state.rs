use crate::config::{AppConfig, EngineOutputOptions};
use lumistripe_engine_animation_definitions::{AnimationDefinition, catalog};
use lumistripe_engine_config::OutputBackend;
use lumistripe_engine_protocol::{
    RenderAnimationChoice, RenderCycleOrder, RenderCycleTiming, RenderCycling, RenderDynamicIdle,
    RenderDynamicIdleMode, RenderDynamicSelector, RenderOutput, RenderPowerConfig,
    RenderSceneUpdate, RenderSchedulerConfig, RenderSwitchQuantization, ResponseResult,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::time::Instant;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
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
    pub outputs: Vec<OutputControl>,
    pub revision: u64,
    pub started_at: Instant,
    pub engine_health: Option<ResponseResult>,
    pub engine_error: Option<String>,
    pub audio_status: Option<lumistripe_engine_protocol::EngineAudioStatus>,
    pub audio_started: bool,
}

impl ControlModel {
    pub fn new(config: AppConfig) -> Self {
        let outputs = config
            .outputs
            .iter()
            .map(|output| OutputControl {
                stripe_id: output.id.clone(),
                mode: PlaybackMode::Static,
                solid_color: "#7C3AED".to_owned(),
                animation: "rainbow_cycle".to_owned(),
                brightness: 1.0,
                blackout: false,
                music_recognition_enabled: true,
            })
            .collect();
        Self {
            config,
            outputs,
            revision: 1,
            started_at: Instant::now(),
            engine_health: None,
            engine_error: None,
            audio_status: None,
            audio_started: false,
        }
    }

    pub fn initial_scene(&self) -> RenderSceneUpdate {
        self.render_scene(self.revision)
    }

    pub fn render_scene(&self, revision: u64) -> RenderSceneUpdate {
        let outputs = self
            .outputs
            .iter()
            .map(|control| {
                let definition = catalog()
                    .animation(&control.animation)
                    .expect("control state only stores known catalog animations");
                RenderOutput {
                    animation: control.animation.clone(),
                    settings: default_settings(definition),
                    brightness: Some(control.brightness),
                    solid_color: (control.mode == PlaybackMode::Solid)
                        .then(|| parse_color(&control.solid_color).expect("stored color is valid")),
                    blackout: control.blackout,
                    cycling: match control.mode {
                        PlaybackMode::Cycling => Some(cycling_config(false)),
                        PlaybackMode::Dynamic => Some(cycling_config(true)),
                        PlaybackMode::Solid | PlaybackMode::Static => None,
                    },
                    scene_revision: revision,
                    ..RenderOutput::default()
                }
            })
            .collect();
        RenderSceneUpdate {
            revision,
            timestamp_ns: monotonic_time_ns(),
            brightness: 1.0,
            power: RenderPowerConfig::default(),
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
        let output_configs = self
            .config
            .outputs
            .iter()
            .map(output_topology_value)
            .collect::<Vec<_>>();
        let playback = self
            .outputs
            .iter()
            .map(|output| stripe_playback_value(output, output_stats, audio_active))
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
            "music_idle": {"mode": "off", "color": "#2060FF", "animation": "", "brightness": 1.0},
            "music_gate": gate,
            "bpm": bpm,
            "audio_status": audio_error.clone().unwrap_or_else(|| {
                if audio_active { "Rust audio capture active.".to_owned() }
                else { "No audio source active.".to_owned() }
            }),
            "configured_audio_source": self.config.audio_device,
            "active_audio_source": audio.and_then(|status| status.device.as_ref().map(|device| device.name.clone())),
            "audio_monitoring": audio.map(|status| status.active),
            "active_effects": active_effects,
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
            "last_output_at": Value::Null,
            "last_output_age_seconds": output_age_seconds(output_stats.get("last_successful_update_epoch_ms")),
            "application_version": env!("CARGO_PKG_VERSION"),
            "application_name": "LumiStripe",
            "color_corrections": [],
            "calibration": {"active": false, "output_index": Value::Null, "pattern": Value::Null, "expires_in_seconds": Value::Null},
            "stripe_topology": {
                "layout": "mirrored",
                "outputs": output_configs,
                "power_budget_enabled": false,
                "power_budget_watts": Value::Null,
                "shared_supply_enabled": false,
                "supply_capacity_watts": 200.0,
                "pi_reserve_watts": 15.0,
            },
            "power_budget": {
                "enabled": false,
                "budget_watts": Value::Null,
                "estimated_watts": output_stats.get("estimated_power_watts").and_then(Value::as_f64).unwrap_or(0.0),
                "applied_scale": output_stats.get("applied_power_scale").and_then(Value::as_f64).unwrap_or(1.0),
                "limiting_output_id": output_stats.get("limiting_output_index").and_then(Value::as_u64).and_then(|index| self.config.outputs.get(index as usize)).map(|item| item.id.clone()),
                "outputs": power_outputs(&self.config.outputs, output_stats),
                "shared_supply_enabled": false,
                "supply_capacity_watts": Value::Null,
                "pi_reserve_watts": Value::Null,
                "available_led_watts": Value::Null,
            },
            "sequencer_settings": {"switch_quantization": "off", "bars_per_phrase": 8},
            "transition_settings": {"enabled": true, "duration_ms": 300, "effect": "crossfade"},
            "sequence_library": {"sequences": [], "active_sequence_id": Value::Null},
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

fn output_topology_value(output: &EngineOutputOptions) -> Value {
    let (backend, spi_device, spi_speed_hz, chip, data_pin, clock_pin) = match &output.backend {
        OutputBackend::Mock => ("spi", "/dev/spidev0.0", 1_000_000, "/dev/gpiochip0", 10, 11),
        OutputBackend::MockSpi => ("spi", "/dev/spidev0.0", 1_000_000, "/dev/gpiochip0", 10, 11),
        OutputBackend::Spi { device, speed_hz } => {
            ("spi", device.as_str(), *speed_hz, "/dev/gpiochip0", 10, 11)
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
        "reversed": false,
        "zone_count": 1,
        "zone_pattern": "repeat",
        "spi_device": spi_device,
        "spi_speed_hz": spi_speed_hz,
        "chip": chip,
        "data_pin": data_pin,
        "clock_pin": clock_pin,
        "voltage_v": 5.0,
        "full_white_current_a": 0.06,
        "power_limit_watts": Value::Null,
        "last_output_at": Value::Null,
        "error": Value::Null,
    })
}

fn stripe_playback_value(output: &OutputControl, stats: &Value, audio_active: bool) -> Value {
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
        "music_idle": {"mode": "off", "color": "#2060FF", "animation": "", "brightness": 1.0},
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
                "limit_watts": Value::Null,
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

#[cfg(test)]
mod tests {
    use super::{ControlModel, PlaybackMode, animation_list};
    use crate::config::{AppConfig, EngineOutputOptions};
    use lumistripe_engine_config::OutputBackend;

    fn test_model() -> ControlModel {
        ControlModel::new(AppConfig {
            outputs: vec![EngineOutputOptions {
                id: "main".to_owned(),
                name: "Main stripe".to_owned(),
                pixel_count: 8,
                backend: OutputBackend::Mock,
            }],
            runtime_kind: "simulation",
            output_backend: "simulation",
            output_devices: vec!["simulation".to_owned()],
            spi_speed_hz: None,
            audio_device: None,
            secure_cookie: false,
            static_dir: None,
        })
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
}
