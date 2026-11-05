//! Per-instance animation state and reusable-buffer implementations.

use lumistripe_engine_animation_definitions::{AnimationDefinition, catalog};
use lumistripe_engine_effects::EffectInstance;
pub use lumistripe_engine_render_primitives::RenderingInput;
use lumistripe_engine_render_primitives::{
    AudioInput, AudioReactive, FrameBuffer, Palette, Rgba, alpha_byte, band_window,
    catalog_palette_hsl_to_rgb, hsl_to_rgb, map_rgb_palette, palette_hsl_to_rgb,
};
use serde_json::{Map, Value};
use std::collections::BTreeMap;

const SUPPORTED_ANIMATIONS: &[&str] = &[
    "rainbow",
    "rainbow_cycle",
    "theater_chase",
    "strobe",
    "pulse",
    "police",
    "bpm",
    "wave",
    "sinelon",
    "bouncing_ball",
    "color_wipe",
    "juggle",
    "comet",
    "aurora",
    "fire",
    "twinkle",
    "rainbow_strobe",
    "laser_sweep",
    "disco_sparkle",
    "dual_comet",
    "dance_floor",
    "red_rave_sweep",
    "red_rave_chase",
    "red_blackout_strobe",
    "glow_rush",
    "rave_pulse",
    "rave_scanner",
    "neon_confetti",
    "strobe_chase",
    "plasma_rave",
    "dual_laser",
    "disco_comet",
    "neon_storm",
    "peak_mirror",
    "shockwave",
    "center_burst",
    "mirror_flash",
    "spectrum_flash",
    "club_flash",
    "color_burst",
    "drop_explosion",
    "drop_wave",
    "bass_drop",
    "beat_explosion",
    "beat_ripple",
    "beat_tunnel",
    "beat_wave",
    "hard_beat",
    "confetti",
    "comet_storm",
    "electric_storm",
    "firework_burst",
    "lightning_strike",
    "pixel_explosion",
];

pub fn supported_animations() -> &'static [&'static str] {
    SUPPORTED_ANIMATIONS
}

