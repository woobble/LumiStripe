//! Stateful effect rendering and reusable SCREEN-layer composition.

use lumistripe_engine_animation_definitions::{EffectDefinition, catalog};
use lumistripe_engine_compositing::{CompositeError, screen_composite};
use lumistripe_engine_render_primitives::{
    AudioInput, AudioReactive, FrameBuffer, Palette, RenderingInput, Rgba, alpha_byte, band_window,
    catalog_palette_hsl_to_rgb, hsl_to_rgb,
};
use serde_json::{Map, Value};
use std::collections::BTreeMap;

mod renderers;
mod rng;

use rng::PythonRandom;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct BurstState {
    center: f64,
    radius: f64,
    speed: f64,
    hue_seed: i64,
    strength: f64,
    width: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct StormStreak {
    position: f64,
    length: usize,
    speed: f64,
    hue: i32,
    alpha: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct FireworkState {
    phase: f64,
    speed: f64,
    strength: f64,
    hue: i32,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct PixelBurstState {
    position: f64,
    radius: f64,
    hue: i32,
    alpha: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct EffectInstance {
    definition: EffectDefinition,
    settings: BTreeMap<String, Value>,
    frame_index: u64,
    flash: f64,
    hue: i64,
    confetti_intensities: Vec<f32>,
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
    club_flash_value: f64,
    club_flash_segment_start: usize,
    club_flash_segment_width: usize,
    club_flash_whole_strip: bool,
    club_flash_hue_seed: i64,
    color_bursts: Vec<BurstState>,
    drop_explosion_charge: f64,
    drop_explosion_flash: f64,
    drop_wave_charge: f64,
    drop_wave_radius: f64,
    drop_wave_speed: f64,
    drop_wave_hue_seed: i64,
    drop_wave_value: f64,
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
    electric_storm_streaks: Vec<StormStreak>,
    electric_storm_flash: f64,
    firework_bursts: Vec<FireworkState>,
    firework_hue: i32,
    lightning_flash: f64,
    lightning_position: usize,
    lightning_length: usize,
    lightning_hue: i32,
    pixel_bursts: Vec<PixelBurstState>,
    rng: PythonRandom,
}

impl EffectInstance {
    pub fn new(name: &str, settings: &Map<String, Value>) -> Result<Self, EffectError> {
        let effect = catalog()
            .effect(name)
            .ok_or_else(|| EffectError::UnknownEffect(name.to_owned()))?
            .clone();
        let animation = catalog()
            .animation(name)
            .ok_or_else(|| EffectError::UnknownEffect(name.to_owned()))?;
        let settings = animation
            .normalize_settings(settings, true)
            .map_err(|error| EffectError::InvalidSettings(error.to_string()))?;
        Ok(Self {
            definition: effect,
            settings,
            frame_index: 0,
            flash: 0.0,
            hue: 0,
            confetti_intensities: Vec::new(),
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
            club_flash_value: 0.0,
            club_flash_segment_start: 0,
            club_flash_segment_width: 0,
            club_flash_whole_strip: false,
            club_flash_hue_seed: 0,
            color_bursts: Vec::with_capacity(6),
            drop_explosion_charge: 0.0,
            drop_explosion_flash: 0.0,
            drop_wave_charge: 0.0,
            drop_wave_radius: 0.0,
            drop_wave_speed: 0.0,
            drop_wave_hue_seed: 0,
            drop_wave_value: 0.0,
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
            electric_storm_streaks: Vec::with_capacity(12),
            electric_storm_flash: 0.0,
            firework_bursts: Vec::with_capacity(12),
            firework_hue: 0,
            lightning_flash: 0.0,
            lightning_position: 0,
            lightning_length: 0,
            lightning_hue: 200,
            pixel_bursts: Vec::with_capacity(12),
            rng: PythonRandom::new(12_345),
        })
    }

    pub fn name(&self) -> &str {
        &self.definition.name
    }

    pub fn frame_index(&self) -> u64 {
        self.frame_index
    }

    /// Compare a full or partial settings map without rebuilding effect state.
    pub fn settings_match(&self, values: &Map<String, Value>) -> bool {
        let Some(definition) = catalog().animation(self.name()) else {
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

    /// Seed random effects with a reproducible Python-compatible sequence.
    pub fn set_random_seed(&mut self, seed: u32) {
        self.rng.seed(seed);
    }

    pub fn supports_effect(name: &str) -> bool {
        matches!(
            name,
            "hard_beat"
                | "confetti"
                | "bass_drop"
                | "beat_explosion"
                | "beat_ripple"
                | "beat_tunnel"
                | "beat_wave"
                | "club_flash"
                | "color_burst"
                | "drop_explosion"
                | "drop_wave"
                | "shockwave"
                | "center_burst"
                | "mirror_flash"
                | "spectrum_flash"
                | "electric_storm"
                | "firework_burst"
                | "lightning_strike"
                | "pixel_explosion"
        )
    }

    pub fn set_settings(&mut self, settings: &Map<String, Value>) -> Result<(), EffectError> {
        let animation = catalog()
            .animation(self.name())
            .ok_or_else(|| EffectError::UnknownEffect(self.name().to_owned()))?;
        self.settings = animation
            .normalize_settings(settings, true)
            .map_err(|error| EffectError::InvalidSettings(error.to_string()))?;
        Ok(())
    }

    pub fn reset(&mut self) {
        self.frame_index = 0;
        self.flash = 0.0;
        self.hue = 0;
        self.confetti_intensities.clear();
        self.bass_drop_charge = 0.0;
        self.bass_drop_flash = 0.0;
        self.beat_explosion_radius = 0.0;
        self.beat_explosion_burst = 0.0;
        self.beat_ripple_radius = 0.0;
        self.beat_ripple_burst = 0.0;
        self.beat_ripple_hue = 0;
        self.beat_tunnel_radius = 0.0;
        self.beat_tunnel_direction = 1.0;
        self.beat_tunnel_pulse = 0.0;
        self.beat_wave_positions = [0.0; 8];
        self.beat_wave_speeds = [0.0; 8];
        self.beat_wave_strengths = [0.0; 8];
        self.beat_wave_count = 0;
        self.beat_wave_hue = 0;
        self.club_flash_value = 0.0;
        self.club_flash_segment_start = 0;
        self.club_flash_segment_width = 0;
        self.club_flash_whole_strip = false;
        self.club_flash_hue_seed = 0;
        self.color_bursts.clear();
        self.drop_explosion_charge = 0.0;
        self.drop_explosion_flash = 0.0;
        self.drop_wave_charge = 0.0;
        self.drop_wave_radius = 0.0;
        self.drop_wave_speed = 0.0;
        self.drop_wave_hue_seed = 0;
        self.drop_wave_value = 0.0;
        self.shockwave_radius = 0.0;
        self.shockwave_accent = 0.0;
        self.center_burst_radius = 0.0;
        self.center_burst_speed = 0.0;
        self.center_burst_width = 1.4;
        self.center_burst_hue_seed = 0;
        self.center_burst_flash = 0.0;
        self.mirror_flash_value = 0.0;
        self.mirror_flash_alternate = 0;
        self.mirror_flash_hue_seed = 0;
        self.spectrum_flash_value = 0.0;
        self.spectrum_flash_hue_phase = 0.0;
        self.electric_storm_streaks.clear();
        self.electric_storm_flash = 0.0;
        self.firework_bursts.clear();
        self.firework_hue = 0;
        self.lightning_flash = 0.0;
        self.lightning_position = 0;
        self.lightning_length = 0;
        self.lightning_hue = 200;
        self.pixel_bursts.clear();
        self.rng.seed(12_345);
    }

    pub fn strength(&self) -> f32 {
        self.definition.strength as f32
    }

    pub fn render_next(
        &mut self,
        input: RenderingInput,
        destination: &mut FrameBuffer,
    ) -> Result<(), EffectError> {
        let frame = self.frame_index.min(i64::MAX as u64) as i64;
        self.render_at(frame, input, destination)
    }

    pub fn render_at(
        &mut self,
        frame: i64,
        input: RenderingInput,
        destination: &mut FrameBuffer,
    ) -> Result<(), EffectError> {
        if !input.brightness.is_finite() {
            return Err(EffectError::InvalidInput("brightness must be finite"));
        }
        let mut input = input;
        input.audio = input.audio.and_then(|audio| self.effective_audio(audio));
        if self.definition.name == "hard_beat" {
            render_hard_beat(self, frame, input, destination);
        } else if self.definition.name == "confetti" {
            render_confetti(self, frame, input, destination);
        } else if !renderers::render(self, frame, input, destination) {
            return Err(EffectError::NotPorted(self.definition.name.clone()));
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

#[derive(Debug)]
struct ActiveLayer {
    effect: EffectInstance,
    intensity: f32,
    frame: FrameBuffer,
}

/// Owns active effect state and scratch frames; frame-size changes are explicit.
#[derive(Debug, Default)]
pub struct EffectStack {
    active: Vec<ActiveLayer>,
    max_overlay_strength: f32,
}

impl EffectStack {
    pub fn new(max_overlay_strength: f32) -> Result<Self, EffectError> {
        if !max_overlay_strength.is_finite()
            || max_overlay_strength <= 0.0
            || max_overlay_strength > 1.0
        {
            return Err(EffectError::InvalidInput(
                "overlay strength must be between zero and one",
            ));
        }
        Ok(Self {
            active: Vec::with_capacity(2),
            max_overlay_strength,
        })
    }

    pub fn active_names(&self) -> impl Iterator<Item = &str> {
        self.active.iter().map(|layer| layer.effect.name())
    }

    pub fn set_max_overlay_strength(&mut self, strength: f32) -> Result<(), EffectError> {
        if !strength.is_finite() || strength <= 0.0 || strength > 1.0 {
            return Err(EffectError::InvalidInput(
                "overlay strength must be between zero and one",
            ));
        }
        self.max_overlay_strength = strength;
        Ok(())
    }

    /// Reconcile active layers while retaining state for effects that remain active.
    ///
    /// Effect instances are stateful (for example, beat flashes decay across
    /// frames). Adding or removing another layer must not restart those effects.
    pub fn reconfigure(
        &mut self,
        definitions: &[(&str, &Map<String, Value>, f32)],
        pixel_count: usize,
    ) -> Result<(), EffectError> {
        if definitions.len() > 2 {
            return Err(EffectError::Capacity);
        }

        // Validate all configurations before moving existing state. Matching
        // active effects need no temporary instances or heap allocation when
        // only their intensity or a neighboring layer changes.
        let mut retained_indices = [None; 2];
        let mut retained = [false; 2];
        let mut prepared = [None, None];
        for (position, (name, settings, intensity)) in definitions.iter().enumerate() {
            if !intensity.is_finite() || !(0.0..=1.0).contains(intensity) {
                return Err(EffectError::InvalidInput(
                    "effect intensity must be in [0, 1]",
                ));
            }
            let previous_index = self.active.iter().enumerate().position(|(index, layer)| {
                !retained[index]
                    && layer.effect.name() == *name
                    && layer.effect.settings_match(settings)
            });
            if let Some(index) = previous_index {
                retained_indices[position] = Some(index);
                retained[index] = true;
            } else {
                prepared[position] = Some(EffectInstance::new(name, settings)?);
            }
        }
        if self.active.capacity() < definitions.len() {
            self.active
                .reserve(definitions.len().saturating_sub(self.active.len()));
        }
        let mut previous: [Option<ActiveLayer>; 2] = [None, None];
        for (index, layer) in self.active.drain(..).enumerate() {
            previous[index] = Some(layer);
        }
        for (position, (_, _, intensity)) in definitions.iter().enumerate() {
            let mut layer = if let Some(index) = retained_indices[position] {
                previous[index]
                    .take()
                    .expect("matched effect state must remain available")
            } else {
                ActiveLayer {
                    effect: prepared[position]
                        .take()
                        .expect("new effect state must be prepared before reconfiguration"),
                    intensity: *intensity,
                    frame: FrameBuffer::new(pixel_count)
                        .map_err(|_| EffectError::InvalidInput("effect frame size overflow"))?,
                }
            };
            if layer.frame.pixel_count() != pixel_count {
                layer.frame = FrameBuffer::new(pixel_count)
                    .map_err(|_| EffectError::InvalidInput("effect frame size overflow"))?;
            }
            layer.intensity = *intensity;
            self.active.push(layer);
        }
        Ok(())
    }

    pub fn push(
        &mut self,
        name: &str,
        settings: &Map<String, Value>,
        intensity: f32,
        pixel_count: usize,
    ) -> Result<(), EffectError> {
        if self.active.len() >= 2 {
            return Err(EffectError::Capacity);
        }
        if !intensity.is_finite() || !(0.0..=1.0).contains(&intensity) {
            return Err(EffectError::InvalidInput(
                "effect intensity must be in [0, 1]",
            ));
        }
        self.active.push(ActiveLayer {
            effect: EffectInstance::new(name, settings)?,
            intensity,
            frame: FrameBuffer::new(pixel_count)
                .map_err(|_| EffectError::InvalidInput("effect frame size overflow"))?,
        });
        Ok(())
    }

    pub fn clear(&mut self) {
        self.active.clear();
    }

    pub fn render_and_composite(
        &mut self,
        base: &FrameBuffer,
        destination: &mut FrameBuffer,
        input: RenderingInput,
        base_intensity: f32,
    ) -> Result<(), EffectError> {
        self.render_and_composite_with_trigger_beats(
            base,
            destination,
            input,
            base_intensity,
            &[None, None],
        )
    }

    pub fn render_and_composite_with_trigger_beats(
        &mut self,
        base: &FrameBuffer,
        destination: &mut FrameBuffer,
        input: RenderingInput,
        base_intensity: f32,
        trigger_beat_strengths: &[Option<f64>; 2],
    ) -> Result<(), EffectError> {
        let mut strengths = [0.0f32; 2];
        let mut used = 0.0f32;
        for (index, layer) in self.active.iter_mut().enumerate() {
            if layer.frame.pixel_count() != base.pixel_count() {
                layer.frame = FrameBuffer::new(base.pixel_count())
                    .map_err(|_| EffectError::InvalidInput("effect frame size overflow"))?;
            }
            let effect_input = if let Some(beat_strength) = trigger_beat_strengths[index] {
                let audio = input.audio.unwrap_or_default();
                RenderingInput {
                    audio: Some(AudioInput {
                        beat: true,
                        beat_strength,
                        ..audio
                    }),
                    ..input
                }
            } else {
                input
            };
            layer.effect.render_next(effect_input, &mut layer.frame)?;
            let requested = layer.effect.strength() * layer.intensity;
            strengths[index] = requested.min((self.max_overlay_strength - used).max(0.0));
            used += strengths[index];
        }
        let first = self
            .active
            .first()
            .map(|layer| (&layer.frame, strengths[0]));
        let second = self.active.get(1).map(|layer| (&layer.frame, strengths[1]));
        screen_composite(base, destination, base_intensity, first, second)
            .map_err(EffectError::Composite)
    }
}

fn render_hard_beat(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let brightness =
        (input.brightness * effect.setting_number("intensity", 100.0) / 100.0).clamp(0.0, 1.0);
    let duty_cycle = effect.setting_number("duty_cycle", 30.0);
    let (alpha, hue, lightness) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat {
            effect.flash = (effect.flash + 0.9).min(1.0);
            effect.hue = (effect.hue + 43).rem_euclid(256);
        }
        let decay = effect.setting_number("decay", 50.0) / 100.0;
        let release = 0.005 + decay * 0.08 + (1.0 - duty_cycle / 100.0) * 0.04;
        effect.flash = (effect.flash - release - reactive.high * 0.04).max(0.0);
        (
            effect.flash,
            effect.hue,
            (50.0 + reactive.mid * 20.0 + effect.flash * 20.0) as i32,
        )
    } else {
        let on_frames = python_round(8.0 * duty_cycle / 100.0).max(1);
        if frame.rem_euclid(8) < on_frames {
            (1.0, frame * 10 % 256, 70)
        } else {
            (0.0, 0, 0)
        }
    };
    if alpha < 0.01 {
        destination.clear();
        return;
    }
    let [red, green, blue] = hsl_to_rgb(hue as f64, 100.0, f64::from(lightness));
    destination.fill(Rgba::new(red, green, blue, alpha_byte(alpha, brightness)));
}

fn render_confetti(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    if effect.confetti_intensities.len() != length {
        effect.confetti_intensities = vec![0.0; length];
    }
    let count = effect.setting_number("count", 4.0);
    let spread = effect.setting_number("spread", 60.0) / 100.0;
    let fade = effect.setting_number("fade_time", 50.0) / 100.0;
    let (audio_mode, accent, high, bloom, hue_shift, density, addition, decay) =
        if let Some(audio) = input.audio {
            let reactive = AudioReactive::from_input(audio);
            (
                true,
                reactive.accent,
                reactive.high,
                reactive.beat_pulse(0.0, 0.45),
                reactive.hue_shift(frame, 0.25),
                (4.0 + count * 2.0 + reactive.drive() * 36.0 + shimmer(reactive) * 22.0) as u32,
                0.4 + reactive.high * 0.15,
                (0.68 + fade * 0.30 - reactive.high * 0.03).max(0.68),
            )
        } else {
            (false, 0.0, 0.0, 0.0, 0, 0, 0.45, 0.72 + fade * 0.32)
        };
    let frame_seed = (frame as u64).wrapping_mul(1_103_515_245);
    let center = if length == 0 {
        0
    } else {
        (frame as u64).wrapping_mul(37).rem_euclid(length as u64) as usize
    };
    let brightness =
        (input.brightness * effect.setting_number("intensity", 100.0) / 100.0).clamp(0.0, 1.0);
    let palette = effect.palette();

    for index in 0..length {
        let seed = frame_seed.wrapping_add((index as u64).wrapping_mul(12_345));
        let previous = f64::from(effect.confetti_intensities[index]);
        let mut strength = (previous * decay) as f32;
        let direct = index.abs_diff(center);
        let distance = direct.min(length.saturating_sub(direct));
        let spread_weight = (1.0 - spread)
            + spread * (1.0 - distance as f64 / (length as f64 * 0.5).max(1.0)).max(0.0);
        let triggered = if audio_mode {
            accent > 0.55 || ((seed % 100) as f64) < f64::from(density) * spread_weight
        } else {
            ((seed % 100) as f64) < (4.0 + count * 3.0) * spread_weight
        };
        if triggered {
            strength = (f64::from(strength) + addition).min(1.0) as f32;
        }
        effect.confetti_intensities[index] = strength;

        let (rgb, alpha) = if strength > 0.03 {
            let hue = if audio_mode {
                ((seed / 100) as i64
                    + i64::from(hue_shift)
                    + (band_window(input.audio.unwrap(), index, length, 1) * 18.0) as i64)
                    .rem_euclid(256)
            } else {
                ((seed / 100) as i64 + frame * 2).rem_euclid(256)
            };
            let lightness = if audio_mode {
                46 + (f64::from(strength) * 20.0 + high * 8.0) as i32
            } else {
                48 + (f64::from(strength) * 16.0) as i32
            };
            let alpha = if audio_mode {
                (0.12 + f64::from(strength) * 0.78 + bloom * 0.08).min(1.0)
            } else {
                (0.12 + f64::from(strength) * 0.88).min(1.0)
            };
            (
                catalog_palette_hsl_to_rgb(hue as f64, 100.0, f64::from(lightness), palette),
                alpha,
            )
        } else {
            (transparent_palette_rgb(palette), 0.0)
        };
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn shimmer(reactive: AudioReactive) -> f64 {
    (reactive.high * 0.6 + reactive.mid * 0.2 + reactive.accent * 0.2).clamp(0.0, 1.0)
}

fn transparent_palette_rgb(palette: Palette) -> [u8; 3] {
    match palette {
        Palette::Rainbow => [0, 0, 0],
        Palette::Custom(color) => [
            ((color >> 16) & 0xff) as u8,
            ((color >> 8) & 0xff) as u8,
            (color & 0xff) as u8,
        ],
        Palette::Neon | Palette::Fire => [91, 91, 91],
    }
}

fn python_round(value: f64) -> i64 {
    let lower = value.floor();
    let fraction = value - lower;
    if fraction < 0.5 {
        lower as i64
    } else if fraction > 0.5 || (lower as i64).rem_euclid(2) != 0 {
        (lower + 1.0) as i64
    } else {
        lower as i64
    }
}

#[derive(Debug)]
pub enum EffectError {
    UnknownEffect(String),
    NotPorted(String),
    InvalidSettings(String),
    InvalidInput(&'static str),
    Capacity,
    Composite(CompositeError),
}

impl std::fmt::Display for EffectError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownEffect(name) => write!(formatter, "unknown effect: {name}"),
            Self::NotPorted(name) => write!(formatter, "effect renderer is not ported: {name}"),
            Self::InvalidSettings(message) => formatter.write_str(message),
            Self::InvalidInput(message) => formatter.write_str(message),
            Self::Capacity => formatter.write_str("the effect stack supports at most two effects"),
            Self::Composite(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for EffectError {}

#[cfg(test)]
mod tests {
    use super::{EffectInstance, EffectStack};
    use lumistripe_engine_render_primitives::{AudioInput, FrameBuffer, RenderingInput};
    use serde_json::Map;

    #[test]
    fn hard_beat_uses_timestamped_audio_and_retains_flash_state() {
        let mut effect = EffectInstance::new("hard_beat", &Map::new()).unwrap();
        let mut output = FrameBuffer::new(24).unwrap();
        effect
            .render_next(
                RenderingInput {
                    timestamp_ns: 20_000_000,
                    brightness: 0.8,
                    audio: Some(AudioInput {
                        rms: 0.2,
                        bands: [0.1, 0.3, 0.4, 0.6, 0.2, 0.1, 0.2, 0.2],
                        beat: true,
                        beat_strength: 0.8,
                    }),
                    ..RenderingInput::default()
                },
                &mut output,
            )
            .unwrap();
        assert_eq!(effect.frame_index(), 1);
        assert!(output.as_bytes().chunks_exact(4).all(|pixel| pixel[3] > 0));
    }

    #[test]
    fn effect_stack_composites_into_caller_owned_buffer() {
        let mut stack = EffectStack::new(0.75).unwrap();
        stack.push("hard_beat", &Map::new(), 1.0, 8).unwrap();
        stack.push("confetti", &Map::new(), 0.5, 8).unwrap();
        let mut base = FrameBuffer::new(8).unwrap();
        let mut output = FrameBuffer::new(8).unwrap();
        let address = output.as_bytes().as_ptr();
        let input = RenderingInput {
            audio: Some(AudioInput {
                beat: true,
                beat_strength: 0.9,
                ..AudioInput::default()
            }),
            brightness: 1.0,
            ..RenderingInput::default()
        };
        stack
            .render_and_composite(&base, &mut output, input, 1.0)
            .unwrap();
        assert_eq!(output.as_bytes().as_ptr(), address);
        assert!(output.as_bytes().iter().any(|value| *value > 0));
        base.clear();
    }

    #[test]
    fn effect_stack_uses_scene_trigger_beats_when_audio_has_no_beat() {
        let mut stack = EffectStack::new(0.75).unwrap();
        stack.push("hard_beat", &Map::new(), 1.0, 24).unwrap();
        let base = FrameBuffer::new(24).unwrap();
        let mut output = FrameBuffer::new(24).unwrap();
        stack
            .render_and_composite_with_trigger_beats(
                &base,
                &mut output,
                RenderingInput {
                    audio: Some(AudioInput {
                        beat: false,
                        beat_strength: 0.0,
                        ..AudioInput::default()
                    }),
                    ..RenderingInput::default()
                },
                1.0,
                &[Some(0.9), None],
            )
            .unwrap();

        assert!(output.as_bytes().chunks_exact(4).any(|pixel| pixel[3] > 0));
    }

    #[test]
    fn confetti_reuses_intensity_state_after_initialization() {
        let mut effect = EffectInstance::new("confetti", &Map::new()).unwrap();
        let mut output = FrameBuffer::new(64).unwrap();
        let input = RenderingInput {
            brightness: 0.8,
            ..RenderingInput::default()
        };
        effect.render_next(input, &mut output).unwrap();
        let address = effect.confetti_intensities.as_ptr() as usize;
        let capacity = effect.confetti_intensities.capacity();
        for _ in 0..8 {
            effect.render_next(input, &mut output).unwrap();
            assert_eq!(address, effect.confetti_intensities.as_ptr() as usize);
            assert_eq!(capacity, effect.confetti_intensities.capacity());
        }
    }

    #[test]
    fn migrated_random_effects_reuse_bounded_state_buffers() {
        let input = RenderingInput {
            brightness: 0.8,
            audio: Some(AudioInput {
                rms: 0.44,
                bands: [0.82, 0.64, 0.48, 0.31, 0.57, 0.26, 0.41, 0.19],
                beat: true,
                beat_strength: 0.91,
            }),
            ..RenderingInput::default()
        };
        for name in ["electric_storm", "firework_burst", "pixel_explosion"] {
            let mut effect = EffectInstance::new(name, &Map::new()).unwrap();
            effect.set_random_seed(12_345);
            let mut output = FrameBuffer::new(64).unwrap();
            effect.render_next(input, &mut output).unwrap();
            let (address, capacity) = match name {
                "electric_storm" => (
                    effect.electric_storm_streaks.as_ptr() as usize,
                    effect.electric_storm_streaks.capacity(),
                ),
                "firework_burst" => (
                    effect.firework_bursts.as_ptr() as usize,
                    effect.firework_bursts.capacity(),
                ),
                _ => (
                    effect.pixel_bursts.as_ptr() as usize,
                    effect.pixel_bursts.capacity(),
                ),
            };
            for _ in 0..32 {
                effect.render_next(input, &mut output).unwrap();
                let (current_address, current_capacity) = match name {
                    "electric_storm" => (
                        effect.electric_storm_streaks.as_ptr() as usize,
                        effect.electric_storm_streaks.capacity(),
                    ),
                    "firework_burst" => (
                        effect.firework_bursts.as_ptr() as usize,
                        effect.firework_bursts.capacity(),
                    ),
                    _ => (
                        effect.pixel_bursts.as_ptr() as usize,
                        effect.pixel_bursts.capacity(),
                    ),
                };
                assert_eq!(current_address, address, "{name} state allocation moved");
                assert_eq!(current_capacity, capacity, "{name} state capacity changed");
            }
        }
    }

    #[test]
    fn effect_stack_retains_state_when_layers_are_added_or_intensity_changes() {
        let beat_settings = Map::new();
        let confetti_settings = Map::new();
        let mut stack = EffectStack::new(0.75).unwrap();
        stack
            .reconfigure(&[("hard_beat", &beat_settings, 1.0)], 24)
            .unwrap();
        let base = FrameBuffer::new(24).unwrap();
        let mut output = FrameBuffer::new(24).unwrap();
        let input = RenderingInput {
            brightness: 1.0,
            audio: Some(AudioInput {
                beat: true,
                beat_strength: 0.9,
                ..AudioInput::default()
            }),
            ..RenderingInput::default()
        };
        stack
            .render_and_composite(&base, &mut output, input, 1.0)
            .unwrap();
        stack
            .render_and_composite(&base, &mut output, input, 1.0)
            .unwrap();
        assert_eq!(stack.active[0].effect.frame_index(), 2);

        stack
            .reconfigure(
                &[
                    ("hard_beat", &beat_settings, 0.6),
                    ("confetti", &confetti_settings, 0.4),
                ],
                24,
            )
            .unwrap();
        assert_eq!(stack.active[0].effect.frame_index(), 2);
        assert_eq!(stack.active[0].intensity, 0.6);
        assert_eq!(stack.active[1].effect.frame_index(), 0);

        stack
            .render_and_composite(&base, &mut output, input, 1.0)
            .unwrap();
        assert_eq!(stack.active[0].effect.frame_index(), 3);
        assert_eq!(stack.active[1].effect.frame_index(), 1);
    }

    #[test]
    fn migrated_effects_match_python_and_native_c_golden_frames() {
        const RGB_TOLERANCE: u8 = 5;
        const ALPHA_TOLERANCE: u8 = 1;
        let fixtures: serde_json::Value =
            serde_json::from_str(include_str!("../golden_frames.json"))
                .expect("Python/C effect golden fixtures must be valid JSON");
        let cases = fixtures["cases"].as_array().expect("effect fixture cases");
        assert_eq!(cases.len(), 38);
        for case in cases {
            let name = case["name"].as_str().expect("effect name");
            let settings = case["settings"]
                .as_object()
                .expect("effect settings")
                .clone();
            let pixel_count = case["pixel_count"].as_u64().expect("pixel count") as usize;
            let brightness = case["brightness"].as_f64().expect("brightness");
            let delta_ns = case["delta_ns"].as_u64().expect("frame delta");
            let audio_frames = case["audio"].as_array().expect("audio sequence");
            let golden_frames = case["frames_hex"].as_array().expect("golden frames");
            let mut effect = EffectInstance::new(name, &settings).unwrap();
            effect.set_random_seed(12_345);
            let mut output = FrameBuffer::new(pixel_count).unwrap();
            for (index, (audio, golden)) in audio_frames.iter().zip(golden_frames).enumerate() {
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
                effect
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
                let expected = decode_hex(golden.as_str().unwrap());
                for (byte_index, (actual, expected)) in
                    output.as_bytes().iter().zip(expected).enumerate()
                {
                    let tolerance = if byte_index % 4 == 3 {
                        ALPHA_TOLERANCE
                    } else {
                        RGB_TOLERANCE
                    };
                    let error = actual.abs_diff(expected);
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