pub fn supports_animation(name: &str) -> bool {
    SUPPORTED_ANIMATIONS.contains(&name)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TravelDirection {
    Forward,
    Reverse,
    Bounce,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AnimationState {
    beat_flash: f64,
    phase: f64,
    hue_phase: f64,
    pulse: f64,
    pulse_hue: i32,
    bpm_flash: f64,
    bpm_hue: i32,
    ball_position: f64,
    ball_speed: f64,
    ball_direction: f64,
    wipe_head: f64,
    wipe_speed: f64,
    wipe_direction: f64,
    wipe_palette_index: usize,
    juggle_positions: [f64; 12],
    juggle_speeds: [f64; 12],
    juggle_initialized_count: usize,
    juggle_scaled_length: usize,
    comet_position: f64,
    comet_direction: f64,
    aurora_glow: f64,
    fire_heat: Vec<f32>,
    fire_next_heat: Vec<f32>,
    twinkle_levels: Vec<f32>,
    twinkle_hues: Vec<i16>,
    twinkle_bloom: f64,
    laser_position: f64,
    laser_direction: f64,
    disco_sparkle_levels: Vec<f32>,
    dual_comet_phase: f64,
    dual_comet_direction: f64,
    red_sweep_position: f64,
    red_sweep_direction: f64,
    red_chase_phase: f64,
    red_chase_direction: f64,
    red_flash_frames: i64,
    red_last_beat_frame: i64,
    glow_rush_position: f64,
    glow_rush_hue: i32,
    rave_pulse_flash: f64,
    rave_pulse_hue: i32,
    rave_scanner_phase: f64,
    rave_scanner_direction: f64,
    rave_scanner_flash: f64,
    neon_confetti_levels: Vec<f32>,
    neon_confetti_hues: Vec<i16>,
    strobe_chase_phase: f64,
    strobe_chase_hue_phase: f64,
    strobe_chase_flash: f64,
    dual_laser_a_position: f64,
    dual_laser_a_direction: f64,
    dual_laser_b_position: f64,
    dual_laser_b_direction: f64,
    dual_laser_burst: f64,
    dual_laser_a_hue: i32,
    dual_laser_b_hue: i32,
    disco_comet_phase: f64,
    disco_comet_direction: f64,
    neon_storm_levels: Vec<f32>,
    neon_storm_hues: Vec<i16>,
    neon_storm_streak_positions: Vec<f64>,
    peak_mirror_peaks: [f64; 8],
    peak_mirror_center_flash: f64,
    shockwave_radius: f64,
    shockwave_accent: f64,
    center_burst_radius: f64,
    center_burst_speed: f64,
    center_burst_width: f64,
    center_burst_hue_seed: i64,
    center_burst_flash: f64,
    mirror_flash_value: f64,
    mirror_flash_alternate: i32,
    mirror_flash_hue_seed: i64,
    spectrum_flash_value: f64,
    spectrum_flash_hue_phase: f64,
    bass_drop_charge: f64,
    bass_drop_flash: f64,
    beat_explosion_radius: f64,
    beat_explosion_burst: f64,
    beat_ripple_radius: f64,
    beat_ripple_burst: f64,
    beat_ripple_hue: i32,
    beat_tunnel_radius: f64,
    beat_tunnel_direction: f64,
    beat_tunnel_pulse: f64,
    beat_wave_positions: [f64; 8],
    beat_wave_speeds: [f64; 8],
    beat_wave_strengths: [f64; 8],
    beat_wave_count: usize,
    beat_wave_hue: i32,
    comet_storm_phase: f64,
    comet_storm_hue_phase: f64,
}

impl Default for AnimationState {
    fn default() -> Self {
        Self {
            beat_flash: 0.0,
            phase: 0.0,
            hue_phase: 0.0,
            pulse: 0.0,
            pulse_hue: 0,
            bpm_flash: 0.0,
            bpm_hue: 0,
            ball_position: 0.0,
            ball_speed: 0.85,
            ball_direction: 1.0,
            wipe_head: 0.0,
            wipe_speed: 0.85,
            wipe_direction: 1.0,
            wipe_palette_index: 0,
            juggle_positions: [
                0.0, 25.0, 50.0, 75.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            ],
            juggle_speeds: [2.5, 2.0, 1.8, 2.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            juggle_initialized_count: 4,
            juggle_scaled_length: 0,
            comet_position: 0.0,
            comet_direction: 1.0,
            aurora_glow: 0.0,
            fire_heat: Vec::new(),
            fire_next_heat: Vec::new(),
            twinkle_levels: Vec::new(),
            twinkle_hues: Vec::new(),
            twinkle_bloom: 0.0,
            laser_position: -2.0,
            laser_direction: 1.0,
            disco_sparkle_levels: Vec::new(),
            dual_comet_phase: 0.0,
            dual_comet_direction: 1.0,
            red_sweep_position: 0.0,
            red_sweep_direction: 1.0,
            red_chase_phase: 0.0,
            red_chase_direction: 1.0,
            red_flash_frames: 0,
            red_last_beat_frame: -10_000,
            glow_rush_position: 0.0,
            glow_rush_hue: 0,
            rave_pulse_flash: 0.0,
            rave_pulse_hue: 0,
            rave_scanner_phase: 0.0,
            rave_scanner_direction: 1.0,
            rave_scanner_flash: 0.0,
            neon_confetti_levels: Vec::new(),
            neon_confetti_hues: Vec::new(),
            strobe_chase_phase: 0.0,
            strobe_chase_hue_phase: 0.0,
            strobe_chase_flash: 0.0,
            dual_laser_a_position: -2.0,
            dual_laser_a_direction: 1.0,
            dual_laser_b_position: -2.0,
            dual_laser_b_direction: 1.0,
            dual_laser_burst: 0.0,
            dual_laser_a_hue: 0,
            dual_laser_b_hue: 200,
            disco_comet_phase: 0.0,
            disco_comet_direction: 1.0,
            neon_storm_levels: Vec::new(),
            neon_storm_hues: Vec::new(),
            neon_storm_streak_positions: Vec::new(),
            peak_mirror_peaks: [0.0; 8],
            peak_mirror_center_flash: 0.0,
            shockwave_radius: 0.0,
            shockwave_accent: 0.0,
            center_burst_radius: 0.0,
            center_burst_speed: 0.0,
            center_burst_width: 1.4,
            center_burst_hue_seed: 0,
            center_burst_flash: 0.0,
            mirror_flash_value: 0.0,
            mirror_flash_alternate: 0,
            mirror_flash_hue_seed: 0,
            spectrum_flash_value: 0.0,
            spectrum_flash_hue_phase: 0.0,
            bass_drop_charge: 0.0,
            bass_drop_flash: 0.0,
            beat_explosion_radius: 0.0,
            beat_explosion_burst: 0.0,
            beat_ripple_radius: 0.0,
            beat_ripple_burst: 0.0,
            beat_ripple_hue: 0,
            beat_tunnel_radius: 0.0,
            beat_tunnel_direction: 1.0,
            beat_tunnel_pulse: 0.0,
            beat_wave_positions: [0.0; 8],
            beat_wave_speeds: [0.0; 8],
            beat_wave_strengths: [0.0; 8],
            beat_wave_count: 0,
            beat_wave_hue: 0,
            comet_storm_phase: 0.0,
            comet_storm_hue_phase: 0.0,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct AnimationInstance {
    name: String,
    settings: BTreeMap<String, Value>,
    frame_index: u64,
    state: AnimationState,
    effect: Option<EffectInstance>,
}

impl AnimationInstance {
    pub fn validate(name: &str, settings: &Map<String, Value>) -> Result<(), AnimationError> {
        let definition = catalog()
            .animation(name)
            .ok_or_else(|| AnimationError::UnknownAnimation(name.to_owned()))?;
        definition
            .validate_settings(settings, true)
            .map_err(|error| AnimationError::InvalidSettings(error.to_string()))
    }

    pub fn new(name: &str, settings: &Map<String, Value>) -> Result<Self, AnimationError> {
        let definition = catalog()
            .animation(name)
            .ok_or_else(|| AnimationError::UnknownAnimation(name.to_owned()))?;
        let normalized = definition
            .normalize_settings(settings, true)
            .map_err(|error| AnimationError::InvalidSettings(error.to_string()))?;
        let effect = if EffectInstance::supports_effect(name) {
            Some(
                EffectInstance::new(name, settings)
                    .map_err(|error| AnimationError::Effect(error.to_string()))?,
            )
        } else {
            None
        };
        Ok(Self {
            name: name.to_owned(),
            settings: normalized,
            frame_index: 0,
            state: AnimationState::default(),
            effect,
        })
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn frame_index(&self) -> u64 {
        self.frame_index
    }

    pub fn set_random_seed(&mut self, seed: u32) {
        if let Some(effect) = &mut self.effect {
            effect.set_random_seed(seed);
        }
    }

    pub fn settings(&self) -> &BTreeMap<String, Value> {
        &self.settings
    }

    /// Compares a full or partial settings object with this instance without allocating.
    pub fn settings_match(&self, values: &Map<String, Value>) -> bool {
        let Some(definition) = catalog().animation(&self.name) else {
            return false;
        };
        if values
            .keys()
            .any(|key| !definition.settings.iter().any(|setting| setting.id == *key))
        {
            return false;
        }
        definition.settings.iter().all(|setting| {
            let expected = values.get(&setting.id).unwrap_or(&setting.default);
            let Some(current) = self.settings.get(&setting.id) else {
                return false;
            };
            match setting.setting_type.as_str() {
                "number" => expected.as_f64() == current.as_f64(),
                "color" => expected
                    .as_str()
                    .zip(current.as_str())
                    .is_some_and(|(left, right)| left.eq_ignore_ascii_case(right)),
                _ => expected == current,
            }
        })
    }

    pub fn set_settings(&mut self, values: &Map<String, Value>) -> Result<(), AnimationError> {
        let definition = catalog()
            .animation(&self.name)
            .ok_or_else(|| AnimationError::UnknownAnimation(self.name.clone()))?;
        self.settings = definition
            .normalize_settings(values, true)
            .map_err(|error| AnimationError::InvalidSettings(error.to_string()))?;
        if let Some(effect) = &mut self.effect {
            effect
                .set_settings(values)
                .map_err(|error| AnimationError::Effect(error.to_string()))?;
        }
        Ok(())
    }

    pub fn reset(&mut self) {
        self.frame_index = 0;
        self.state = AnimationState::default();
        if let Some(effect) = &mut self.effect {
            effect.reset();
        }
    }

    pub fn render_next(
        &mut self,
        input: RenderingInput,
        destination: &mut FrameBuffer,
    ) -> Result<(), AnimationError> {
        if !input.brightness.is_finite() {
            return Err(AnimationError::InvalidInput("brightness must be finite"));
        }
        let definition = catalog()
            .animation(&self.name)
            .expect("animation instance was validated at construction");
        let frame = effective_frame(definition, self.frame_index, &self.settings);
        if let Some(effect) = &mut self.effect {
            effect
                .render_at(frame, input, destination)
                .map_err(|error| AnimationError::Effect(error.to_string()))?;
            self.frame_index = self.frame_index.saturating_add(1);
            return Ok(());
        }
        let mut input = input;
        if !matches!(self.name.as_str(), "pulse" | "comet") {
            input.audio = input.audio.and_then(|audio| self.effective_audio(audio));
        }
        match self.name.as_str() {
            "rainbow" => render_rainbow(self, frame, input, destination),
            "rainbow_cycle" => render_rainbow_cycle(self, frame, input, destination),
            "theater_chase" => render_theater_chase(self, frame, input, destination),
            "strobe" => render_strobe(self, frame, input, destination),
            "pulse" => render_pulse(self, frame, input, destination),
            "police" => render_police(self, frame, input, destination),
            "bpm" => render_bpm(self, frame, input, destination),
            "wave" => render_wave(self, frame, input, destination),
            "sinelon" => render_sinelon(self, frame, input, destination),
            "bouncing_ball" => render_bouncing_ball(self, frame, input, destination),
            "color_wipe" => render_color_wipe(self, input, destination),
            "juggle" => render_juggle(self, frame, input, destination),
            "comet" => render_comet(self, frame, input, destination),
            "aurora" => render_aurora(self, frame, input, destination),
            "fire" => render_fire(self, frame, input, destination),
            "twinkle" => render_twinkle(self, frame, input, destination),
            "rainbow_strobe" => render_rainbow_strobe(self, frame, input, destination),
            "laser_sweep" => render_laser_sweep(self, frame, input, destination),
            "disco_sparkle" => render_disco_sparkle(self, frame, input, destination),
            "dual_comet" => render_dual_comet(self, input, destination),
            "dance_floor" => render_dance_floor(self, frame, input, destination),
            "red_rave_sweep" => render_red_rave_sweep(self, frame, input, destination),
            "red_rave_chase" => render_red_rave_chase(self, frame, input, destination),
            "red_blackout_strobe" => render_red_blackout_strobe(self, frame, input, destination),
            "glow_rush" => render_glow_rush(self, frame, input, destination),
            "rave_pulse" => render_rave_pulse(self, frame, input, destination),
            "rave_scanner" => render_rave_scanner(self, frame, input, destination),
            "neon_confetti" => render_neon_confetti(self, frame, input, destination),
            "strobe_chase" => render_strobe_chase(self, frame, input, destination),
            "plasma_rave" => render_plasma_rave(self, frame, input, destination),
            "dual_laser" => render_dual_laser(self, frame, input, destination),
            "disco_comet" => render_disco_comet(self, frame, input, destination),
            "neon_storm" => render_neon_storm(self, frame, input, destination),
            "peak_mirror" => render_peak_mirror(self, frame, input, destination),
            "shockwave" => render_shockwave(self, frame, input, destination),
            "center_burst" => render_center_burst(self, frame, input, destination),
            "mirror_flash" => render_mirror_flash(self, frame, input, destination),
            "spectrum_flash" => render_spectrum_flash(self, frame, input, destination),
            "bass_drop" => render_bass_drop(self, frame, input, destination),
            "beat_explosion" => render_beat_explosion(self, frame, input, destination),
            "beat_ripple" => render_beat_ripple(self, frame, input, destination),
            "beat_tunnel" => render_beat_tunnel(self, frame, input, destination),
            "beat_wave" => render_beat_wave(self, frame, input, destination),
            "comet_storm" => render_comet_storm(self, frame, input, destination),
            _ => return Err(AnimationError::NotPorted(self.name.clone())),
        }
        self.frame_index = self.frame_index.saturating_add(1);
        Ok(())
    }

    fn setting_number(&self, name: &str, default: f64) -> f64 {
        self.settings
            .get(name)
            .and_then(Value::as_f64)
            .unwrap_or(default)
    }

    fn setting_string<'a>(&'a self, name: &str, default: &'a str) -> &'a str {
        self.settings
            .get(name)
            .and_then(Value::as_str)
            .unwrap_or(default)
    }

    fn palette(&self) -> Palette {
        let name = self.setting_string("palette", "rainbow");
        let custom = self.setting_string("custom_color", "#7C3AED");
        let rgb = u32::from_str_radix(custom.trim_start_matches('#'), 16).unwrap_or(0x7c3aed);
        Palette::parse(name, rgb).unwrap_or_default()
    }

    fn brightness(&self, input: RenderingInput) -> f64 {
        (input.brightness * self.setting_number("intensity", 100.0) / 100.0).clamp(0.0, 1.0)
    }

    fn effective_audio(&self, audio: AudioInput) -> Option<AudioInput> {
        let reactivity = self.setting_number("music_reactivity", 100.0) / 100.0;
        if reactivity <= 0.0 {
            return None;
        }
        let gain = reactivity * (0.5 + self.setting_number("sensitivity", 50.0) / 100.0);
        let beat_gain = reactivity * self.setting_number("beat_sensitivity", 100.0) / 100.0;
        if gain == 1.0 && beat_gain == 1.0 {
            return Some(audio);
        }
        let beat_strength = (audio.beat_strength * beat_gain).min(1.0);
        Some(AudioInput {
            rms: (audio.rms * gain).min(1.0),
            bands: audio.bands.map(|value| (value * gain).min(1.0)),
            beat: audio.beat && beat_gain > 0.0 && beat_strength >= 0.08,
            beat_strength,
        })
    }
}

fn effective_frame(
    definition: &AnimationDefinition,
    frame: u64,
    settings: &BTreeMap<String, Value>,
) -> i64 {
    if definition.renderer == "Comet" || definition.renderer == "Pulse" {
        return frame.min(i64::MAX as u64) as i64;
    }
    let Some(speed) = settings.get("speed").and_then(Value::as_f64) else {
        return frame.min(i64::MAX as u64) as i64;
    };
    let scaled = frame as f64 * (0.25 + speed * 0.015);
    python_round(scaled)
}

fn python_round(value: f64) -> i64 {
    let lower = value.floor();
    let fraction = value - lower;
    let rounded = if fraction < 0.5 {
        lower
    } else if fraction > 0.5 || (lower as i64).rem_euclid(2) != 0 {
        lower + 1.0
    } else {
        lower
    };
    rounded as i64
}

fn render_rainbow(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let spread = 0.35 + instance.setting_number("color_spread", 50.0) / 100.0 * 1.3;
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let count = destination.pixel_count();
    let (base_hue, saturation, alpha, mid, audio) = match input.audio {
        None => (0.0, 100.0, 1.0, 0.0, None),
        Some(audio) => {
            let reactive = AudioReactive::from_input(audio);
            if audio.beat {
                instance.state.beat_flash = 1.0;
            }
            instance.state.beat_flash *= 0.88;
            (
                f64::from(reactive.hue_shift(frame, 0.15)),
                f64::from((62.0 + reactive.low * 34.0) as i32),
                (0.42 + reactive.rms * 0.58 + instance.state.beat_flash * 0.4).min(1.0),
                reactive.mid,
                Some(audio),
            )
        }
    };
    let output_alpha = alpha_byte(alpha, brightness);
    for index in 0..count {
        let position = if count <= 1 {
            0.0
        } else {
            index as f64 / (count - 1) as f64
        };
        let local = audio.map_or(0.0, |audio| band_window(audio, index, count, 1));
        let hue = if audio.is_some() {
            (position * 256.0 * spread) as i32 + base_hue as i32 + (local * 44.0) as i32
        } else {
            truncated_modulo_256(position * 256.0 * spread + frame as f64 * 1.5)
        }
        .rem_euclid(256);
        let lightness = if audio.is_some() {
            48 + (mid * 6.0 + local * 6.0) as i32
        } else {
            50
        };
        let [red, green, blue] =
            palette_hsl_to_rgb(f64::from(hue), saturation, f64::from(lightness), palette);
        destination.set_pixel(index, Rgba::new(red, green, blue, output_alpha));
    }
}

fn render_rainbow_cycle(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let spread = 0.35 + instance.setting_number("color_spread", 50.0) / 100.0 * 1.3;
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let count = destination.pixel_count();
    let (base_hue, low, beat_pulse, audio) = match input.audio {
        None => (0, 0.0, 0.0, None),
        Some(audio) => {
            let reactive = AudioReactive::from_input(audio);
            (
                reactive.hue_shift(frame, reactive.speed(0.35, 2.2).max(0.1)),
                reactive.low,
                reactive.beat_pulse(0.0, 0.35),
                Some(audio),
            )
        }
    };
    let alpha = alpha_byte(1.0, brightness);
    for index in 0..count {
        let position = if count <= 1 {
            0.0
        } else {
            index as f64 / (count - 1) as f64
        };
        let local = audio.map_or(0.0, |audio| band_window(audio, index, count, 1));
        let (hue, lightness) = if let Some(_audio) = audio {
            (
                base_hue + (position * 256.0 * spread) as i32 + (local * 22.0) as i32,
                (48.0 + low * 6.0 + local * 6.0 + beat_pulse * 8.0) as i32,
            )
        } else {
            let wave = (frame as f64 * 0.035).sin() * 10.0;
            let light_wave = ((frame as f64 * 0.06 + position * 6.0).sin() * 0.5 + 0.5) * 4.0;
            (
                frame.rem_euclid(256) as i32 + (position * 256.0 * spread) as i32 + wave as i32,
                48 + light_wave as i32,
            )
        };
        let [red, green, blue] = palette_hsl_to_rgb(
            f64::from(hue.rem_euclid(256)),
            100.0,
            f64::from(lightness.min(64)),
            palette,
        );
        destination.set_pixel(index, Rgba::new(red, green, blue, alpha));
    }
}

fn render_theater_chase(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let (stride, phase, hue, drive, audio_mode) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let stride = (instance.setting_number("count", 3.0) as usize
            + usize::from(reactive.high > 0.65))
        .max(1);
        instance.state.phase =
            (instance.state.phase + 0.28 + reactive.drive() * 0.65 + reactive.accent * 0.2)
                % stride as f64;
        instance.state.hue_phase = (instance.state.hue_phase + 0.45 + reactive.high * 1.2) % 360.0;
        (
            stride,
            instance.state.phase,
            instance.state.hue_phase as i32,
            reactive.drive(),
            true,
        )
    } else {
        let stride = (instance.setting_number("count", 3.0) as usize).max(1);
        (
            stride,
            frame.rem_euclid(stride as i64) as f64,
            (frame * 5).rem_euclid(256) as i32,
            0.0,
            false,
        )
    };
    for index in 0..destination.pixel_count() {
        let slot = index % stride;
        let (alpha, saturation, lightness) = if audio_mode {
            let direct = (slot as f64 - phase).abs();
            let distance = direct.min(stride as f64 - direct);
            let glow = (1.0 - distance / 1.35).max(0.0);
            let alpha = glow * (0.72 + drive * 0.22);
            (if alpha <= 0.01 { 0.0 } else { alpha }, 96.0, 52.0)
        } else {
            let center = (phase as i64).rem_euclid(stride as i64) as usize;
            let before = if center == 0 { stride - 1 } else { center - 1 };
            let after = if center == stride - 1 { 0 } else { center + 1 };
            if slot == center {
                (1.0, 100.0, 55.0)
            } else if slot == before || slot == after {
                (0.25, 80.0, 42.0)
            } else {
                (0.0, 100.0, 55.0)
            }
        };
        let [red, green, blue] = if let Palette::Custom(rgb) = palette {
            [
                ((rgb >> 16) & 0xff) as u8,
                ((rgb >> 8) & 0xff) as u8,
                (rgb & 0xff) as u8,
            ]
        } else if alpha <= 0.0 {
            [0, 0, 0]
        } else {
            let mut rgb = hsl_to_rgb(f64::from(hue), saturation, lightness);
            map_rgb_palette(&mut rgb, palette);
            rgb
        };
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_strobe(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let flash_rate = instance.setting_number("flash_rate", 50.0);
    let duty_cycle = instance.setting_number("duty_cycle", 30.0);
    let period = python_round(28.0 - flash_rate * 0.24).max(2);
    let duration = python_round(period as f64 * duty_cycle / 160.0).max(1);
    let (cycle, hue) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        (
            if audio.beat {
                0
            } else {
                frame.rem_euclid(period)
            },
            reactive.hue_shift(frame, 1.2),
        )
    } else {
        (
            frame.rem_euclid(period),
            (frame * 11).rem_euclid(256) as i32,
        )
    };
    let brightness = instance.brightness(input);
    let pixel = if cycle < duration {
        let rgb = [255, hue as u8, 255];
        match instance.palette() {
            Palette::Custom(custom) => Rgba::new(
                (custom >> 16) as u8,
                (custom >> 8) as u8,
                custom as u8,
                alpha_byte(1.0, brightness),
            ),
            palette => {
                let mut mapped = rgb;
                map_rgb_palette(&mut mapped, palette);
                Rgba::new(mapped[0], mapped[1], mapped[2], alpha_byte(1.0, brightness))
            }
        }
    } else {
        Rgba::TRANSPARENT
    };
    destination.fill(pixel);
}

fn render_pulse(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let period = python_round(240.0 - instance.setting_number("speed", 50.0) * 2.4).max(30);
    let phase = frame.rem_euclid(period) as f64 / period as f64;
    let mut normalized = (phase * std::f64::consts::TAU).sin() * 0.5 + 0.5;
    match instance.setting_string("pulse_curve", "sine") {
        "smoothstep" => normalized = normalized * normalized * (3.0 - 2.0 * normalized),
        "sharp" => normalized = normalized.powf(2.4),
        _ => normalized = normalized.powf(1.2),
    }
    let minimum = instance.setting_number("minimum_intensity", 22.0) / 100.0;
    let maximum = instance.setting_number("maximum_intensity", 100.0) / 100.0;
    let mut intensity = minimum + normalized * (maximum - minimum);
    let reactivity = instance.setting_number("music_reactivity", 70.0) / 100.0;
    if let Some(audio) = input.audio.filter(|_| reactivity > 0.0) {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat {
            instance.state.pulse =
                (instance.state.pulse + (0.55 + reactive.accent * 0.35) * reactivity).min(1.0);
            instance.state.pulse_hue = (instance.state.pulse_hue
                + ((23.0 + reactive.mid * 18.0) * reactivity) as i32)
                .rem_euclid(256);
        }
        instance.state.pulse = (instance.state.pulse * (1.0 - 0.08 * reactivity))
            .max(reactive.beat_pulse(0.0, 0.18) * reactivity);
        instance.state.pulse_hue =
            (instance.state.pulse_hue + (reactive.low * 1.5 * reactivity) as i32).rem_euclid(256);
        intensity =
            intensity.max((minimum + instance.state.pulse * (maximum - minimum)).min(maximum));
    }
    let hue = (frame * 2
        + (normalized * 18.0) as i64
        + (instance.state.pulse_hue as f64 * reactivity) as i64)
        .rem_euclid(256);
    let palette = instance.palette();
    let brightness = input.brightness.clamp(0.0, 1.0);
    let alpha = alpha_byte(intensity.min(maximum), brightness);
    for index in 0..destination.pixel_count() {
        let spatial = (phase * std::f64::consts::TAU + index as f64 * 0.05).cos() * 0.5 + 0.5;
        let lightness = 46.0 + (spatial * 5.0) as f64;
        let [red, green, blue] = catalog_palette_hsl_to_rgb(hue as f64, 82.0, lightness, palette);
        destination.set_pixel(index, Rgba::new(red, green, blue, alpha));
    }
}

fn render_police(
    instance: &AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let half = destination.pixel_count() / 2;
    let pattern = instance.setting_string("pattern", "alternate");
    let (flash, phase, on_frames) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let rate = (reactive.speed(2.0, 10.0) as i64).max(2);
        let on_frames =
            python_round(rate as f64 * instance.setting_number("duty_cycle", 30.0) / 100.0).max(1);
        (
            audio.beat || frame.rem_euclid(rate) < on_frames,
            frame.rem_euclid(rate * 2),
            on_frames,
        )
    } else {
        let rate = python_round(24.0 - instance.setting_number("speed", 50.0) * 0.16).max(2);
        let on_frames =
            python_round(rate as f64 * instance.setting_number("duty_cycle", 30.0) / 100.0).max(1);
        let phase = frame.rem_euclid(rate);
        (phase < on_frames, phase, on_frames)
    };
    let beat = input.audio.is_some_and(|audio| audio.beat);
    let intensity = instance.brightness(input);
    let (left, right) = if beat {
        ([255, 255, 255], [255, 255, 255])
    } else if flash {
        if pattern == "solid" {
            ([255, 0, 0], [255, 0, 0])
        } else if pattern == "double" && phase < on_frames / 2 {
            ([255, 255, 255], [255, 255, 255])
        } else {
            ([255, 0, 0], [0, 0, 255])
        }
    } else if pattern == "solid" {
        ([0, 0, 0], [0, 0, 0])
    } else {
        ([0, 0, 255], [255, 0, 0])
    };
    for index in 0..destination.pixel_count() {
        let rgb = if index < half { left } else { right };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(1.0, intensity)),
        );
    }
}

fn render_red_rave_sweep(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let (drive, low, accent) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        (
            reactive.drive(),
            reactive.low,
            if audio.beat { reactive.accent } else { 0.0 },
        )
    } else {
        (0.35, 0.25, 0.0)
    };
    let length = destination.pixel_count();
    let width_scale = instance.setting_number("width", 30.0) / 30.0;
    let width = python_round(length as f64 * (0.12 + low * 0.10) * width_scale).max(1) as usize;
    let limit = length.saturating_sub(width) as f64;
    let speed = (0.35 + drive * 0.85 + accent * 0.55)
        * (0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5);
    instance.state.red_sweep_position += instance.state.red_sweep_direction * speed;
    if instance.state.red_sweep_position >= limit {
        let overshoot = instance.state.red_sweep_position - limit;
        instance.state.red_sweep_position = (limit - overshoot).max(0.0);
        instance.state.red_sweep_direction = -1.0;
    } else if instance.state.red_sweep_position <= 0.0 {
        instance.state.red_sweep_position = (-instance.state.red_sweep_position).min(limit);
        instance.state.red_sweep_direction = 1.0;
    }
    let start = python_round(instance.state.red_sweep_position).max(0) as usize;
    let on_frames = python_round(10.0 * instance.setting_number("duty_cycle", 90.0) / 100.0).max(1);
    let on = frame.rem_euclid(10) < on_frames;
    let alpha = alpha_byte(1.0, instance.brightness(input));
    for index in 0..length {
        let illuminated = on && index >= start && index - start < width;
        destination.set_pixel(
            index,
            if illuminated {
                Rgba::new(255, 0, 0, alpha)
            } else {
                Rgba::new(0, 0, 0, 255)
            },
        );
    }
}

fn render_red_rave_chase(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let (drive, beat) = if let Some(audio) = input.audio {
        (AudioReactive::from_input(audio).drive(), audio.beat)
    } else {
        (0.4, frame > 0 && frame.rem_euclid(18) == 0)
    };
    let length = destination.pixel_count();
    let count = instance.setting_number("count", 4.0);
    let period = python_round(length as f64 / count).max(2) as usize;
    let block = python_round(period as f64 * instance.setting_number("duty_cycle", 50.0) / 100.0)
        .max(1) as usize;
    if beat {
        instance.state.red_chase_direction *= -1.0;
    }
    instance.state.red_chase_phase = (instance.state.red_chase_phase
        + instance.state.red_chase_direction
            * (0.45 + drive * 0.9)
            * (0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5))
        .rem_euclid(period as f64);
    let alpha = alpha_byte(1.0, instance.brightness(input));
    let midpoint = (length + 1) / 2;
    for index in 0..length {
        let mirrored = if index < midpoint {
            index
        } else {
            length - 1 - index
        };
        let illuminated = (mirrored as f64 + instance.state.red_chase_phase)
            .rem_euclid(period as f64)
            < block as f64;
        destination.set_pixel(
            index,
            if illuminated {
                Rgba::new(255, 0, 0, alpha)
            } else {
                Rgba::new(0, 0, 0, 255)
            },
        );
    }
}

fn render_red_blackout_strobe(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let duty_cycle = instance.setting_number("duty_cycle", 30.0);
    let on = if let Some(audio) = input.audio {
        let min_gap = python_round(20.0 - instance.setting_number("flash_rate", 50.0) * 0.2).max(1);
        if audio.beat && frame - instance.state.red_last_beat_frame >= min_gap {
            instance.state.red_last_beat_frame = frame;
            instance.state.red_flash_frames = python_round(5.0 * duty_cycle / 100.0).max(1);
        }
        let active = instance.state.red_flash_frames > 0;
        instance.state.red_flash_frames = (instance.state.red_flash_frames - 1).max(0);
        active
    } else {
        let period = python_round(20.0 - instance.setting_number("flash_rate", 50.0) * 0.2).max(2);
        let on_frames = python_round(period as f64 * duty_cycle / 100.0).max(1);
        frame.rem_euclid(period) < on_frames
    };
    let alpha = if on {
        alpha_byte(1.0, instance.brightness(input))
    } else {
        255
    };
    destination.fill(if on {
        Rgba::new(255, 0, 0, alpha)
    } else {
        Rgba::new(0, 0, 0, alpha)
    });
}

fn render_glow_rush(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    if instance.frame_index == 0 {
        destination.fill(Rgba::BLACK);
    }
    let length = destination.pixel_count();
    let max_position = length.saturating_sub(1) as f64;
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let (width, pulse, glow_width, main_light, glow_alpha, audio_mode) = if let Some(audio) =
        input.audio
    {
        let reactive = AudioReactive::from_input(audio);
        let width = (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.0333)
            .max(1.0)
            + reactive.high * 2.0;
        instance.state.glow_rush_position = (instance.state.glow_rush_position
            + reactive.speed(0.6, 3.5) * speed_scale)
            .rem_euclid(max_position + width * 2.0);
        instance.state.glow_rush_hue = reactive.hue_shift(frame, 0.3);
        let pulse = (0.7 + reactive.rms * 0.3 * 0.5 + reactive.accent * 0.3).clamp(0.0, 1.0);
        (
            width,
            pulse,
            width * (2.0 + instance.setting_number("frequency", 50.0) / 50.0),
            (40.0 + reactive.mid * 20.0) as i32,
            0.06,
            true,
        )
    } else {
        let width =
            (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.0333).max(1.0);
        instance.state.glow_rush_position = (instance.state.glow_rush_position + 1.5 * speed_scale)
            .rem_euclid(max_position + width * 2.0);
        instance.state.glow_rush_hue = (frame * 2).rem_euclid(256) as i32;
        (
            width,
            0.88 + 0.12
                * (frame as f64 * (0.02 + instance.setting_number("frequency", 50.0) / 1000.0))
                    .sin(),
            width * 3.3,
            60,
            0.08,
            false,
        )
    };
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let distance = (index as f64 - instance.state.glow_rush_position).abs();
        let normalized = distance / width;
        let mut tail = (1.0 - normalized).max(0.0);
        tail *= tail;
        let (alpha, lightness) = if tail > 0.01 {
            ((tail * pulse).min(1.0), main_light)
        } else {
            let alpha = (1.0 - distance / glow_width).max(0.0) * glow_alpha;
            if alpha <= 0.01 {
                continue;
            }
            (alpha, if audio_mode { 20 } else { 30 })
        };
        let [red, green, blue] = palette_hsl_to_rgb(
            f64::from(instance.state.glow_rush_hue),
            100.0,
            f64::from(lightness),
            palette,
        );
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_rave_pulse(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let (wave, hue, lightness) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if reactive.accent >= instance.state.rave_pulse_flash {
            instance.state.rave_pulse_flash = reactive.accent.min(1.0);
        } else {
            instance.state.rave_pulse_flash = (instance.state.rave_pulse_flash - 0.08)
                .max(reactive.accent)
                .max(0.0);
        }
        if audio.beat {
            instance.state.rave_pulse_flash = (instance.state.rave_pulse_flash + 0.6).min(1.0);
            instance.state.rave_pulse_hue = (instance.state.rave_pulse_hue + 37).rem_euclid(256);
        }
        (
            (instance.state.rave_pulse_flash + reactive.drive()).min(1.0),
            instance.state.rave_pulse_hue,
            (40.0 + reactive.mid * 20.0 + instance.state.rave_pulse_flash * 20.0) as i32,
        )
    } else {
        (
            (1.0 + (frame as f64 * 0.15).sin()) * 0.5,
            (frame * 4).rem_euclid(256) as i32,
            55,
        )
    };
    let alpha = match instance.setting_string("pulse_curve", "sine") {
        "sharp" => wave.powf(2.2),
        "smoothstep" => wave * wave * (3.0 - 2.0 * wave),
        _ => wave * wave,
    };
    let [red, green, blue] = palette_hsl_to_rgb(
        f64::from(hue),
        100.0,
        f64::from(lightness),
        instance.palette(),
    );
    let pixel = Rgba::new(
        red,
        green,
        blue,
        alpha_byte(alpha, instance.brightness(input)),
    );
    destination.fill(pixel);
}

fn render_rave_scanner(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const CLUB_HUES: [i32; 7] = [330, 18, 48, 126, 190, 224, 286];
    let default_audio = AudioInput {
        rms: 0.2,
        bands: [0.2, 0.24, 0.22, 0.2, 0.18, 0.16, 0.14, 0.12],
        beat: false,
        beat_strength: 0.0,
    };
    let audio = input.audio.unwrap_or(default_audio);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(19) == 0, |frame| frame.beat);
    let length = destination.pixel_count().max(1);
    let travel = match instance.setting_string("direction", "bounce") {
        "forward" => TravelDirection::Forward,
        "reverse" => TravelDirection::Reverse,
        _ => TravelDirection::Bounce,
    };
    if beat && travel == TravelDirection::Bounce {
        instance.state.rave_scanner_direction *= -1.0;
        instance.state.rave_scanner_flash = 1.0;
    }
    let width = (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0)
        + reactive.low * 3.0;
    let speed = (reactive.speed(0.65, 1.75) + reactive.low * 0.35)
        * (0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5);
    let movement_direction = match travel {
        TravelDirection::Reverse => -1.0,
        TravelDirection::Forward => 1.0,
        TravelDirection::Bounce => instance.state.rave_scanner_direction,
    };
    instance.state.rave_scanner_phase += movement_direction * speed.max(0.45);
    let maximum = length.saturating_sub(1) as f64;
    if instance.state.rave_scanner_phase <= 0.0 || instance.state.rave_scanner_phase >= maximum {
        if travel == TravelDirection::Bounce {
            instance.state.rave_scanner_direction *= -1.0;
            instance.state.rave_scanner_phase =
                instance.state.rave_scanner_phase.clamp(0.0, maximum);
        } else {
            instance.state.rave_scanner_phase =
                instance.state.rave_scanner_phase.rem_euclid(length as f64);
        }
    }
    if beat {
        instance.state.rave_scanner_flash = 1.0;
    }
    instance.state.rave_scanner_flash = (instance.state.rave_scanner_flash - 0.16).max(0.0);
    let hue = reactive.hue_shift(frame, 2.2);
    let beam_brightness =
        (0.25 + reactive.low * 0.6 + instance.state.rave_scanner_flash * 0.5).min(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::BLACK);
    for index in 0..destination.pixel_count() {
        let distance = (index as f64 - instance.state.rave_scanner_phase).abs();
        if distance > width {
            continue;
        }
        let intensity = (1.0 - distance / width).max(0.0);
        let alpha = (beam_brightness * (0.25 + intensity * 0.75)).min(1.0);
        let distance_hue = ((distance * 18.0) as i64).rem_euclid(CLUB_HUES.len() as i64) as i32;
        let color_index = (hue.rem_euclid(CLUB_HUES.len() as i32) + distance_hue)
            .rem_euclid(CLUB_HUES.len() as i32) as usize;
        let [red, green, blue] =
            palette_hsl_to_rgb(f64::from(CLUB_HUES[color_index]), 100.0, 60.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_neon_confetti(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.16,
        bands: [0.1, 0.12, 0.12, 0.14, 0.12, 0.16, 0.18, 0.2],
        beat: false,
        beat_strength: 0.0,
    };
    const NEON_HUES: [i32; 7] = [300, 160, 52, 206, 12, 268, 132];
    let length = destination.pixel_count();
    if instance.state.neon_confetti_levels.len() != length {
        instance.state.neon_confetti_levels = vec![0.0; length];
        instance.state.neon_confetti_hues = vec![0; length];
    }
    let reactive = AudioReactive::from_input(input.audio.unwrap_or(DEFAULT_AUDIO));
    let fade = instance.setting_number("fade_time", 50.0) / 100.0;
    let count = instance.setting_number("count", 4.0);
    let spread = instance.setting_number("spread", 60.0) / 100.0;
    let fade_multiplier = (0.68 + fade * 0.28 - reactive.high * 0.02).max(0.7);
    let shimmer =
        (reactive.high * 0.6 + reactive.mid * 0.2 + reactive.accent * 0.2).clamp(0.0, 1.0);
    let spawn_threshold = (0.002
        + count * 0.003
        + reactive.drive() * 0.018
        + shimmer * 0.012
        + reactive.accent * 0.03)
        * 10_000.0;
    let safe_length = length.max(1) as u64;
    let center = (frame as u64).wrapping_mul(53) % safe_length;
    let frame_seed = (frame as u64).wrapping_mul(48_271);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::BLACK);
    for index in 0..length {
        let seed = frame_seed.wrapping_add((index as u64).wrapping_mul(69_621)) & 0xffff_ffff;
        let direct_distance = index.abs_diff(center as usize) as f64;
        let distance = direct_distance.min(length as f64 - direct_distance);
        let weight =
            (1.0 - spread) + spread * (1.0 - distance / (length as f64 * 0.5).max(1.0)).max(0.0);
        let mut level =
            (f64::from(instance.state.neon_confetti_levels[index]) * fade_multiplier) as f32;
        if ((seed % 10_000) as f64) < spawn_threshold * weight {
            level = (f64::from(level) + 0.5 + reactive.high * 0.25).min(1.0) as f32;
            instance.state.neon_confetti_hues[index] =
                ((seed / 100 + (index as u64).wrapping_mul(13)) % 360) as i16;
        }
        instance.state.neon_confetti_levels[index] = level;
        if level <= 0.015 {
            continue;
        }
        let hue_index = instance.state.neon_confetti_hues[index].rem_euclid(7) as usize;
        let [red, green, blue] =
            palette_hsl_to_rgb(f64::from(NEON_HUES[hue_index]), 100.0, 64.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(
                red,
                green,
                blue,
                alpha_byte(0.12 + f64::from(level) * 0.78, brightness),
            ),
        );
    }
}

fn render_strobe_chase(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const CLUB_HUES: [i32; 7] = [330, 18, 48, 126, 190, 224, 286];
    const WARM_HUES: [i32; 4] = [0, 20, 38, 320];
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.2,
        bands: [0.22, 0.2, 0.18, 0.16, 0.14, 0.18, 0.2, 0.22],
        beat: false,
        beat_strength: 0.0,
    };
    let audio = input.audio.unwrap_or(DEFAULT_AUDIO);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(12) == 0, |frame| frame.beat);
    let bass_hit = if input.audio.is_some() {
        beat && (reactive.low > 0.34 || reactive.accent > 0.52)
    } else {
        frame.rem_euclid(18) == 0
    };
    let stride = (instance.setting_number("count", 4.0) as usize).max(2);
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let advance = (0.3 + reactive.drive() * 0.65) * speed_scale;
    instance.state.strobe_chase_phase =
        (instance.state.strobe_chase_phase + advance + if beat { 0.25 } else { 0.0 })
            .rem_euclid(stride as f64);
    instance.state.strobe_chase_hue_phase =
        (instance.state.strobe_chase_hue_phase + 0.6 + reactive.high * 1.2).rem_euclid(360.0);
    if bass_hit {
        instance.state.strobe_chase_flash = 1.0;
    }
    let release = 0.02 + (1.0 - instance.setting_number("duty_cycle", 30.0) / 100.0) * 0.24;
    instance.state.strobe_chase_flash = (instance.state.strobe_chase_flash - release).max(0.0);
    let hue = instance.state.strobe_chase_hue_phase as i32;
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    if instance.state.strobe_chase_flash > 0.0 {
        let warm_hue = WARM_HUES[(hue + frame as i32).rem_euclid(4) as usize];
        let alpha = (0.12 + instance.state.strobe_chase_flash * 0.88).min(1.0);
        let [red, green, blue] = palette_hsl_to_rgb(warm_hue as f64, 100.0, 60.0, palette);
        destination.fill(Rgba::new(red, green, blue, alpha_byte(alpha, brightness)));
        return;
    }
    destination.fill(Rgba::BLACK);
    for index in 0..destination.pixel_count() {
        let slot = index % stride;
        let direct = (slot as f64 - instance.state.strobe_chase_phase).abs();
        let distance = direct.min(stride as f64 - direct);
        let chase = (1.0 - distance / 1.2).max(0.0);
        if chase <= 0.01 {
            continue;
        }
        let alpha = chase * (0.34 + reactive.drive() * 0.5);
        let color_seed = hue + (index * 11) as i32;
        let club_hue = CLUB_HUES[color_seed.rem_euclid(CLUB_HUES.len() as i32) as usize];
        let [red, green, blue] = palette_hsl_to_rgb(club_hue as f64, 100.0, 58.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_dance_floor(
    instance: &AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let frequency = instance.setting_number("frequency", 50.0);
    let segment_width = python_round(16.0 - frequency * 0.16).max(2) as usize;
    let length = destination.pixel_count();
    let segments = length.div_ceil(segment_width).max(1);
    let (audio_mode, hue_shift, mid, high, drive, beat_boost) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        (
            true,
            reactive.hue_shift(frame, 0.15),
            reactive.mid,
            reactive.high,
            reactive.drive(),
            if audio.beat { reactive.accent } else { 0.0 },
        )
    } else {
        (false, 0, 0.0, 0.0, 0.0, 0.0)
    };
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let segment = index / segment_width;
        let segment_hue = (segment % 256 * 40) as i32;
        let phase = segment as f64 * (0.4 + frequency / 50.0) + frame as f64 * 0.08;
        let breathing = phase.sin() * 0.5 + 0.5;
        let (hue, lightness, alpha) = if audio_mode {
            (
                (segment_hue + hue_shift).rem_euclid(256),
                (30.0
                    + mid * 20.0
                    + segment as f64 / segments as f64 * high * 25.0
                    + beat_boost * 25.0
                    + breathing * 4.0) as i32,
                (0.6 + drive * 0.4 + beat_boost * 0.3).min(1.0),
            )
        } else {
            (
                (segment_hue + frame.rem_euclid(128) as i32 * 2).rem_euclid(256),
                40 + ((1.0 + phase.sin()) * 15.0) as i32,
                1.0,
            )
        };
        let [red, green, blue] =
            palette_hsl_to_rgb(f64::from(hue), 100.0, f64::from(lightness), palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_plasma_rave(
    instance: &AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let frequency = 0.5 + instance.setting_number("frequency", 50.0) / 100.0;
    let reactive = input.audio.map(AudioReactive::from_input);
    let speed = reactive.map_or(0.0, |audio| audio.speed(0.03, 0.12));
    let hue_base = reactive.map_or(0, |audio| audio.hue_shift(frame, 0.15));
    let pulse = reactive.map_or(0.0, |audio| {
        (audio.rms * 0.15 + audio.accent * 0.3).clamp(0.0, 1.0)
    });
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let length = destination.pixel_count();
    for index in 0..length {
        let position = index as f64 / length.saturating_sub(1).max(1) as f64;
        let (v1_phase, v2_phase, v3_phase, hue_scale) = if reactive.is_some() {
            (
                position * 4.0 * frequency + frame as f64 * speed * 0.8,
                position * 8.0 * frequency - frame as f64 * speed * 1.2 + 1.2,
                (position + frame as f64 * 0.01 * speed) * 12.0 * frequency,
                180.0,
            )
        } else {
            (
                position * 4.0 * frequency + frame as f64 * 0.05,
                position * 8.0 * frequency - frame as f64 * 0.07 + 1.2,
                (position + frame as f64 * 0.01) * 12.0 * frequency,
                255.0,
            )
        };
        let plasma = v1_phase.sin() + v2_phase.sin() + v3_phase.sin();
        let hue = if reactive.is_some() {
            (hue_base + ((plasma * 0.5 + 0.5) * hue_scale) as i32).rem_euclid(256)
        } else {
            (((plasma * 0.5 + 0.5) * hue_scale + frame as f64 * 2.0) as i64).rem_euclid(256) as i32
        };
        let alpha = 0.4 + plasma.abs() * 0.3 + pulse;
        let alpha = if reactive.is_some() {
            alpha.min(1.0)
        } else {
            alpha
        };
        let [red, green, blue] = palette_hsl_to_rgb(f64::from(hue), 100.0, 55.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(
                red,
                green,
                blue,
                ((alpha * brightness).clamp(0.0, 1.0) * 255.0) as u8,
            ),
        );
    }
}

fn render_dual_laser(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let maximum = length.saturating_sub(1) as f64;
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let (base_alpha, width) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat {
            instance.state.dual_laser_a_direction *= -1.0;
            instance.state.dual_laser_b_direction *= -1.0;
            instance.state.dual_laser_burst = 1.0;
        }
        let speed = reactive.speed(0.6, 2.5) * speed_scale;
        advance_dual_laser(
            &mut instance.state.dual_laser_a_position,
            instance.state.dual_laser_a_direction,
            speed,
            maximum,
        );
        advance_dual_laser(
            &mut instance.state.dual_laser_b_position,
            instance.state.dual_laser_b_direction,
            speed,
            maximum,
        );
        instance.state.dual_laser_a_hue = reactive.hue_shift(frame, 0.6);
        instance.state.dual_laser_b_hue = (instance.state.dual_laser_a_hue + 140).rem_euclid(256);
        instance.state.dual_laser_burst = (instance.state.dual_laser_burst - 0.06).max(0.0);
        (
            0.7 + instance.state.dual_laser_burst * 0.3,
            (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0)
                + reactive.high * 2.5
                + instance.state.dual_laser_burst * 3.0,
        )
    } else {
        let speed = 1.5 * speed_scale;
        advance_dual_laser(
            &mut instance.state.dual_laser_a_position,
            instance.state.dual_laser_a_direction,
            speed,
            maximum,
        );
        advance_dual_laser(
            &mut instance.state.dual_laser_b_position,
            instance.state.dual_laser_b_direction,
            speed,
            maximum,
        );
        instance.state.dual_laser_a_hue = frame.wrapping_mul(5).rem_euclid(256) as i32;
        instance.state.dual_laser_b_hue = (instance.state.dual_laser_a_hue + 140).rem_euclid(256);
        (
            0.9,
            (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0),
        )
    };
    let separation = instance.setting_number("separation", 50.0) / 100.0;
    instance.state.dual_laser_b_position = (instance.state.dual_laser_a_position
        + maximum * separation)
        .rem_euclid(length.max(1) as f64);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::BLACK);
    for index in 0..length {
        for (position, hue) in [
            (
                instance.state.dual_laser_a_position,
                instance.state.dual_laser_a_hue,
            ),
            (
                instance.state.dual_laser_b_position,
                instance.state.dual_laser_b_hue,
            ),
        ] {
            let distance = (index as f64 - position).abs();
            if distance >= width {
                continue;
            }
            let alpha = (1.0 - distance / width) * base_alpha;
            let [red, green, blue] = palette_hsl_to_rgb(f64::from(hue), 100.0, 60.0, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
            );
        }
    }
}

fn advance_dual_laser(position: &mut f64, direction: f64, speed: f64, maximum: f64) {
    *position += speed * direction;
    if *position > maximum + 2.0 {
        *position = -2.0;
    } else if *position < -2.0 {
        *position = maximum + 2.0;
    }
}

fn render_disco_comet(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.18,
        bands: [0.08, 0.12, 0.16, 0.18, 0.14, 0.16, 0.18, 0.2],
        beat: false,
        beat_strength: 0.0,
    };
    const CLUB_HUES: [i32; 7] = [330, 18, 48, 126, 190, 224, 286];
    const NEON_HUES: [i32; 7] = [300, 160, 52, 206, 12, 268, 132];
    let audio = input.audio.unwrap_or(DEFAULT_AUDIO);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(21) == 0, |frame| frame.beat);
    if beat {
        instance.state.disco_comet_direction *= -1.0;
    }
    let travel = match instance.setting_string("direction", "bounce") {
        "forward" => TravelDirection::Forward,
        "reverse" => TravelDirection::Reverse,
        _ => TravelDirection::Bounce,
    };
    let length = destination.pixel_count().max(1);
    let speed = reactive.speed(0.45, 1.65)
        * (0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5)
        + reactive.rms * 0.35
        + reactive.mid * 0.15;
    instance.state.disco_comet_phase += instance.state.disco_comet_direction * speed.max(0.3);
    let maximum = length.saturating_sub(1) as f64;
    match travel {
        TravelDirection::Forward => {
            instance.state.disco_comet_phase =
                instance.state.disco_comet_phase.rem_euclid(length as f64);
            instance.state.disco_comet_direction = 1.0;
        }
        TravelDirection::Reverse => {
            instance.state.disco_comet_phase =
                instance.state.disco_comet_phase.rem_euclid(length as f64);
            instance.state.disco_comet_direction = -1.0;
        }
        TravelDirection::Bounce
            if instance.state.disco_comet_phase <= 0.0
                || instance.state.disco_comet_phase >= maximum =>
        {
            instance.state.disco_comet_direction *= -1.0;
            instance.state.disco_comet_phase = instance.state.disco_comet_phase.clamp(0.0, maximum);
        }
        TravelDirection::Bounce => {}
    }
    let width = (length as f64 * instance.setting_number("tail_length", 8.0) / 100.0).max(1.0);
    let trail = 0.15 + reactive.drive() * 0.25;
    let sparkle_probability = 0.08 + reactive.high * 0.32 + reactive.accent * 0.18;
    let sparkle_threshold = (sparkle_probability * 100.0) as i64;
    let hue = reactive.hue_shift(frame, 0.55);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::BLACK);
    for index in 0..destination.pixel_count() {
        let distance = (index as f64 - instance.state.disco_comet_phase).abs();
        if distance > width {
            continue;
        }
        let intensity = (1.0 - distance / width).max(0.0);
        let alpha = (trail + intensity * 0.85).min(1.0);
        let club_index = (hue + (index * 5) as i32).rem_euclid(CLUB_HUES.len() as i32) as usize;
        let [red, green, blue] =
            palette_hsl_to_rgb(CLUB_HUES[club_index] as f64, 100.0, 58.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
        let sparkle_seed = frame
            .wrapping_mul(97)
            .wrapping_add((index as i64).wrapping_mul(31))
            .wrapping_add((reactive.high * 100.0) as i64);
        if distance < width * 0.8 && sparkle_seed.rem_euclid(100) < sparkle_threshold {
            let neon_index =
                (hue + sparkle_seed as i32).rem_euclid(NEON_HUES.len() as i32) as usize;
            let sparkle_alpha = (0.65 + reactive.high * 0.35).min(1.0);
            let [red, green, blue] =
                palette_hsl_to_rgb(NEON_HUES[neon_index] as f64, 100.0, 62.0, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(sparkle_alpha, brightness)),
            );
            if index + 1 < length && sparkle_seed.div_euclid(7).rem_euclid(2) == 0 {
                let neighbor_seed = sparkle_seed.wrapping_add(13);
                let neon_index =
                    (hue + neighbor_seed as i32).rem_euclid(NEON_HUES.len() as i32) as usize;
                let sparkle_alpha = (0.42 + reactive.high * 0.24).min(1.0);
                let [red, green, blue] =
                    palette_hsl_to_rgb(NEON_HUES[neon_index] as f64, 100.0, 62.0, palette);
                destination.set_pixel(
                    index + 1,
                    Rgba::new(red, green, blue, alpha_byte(sparkle_alpha, brightness)),
                );
            }
        }
    }
}

fn render_neon_storm(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.22,
        bands: [0.65, 0.6, 0.2, 0.18, 0.16, 0.18, 0.22, 0.24],
        beat: false,
        beat_strength: 0.0,
    };
    const PALETTE: [(i32, f64, f64); 5] = [
        (0, 100.0, 60.0),
        (200, 100.0, 55.0),
        (120, 100.0, 50.0),
        (300, 100.0, 60.0),
        (50, 100.0, 55.0),
    ];
    let length = destination.pixel_count();
    if instance.state.neon_storm_levels.len() != length {
        instance.state.neon_storm_levels = vec![0.0; length];
        instance.state.neon_storm_hues = vec![0; length];
        instance.state.neon_storm_streak_positions.clear();
    }
    let reactive = AudioReactive::from_input(input.audio.unwrap_or(DEFAULT_AUDIO));
    let decay = instance.setting_number("decay", 50.0) / 100.0;
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let fade_multiplier = 0.66 + decay * 0.32;
    for level in &mut instance.state.neon_storm_levels {
        *level = (f64::from(*level) * fade_multiplier) as f32;
    }
    let count = instance.setting_number("count", 4.0) as usize;
    let streak_count = count.min(1 + (reactive.low * count as f64) as usize);
    while instance.state.neon_storm_streak_positions.len() < streak_count {
        let next = instance.state.neon_storm_streak_positions.len();
        instance
            .state
            .neon_storm_streak_positions
            .push((next + 1) as f64 * length as f64 / (streak_count + 1) as f64);
    }
    instance
        .state
        .neon_storm_streak_positions
        .truncate(streak_count);
    for streak in 0..streak_count {
        let speed = (0.35 + streak as f64 * 0.18 + reactive.drive() * 1.1 + reactive.high * 0.5)
            * speed_scale;
        instance.state.neon_storm_streak_positions[streak] =
            (instance.state.neon_storm_streak_positions[streak] + speed)
                .rem_euclid(length.max(1) as f64);
    }
    let shimmer =
        (reactive.high * 0.6 + reactive.mid * 0.2 + reactive.accent * 0.2).clamp(0.0, 1.0);
    let spawn_threshold =
        (0.006 + reactive.drive() * 0.018 + shimmer * 0.016 + reactive.accent * 0.035) * 10_000.0;
    let frame_seed = (frame as u64).wrapping_mul(48_271);
    for index in 0..length {
        let seed = frame_seed.wrapping_add((index as u64).wrapping_mul(63_689)) & 0xffff_ffff;
        if ((seed % 10_000) as f64) < spawn_threshold {
            instance.state.neon_storm_levels[index] =
                (f64::from(instance.state.neon_storm_levels[index]) + 0.55).min(1.0) as f32;
            instance.state.neon_storm_hues[index] = ((seed / 100) % PALETTE.len() as u64) as i16;
        }
    }
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::TRANSPARENT);
    for index in 0..length {
        let particle = f64::from(instance.state.neon_storm_levels[index]);
        let mut streak_strength = 0.0_f64;
        let mut streak_seed = 0;
        for (streak_index, position) in instance
            .state
            .neon_storm_streak_positions
            .iter()
            .copied()
            .enumerate()
        {
            let absolute_distance = (index as f64 - position).abs();
            let distance = absolute_distance.min((length as f64 - absolute_distance).max(0.0));
            if distance < 3.5 {
                let strength = 1.0 - distance / 3.5;
                if strength > streak_strength {
                    streak_strength = strength;
                    streak_seed = streak_index;
                }
            }
        }
        let alpha = (particle * 0.8 + streak_strength * (0.35 + reactive.drive() * 0.3)).min(1.0);
        if alpha <= 0.01 {
            continue;
        }
        let palette_index = if particle >= streak_strength {
            instance.state.neon_storm_hues[index] as usize
        } else {
            streak_seed
        } % PALETTE.len();
        let (hue, saturation, lightness) = PALETTE[palette_index];
        let [red, green, blue] = palette_hsl_to_rgb(f64::from(hue), saturation, lightness, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_peak_mirror(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let (levels, hue_shift) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let decay_rate =
            0.04 + instance.setting_number("decay", 50.0) * 0.0022 + reactive.high * 0.08;
        let mut levels = [0.0; 8];
        for (band, level) in levels.iter_mut().enumerate() {
            let target = audio.bands[band];
            let peak = &mut instance.state.peak_mirror_peaks[band];
            if target >= *peak {
                *peak = target.min(1.0);
            } else {
                *peak = (*peak - decay_rate).max(target).max(0.0);
            }
            *level = *peak;
        }
        if audio.beat {
            instance.state.peak_mirror_center_flash = instance
                .state
                .peak_mirror_center_flash
                .max(reactive.beat_pulse(0.0, 0.95));
            for level in &mut levels {
                *level = (*level + reactive.accent * 0.45).min(1.0);
            }
        }
        (levels, reactive.hue_shift(frame, 0.4).rem_euclid(360))
    } else {
        let mut levels = [0.0; 8];
        for (band, level) in levels.iter_mut().enumerate() {
            *level = 0.14 + ((frame as f64 * 0.08 + band as f64 * 0.7).sin() * 0.5 + 0.5) * 0.38;
        }
        (levels, ((frame as f64 * 0.4) as i64).rem_euclid(360) as i32)
    };
    let length = destination.pixel_count();
    if length == 0 {
        return;
    }
    let mirror_span = ((length - 1) / 2).max(1);
    let center = (length - 1) as f64 / 2.0;
    let center_width =
        (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.08).max(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let mirrored = index.min(length - 1 - index);
        let position = if length == 1 {
            1.0
        } else {
            (mirrored as f64 / mirror_span as f64).min(1.0)
        };
        let band_position = position * 7.0;
        let lower = band_position as usize;
        let upper = (lower + 1).min(7);
        let blend = band_position - lower as f64;
        let interpolated = levels[lower] * (1.0 - blend) + levels[upper] * blend;
        let center_glow = (1.0 - (index as f64 - center).abs() / center_width).max(0.0);
        let beat_glow = instance.state.peak_mirror_center_flash * center_glow;
        let level = interpolated.max(beat_glow);
        if level <= 0.015 {
            let rgb = if matches!(palette, Palette::Rainbow) {
                [0, 0, 0]
            } else {
                catalog_palette_hsl_to_rgb(0.0, 0.0, 36.0, palette)
            };
            destination.set_pixel(index, Rgba::new(rgb[0], rgb[1], rgb[2], 0));
            continue;
        }
        let hue = (hue_shift + (band_position * 28.0) as i32).rem_euclid(360);
        let lightness = (48.0 + level * 12.0 + beat_glow * 6.0).min(66.0);
        let alpha = (0.08 + level * 0.9).min(1.0);
        let [red, green, blue] = palette_hsl_to_rgb(f64::from(hue), 100.0, lightness, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
    if input.audio.is_some() {
        instance.state.peak_mirror_center_flash *= 0.92;
    }
}

fn render_shockwave(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let mut width =
        (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    let reactive = input.audio.map(AudioReactive::from_input);
    let (hue, mid, audio) = if let (Some(audio_input), Some(reactive)) = (input.audio, reactive) {
        let release = 0.04 + reactive.high * 0.04;
        if reactive.accent >= instance.state.shockwave_accent {
            instance.state.shockwave_accent = reactive.accent.min(1.0);
        } else {
            instance.state.shockwave_accent = (instance.state.shockwave_accent - release)
                .max(reactive.accent)
                .max(0.0);
        }
        if audio_input.beat {
            instance.state.shockwave_radius = 0.0;
        } else {
            instance.state.shockwave_radius += reactive.speed(0.48, 1.7) * speed_scale;
            if instance.state.shockwave_radius > center + 8.0 {
                instance.state.shockwave_radius = 0.0;
            }
        }
        width += reactive.high * 2.0;
        (
            (170 + (reactive.low * 18.0) as i32 - (reactive.high * 10.0) as i32).rem_euclid(256),
            reactive.mid,
            Some(audio_input),
        )
    } else {
        instance.state.shockwave_radius =
            (frame as f64 * 0.32 * speed_scale).rem_euclid(center + 6.0);
        (188, 0.0, None)
    };
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let distance = (index as f64 - center).abs();
        let ring = (1.0 - (distance - instance.state.shockwave_radius).abs() / width).max(0.0);
        let ambient = (1.0 - distance / center.max(1.0)).max(0.0) * 0.12;
        let (alpha, saturation, lightness) = if let Some(audio) = audio {
            let band = band_window(audio, index, length, 1);
            (
                (ambient + ring * 0.88 + instance.state.shockwave_accent * 0.05).min(1.0),
                95.0,
                (52.0 + mid * 10.0 + band * 8.0 + instance.state.shockwave_accent * 4.0).min(68.0)
                    as i32 as f64,
            )
        } else {
            (ambient + ring * 0.88, 100.0, 58.0)
        };
        let [red, green, blue] = palette_hsl_to_rgb(f64::from(hue), saturation, lightness, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_center_burst(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.18,
        bands: [0.18, 0.2, 0.22, 0.2, 0.16, 0.14, 0.12, 0.1],
        beat: false,
        beat_strength: 0.0,
    };
    const CLUB_HUES: [i32; 7] = [330, 18, 48, 126, 190, 224, 286];
    let length = destination.pixel_count().max(1);
    let audio = input.audio.unwrap_or(DEFAULT_AUDIO);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(32) == 0, |audio| audio.beat);
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    if beat && (reactive.low > 0.22 || reactive.accent > 0.46 || reactive.rms > 0.5) {
        instance.state.center_burst_radius = 0.0;
        instance.state.center_burst_speed = (0.9 + reactive.accent * 2.2) * speed_scale;
        instance.state.center_burst_width =
            (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.1).max(1.0)
                + reactive.accent * 2.0;
        instance.state.center_burst_hue_seed =
            frame.wrapping_mul(23) + (reactive.low * 80.0) as i64;
        instance.state.center_burst_flash = (0.35 + reactive.accent * 0.65).min(1.0);
    } else if instance.state.center_burst_flash > 0.0 {
        instance.state.center_burst_speed = instance
            .state
            .center_burst_speed
            .max((0.75 + reactive.drive() * 1.1) * speed_scale);
    } else {
        instance.state.center_burst_radius += 0.3 + reactive.drive() * 0.4;
    }
    instance.state.center_burst_radius += instance.state.center_burst_speed;
    instance.state.center_burst_flash = (instance.state.center_burst_flash - 0.08).max(0.0);
    let center = length.saturating_sub(1) as f64 / 2.0;
    let width = instance.state.center_burst_width.max(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::BLACK);
    for index in 0..destination.pixel_count() {
        let distance = (index as f64 - center).abs();
        let intensity =
            (1.0 - (distance - instance.state.center_burst_radius).abs() / width).max(0.0);
        if intensity <= 0.0 {
            continue;
        }
        let alpha = (intensity * (0.28 + instance.state.center_burst_flash * 0.72)).min(1.0);
        let hue_index = (instance.state.center_burst_hue_seed + index as i64)
            .rem_euclid(CLUB_HUES.len() as i64) as usize;
        let [red, green, blue] =
            palette_hsl_to_rgb(CLUB_HUES[hue_index] as f64, 100.0, 60.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_mirror_flash(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.16,
        bands: [0.16, 0.14, 0.14, 0.16, 0.14, 0.12, 0.1, 0.08],
        beat: false,
        beat_strength: 0.0,
    };
    const CLUB_HUES: [i32; 7] = [330, 18, 48, 126, 190, 224, 286];
    const WARM_HUES: [i32; 4] = [0, 20, 38, 320];
    let audio = input.audio.unwrap_or(DEFAULT_AUDIO);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(17) == 0, |audio| audio.beat);
    if beat && (reactive.low > 0.2 || reactive.accent > 0.42 || reactive.rms > 0.4) {
        instance.state.mirror_flash_value = (0.34 + reactive.accent * 0.66).min(1.0);
        instance.state.mirror_flash_hue_seed =
            frame.wrapping_mul(31) + (reactive.mid * 60.0) as i64;
        instance.state.mirror_flash_alternate = 1 - instance.state.mirror_flash_alternate;
    }
    let decay = instance.setting_number("decay", 50.0) / 100.0;
    let release = 0.02 + decay * 0.16;
    instance.state.mirror_flash_value = (instance.state.mirror_flash_value - release).max(0.0);
    let length = destination.pixel_count().max(1);
    let center = length.saturating_sub(1) as f64 / 2.0;
    let width = instance.setting_number("width", 30.0) / 100.0;
    let span = (center * (0.2 + width * 0.8) * (0.3 + reactive.drive() * 0.7)).max(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    destination.fill(Rgba::BLACK);
    for index in 0..length.div_ceil(2) {
        let distance = (index as f64 - center).abs();
        let intensity = (1.0 - distance / span).max(0.0) * instance.state.mirror_flash_value;
        if intensity <= 0.0 {
            continue;
        }
        let hue_seed = instance.state.mirror_flash_hue_seed
            + if instance.state.mirror_flash_alternate != 0 {
                (index * 13) as i64
            } else {
                -((index * 9) as i64)
            };
        let alpha = (0.14 + intensity * 0.86).min(1.0);
        let club_hue = CLUB_HUES[hue_seed.rem_euclid(CLUB_HUES.len() as i64) as usize];
        let [red, green, blue] = palette_hsl_to_rgb(club_hue as f64, 100.0, 62.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
        let mirror = length - 1 - index;
        if mirror == index {
            continue;
        }
        let rgb = if instance.state.mirror_flash_alternate == 0 {
            [red, green, blue]
        } else {
            let warm_hue = WARM_HUES[(hue_seed + 11).rem_euclid(WARM_HUES.len() as i64) as usize];
            palette_hsl_to_rgb(warm_hue as f64, 100.0, 60.0, palette)
        };
        destination.set_pixel(
            mirror,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn render_spectrum_flash(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.18,
        bands: [0.2, 0.22, 0.26, 0.24, 0.2, 0.18, 0.16, 0.14],
        beat: false,
        beat_strength: 0.0,
    };
    const WARM_HUES: [i32; 4] = [0, 20, 38, 320];
    let audio = input.audio.unwrap_or(DEFAULT_AUDIO);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(27) == 0, |audio| audio.beat);
    let onset = if audio.beat {
        audio.beat_strength
    } else {
        reactive.rms * 0.18
    };
    if beat
        && (reactive.low > 0.45 || reactive.accent > 0.68 || onset > 0.58 || reactive.rms > 0.82)
    {
        instance.state.spectrum_flash_value = 1.0;
    }
    let decay = instance.setting_number("decay", 50.0) / 100.0;
    let release = 0.02 + decay * 0.12;
    instance.state.spectrum_flash_value = (instance.state.spectrum_flash_value - release).max(0.0);
    instance.state.spectrum_flash_hue_phase =
        (instance.state.spectrum_flash_hue_phase + 0.35 + reactive.high * 0.9).rem_euclid(360.0);

    let length = destination.pixel_count();
    let bands = audio
        .bands
        .map(|band| if band.is_finite() { band } else { 0.0 });
    let blend = instance.setting_number("width", 30.0) / 100.0 * 0.5;
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let ratio = if length <= 1 {
            0.0
        } else {
            index as f64 / (length - 1) as f64
        };
        let band_index = ((ratio * 8.0) as usize).min(7);
        let neighbor = (bands[band_index.saturating_sub(1)] + bands[(band_index + 1).min(7)]) * 0.5;
        let band = bands[band_index] * (1.0 - blend) + neighbor * blend;
        let mut hue = (instance.state.spectrum_flash_hue_phase + ratio * 300.0).rem_euclid(360.0);
        let mut lightness = 56.0;
        let mut alpha = (0.18 + band * 0.72).clamp(0.0, 1.0);
        if instance.state.spectrum_flash_value > 0.0 {
            let hue_seed = (instance.state.spectrum_flash_hue_phase / 8.0) as i64;
            let warm_index = (hue_seed + index as i64).rem_euclid(WARM_HUES.len() as i64) as usize;
            hue = WARM_HUES[warm_index] as f64;
            lightness = 60.0;
            alpha = (0.1 + instance.state.spectrum_flash_value * 0.9).min(1.0);
        }
        let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, lightness, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_bass_drop(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let speed_setting = instance.setting_number("speed", 50.0);
    let speed = 0.25 + speed_setting / 100.0 * 1.5;
    let decay = 0.01 + instance.setting_number("decay", 50.0) * 0.0005;
    let (alpha, mut rgb) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let flash = decay_step(&mut instance.state.bass_drop_flash, reactive.accent, decay);
        let onset = if audio.beat {
            audio.beat_strength
        } else {
            reactive.rms * 0.18
        };
        if audio.beat
            && (reactive.low > 0.3 || reactive.accent > 0.7 || onset > 0.58 || reactive.rms > 0.8)
        {
            instance.state.bass_drop_flash = 1.0;
            instance.state.bass_drop_charge = 0.0;
        }
        instance.state.bass_drop_charge =
            (instance.state.bass_drop_charge + (0.006 + reactive.low * 0.012) * speed).min(1.0);
        if audio.beat && instance.state.bass_drop_charge < 0.3 {
            instance.state.bass_drop_charge = 0.0;
        }
        let tension = instance.state.bass_drop_charge;
        let preview = if tension > 0.6 {
            reactive.rms * 0.3 + reactive.mid * 0.3
        } else {
            0.0
        };
        let alpha = (preview * tension * 0.4).max(flash).min(1.0);
        let hue = (20.0 + reactive.low * 30.0) as i32;
        let color_brightness = (flash * 2.0 + tension * 0.3).min(1.0);
        let rgb = if alpha > 0.0 {
            [
                hue.rem_euclid(256) as u8,
                (color_brightness * 200.0) as u8,
                (color_brightness * (255.0 - hue as f64 * 0.3)) as u8,
            ]
        } else {
            [0, 0, 0]
        };
        ((alpha * 2.0).min(1.0), rgb)
    } else {
        let period = (100.0 - speed_setting * 0.8).round().max(16.0) as i64;
        instance.state.bass_drop_charge =
            (instance.state.bass_drop_charge + 0.008 * speed).min(1.0);
        if frame.rem_euclid(period) < 2 {
            instance.state.bass_drop_flash = 1.0;
            instance.state.bass_drop_charge = 0.0;
        }
        let flash = decay_step(&mut instance.state.bass_drop_flash, 0.0, decay);
        let tension = instance.state.bass_drop_charge;
        let preview = if tension > 0.6 {
            frame.rem_euclid(30) as f64 / 30.0
        } else {
            0.0
        };
        let alpha = (preview * tension * 0.4).max(flash).min(1.0);
        let hue = frame.wrapping_mul(3).rem_euclid(256) as u8;
        let rgb = if alpha > 0.0 {
            [
                hue,
                (200.0 - f64::from(hue) * 0.5) as u8,
                (255.0 - f64::from(hue) * 0.3) as u8,
            ]
        } else {
            [0, 0, 0]
        };
        (alpha, rgb)
    };
    rgb = map_color_rgb(rgb, instance.palette());
    let alpha = alpha_byte(alpha, instance.brightness(input));
    destination.fill(Rgba::new(rgb[0], rgb[1], rgb[2], alpha));
}

fn render_beat_explosion(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let speed = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let width = (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(4.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let activity = (reactive.drive() * 0.65 + reactive.accent * 0.25 + reactive.high * 0.1)
            .clamp(0.0, 1.0);
        let target = reactive.accent.max(activity * 0.35);
        decay_step(&mut instance.state.beat_explosion_burst, target, 0.06);
        if audio.beat {
            instance.state.beat_explosion_radius = 0.0;
        } else {
            instance.state.beat_explosion_radius += reactive.speed(0.35, 2.0) * speed;
            if instance.state.beat_explosion_radius > center + 8.0 {
                instance.state.beat_explosion_radius = 0.0;
            }
        }
        for index in 0..length {
            let distance = (index as f64 - center).abs();
            let ring = (1.0
                - (distance - instance.state.beat_explosion_radius).abs()
                    / (width + reactive.high * 2.0))
                .max(0.0);
            let ambient = (1.0 - distance / center.max(1.0)).max(0.0) * 0.08;
            let alpha =
                (ambient + ring * (0.15 + instance.state.beat_explosion_burst * 0.75)).min(1.0);
            let hue =
                (10.0 + (reactive.low * 30.0) as i64 as f64 + frame.rem_euclid(256) as f64 * 2.0)
                    .rem_euclid(256.0);
            let lightness =
                (50.0 + reactive.mid * 15.0 + instance.state.beat_explosion_burst * 15.0) as i32
                    as f64;
            let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, lightness, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
            );
        }
    } else {
        instance.state.beat_explosion_radius =
            (frame as f64 * 0.45 * speed).rem_euclid(center + 6.0);
        let alpha = if frame.rem_euclid(30) < 3 { 0.9 } else { 0.0 };
        for index in 0..length {
            let distance = (index as f64 - center).abs();
            let ring =
                (1.0 - (distance - instance.state.beat_explosion_radius).abs() / width).max(0.0);
            let hue = frame
                .wrapping_mul(4)
                .wrapping_add((index * 2) as i64)
                .rem_euclid(256) as f64;
            let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 55.0, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(ring * alpha, brightness)),
            );
        }
    }
}

fn render_beat_ripple(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    destination.fill(Rgba::TRANSPARENT);
    let length = destination.pixel_count();
    let max_pos = length.saturating_sub(1) as f64;
    let speed = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let base_width =
        (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    let decay_setting = instance.setting_number("decay", 50.0) / 100.0;
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let (radius, hue, width, alpha_scale, lightness, high) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let hit = decay_step(
            &mut instance.state.beat_ripple_burst,
            reactive.accent,
            0.02 + decay_setting * 0.06,
        );
        if audio.beat {
            instance.state.beat_ripple_radius = 0.0;
            instance.state.beat_ripple_hue = (instance.state.beat_ripple_hue + 31).rem_euclid(256);
        } else {
            instance.state.beat_ripple_radius += reactive.speed(0.4, 2.2) * speed;
        }
        (
            (-instance.state.beat_ripple_radius).rem_euclid(max_pos + 1.0),
            instance.state.beat_ripple_hue,
            base_width + reactive.high * 2.0,
            (0.15 + hit * 0.6 + reactive.low * 0.15) * (0.5 + decay_setting),
            (40.0 + reactive.mid * 20.0 + hit * 15.0) as i32 as f64,
            reactive.high,
        )
    } else {
        let center = max_pos * 0.5;
        let decay = 0.4 + instance.setting_number("decay", 50.0) / 100.0 * 0.6;
        instance.state.beat_ripple_radius = (frame as f64 * 0.4 * speed).rem_euclid(center + 6.0);
        instance.state.beat_ripple_hue = frame.wrapping_mul(3).rem_euclid(256) as i32;
        (
            instance.state.beat_ripple_radius,
            instance.state.beat_ripple_hue,
            base_width,
            0.1 + decay * (1.0 - instance.state.beat_ripple_radius / (center + 6.0)),
            50.0,
            0.0,
        )
    };
    let audio_mode = input.audio.is_some();
    let center = if audio_mode { max_pos } else { max_pos * 0.5 };
    for index in 0..length {
        let distance = (index as f64 - center).abs();
        let (ring, mirror) = if audio_mode {
            let left = (index as f64 - radius).abs();
            let right = (index as f64 - (max_pos - radius)).abs();
            (
                (1.0 - left / width.max(0.001)).max(0.0),
                (1.0 - right / width.max(0.001)).max(0.0),
            )
        } else {
            (
                (1.0 - (distance - radius).abs() / width.max(0.001)).max(0.0),
                (1.0 - (distance - (center - radius + center)).abs() / width.max(0.001)).max(0.0),
            )
        };
        let alpha = if audio_mode {
            ring.max(mirror) * alpha_scale
        } else {
            ring.max(mirror) * alpha_scale
        };
        if alpha > 0.01 {
            let [red, green, blue] = palette_hsl_to_rgb(hue as f64, 100.0, lightness, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
            );
        }
    }
    let _ = high;
}

fn render_beat_tunnel(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let max_dist = center.max(1.0);
    let speed = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let base_width =
        (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let clear = palette_hsl_to_rgb(0.0, 0.0, 0.0, palette);
    destination.fill(Rgba::new(clear[0], clear[1], clear[2], 0));
    let mut rings = [(0.0, 0.0, 0.0, 0_i32, 0.0, 0.0); 2];
    let ring_count = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let hit = decay_step(&mut instance.state.beat_tunnel_pulse, reactive.accent, 0.05);
        if audio.beat {
            instance.state.beat_tunnel_radius = 0.0;
            instance.state.beat_tunnel_direction = 1.0;
        } else {
            instance.state.beat_tunnel_radius +=
                reactive.speed(0.3, 1.5) * speed * instance.state.beat_tunnel_direction;
            if instance.state.beat_tunnel_radius > max_dist + 2.0 {
                instance.state.beat_tunnel_direction = -1.0;
            } else if instance.state.beat_tunnel_radius < -2.0 {
                instance.state.beat_tunnel_direction = 1.0;
            }
        }
        let hue = (170.0 + (reactive.low * 30.0) as i32 as f64
            - (reactive.high * 20.0) as i32 as f64)
            .rem_euclid(256.0) as i32;
        rings[0] = (
            center,
            instance.state.beat_tunnel_radius.abs(),
            base_width + reactive.high * 2.0,
            hue,
            0.7 + hit * 0.3,
            max_dist,
        );
        let inner =
            (instance.state.beat_tunnel_radius.abs() - max_dist * 0.4).rem_euclid(max_dist + 2.0);
        if inner > 1.0 {
            rings[1] = (
                center,
                inner,
                (base_width * 0.65).max(1.0),
                (hue + 100).rem_euclid(256),
                0.4 + hit * 0.3,
                max_dist,
            );
            2
        } else {
            1
        }
    } else {
        instance.state.beat_tunnel_radius += 0.5 * speed * instance.state.beat_tunnel_direction;
        if instance.state.beat_tunnel_radius > max_dist || instance.state.beat_tunnel_radius < 0.0 {
            instance.state.beat_tunnel_direction *= -1.0;
            instance.state.beat_tunnel_radius =
                instance.state.beat_tunnel_radius.clamp(0.0, max_dist);
        }
        let hue_a = frame.wrapping_mul(4).rem_euclid(256) as i32;
        let hue_b = frame.wrapping_mul(4).wrapping_add(120).rem_euclid(256) as i32;
        let inner = (instance.state.beat_tunnel_radius - max_dist * 0.5).rem_euclid(max_dist + 4.0);
        rings[0] = (
            center,
            instance.state.beat_tunnel_radius,
            base_width,
            hue_a,
            0.85,
            max_dist,
        );
        rings[1] = (
            center,
            inner,
            (base_width * 0.75).max(1.0),
            hue_b,
            0.6,
            max_dist,
        );
        2
    };
    for &(ring_center, radius, width, hue, ring_alpha, ring_max_dist) in &rings[..ring_count] {
        let [red, green, blue] = palette_hsl_to_rgb(hue as f64, 80.0, 55.0, palette);
        for index in 0..length {
            let distance = (index as f64 - ring_center).abs();
            let ring = (1.0 - (distance - radius).abs() / width.max(0.001)).max(0.0);
            let ambient = (1.0 - distance / ring_max_dist.max(0.001)).max(0.0) * 0.1;
            let mut combined = (ambient + ring * ring_alpha).min(1.0);
            if combined <= 0.0 {
                continue;
            }
            if let Some(existing) = destination.pixel(index) {
                let existing_alpha = f64::from(existing.0[3]) / 255.0;
                if existing_alpha > 0.0 {
                    combined = combined.max(existing_alpha);
                }
            }
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(combined, brightness)),
            );
        }
    }
}

fn render_beat_wave(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let clear = palette_hsl_to_rgb(0.0, 0.0, 0.0, palette);
    let Some(audio) = input.audio else {
        destination.fill(Rgba::new(clear[0], clear[1], clear[2], 0));
        return;
    };
    let reactive = AudioReactive::from_input(audio);
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let width = (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    if audio.beat {
        instance.state.beat_wave_hue = (instance.state.beat_wave_hue + 17).rem_euclid(256);
        let slot = if instance.state.beat_wave_count < 8 {
            let slot = instance.state.beat_wave_count;
            instance.state.beat_wave_count += 1;
            slot
        } else {
            instance.state.beat_wave_positions.copy_within(1..8, 0);
            instance.state.beat_wave_speeds.copy_within(1..8, 0);
            instance.state.beat_wave_strengths.copy_within(1..8, 0);
            7
        };
        instance.state.beat_wave_positions[slot] = 0.0;
        instance.state.beat_wave_speeds[slot] =
            (0.5 + reactive.speed(0.1, 0.5) * (1.0 + reactive.accent * 0.5)) * speed_scale;
        instance.state.beat_wave_strengths[slot] = 0.6 + reactive.accent * 0.4;
    }
    let mut retained = 0;
    for index in 0..instance.state.beat_wave_count {
        let position =
            instance.state.beat_wave_positions[index] + instance.state.beat_wave_speeds[index];
        let strength = instance.state.beat_wave_strengths[index] * 0.98;
        if strength > 0.01 && position < length as f64 {
            instance.state.beat_wave_positions[retained] = position;
            instance.state.beat_wave_speeds[retained] = instance.state.beat_wave_speeds[index];
            instance.state.beat_wave_strengths[retained] = strength;
            retained += 1;
        }
    }
    instance.state.beat_wave_count = retained;
    let glow = 0.04 + reactive.rms * 0.12;
    let hue = reactive.hue_shift(frame, 0.25) as f64;
    for index in 0..length {
        let mut combined = glow;
        for wave in 0..instance.state.beat_wave_count {
            let distance = (index as f64 - instance.state.beat_wave_positions[wave]).abs();
            if distance < width {
                let wave_alpha =
                    instance.state.beat_wave_strengths[wave] * (1.0 - distance / width);
                combined = (combined + wave_alpha).min(1.0);
            }
        }
        let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 50.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(combined, brightness)),
        );
    }
}

fn decay_step(value: &mut f64, target: f64, release: f64) -> f64 {
    let target = target.clamp(0.0, 1.0);
    let release = release.clamp(0.0, 1.0);
    if target >= *value {
        *value = target;
    } else {
        *value = (*value - release).max(target).max(0.0);
    }
    *value
}

fn map_color_rgb(mut rgb: [u8; 3], palette: Palette) -> [u8; 3] {
    if matches!(palette, Palette::Custom(_)) {
        return palette_hsl_to_rgb(0.0, 0.0, 0.0, palette);
    }
    map_rgb_palette(&mut rgb, palette);
    rgb
}

fn render_comet_storm(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let count = instance
        .setting_number("count", 4.0)
        .round()
        .clamp(1.0, 12.0) as usize;
    let tail_length =
        (length as f64 * instance.setting_number("tail_length", 8.0) / 100.0).max(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    let clear = palette_hsl_to_rgb(0.0, 0.0, 0.0, palette);
    destination.fill(Rgba::new(clear[0], clear[1], clear[2], 0));
    let mut comets = [(0.0, 0.0, 0.0, 0.0); 12];
    let comet_count = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let shimmer =
            (reactive.high * 0.6 + reactive.mid * 0.2 + reactive.accent * 0.2).clamp(0.0, 1.0);
        let comet_count = (count + (shimmer * 2.0) as usize).clamp(1, 12);
        let speed = reactive.speed(0.8, 3.5)
            * (0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5);
        let phase_length = length.max(1) as f64;
        instance.state.comet_storm_phase =
            (instance.state.comet_storm_phase + speed).rem_euclid(phase_length);
        instance.state.comet_storm_hue_phase =
            (instance.state.comet_storm_hue_phase + 0.5 + reactive.high * 1.4).rem_euclid(360.0);
        let tail = tail_length * (0.75 + reactive.high * 0.5);
        let alpha = 0.4 + (reactive.rms * 0.2 + reactive.accent * 0.4).clamp(0.0, 1.0);
        for comet in 0..comet_count {
            let seed = (comet as i64)
                .wrapping_mul(1_103_515_245)
                .wrapping_add(frame.div_euclid(30).wrapping_mul(12_345));
            let offset = seed.rem_euclid(200) + seed.div_euclid(100) * 7;
            let head = (instance.state.comet_storm_phase + offset as f64).rem_euclid(phase_length);
            let hue =
                (instance.state.comet_storm_hue_phase + comet as f64 * 40.0).rem_euclid(360.0);
            comets[comet] = (head, tail, hue, alpha);
        }
        comet_count
    } else {
        let frame_speed = 2.0 * (0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5);
        let phase_length = length.max(1) as f64;
        for comet in 0..count {
            let offset = comet * length.max(1) / count;
            let head = (frame as f64 * frame_speed + offset as f64).rem_euclid(phase_length);
            let hue = (comet as i64 * 51 + frame.wrapping_mul(3)).rem_euclid(256) as f64;
            comets[comet] = (head, tail_length, hue, 0.7);
        }
        count
    };
    for &(head, tail, hue, comet_alpha) in &comets[..comet_count] {
        for index in 0..length {
            let direct = (head - index as f64).abs();
            let distance = direct.min((length as f64 - direct).max(0.0));
            let intensity = (1.0 - distance / tail.max(0.001)).max(0.0);
            if intensity <= 0.0 {
                continue;
            }
            let strength = (intensity * comet_alpha).min(1.0);
            let existing = destination
                .pixel(index)
                .map_or(0.0, |pixel| f64::from(pixel.0[3]) / 255.0);
            if strength < existing {
                continue;
            }
            let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 55.0, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(strength, brightness)),
            );
        }
    }
}

fn render_bpm(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let (mut rgb, alpha) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat {
            instance.state.bpm_flash = 1.0;
            instance.state.bpm_hue = (instance.state.bpm_hue + 31).rem_euclid(256);
        }
        let decay = 0.98 - instance.setting_number("decay", 50.0) * 0.0016 + reactive.rms * 0.04;
        instance.state.bpm_flash *= decay;
        (
            [instance.state.bpm_hue as u8, 255, 255],
            (instance.state.bpm_flash + reactive.rms * 0.15).min(1.0),
        )
    } else {
        let beat = ((frame as f64 * 0.1).sin() * 0.5 + 0.5).powf(3.0);
        (
            [frame.wrapping_mul(5).rem_euclid(256) as u8, 255, 255],
            beat,
        )
    };
    let palette = instance.palette();
    if let Palette::Custom(custom) = palette {
        rgb = [
            ((custom >> 16) & 0xff) as u8,
            ((custom >> 8) & 0xff) as u8,
            (custom & 0xff) as u8,
        ];
    } else {
        map_rgb_palette(&mut rgb, palette);
    }
    let alpha = alpha_byte(alpha, instance.brightness(input));
    destination.fill(Rgba::new(rgb[0], rgb[1], rgb[2], alpha));
}

fn render_wave(
    instance: &AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let count = destination.pixel_count();
    let frequency = 0.5 + instance.setting_number("frequency", 50.0) / 100.0;
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..count {
        let position = if count <= 1 {
            0.0
        } else {
            index as f64 / (count - 1) as f64
        };
        let (rgb, alpha) = if let Some(audio) = input.audio {
            let reactive = AudioReactive::from_input(audio);
            let amplitude = 0.24 + band_window(audio, index, count, 1) * 0.76;
            let phase = position * 5.5 * frequency
                + frame as f64 * reactive.speed(0.16, 0.62)
                + reactive.low * 2.0;
            let crest = (phase.sin() * 0.5 + 0.5) * amplitude;
            let hue = reactive.hue_shift(frame.saturating_add(index as i64), 0.08);
            let lightness = 56.0 + (reactive.mid * 8.0 + crest * 6.0) as i32 as f64;
            (
                palette_rgb(
                    hsl_to_rgb(
                        f64::from((160 + hue / 6).rem_euclid(256)),
                        100.0,
                        lightness.min(68.0),
                    ),
                    palette,
                ),
                0.22 + crest * 0.78,
            )
        } else {
            let phase = position * 5.5 * frequency + frame as f64 * 0.28;
            let crest = phase.sin() * 0.5 + 0.5;
            let hue = ((frame.saturating_add(index as i64)) * 2).rem_euclid(256) as u8;
            (palette_rgb([0, hue, 255], palette), 0.22 + crest * 0.78)
        };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn render_sinelon(
    instance: &AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let count = destination.pixel_count();
    let (position, hue) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        (
            (frame as f64 * reactive.speed(0.015, 0.12)).sin() * (0.25 + reactive.drive() * 0.75)
                + 0.5,
            reactive.hue_shift(frame, 0.3),
        )
    } else {
        (
            (frame as f64 * 0.05).sin() * 0.5 + 0.5,
            (frame * 4).rem_euclid(256) as i32,
        )
    };
    let center = position * count.saturating_sub(1) as f64;
    let width = (count as f64 * instance.setting_number("fade_time", 50.0) / 100.0 * 0.16).max(1.0);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..count {
        let distance = (index as f64 - center).abs();
        let alpha = if distance < width {
            1.0 - distance / width
        } else {
            0.0
        };
        let rgb = if alpha > 0.0 {
            palette_rgb([hue as u8, 255, 255], palette)
        } else {
            [0, 0, 0]
        };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn palette_rgb(mut rgb: [u8; 3], palette: Palette) -> [u8; 3] {
    if let Palette::Custom(custom) = palette {
        return [
            ((custom >> 16) & 0xff) as u8,
            ((custom >> 8) & 0xff) as u8,
            custom as u8,
        ];
    }
    map_rgb_palette(&mut rgb, palette);
    rgb
}

fn render_bouncing_ball(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let count = destination.pixel_count();
    let direction = travel_direction(instance);
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let (hue, radius, glow_width, glow, audio_mode) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat
            && direction == TravelDirection::Bounce
            && (reactive.low > 0.3 || reactive.accent > 0.35)
        {
            instance.state.ball_direction *= -1.0;
        }
        if audio.beat {
            instance.state.ball_speed =
                (instance.state.ball_speed + reactive.accent * 1.2 + reactive.low * 0.4).min(3.0);
        }
        instance.state.ball_speed =
            (instance.state.ball_speed * 0.985 + reactive.drive() * 0.08).clamp(0.45, 3.2);
        let movement = instance.state.ball_speed * speed_scale;
        update_ball_position(
            &mut instance.state.ball_position,
            &mut instance.state.ball_direction,
            movement,
            direction,
            count,
        );
        (
            reactive.hue_shift(frame, 0.35),
            (count as f64 * instance.setting_number("particle_size", 35.0) / 100.0 * 0.1).max(1.0)
                + reactive.low * 3.0,
            (count as f64 * 0.04).max(1.0),
            0.1 + reactive.rms * 0.5,
            true,
        )
    } else {
        let speed = 0.85 * speed_scale;
        update_ball_position(
            &mut instance.state.ball_position,
            &mut instance.state.ball_direction,
            speed,
            direction,
            count,
        );
        (
            (frame * 5).rem_euclid(256) as i32,
            (count as f64 * instance.setting_number("particle_size", 35.0) / 100.0 * 0.1).max(1.0),
            1.0,
            0.0,
            false,
        )
    };
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..count {
        let distance = (index as f64 - instance.state.ball_position).abs();
        let (alpha, saturation, lightness) = if !audio_mode {
            (
                if distance < radius {
                    1.0 - distance / radius
                } else {
                    0.0
                },
                100.0,
                56.0,
            )
        } else if distance < radius {
            ((1.0 - distance / radius + glow * 0.3).min(1.0), 100.0, 56.0)
        } else if distance < radius + glow_width {
            (
                (glow * (1.0 - (distance - radius) / glow_width)).max(0.0),
                90.0,
                42.0,
            )
        } else {
            (0.0, 100.0, 56.0)
        };
        let rgb = if matches!(palette, Palette::Custom(_)) {
            palette_rgb([0, 0, 0], palette)
        } else if alpha > 0.0 {
            palette_rgb(hsl_to_rgb(f64::from(hue), saturation, lightness), palette)
        } else {
            palette_rgb([0, 0, 0], palette)
        };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn update_ball_position(
    position: &mut f64,
    motion_direction: &mut f64,
    movement: f64,
    direction: TravelDirection,
    count: usize,
) {
    let maximum = count.saturating_sub(1) as f64;
    if direction == TravelDirection::Forward {
        *position = (*position + movement).rem_euclid(count.max(1) as f64);
    } else if direction == TravelDirection::Reverse {
        *position = (*position - movement).rem_euclid(count.max(1) as f64);
    } else {
        *position += movement * *motion_direction;
        if *position >= maximum {
            *position = maximum;
            *motion_direction = -1.0;
        }
        if *position < 0.0 {
            *position = 0.0;
            *motion_direction = 1.0;
        }
    }
}

fn travel_direction(instance: &AnimationInstance) -> TravelDirection {
    match instance.setting_string("direction", "bounce") {
        "forward" => TravelDirection::Forward,
        "reverse" => TravelDirection::Reverse,
        _ => TravelDirection::Bounce,
    }
}

fn render_color_wipe(
    instance: &mut AnimationInstance,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const PALETTE: [u32; 4] = [0xff_00_4d, 0x00_e5_ff, 0xff_e6_00, 0x00_ff_6a];
    let count = destination.pixel_count();
    let mut reactive = None;
    if let Some(audio) = input.audio {
        let response = AudioReactive::from_input(audio);
        let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
        instance.state.wipe_speed = response.speed(0.7, 3.2).max(0.6) * speed_scale;
        if audio.beat || response.accent > 0.65 {
            instance.state.wipe_palette_index =
                (instance.state.wipe_palette_index + 1) % PALETTE.len();
        }
        reactive = Some((audio, response));
    }
    let direction = travel_direction(instance);
    let length = count.max(1) as f64;
    let sign = if direction == TravelDirection::Bounce {
        instance.state.wipe_direction
    } else if direction == TravelDirection::Reverse {
        -1.0
    } else {
        1.0
    };
    if direction == TravelDirection::Bounce {
        instance.state.wipe_head += sign * instance.state.wipe_speed;
        let maximum = count.saturating_sub(1) as f64;
        if instance.state.wipe_head >= maximum {
            instance.state.wipe_head = maximum;
            instance.state.wipe_direction = -1.0;
            instance.state.wipe_palette_index =
                (instance.state.wipe_palette_index + 1) % PALETTE.len();
        } else if instance.state.wipe_head <= 0.0 {
            instance.state.wipe_head = 0.0;
            instance.state.wipe_direction = 1.0;
            instance.state.wipe_palette_index =
                (instance.state.wipe_palette_index + 1) % PALETTE.len();
        }
    } else {
        instance.state.wipe_head =
            (instance.state.wipe_head + sign * instance.state.wipe_speed).rem_euclid(length);
    }
    let tail = (count as f64 * instance.setting_number("tail_length", 8.0) / 100.0).max(1.0);
    let palette_rgb_value = PALETTE[instance.state.wipe_palette_index % PALETTE.len()];
    let hue = ((palette_rgb_value >> 16) & 0xff) as f64;
    let saturation = ((palette_rgb_value >> 8) & 0xff) as f64;
    let (local, boost) = reactive.map_or((0.0, 0.0), |(audio, response)| {
        (
            band_window(audio, instance.state.wipe_head as usize, count, 1),
            response.beat_pulse(0.0, 0.4),
        )
    });
    let brightness = instance.brightness(input);
    let palette = instance.palette();
    for index in 0..count {
        let distance = if sign > 0.0 {
            (instance.state.wipe_head - index as f64).rem_euclid(length)
        } else {
            (index as f64 - instance.state.wipe_head).rem_euclid(length)
        };
        let fade = (1.0 - distance / tail).max(0.0);
        let lightness = (44 + (fade * 18.0 + local * 12.0 + boost * 10.0) as i32).min(66);
        let rgb = palette_rgb(hsl_to_rgb(hue, saturation, f64::from(lightness)), palette);
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(1.0, brightness)),
        );
    }
}

fn render_juggle(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const BALLS: usize = 12;
    let length = destination.pixel_count();
    let maximum = length.saturating_sub(1) as f64;
    let ball_count = (instance.setting_number("count", 4.0) as usize).min(BALLS);
    prepare_juggle_state(&mut instance.state, length, ball_count);
    let radius =
        (length as f64 * instance.setting_number("particle_size", 35.0) / 100.0 * 0.17).max(1.0);
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let audio = input.audio.map(AudioReactive::from_input);
    let speed_factor = audio.map_or(1.0, |reactive| 0.45 + reactive.drive() * 1.4);
    if let Some(audio_input) = input.audio {
        if audio_input.beat {
            let reactive = audio.expect("audio input and reactive features are paired");
            for ball in 0..ball_count {
                let direction = if instance.state.juggle_speeds[ball] >= 0.0 {
                    1.0
                } else {
                    -1.0
                };
                instance.state.juggle_speeds[ball] = direction
                    * instance.state.juggle_speeds[ball].abs().max(2.0)
                    * (0.75 + reactive.accent * 0.6);
            }
        }
    }
    for ball in 0..ball_count {
        instance.state.juggle_positions[ball] +=
            instance.state.juggle_speeds[ball] * speed_factor * speed_scale;
        if instance.state.juggle_positions[ball] >= maximum {
            instance.state.juggle_positions[ball] = maximum;
            instance.state.juggle_speeds[ball] = -instance.state.juggle_speeds[ball];
        }
        if instance.state.juggle_positions[ball] < 0.0 {
            instance.state.juggle_positions[ball] = 0.0;
            instance.state.juggle_speeds[ball] = -instance.state.juggle_speeds[ball];
        }
    }
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let mut composite = 0.0;
        for ball in 0..ball_count {
            let distance = (index as f64 - instance.state.juggle_positions[ball]).abs();
            if distance < radius {
                composite += (1.0 - distance / radius) * 0.6;
            }
        }
        let alpha = composite.min(1.0);
        let rgb = if alpha > 0.0 {
            let hue = if let Some(reactive) = audio {
                (reactive.hue_shift(frame.saturating_add(index as i64), 0.18)
                    + (index * 11 % 128) as i32)
                    .rem_euclid(256) as u8
            } else {
                ((index * 32 + 50) % 256) as u8
            };
            palette_rgb([hue, 255, 200], palette)
        } else {
            palette_rgb([0, 0, 0], palette)
        };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn prepare_juggle_state(state: &mut AnimationState, length: usize, count: usize) {
    if state.juggle_scaled_length != length {
        let old_span = if state.juggle_scaled_length == 0 {
            99.0
        } else {
            state.juggle_scaled_length.saturating_sub(1).max(1) as f64
        };
        let new_span = length.saturating_sub(1) as f64;
        for position in state
            .juggle_positions
            .iter_mut()
            .take(state.juggle_initialized_count)
        {
            *position = *position * new_span / old_span;
        }
        state.juggle_scaled_length = length;
    }
    while state.juggle_initialized_count < count {
        let index = state.juggle_initialized_count;
        state.juggle_positions[index] =
            (index * 37 % 100) as f64 * length.saturating_sub(1) as f64 / 100.0;
        state.juggle_speeds[index] = 1.6 + (index % 3) as f64 * 0.35;
        state.juggle_initialized_count += 1;
    }
}

fn render_comet(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let maximum = length.saturating_sub(1) as f64;
    let direction = travel_direction(instance);
    let reactivity = input.audio.map_or(0.0, |_| {
        instance.setting_number("music_reactivity", 70.0) / 100.0
    });
    let mut speed = 0.25 + instance.setting_number("speed", 65.0) / 100.0 * 1.5;
    let reactive = input
        .audio
        .map(AudioReactive::from_input)
        .filter(|_| reactivity > 0.0);
    if let Some(response) = reactive {
        let audio_speed = 0.72 + response.drive() * 0.65 + response.accent * 0.45;
        speed *= 1.0 + (audio_speed - 1.0) * reactivity;
        if direction == TravelDirection::Bounce
            && input.audio.is_some_and(|audio| audio.beat)
            && response.accent > 0.45
            && response.low > 0.3
        {
            instance.state.comet_direction *= -1.0;
        }
    }
    advance_comet(
        &mut instance.state.comet_position,
        &mut instance.state.comet_direction,
        speed,
        maximum,
        direction,
    );
    let mut tail = (maximum * instance.setting_number("tail_length", 8.0) / 100.0).max(1.0);
    let mut shimmer = 0.0;
    let mut hue = (frame * 3).rem_euclid(360) as f64;
    if let Some(response) = reactive {
        tail *= 1.0 + reactivity * (response.high * 0.7 - 0.15);
        shimmer = reactivity * (response.accent * 0.25 + response.rms * 0.1);
        hue += reactivity * f64::from(response.hue_shift(frame, 0.2)) * 360.0 / 256.0;
    }
    let intensity = instance.setting_number("intensity", 100.0) / 100.0;
    let palette = instance.palette();
    let custom_color = instance.setting_string("custom_color", "#7C3AED");
    let custom_rgb =
        u32::from_str_radix(custom_color.trim_start_matches('#'), 16).unwrap_or(0x7c3aed);
    for index in 0..length {
        let strength = (1.0 - (instance.state.comet_position - index as f64).abs() / tail).max(0.0);
        if strength <= 0.0 || intensity <= 0.0 {
            destination.set_pixel(index, Rgba::TRANSPARENT);
            continue;
        }
        let alpha = ((0.14 + strength * 0.86 + shimmer) * intensity).min(1.0);
        let lightness = (48 + (strength * 12.0 + shimmer * 10.0) as i32).min(64);
        let rgb = catalog_palette_hsl_to_rgb(
            hue,
            100.0,
            f64::from(lightness),
            match palette {
                Palette::Custom(_) => Palette::Custom(custom_rgb),
                other => other,
            },
        );
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, input.brightness)),
        );
    }
}

fn advance_comet(
    position: &mut f64,
    motion_direction: &mut f64,
    speed: f64,
    maximum: f64,
    direction: TravelDirection,
) {
    if maximum <= 0.0 {
        *position = 0.0;
    } else if direction == TravelDirection::Forward {
        *position = (*position + speed).rem_euclid(maximum + 1.0);
    } else if direction == TravelDirection::Reverse {
        *position = (*position - speed).rem_euclid(maximum + 1.0);
    } else {
        *position += speed * *motion_direction;
        if *position >= maximum {
            *position = maximum;
            *motion_direction = -1.0;
        } else if *position <= 0.0 {
            *position = 0.0;
            *motion_direction = 1.0;
        }
    }
}

fn render_aurora(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let frequency = 0.5 + instance.setting_number("frequency", 50.0) / 100.0;
    let brightness = instance.brightness(input);
    let palette = instance.palette();
    let reactive = input.audio.map(AudioReactive::from_input);
    let beat_glow = reactive.map(|response| {
        instance.state.aurora_glow = if response.accent >= instance.state.aurora_glow {
            response.accent
        } else {
            (instance.state.aurora_glow - 0.045)
                .max(response.accent)
                .max(0.0)
        };
        instance.state.aurora_glow
    });
    for index in 0..length {
        let position = if length <= 1 {
            0.0
        } else {
            index as f64 / (length - 1) as f64
        };
        let (hue, saturation, lightness, alpha) =
            if let (Some(audio), Some(reactive), Some(beat_glow)) =
                (input.audio, reactive, beat_glow)
            {
                let speed = reactive.speed(0.028, 0.085);
                let local = band_window(audio, index, length, 1);
                let low_wave =
                    (frame as f64 * speed + position * 5.8 * frequency + reactive.low * 4.0).sin();
                let mid_wave = (frame as f64 * (speed * 0.9)
                    + position * 8.5 * frequency
                    + reactive.mid * 5.0)
                    .sin();
                let high_wave = (frame as f64 * (speed * 1.7) - position * 11.5 * frequency
                    + reactive.high * 7.0)
                    .cos();
                let curtain = (low_wave * 0.38 + mid_wave * 0.34 + high_wave * 0.28) * 0.5 + 0.5;
                let alpha = (0.12 + curtain * 0.72 + beat_glow * 0.2).min(1.0);
                let hue = (reactive.hue_shift(frame, 0.35)
                    + 120
                    + (position * 34.0) as i32
                    + (reactive.mid * 16.0) as i32
                    + (local * 10.0) as i32)
                    .rem_euclid(256);
                let lightness =
                    (50.0 + reactive.high * 12.0 + local * 8.0 + beat_glow * 8.0) as i32;
                (f64::from(hue), 90.0, f64::from(lightness.min(68)), alpha)
            } else {
                let phase = frame as f64 * 0.038 + position * 7.5 * frequency;
                let curtain = (phase.sin() * 0.5 + 0.5) * 0.58
                    + ((phase * 0.55 + 1.4).sin() * 0.5 + 0.5) * 0.22;
                let intensity = (0.12 + curtain).min(1.0);
                let hue = (132.0 + (phase * 0.75).cos() * 24.0 + position * 10.0) as i32;
                (f64::from(hue), 100.0, 56.0, intensity)
            };
        let rgb = palette_rgb(hsl_to_rgb(hue, saturation, lightness), palette);
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn render_fire(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    if instance.state.fire_heat.len() != length {
        instance.state.fire_heat = vec![0.0; length];
        instance.state.fire_next_heat = vec![0.0; length];
    }

    let (drive, bass, shimmer, accent) = input.audio.map_or((0.18, 0.08, 0.08, 0.0), |audio| {
        let reactive = AudioReactive::from_input(audio);
        (
            reactive.drive(),
            reactive.low,
            reactive.high,
            reactive.accent,
        )
    });
    let cooling = instance.setting_number("cooling", 50.0) / 100.0;
    let sparking = instance.setting_number("sparking", 35.0) / 100.0;
    let retention = 0.94 - cooling * 0.16 + drive * 0.04;
    let spark_threshold = 0.96 - sparking * 0.58 - shimmer * 0.10 - accent * 0.06;
    let spark_strength = 0.10 + sparking * 0.34 + drive * 0.18 + accent * 0.08;
    let fuel = 0.015 + bass * 0.025;
    let seed_base = (frame as u64).wrapping_mul(2_654_435_761);

    for index in 0..length {
        let seed = seed_base.wrapping_add((index as u64).wrapping_mul(314_159));
        let noise = f64::from(((seed >> 16) & 0xff) as u8) / 255.0;
        let current = instance.state.fire_heat[index];
        let left = if index > 0 {
            instance.state.fire_heat[index - 1]
        } else {
            current
        };
        let right = if index + 1 < length {
            instance.state.fire_heat[index + 1]
        } else {
            current
        };
        let spread = current * 0.68_f32 + (left + right) * 0.14_f32;
        let retained = spread * retention as f32;
        let spark = ((noise - spark_threshold).max(0.0) * spark_strength) as f32;
        let updated = retained + spark + fuel as f32;
        instance.state.fire_next_heat[index] = updated.min(1.0);
    }
    std::mem::swap(
        &mut instance.state.fire_heat,
        &mut instance.state.fire_next_heat,
    );

    let brightness = instance.brightness(input);
    let output_alpha = alpha_byte(1.0, brightness);
    for (index, heat) in instance.state.fire_heat.iter().copied().enumerate() {
        let intensity = ((f64::from(heat) - 0.025) / 0.7).clamp(0.0, 1.0);
        let (red, green, blue) = if intensity <= 0.0 {
            (0, 0, 0)
        } else if intensity < 0.35 {
            let phase = intensity / 0.35;
            ((35.0 + phase * 140.0) as u8, (phase * 15.0) as u8, 0)
        } else if intensity < 0.75 {
            let phase = (intensity - 0.35) / 0.4;
            (
                (175.0 + phase * 80.0) as u8,
                (15.0 + phase * 100.0) as u8,
                (phase * 8.0) as u8,
            )
        } else {
            let phase = (intensity - 0.75) / 0.25;
            (
                255,
                (115.0 + phase * 100.0) as u8,
                (8.0 + phase * 18.0) as u8,
            )
        };
        destination.set_pixel(index, Rgba::new(red, green, blue, output_alpha));
    }
}

fn render_twinkle(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    if instance.state.twinkle_levels.len() != length {
        instance.state.twinkle_levels = vec![0.0; length];
        instance.state.twinkle_hues = vec![0; length];
    }

    let reactive = input.audio.map_or_else(
        || {
            AudioReactive::from_input(AudioInput {
                rms: 0.12,
                bands: [0.08, 0.09, 0.1, 0.11, 0.12, 0.16, 0.2, 0.22],
                beat: false,
                beat_strength: 0.0,
            })
        },
        AudioReactive::from_input,
    );
    if let Some(audio) = input.audio {
        if audio.beat {
            instance.state.twinkle_bloom = 1.0_f64.min(
                instance
                    .state
                    .twinkle_bloom
                    .max(0.2 + reactive.accent * 0.75),
            );
        } else {
            instance.state.twinkle_bloom *= 0.92;
        }
    }

    let fade = instance.setting_number("fade_time", 50.0) / 100.0;
    let density = instance.setting_number("density", 40.0) / 100.0;
    let particle_size = instance.setting_number("particle_size", 35.0) / 100.0;
    let fade_factor = (0.80 + fade * 0.16 - reactive.high * 0.02).max(0.68);
    let spawn_threshold =
        ((0.001 + density * 0.025 + reactive.drive() * 0.008 + twinkle_shimmer(reactive) * 0.012)
            * 10_000.0) as u32;
    let radius = python_round(length as f64 * particle_size * 0.04).max(0) as usize;

    for index in 0..length {
        instance.state.twinkle_levels[index] =
            (f64::from(instance.state.twinkle_levels[index]) * fade_factor) as f32;
        let seed = (frame as u32)
            .wrapping_mul(1_103_515_245)
            .wrapping_add((index as u32).wrapping_mul(2_654_435_761));
        if seed % 10_000 < spawn_threshold {
            instance.state.twinkle_levels[index] =
                (f64::from(instance.state.twinkle_levels[index]) + 0.45 + reactive.high * 0.2)
                    .min(1.0) as f32;
            instance.state.twinkle_hues[index] =
                ((154 + (seed % 24) as i32 + (reactive.low * 12.0) as i32) % 360) as i16;
        }
    }

    let brightness = instance.brightness(input);
    let palette = instance.palette();
    for index in 0..length {
        let position = if length <= 1 {
            0.0
        } else {
            index as f64 / (length - 1) as f64
        };
        let drift = (frame as f64 * 0.018 + position * 4.2).sin() * 0.5 + 0.5;
        let mut level = f64::from(instance.state.twinkle_levels[index]);
        if radius > 1 {
            let left = index.saturating_sub(radius);
            let right = (index + radius + 1).min(length);
            for neighbor in left..right {
                let distance = index.abs_diff(neighbor) as f64;
                let nearby = f64::from(instance.state.twinkle_levels[neighbor])
                    * (1.0 - distance / (radius as f64 + 1.0));
                level = level.max(nearby);
            }
        }
        let hue = if level > 0.01 {
            f64::from(instance.state.twinkle_hues[index])
        } else {
            154.0 + (drift * 8.0) as i32 as f64
        };
        let alpha =
            (0.05 + drift * 0.03 + level * 0.38 + instance.state.twinkle_bloom * 0.08).min(0.5);
        let lightness = 28 + (drift * 5.0 + level * 34.0) as i32;
        let mut rgb = hsl_to_rgb(hue, 65.0, f64::from(lightness));
        map_rgb_palette(&mut rgb, palette);
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn render_rainbow_strobe(
    instance: &AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let flash_rate = instance.setting_number("flash_rate", 50.0);
    let duty_cycle = instance.setting_number("duty_cycle", 30.0);
    let period = python_round(24.0 - flash_rate * 0.2).max(2);
    let duration = python_round(period as f64 * duty_cycle / 100.0).max(1);
    let (cycle, hue, lightness) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        (
            if audio.beat {
                0
            } else {
                frame.rem_euclid(period)
            },
            reactive.hue_shift(frame, 2.0),
            (50.0 + instance.setting_number("intensity", 100.0) * 0.18 + reactive.high * 8.0)
                .min(75.0) as i32,
        )
    } else {
        (
            frame.rem_euclid(period),
            (frame * 17).rem_euclid(256) as i32,
            (56.0 + instance.setting_number("intensity", 100.0) * 0.14) as i32,
        )
    };
    let on = cycle < duration;
    let spread = 0.35 + instance.setting_number("color_spread", 50.0) / 100.0 * 1.3;
    let brightness = instance.brightness(input);
    let palette = instance.palette();
    for index in 0..destination.pixel_count() {
        let rgb = if on {
            let pixel_hue = (hue as f64 + index as f64 * spread) as i32;
            catalog_palette_hsl_to_rgb(
                f64::from(pixel_hue.rem_euclid(256)),
                100.0,
                f64::from(lightness),
                palette,
            )
        } else if matches!(palette, Palette::Rainbow) {
            [0, 0, 0]
        } else {
            catalog_palette_hsl_to_rgb(0.0, 0.0, 36.0, palette)
        };
        destination.set_pixel(
            index,
            Rgba::new(
                rgb[0],
                rgb[1],
                rgb[2],
                alpha_byte(if on { 1.0 } else { 0.0 }, brightness),
            ),
        );
    }
}

fn render_laser_sweep(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let maximum = length.saturating_sub(1) as f64 + 2.0;
    let travel = match instance.setting_string("direction", "bounce") {
        "forward" => TravelDirection::Forward,
        "reverse" => TravelDirection::Reverse,
        _ => TravelDirection::Bounce,
    };
    let reactive = input.audio.map(AudioReactive::from_input);
    if input.audio.is_some_and(|audio| audio.beat) && travel == TravelDirection::Bounce {
        instance.state.laser_direction *= -1.0;
    }
    let direction = match travel {
        TravelDirection::Reverse => -1.0,
        TravelDirection::Forward => 1.0,
        TravelDirection::Bounce => instance.state.laser_direction,
    };
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let speed = reactive.map_or(1.2, |audio| audio.speed(0.6, 2.5)) * speed_scale;
    instance.state.laser_position += speed * direction;
    if instance.state.laser_position > maximum {
        if travel == TravelDirection::Bounce {
            instance.state.laser_position = maximum;
            instance.state.laser_direction = -1.0;
        } else {
            instance.state.laser_position = -2.0;
        }
    } else if instance.state.laser_position < -2.0 {
        if travel == TravelDirection::Bounce {
            instance.state.laser_position = -2.0;
            instance.state.laser_direction = 1.0;
        } else {
            instance.state.laser_position = maximum;
        }
    }

    let hue = reactive.map_or((frame * 8).rem_euclid(256) as i32, |audio| {
        audio.hue_shift(frame, 0.8)
    });
    let mut width =
        (length as f64 * instance.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    if let Some(reactive) = reactive {
        width += reactive.high * 2.0;
    }
    let brightness = instance.brightness(input);
    let palette = instance.palette();
    for index in 0..length {
        let distance = (index as f64 - instance.state.laser_position).abs();
        let pixel_alpha = if distance < width {
            (1.0 - distance / width) * 0.9
        } else {
            0.0
        };
        let mut rgb = match palette {
            Palette::Custom(custom) => [
                ((custom >> 16) & 0xff) as u8,
                ((custom >> 8) & 0xff) as u8,
                (custom & 0xff) as u8,
            ],
            _ if pixel_alpha > 0.0 => hsl_to_rgb(f64::from(hue), 100.0, 60.0),
            _ => [0, 0, 0],
        };
        if !matches!(palette, Palette::Custom(_)) {
            map_rgb_palette(&mut rgb, palette);
        }
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(pixel_alpha, brightness)),
        );
    }
}

fn render_disco_sparkle(
    instance: &mut AnimationInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    if instance.state.disco_sparkle_levels.len() != length {
        instance.state.disco_sparkle_levels = vec![0.0; length];
    }
    let audio = input.audio.map(AudioReactive::from_input);
    let fade = instance.setting_number("fade_time", 50.0) / 100.0;
    let density_setting = instance.setting_number("density", 40.0) / 100.0;
    let variation = instance.setting_number("variation", 50.0) / 100.0;
    let (audio_mode, decay, density, accent_trigger, hue_rate, pulse) =
        if let Some(reactive) = audio {
            (
                true,
                0.58 + fade * 0.27 + reactive.high * 0.12,
                (4.0 + density_setting * 35.0
                    + reactive.drive() * 38.0
                    + twinkle_shimmer(reactive) * 22.0) as u32,
                reactive.accent > 0.5,
                reactive.speed(0.1, 0.6),
                (reactive.rms * 5.0 + reactive.accent * 10.0).clamp(0.0, 1.0),
            )
        } else {
            (
                false,
                (0.65 + fade * 0.4).min(1.0),
                (5 + (density_setting * 65.0) as i32) as u32,
                false,
                3.0,
                0.0,
            )
        };

    let frame_seed = (frame as u64).wrapping_mul(1_103_515_245);
    let palette = instance.palette();
    let brightness = instance.brightness(input);
    for index in 0..length {
        let seed = frame_seed.wrapping_add((index as u64).wrapping_mul(12_345));
        let mut strength = (f64::from(instance.state.disco_sparkle_levels[index]) * decay) as f32;
        instance.state.disco_sparkle_levels[index] = strength;
        let trigger = (audio_mode && accent_trigger) || seed % 100 < u64::from(density);
        if trigger {
            let increment = if audio_mode { 0.8 } else { 0.7 };
            strength = (f64::from(strength) + increment).min(1.0) as f32;
            instance.state.disco_sparkle_levels[index] = strength;
        }

        let rgb = if strength <= 0.05 {
            match palette {
                Palette::Rainbow => [0, 0, 0],
                Palette::Custom(color) => [
                    ((color >> 16) & 0xff) as u8,
                    ((color >> 8) & 0xff) as u8,
                    (color & 0xff) as u8,
                ],
                Palette::Neon | Palette::Fire => [91, 91, 91],
            }
        } else {
            let variation_value = (((seed % 21) as i32 - 10) as f64 * variation) as i32;
            let animated_frame = if audio_mode {
                (frame as f64 * hue_rate) as i64
            } else {
                (frame.rem_euclid(256) * 3) % 256
            };
            let hue = ((seed / 100 % 256) as i64 + animated_frame + i64::from(variation_value))
                .rem_euclid(256);
            let lightness = if audio_mode {
                (40.0 + f64::from(strength) * 25.0 + pulse) as i32
            } else {
                50
            };
            catalog_palette_hsl_to_rgb(hue as f64, 100.0, f64::from(lightness), palette)
        };
        let alpha = if strength <= 0.05 {
            0
        } else {
            alpha_byte(1.0, brightness)
        };
        destination.set_pixel(index, Rgba::new(rgb[0], rgb[1], rgb[2], alpha));
    }
}

fn render_dual_comet(
    instance: &mut AnimationInstance,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let maximum = length.saturating_sub(1) as f64;
    let speed_scale = 0.25 + instance.setting_number("speed", 50.0) / 100.0 * 1.5;
    let (alpha, hue_a, hue_b) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat {
            instance.state.dual_comet_direction *= -1.0;
        }
        instance.state.dual_comet_phase +=
            reactive.speed(0.42, 2.1) * speed_scale * instance.state.dual_comet_direction;
        (
            (0.85 + reactive.beat_pulse(0.0, 0.15)).min(1.0),
            10 + (reactive.low * 90.0) as i32,
            150 + (reactive.high * 80.0) as i32,
        )
    } else {
        instance.state.dual_comet_phase += 0.7 * speed_scale * instance.state.dual_comet_direction;
        (0.85, 40, 220)
    };
    if instance.state.dual_comet_phase >= maximum || instance.state.dual_comet_phase <= 0.0 {
        instance.state.dual_comet_direction *= -1.0;
        instance.state.dual_comet_phase = instance.state.dual_comet_phase.clamp(0.0, maximum);
    }
    let width = (length as f64 * instance.setting_number("tail_length", 8.0) / 100.0).max(1.0);
    let separation = instance.setting_number("separation", 50.0) / 100.0;
    let other = (instance.state.dual_comet_phase + maximum * separation)
        .rem_euclid((maximum + 1.0).max(1.0));
    let brightness = instance.brightness(input);
    let palette = instance.palette();
    for index in 0..length {
        let distance_a = (index as f64 - instance.state.dual_comet_phase).abs();
        let distance_b = (index as f64 - other).abs();
        let tail_a = (1.0 - distance_a / width).max(0.0);
        let tail_b = (1.0 - distance_b / width).max(0.0);
        let (pixel_alpha, hue) = if tail_a > tail_b && tail_a > 0.0 {
            ((tail_a * alpha).min(1.0), hue_a)
        } else if tail_b > 0.0 {
            ((tail_b * alpha).min(1.0), hue_b)
        } else {
            (0.0, 0)
        };
        let rgb = if pixel_alpha > 0.0 {
            catalog_palette_hsl_to_rgb(f64::from(hue), 100.0, 55.0, palette)
        } else {
            match palette {
                Palette::Rainbow => [0, 0, 0],
                Palette::Custom(custom) => [
                    ((custom >> 16) & 0xff) as u8,
                    ((custom >> 8) & 0xff) as u8,
                    (custom & 0xff) as u8,
                ],
                Palette::Neon | Palette::Fire => [91, 91, 91],
            }
        };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(pixel_alpha, brightness)),
        );
    }
}

fn twinkle_shimmer(reactive: AudioReactive) -> f64 {
    (reactive.high * 0.6 + reactive.mid * 0.2 + reactive.accent * 0.2).clamp(0.0, 1.0)
}

fn truncated_modulo_256(value: f64) -> i32 {
    (value as i64).rem_euclid(256) as i32
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AnimationError {
    UnknownAnimation(String),
    NotPorted(String),
    InvalidSettings(String),
    InvalidInput(&'static str),
    Effect(String),
}

impl std::fmt::Display for AnimationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownAnimation(name) => write!(formatter, "unknown animation: {name}"),
            Self::NotPorted(name) => write!(formatter, "animation renderer is not ported: {name}"),
            Self::InvalidSettings(message) => formatter.write_str(message),
            Self::InvalidInput(message) => formatter.write_str(message),
            Self::Effect(message) => write!(formatter, "effect renderer failed: {message}"),
        }
    }
}

impl std::error::Error for AnimationError {}

#[cfg(test)]
mod tests {
    use super::{AnimationInstance, RenderingInput};
    use lumistripe_engine_render_primitives::{AudioInput, FrameBuffer};
    use serde_json::{Map, Value};

    #[test]
    fn deterministic_patterns_advance_reusable_frames() {
        for name in [
            "rainbow",
            "rainbow_cycle",
            "theater_chase",
            "strobe",
            "pulse",
            "police",
            "bpm",
            "wave",
            "sinelon",
            "bouncing_ball",
            "color_wipe",
            "juggle",
            "comet",
            "aurora",
            "fire",
            "twinkle",
            "rainbow_strobe",
            "laser_sweep",
            "disco_sparkle",
            "dual_comet",
            "dance_floor",
            "red_rave_sweep",
            "red_rave_chase",
            "red_blackout_strobe",
            "glow_rush",
            "rave_pulse",
            "rave_scanner",
            "neon_confetti",
            "strobe_chase",
            "plasma_rave",
            "dual_laser",
            "disco_comet",
            "neon_storm",
            "peak_mirror",
            "shockwave",
            "center_burst",
            "mirror_flash",
            "spectrum_flash",
            "bass_drop",
            "beat_explosion",
            "beat_ripple",
            "beat_tunnel",
            "beat_wave",
            "hard_beat",
            "confetti",
            "comet_storm",
            "spectrum_flash",
        ] {
            let mut instance = AnimationInstance::new(name, &Map::new()).unwrap();
            let mut frame = FrameBuffer::new(37).unwrap();
            let address = frame.as_bytes().as_ptr();
            instance
                .render_next(
                    RenderingInput {
                        timestamp_ns: 1_000_000,
                        brightness: 0.8,
                        ..RenderingInput::default()
                    },
                    &mut frame,
                )
                .unwrap();
            assert_eq!(frame.pixel_count(), 37);
            assert_eq!(frame.as_bytes().as_ptr(), address);
            assert_eq!(instance.frame_index(), 1);
        }
    }

    #[test]
    fn fire_and_twinkle_reuse_per_pixel_state_after_initialization() {
        let mut fire = AnimationInstance::new("fire", &Map::new()).unwrap();
        let mut twinkle = AnimationInstance::new("twinkle", &Map::new()).unwrap();
        let mut fire_frame = FrameBuffer::new(64).unwrap();
        let mut twinkle_frame = FrameBuffer::new(64).unwrap();
        let input = RenderingInput {
            brightness: 0.8,
            ..RenderingInput::default()
        };
        fire.render_next(input, &mut fire_frame).unwrap();
        twinkle.render_next(input, &mut twinkle_frame).unwrap();

        let fire_addresses = [
            fire.state.fire_heat.as_ptr() as usize,
            fire.state.fire_next_heat.as_ptr() as usize,
        ];
        let fire_capacities = [
            fire.state.fire_heat.capacity(),
            fire.state.fire_next_heat.capacity(),
        ];
        let twinkle_addresses = [
            twinkle.state.twinkle_levels.as_ptr() as usize,
            twinkle.state.twinkle_hues.as_ptr() as usize,
        ];
        let twinkle_capacities = [
            twinkle.state.twinkle_levels.capacity(),
            twinkle.state.twinkle_hues.capacity(),
        ];

        for _ in 0..8 {
            fire.render_next(input, &mut fire_frame).unwrap();
            twinkle.render_next(input, &mut twinkle_frame).unwrap();
            let current_fire_addresses = [
                fire.state.fire_heat.as_ptr() as usize,
                fire.state.fire_next_heat.as_ptr() as usize,
            ];
            assert!(fire_addresses.contains(&current_fire_addresses[0]));
            assert!(fire_addresses.contains(&current_fire_addresses[1]));
            assert_eq!(fire_capacities[0], fire.state.fire_heat.capacity());
            assert_eq!(fire_capacities[1], fire.state.fire_next_heat.capacity());
            assert_eq!(
                twinkle_addresses[0],
                twinkle.state.twinkle_levels.as_ptr() as usize
            );
            assert_eq!(
                twinkle_addresses[1],
                twinkle.state.twinkle_hues.as_ptr() as usize
            );
            assert_eq!(
                twinkle_capacities[0],
                twinkle.state.twinkle_levels.capacity()
            );
            assert_eq!(twinkle_capacities[1], twinkle.state.twinkle_hues.capacity());
        }
    }

    #[test]
    fn disco_sparkle_reuses_intensity_state_after_initialization() {
        let mut instance = AnimationInstance::new("disco_sparkle", &Map::new()).unwrap();
        let mut frame = FrameBuffer::new(64).unwrap();
        let input = RenderingInput {
            brightness: 0.8,
            ..RenderingInput::default()
        };
        instance.render_next(input, &mut frame).unwrap();
        let address = instance.state.disco_sparkle_levels.as_ptr() as usize;
        let capacity = instance.state.disco_sparkle_levels.capacity();
        for _ in 0..8 {
            instance.render_next(input, &mut frame).unwrap();
            assert_eq!(
                address,
                instance.state.disco_sparkle_levels.as_ptr() as usize
            );
            assert_eq!(capacity, instance.state.disco_sparkle_levels.capacity());
        }
    }

    #[test]
    fn neon_confetti_reuses_particle_state_after_initialization() {
        let mut instance = AnimationInstance::new("neon_confetti", &Map::new()).unwrap();
        let mut frame = FrameBuffer::new(64).unwrap();
        let input = RenderingInput {
            brightness: 0.8,
            ..RenderingInput::default()
        };
        instance.render_next(input, &mut frame).unwrap();
        let level_address = instance.state.neon_confetti_levels.as_ptr() as usize;
        let hue_address = instance.state.neon_confetti_hues.as_ptr() as usize;
        let level_capacity = instance.state.neon_confetti_levels.capacity();
        let hue_capacity = instance.state.neon_confetti_hues.capacity();
        for _ in 0..8 {
            instance.render_next(input, &mut frame).unwrap();
            assert_eq!(
                level_address,
                instance.state.neon_confetti_levels.as_ptr() as usize
            );
            assert_eq!(
                hue_address,
                instance.state.neon_confetti_hues.as_ptr() as usize
            );
            assert_eq!(
                level_capacity,
                instance.state.neon_confetti_levels.capacity()
            );
            assert_eq!(hue_capacity, instance.state.neon_confetti_hues.capacity());
        }
    }

    #[test]
    fn neon_storm_reuses_particle_and_streak_state_after_initialization() {
        let mut instance = AnimationInstance::new("neon_storm", &Map::new()).unwrap();
        let mut frame = FrameBuffer::new(64).unwrap();
        let input = RenderingInput {
            brightness: 0.8,
            ..RenderingInput::default()
        };
        instance.render_next(input, &mut frame).unwrap();
        let addresses = [
            instance.state.neon_storm_levels.as_ptr() as usize,
            instance.state.neon_storm_hues.as_ptr() as usize,
            instance.state.neon_storm_streak_positions.as_ptr() as usize,
        ];
        let capacities = [
            instance.state.neon_storm_levels.capacity(),
            instance.state.neon_storm_hues.capacity(),
            instance.state.neon_storm_streak_positions.capacity(),
        ];
        for _ in 0..8 {
            instance.render_next(input, &mut frame).unwrap();
            assert_eq!(
                addresses,
                [
                    instance.state.neon_storm_levels.as_ptr() as usize,
                    instance.state.neon_storm_hues.as_ptr() as usize,
                    instance.state.neon_storm_streak_positions.as_ptr() as usize,
                ]
            );
            assert_eq!(
                capacities,
                [
                    instance.state.neon_storm_levels.capacity(),
                    instance.state.neon_storm_hues.capacity(),
                    instance.state.neon_storm_streak_positions.capacity(),
                ]
            );
        }
    }

    #[test]
    fn timestamped_audio_inputs_produce_repeatable_rainbow_frames() {
        let mut first = AnimationInstance::new("rainbow", &Map::new()).unwrap();
        let mut second = AnimationInstance::new("rainbow", &Map::new()).unwrap();
        let audio = AudioInput {
            rms: 0.42,
            bands: [0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.1],
            beat: true,
            beat_strength: 0.75,
        };
        let input = RenderingInput {
            timestamp_ns: 100_000_000,
            delta_ns: 16_000_000,
            brightness: 0.65,
            audio: Some(audio),
        };
        let mut left = FrameBuffer::new(64).unwrap();
        let mut right = FrameBuffer::new(64).unwrap();
        first.render_next(input, &mut left).unwrap();
        second.render_next(input, &mut right).unwrap();
        assert_eq!(left.as_bytes(), right.as_bytes());
    }

    #[test]
    fn migrated_renderers_match_python_and_native_c_golden_frames() {
        // Palette HSL conversions can differ by up to five channel values
        // between Python's scalar math and C/Rust floating-point evaluation.
        const PALETTE_RGB_TOLERANCE: u8 = 5;
        const OTHER_RGB_TOLERANCE: u8 = 1;
        const ALPHA_TOLERANCE: u8 = 1;
        let fixtures: Value = serde_json::from_str(include_str!("../golden_frames.json"))
            .expect("Python/C golden frame fixtures must be valid JSON");
        let cases = fixtures["cases"].as_array().expect("fixture cases array");
        assert_eq!(cases.len(), 108);
        for case in cases {
            let name = case["name"].as_str().expect("animation name");
            let settings = case["settings"]
                .as_object()
                .expect("animation settings")
                .clone();
            let pixel_count = case["pixel_count"].as_u64().expect("pixel count") as usize;
            let brightness = case["brightness"].as_f64().expect("brightness");
            let delta_ns = case["delta_ns"].as_u64().expect("frame delta");
            let inputs = case["audio"].as_array().expect("audio fixture sequence");
            let expected_frames = case["frames_hex"].as_array().expect("golden frames");
            let mut instance = AnimationInstance::new(name, &settings).unwrap();
            instance.set_random_seed(12_345);
            let mut output = FrameBuffer::new(pixel_count).unwrap();
            for (index, (audio, expected_hex)) in inputs.iter().zip(expected_frames).enumerate() {
                let audio = if audio.is_null() {
                    None
                } else {
                    Some(AudioInput {
                        rms: audio["rms"].as_f64().unwrap(),
                        bands: audio["bands"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|band| band.as_f64().unwrap())
                            .collect::<Vec<_>>()
                            .try_into()
                            .expect("eight audio bands"),
                        beat: audio["beat"].as_bool().unwrap(),
                        beat_strength: audio["beat_strength"].as_f64().unwrap(),
                    })
                };
                instance
                    .render_next(
                        RenderingInput {
                            timestamp_ns: index as u64 * delta_ns,
                            delta_ns,
                            brightness,
                            audio,
                        },
                        &mut output,
                    )
                    .unwrap_or_else(|error| panic!("{name} golden render failed: {error}"));
                let expected = decode_hex(expected_hex.as_str().unwrap());
                assert_eq!(output.as_bytes().len(), expected.len());
                for (byte_index, (actual, expected)) in
                    output.as_bytes().iter().zip(expected).enumerate()
                {
                    let error = actual.abs_diff(expected);
                    let tolerance = if byte_index % 4 == 3 {
                        ALPHA_TOLERANCE
                    } else if matches!(
                        name,
                        "rainbow"
                            | "rainbow_cycle"
                            | "pulse"
                            | "wave"
                            | "color_wipe"
                            | "aurora"
                            | "dance_floor"
                            | "glow_rush"
                            | "rave_pulse"
                            | "rave_scanner"
                            | "neon_confetti"
                            | "strobe_chase"
                            | "plasma_rave"
                            | "dual_laser"
                            | "disco_comet"
                            | "neon_storm"
                            | "peak_mirror"
                            | "shockwave"
                            | "center_burst"
                            | "mirror_flash"
                            | "spectrum_flash"
                            | "bass_drop"
                            | "beat_explosion"
                            | "beat_ripple"
                            | "beat_tunnel"
                            | "beat_wave"
                            | "confetti"
                            | "comet_storm"
                            | "club_flash"
                            | "color_burst"
                            | "drop_explosion"
                            | "drop_wave"
                            | "electric_storm"
                            | "firework_burst"
                            | "lightning_strike"
                            | "pixel_explosion"
                    ) {
                        PALETTE_RGB_TOLERANCE
                    } else {
                        OTHER_RGB_TOLERANCE
                    };
                    assert!(
                        error <= tolerance,
                        "{name} {} frame {index}, byte {byte_index}: Rust={actual}, golden={expected}, error={error}, tolerance={tolerance}",
                        case["variant"].as_str().unwrap()
                    );
                }
            }
        }
    }

    fn decode_hex(encoded: &str) -> Vec<u8> {
        assert_eq!(encoded.len() % 2, 0);
        encoded
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }
}
