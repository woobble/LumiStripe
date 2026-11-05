use super::{EffectInstance, FireworkState, PixelBurstState, StormStreak, python_round, shimmer};
use lumistripe_engine_render_primitives::{
    AudioInput, AudioReactive, FrameBuffer, Palette, RenderingInput, Rgba, alpha_byte, band_window,
    map_rgb_palette, palette_hsl_to_rgb,
};

pub(super) fn render(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) -> bool {
    match effect.name() {
        "bass_drop" => render_bass_drop(effect, frame, input, destination),
        "beat_explosion" => render_beat_explosion(effect, frame, input, destination),
        "beat_ripple" => render_beat_ripple(effect, frame, input, destination),
        "beat_tunnel" => render_beat_tunnel(effect, frame, input, destination),
        "beat_wave" => render_beat_wave(effect, frame, input, destination),
        "club_flash" => render_club_flash(effect, frame, input, destination),
        "color_burst" => render_color_burst(effect, frame, input, destination),
        "drop_explosion" => render_drop_explosion(effect, frame, input, destination),
        "drop_wave" => render_drop_wave(effect, frame, input, destination),
        "shockwave" => render_shockwave(effect, frame, input, destination),
        "center_burst" => render_center_burst(effect, frame, input, destination),
        "mirror_flash" => render_mirror_flash(effect, frame, input, destination),
        "spectrum_flash" => render_spectrum_flash(effect, frame, input, destination),
        "electric_storm" => render_electric_storm(effect, frame, input, destination),
        "firework_burst" => render_firework_burst(effect, frame, input, destination),
        "lightning_strike" => render_lightning_strike(effect, frame, input, destination),
        "pixel_explosion" => render_pixel_explosion(effect, frame, input, destination),
        _ => return false,
    }
    true
}

fn render_bass_drop(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let speed_setting = effect.setting_number("speed", 50.0);
    let speed = 0.25 + speed_setting / 100.0 * 1.5;
    let decay = 0.01 + effect.setting_number("decay", 50.0) * 0.0005;
    let (alpha, mut rgb) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let flash = decay_step(&mut effect.bass_drop_flash, reactive.accent, decay);
        if drop_hit(reactive, audio.beat, 0.3, 0.7, audio.beat_strength) {
            effect.bass_drop_flash = 1.0;
            effect.bass_drop_charge = 0.0;
        }
        effect.bass_drop_charge =
            (effect.bass_drop_charge + (0.006 + reactive.low * 0.012) * speed).min(1.0);
        if audio.beat && effect.bass_drop_charge < 0.3 {
            effect.bass_drop_charge = 0.0;
        }
        let tension = effect.bass_drop_charge;
        let preview = if tension > 0.6 {
            reactive.rms * 0.3 + reactive.mid * 0.3
        } else {
            0.0
        };
        let alpha = (preview * tension * 0.4).max(flash).min(1.0);
        let hue = (20.0 + reactive.low * 30.0) as i32;
        let brightness = (flash * 2.0 + tension * 0.3).min(1.0);
        let rgb = if alpha > 0.0 {
            [
                hue.rem_euclid(256) as u8,
                (brightness * 200.0) as u8,
                (brightness * (255.0 - hue as f64 * 0.3)) as u8,
            ]
        } else {
            [0, 0, 0]
        };
        ((alpha * 2.0).min(1.0), rgb)
    } else {
        let period = (100.0 - speed_setting * 0.8).round().max(16.0) as i64;
        effect.bass_drop_charge = (effect.bass_drop_charge + 0.008 * speed).min(1.0);
        if frame.rem_euclid(period) < 2 {
            effect.bass_drop_flash = 1.0;
            effect.bass_drop_charge = 0.0;
        }
        let flash = decay_step(&mut effect.bass_drop_flash, 0.0, decay);
        let tension = effect.bass_drop_charge;
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
    rgb = mapped_rgb(rgb, effect.palette());
    destination.fill(Rgba::new(
        rgb[0],
        rgb[1],
        rgb[2],
        alpha_byte(alpha, output_brightness(effect, input)),
    ));
}

fn render_beat_explosion(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let speed = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    let width = (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.14).max(4.0);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let activity = (reactive.drive() * 0.65 + reactive.accent * 0.25 + reactive.high * 0.1)
            .clamp(0.0, 1.0);
        decay_step(
            &mut effect.beat_explosion_burst,
            reactive.accent.max(activity * 0.35),
            0.06,
        );
        if audio.beat {
            effect.beat_explosion_radius = 0.0;
        } else {
            effect.beat_explosion_radius += reactive.speed(0.35, 2.0) * speed;
            if effect.beat_explosion_radius > center + 8.0 {
                effect.beat_explosion_radius = 0.0;
            }
        }
        let hue =
            (10 + (reactive.low * 30.0) as i64 + frame.wrapping_mul(2)).rem_euclid(256) as f64;
        let lightness =
            50.0 + (reactive.mid * 15.0 + effect.beat_explosion_burst * 15.0) as i32 as f64;
        for index in 0..length {
            let distance = (index as f64 - center).abs();
            let ring = (1.0
                - (distance - effect.beat_explosion_radius).abs() / (width + reactive.high * 2.0))
                .max(0.0);
            let ambient = (1.0 - distance / center.max(1.0)).max(0.0) * 0.08;
            let alpha = (ambient + ring * (0.15 + effect.beat_explosion_burst * 0.75)).min(1.0);
            let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, lightness, palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
            );
        }
    } else {
        effect.beat_explosion_radius = (frame as f64 * 0.45 * speed).rem_euclid(center + 6.0);
        let alpha = if frame.rem_euclid(30) < 3 { 0.9 } else { 0.0 };
        for index in 0..length {
            let distance = (index as f64 - center).abs();
            let ring = (1.0 - (distance - effect.beat_explosion_radius).abs() / width).max(0.0);
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
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    destination.clear();
    let length = destination.pixel_count();
    let max_pos = length.saturating_sub(1) as f64;
    let speed = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    let base_width = (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    let decay_setting = effect.setting_number("decay", 50.0) / 100.0;
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    let (radius, hue, width, alpha_scale, lightness, audio_mode) = if let Some(audio) = input.audio
    {
        let reactive = AudioReactive::from_input(audio);
        let hit = decay_step(
            &mut effect.beat_ripple_burst,
            reactive.accent,
            0.02 + decay_setting * 0.06,
        );
        if audio.beat {
            effect.beat_ripple_radius = 0.0;
            effect.beat_ripple_hue = (effect.beat_ripple_hue + 31).rem_euclid(256);
        } else {
            effect.beat_ripple_radius += reactive.speed(0.4, 2.2) * speed;
        }
        (
            (-effect.beat_ripple_radius).rem_euclid(max_pos + 1.0),
            effect.beat_ripple_hue,
            base_width + reactive.high * 2.0,
            (0.15 + hit * 0.6 + reactive.low * 0.15) * (0.5 + decay_setting),
            (40.0 + reactive.mid * 20.0 + hit * 15.0) as i32,
            true,
        )
    } else {
        let center = max_pos * 0.5;
        let decay = 0.4 + effect.setting_number("decay", 50.0) / 100.0 * 0.6;
        effect.beat_ripple_radius = (frame as f64 * 0.4 * speed).rem_euclid(center + 6.0);
        effect.beat_ripple_hue = frame.wrapping_mul(3).rem_euclid(256) as i32;
        (
            effect.beat_ripple_radius,
            effect.beat_ripple_hue,
            base_width,
            0.1 + decay * (1.0 - effect.beat_ripple_radius / (center + 6.0)),
            50,
            false,
        )
    };
    let center = if audio_mode { max_pos } else { max_pos * 0.5 };
    for index in 0..length {
        let distance = (index as f64 - center).abs();
        let (ring, mirror) = if audio_mode {
            (
                (1.0 - (index as f64 - radius).abs() / width.max(0.001)).max(0.0),
                (1.0 - (index as f64 - (max_pos - radius)).abs() / width.max(0.001)).max(0.0),
            )
        } else {
            (
                (1.0 - (distance - radius).abs() / width.max(0.001)).max(0.0),
                (1.0 - (distance - (center + center - radius)).abs() / width.max(0.001)).max(0.0),
            )
        };
        let alpha = ring.max(mirror) * alpha_scale;
        if alpha > 0.01 {
            let [red, green, blue] =
                palette_hsl_to_rgb(hue as f64, 100.0, f64::from(lightness), palette);
            destination.set_pixel(
                index,
                Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
            );
        }
    }
}

fn render_beat_tunnel(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let max_dist = center.max(1.0);
    let speed = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    let base_width = (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    let clear = transparent_palette_rgb(palette);
    destination.fill(Rgba::new(clear[0], clear[1], clear[2], 0));
    let mut rings = [(0.0, 0.0, 0.0, 0_i32, 0.0, 0.0); 2];
    let ring_count = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let hit = decay_step(&mut effect.beat_tunnel_pulse, reactive.accent, 0.05);
        if audio.beat {
            effect.beat_tunnel_radius = 0.0;
            effect.beat_tunnel_direction = 1.0;
        } else {
            effect.beat_tunnel_radius +=
                reactive.speed(0.3, 1.5) * speed * effect.beat_tunnel_direction;
            if effect.beat_tunnel_radius > max_dist + 2.0 {
                effect.beat_tunnel_direction = -1.0;
            } else if effect.beat_tunnel_radius < -2.0 {
                effect.beat_tunnel_direction = 1.0;
            }
        }
        let hue =
            (170 + (reactive.low * 30.0) as i32 - (reactive.high * 20.0) as i32).rem_euclid(256);
        rings[0] = (
            center,
            effect.beat_tunnel_radius.abs(),
            base_width + reactive.high * 2.0,
            hue,
            0.7 + hit * 0.3,
            max_dist,
        );
        let inner = (effect.beat_tunnel_radius.abs() - max_dist * 0.4).rem_euclid(max_dist + 2.0);
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
        effect.beat_tunnel_radius += 0.5 * speed * effect.beat_tunnel_direction;
        if effect.beat_tunnel_radius > max_dist || effect.beat_tunnel_radius < 0.0 {
            effect.beat_tunnel_direction *= -1.0;
            effect.beat_tunnel_radius = effect.beat_tunnel_radius.clamp(0.0, max_dist);
        }
        let hue_a = frame.wrapping_mul(4).rem_euclid(256) as i32;
        let hue_b = frame.wrapping_mul(4).wrapping_add(120).rem_euclid(256) as i32;
        let inner = (effect.beat_tunnel_radius - max_dist * 0.5).rem_euclid(max_dist + 4.0);
        rings[0] = (
            center,
            effect.beat_tunnel_radius,
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
        let [red, green, blue] = palette_hsl_to_rgb(f64::from(hue), 80.0, 55.0, palette);
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
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    let clear = transparent_palette_rgb(palette);
    let Some(audio) = input.audio else {
        destination.fill(Rgba::new(clear[0], clear[1], clear[2], 0));
        return;
    };
    let reactive = AudioReactive::from_input(audio);
    let speed_scale = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    let width = (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    if audio.beat {
        effect.beat_wave_hue = (effect.beat_wave_hue + 17).rem_euclid(256);
        let slot = if effect.beat_wave_count < 8 {
            let slot = effect.beat_wave_count;
            effect.beat_wave_count += 1;
            slot
        } else {
            effect.beat_wave_positions.copy_within(1..8, 0);
            effect.beat_wave_speeds.copy_within(1..8, 0);
            effect.beat_wave_strengths.copy_within(1..8, 0);
            7
        };
        effect.beat_wave_positions[slot] = 0.0;
        effect.beat_wave_speeds[slot] =
            (0.5 + reactive.speed(0.1, 0.5) * (1.0 + reactive.accent * 0.5)) * speed_scale;
        effect.beat_wave_strengths[slot] = 0.6 + reactive.accent * 0.4;
    }
    let mut retained = 0;
    for index in 0..effect.beat_wave_count {
        let position = effect.beat_wave_positions[index] + effect.beat_wave_speeds[index];
        let strength = effect.beat_wave_strengths[index] * 0.98;
        if strength > 0.01 && position < length as f64 {
            effect.beat_wave_positions[retained] = position;
            effect.beat_wave_speeds[retained] = effect.beat_wave_speeds[index];
            effect.beat_wave_strengths[retained] = strength;
            retained += 1;
        }
    }
    effect.beat_wave_count = retained;
    let glow = 0.04 + reactive.rms * 0.12;
    let hue = reactive.hue_shift(frame, 0.25) as f64;
    for index in 0..length {
        let mut combined = glow;
        for wave in 0..effect.beat_wave_count {
            let distance = (index as f64 - effect.beat_wave_positions[wave]).abs();
            if distance < width {
                combined = (combined + effect.beat_wave_strengths[wave] * (1.0 - distance / width))
                    .min(1.0);
            }
        }
        let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 50.0, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(combined, brightness)),
        );
    }
}

fn render_club_flash(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count().max(1);
    let (drive, low, accent, beat) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        (reactive.drive(), reactive.low, reactive.accent, audio.beat)
    } else {
        (
            0.18 + frame.rem_euclid(13) as f64 / 13.0 * 0.28,
            0.08 + frame.rem_euclid(9) as f64 / 9.0 * 0.18,
            0.0,
            frame.rem_euclid(11) == 0,
        )
    };
    let seed = frame * 131 + length as i64 * 17 + (drive * 100.0) as i64 + (low * 80.0) as i64;
    let strong_hit = (beat && low > 0.24) || accent > 0.74 || low > 0.78 || drive > 0.82;
    let mut trigger_prob = 0.03 + drive * 0.08 + accent * 0.07 + low * 0.05;
    let flash_rate = effect.setting_number("flash_rate", 50.0) / 100.0;
    let release = 0.02 + (1.0 - effect.setting_number("duty_cycle", 30.0) / 100.0) * 0.12;
    trigger_prob *= 0.5 + flash_rate;
    if strong_hit || seed.rem_euclid(1000) < (trigger_prob * 1000.0) as i64 {
        let strength = (0.18 + low * 0.34 + accent * 0.52 + drive * 0.24).min(1.0);
        decay_step(&mut effect.club_flash_value, strength, release);
        effect.club_flash_hue_seed = seed;
        effect.club_flash_whole_strip =
            strong_hit && (seed.div_euclid(7).rem_euclid(3) != 0 || strength > 0.72);
        let max_width = (length / 2).max(3);
        effect.club_flash_segment_width =
            (2 + (strength * max_width as f64) as usize).clamp(2, max_width);
        effect.club_flash_segment_start = seed.rem_euclid(length as i64) as usize;
    } else {
        decay_step(&mut effect.club_flash_value, 0.0, release);
    }
    let flash = effect.club_flash_value;
    let brightness = output_brightness(effect, input);
    destination.fill(Rgba::BLACK);
    if flash <= 0.01 || destination.pixel_count() == 0 {
        return;
    }
    let palette = effect.palette();
    if effect.club_flash_whole_strip {
        let warm_hues = [0.0, 20.0, 38.0, 320.0];
        let hue = warm_hues[effect.club_flash_hue_seed.rem_euclid(4) as usize];
        let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 60.0, palette);
        let alpha = alpha_byte((0.2 + flash * 0.8).min(1.0), brightness);
        destination.fill(Rgba::new(red, green, blue, alpha));
        return;
    }
    const CLUB_HUES: [f64; 7] = [330.0, 18.0, 48.0, 126.0, 190.0, 224.0, 286.0];
    let hue = CLUB_HUES[effect.club_flash_hue_seed.rem_euclid(7) as usize];
    let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 58.0, palette);
    let alpha = alpha_byte((0.12 + flash * 0.88).min(1.0), brightness);
    let mirror_alpha = alpha_byte((0.1 + flash * 0.7).min(1.0), brightness);
    let mirror_enabled = flash > 0.6 && length > 6;
    for offset in 0..effect.club_flash_segment_width {
        let index = (effect.club_flash_segment_start + offset) % length;
        destination.set_pixel(index, Rgba::new(red, green, blue, alpha));
        if mirror_enabled {
            let mirror = length.saturating_sub(1 + index);
            let warm_hues = [0.0, 20.0, 38.0, 320.0];
            let warm_hue =
                warm_hues[(effect.club_flash_hue_seed + offset as i64).rem_euclid(4) as usize];
            let [warm_red, warm_green, warm_blue] =
                palette_hsl_to_rgb(warm_hue, 100.0, 60.0, palette);
            destination.set_pixel(
                mirror,
                Rgba::new(warm_red, warm_green, warm_blue, mirror_alpha),
            );
        }
    }
}

fn render_color_burst(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const CLUB_HUES: [f64; 7] = [330.0, 18.0, 48.0, 126.0, 190.0, 224.0, 286.0];
    let length = destination.pixel_count();
    let mut center = 0.0;
    let mut strength = 0.0;
    let spawn = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat || reactive.accent > 0.8 || reactive.rms > 0.88 {
            center = ((frame * 11 + (reactive.mid * 100.0) as i64).rem_euclid(length.max(1) as i64))
                as f64;
            strength = (0.34 + reactive.accent * 0.66).min(1.0);
            true
        } else if reactive.drive() > 0.7 && frame.rem_euclid(9) == 0 {
            center = ((frame * 5 + (reactive.high * 80.0) as i64).rem_euclid(length.max(1) as i64))
                as f64;
            strength = (0.22 + reactive.drive() * 0.45).min(0.78);
            true
        } else {
            false
        }
    } else if frame.rem_euclid(28) == 0 {
        center = ((frame * 7 + length as i64 * 3).rem_euclid(length.max(1) as i64)) as f64;
        strength = 0.42;
        true
    } else {
        false
    };
    if spawn {
        let spread = effect.setting_number("spread", 60.0) / 100.0;
        let strip_center = length.saturating_sub(1) as f64 * 0.5;
        center = strip_center + (center - strip_center) * spread;
        let hue_seed = frame * 17 + (center * 13.0) as i64 + length as i64 * 5;
        let speed =
            (0.55 + strength * 1.95) * (0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5);
        let width = (1.2 + strength * 4.5) * (0.5 + spread);
        if effect.color_bursts.len() == 6 {
            effect.color_bursts.copy_within(1..6, 0);
            effect.color_bursts.pop();
        }
        effect.color_bursts.push(super::BurstState {
            center,
            radius: 0.0,
            speed,
            hue_seed,
            strength,
            width,
        });
    }
    let decay = effect.setting_number("decay", 50.0) / 100.0;
    destination.fill(Rgba::new(0, 0, 0, 255));
    let mut retained = 0;
    for index in 0..effect.color_bursts.len() {
        let mut burst = effect.color_bursts[index];
        burst.radius += burst.speed;
        burst.width = (burst.width + 0.1).min(8.0);
        burst.strength = (burst.strength - (0.01 + decay * 0.06)).max(0.0);
        if burst.strength > 0.0 {
            effect.color_bursts[retained] = burst;
            retained += 1;
        }
    }
    effect.color_bursts.truncate(retained);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    for burst in &effect.color_bursts {
        let alpha = (0.18 + burst.strength * 0.82).min(1.0);
        for index in 0..length {
            let distance = (index as f64 - burst.center).abs();
            let intensity =
                (1.0 - (distance - burst.radius).abs() / burst.width.max(0.001)).max(0.0);
            if intensity > 0.0 {
                let hue_index =
                    (burst.hue_seed.rem_euclid(7) + (index % 7) as i64).rem_euclid(7) as usize;
                let [red, green, blue] =
                    palette_hsl_to_rgb(CLUB_HUES[hue_index], 100.0, 58.0, palette);
                let pixel_alpha = alpha_byte((intensity * alpha).min(1.0), brightness);
                destination.set_pixel(index, Rgba::new(red, green, blue, pixel_alpha));
            }
        }
    }
}

fn render_drop_explosion(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let brightness = output_brightness(effect, input);
    let (tension, preview, flash_alpha, low, high, audio_mode) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        let speed = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
        let bass_energy = (audio.bands[0] + audio.bands[1]) * 0.5;
        if audio.beat && bass_energy > 0.35 {
            effect.drop_explosion_flash = 1.0;
            effect.drop_explosion_charge = 0.0;
        }
        effect.drop_explosion_charge = (effect.drop_explosion_charge
            + (0.004 + reactive.low * 0.014 + reactive.mid * 0.006) * speed)
            .min(1.0);
        let tension = effect.drop_explosion_charge;
        let release = 0.015
            + effect.setting_number("decay", 50.0) * 0.0005
            + (100.0 - effect.setting_number("duty_cycle", 30.0)) * 0.0001;
        let flash = decay_step(&mut effect.drop_explosion_flash, 0.0, release);
        let preview = if tension > 0.65 {
            reactive.mid * 0.3 + reactive.rms * 0.2
        } else {
            0.0
        };
        (tension, preview, flash, reactive.low, reactive.high, true)
    } else {
        let speed = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
        let period = (80.0 - (effect.setting_number("speed", 50.0) - 50.0) * 0.8)
            .round()
            .max(12.0) as i64;
        let on_frames = (period as f64 * effect.setting_number("duty_cycle", 30.0) / 800.0)
            .round()
            .max(1.0) as i64;
        effect.drop_explosion_charge = (effect.drop_explosion_charge + 0.006 * speed).min(1.0);
        if frame.rem_euclid(period) < on_frames {
            effect.drop_explosion_flash = 1.0;
            effect.drop_explosion_charge = 0.0;
        }
        let tension = effect.drop_explosion_charge;
        let preview = if tension > 0.7 {
            frame.rem_euclid(20) as f64 / 20.0
        } else {
            0.0
        };
        let flash_release = effect.setting_number("decay", 50.0) * 0.0001;
        let flash = decay_step(&mut effect.drop_explosion_flash, 0.0, flash_release);
        (tension, preview, flash, 0.0, 0.0, false)
    };
    for index in 0..length {
        let (rgb, alpha) = if flash_alpha > if audio_mode { 0.4 } else { 0.5 } {
            let warmth = if audio_mode {
                (low * 80.0 + 175.0) as u8
            } else {
                255
            };
            (
                [255, warmth, if audio_mode { warmth / 2 } else { 255 }],
                flash_alpha,
            )
        } else if audio_mode && tension > 0.65 && preview > 0.2 {
            let flicker =
                frame.wrapping_add((index * 7) as i64).rem_euclid(5) as f64 / 5.0 * high * 0.3;
            let warmth = (40.0 + low * 60.0) as u8;
            (
                [warmth, warmth / 3, warmth / 6],
                (preview * 0.6 + flicker).min(1.0),
            )
        } else if !audio_mode && tension > 0.7 && preview > 0.3 {
            let flicker = frame.wrapping_add((index * 7) as i64).rem_euclid(6) as f64 / 6.0;
            ([80, 40, 20], preview * 0.5 + flicker * 0.2)
        } else if audio_mode && tension > 0.3 {
            let pulse = frame
                .wrapping_mul(2)
                .wrapping_add(index as i64)
                .rem_euclid(24) as f64
                / 24.0;
            ([20, 8, 3], tension * 0.12 + pulse * 0.06)
        } else if !audio_mode && tension > 0.3 {
            let pulse = frame
                .wrapping_mul(3)
                .wrapping_add(index as i64)
                .rem_euclid(30) as f64
                / 30.0;
            ([30, 10, 5], tension * 0.15 + pulse * 0.08)
        } else {
            ([0, 0, 0], 0.0)
        };
        let rgb = mapped_rgb(rgb, effect.palette());
        destination.set_pixel(
            index,
            Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
        );
    }
}

fn render_drop_wave(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    const DEFAULT_AUDIO: AudioInput = AudioInput {
        rms: 0.16,
        bands: [0.24, 0.22, 0.18, 0.16, 0.14, 0.12, 0.1, 0.08],
        beat: false,
        beat_strength: 0.0,
    };
    let audio = input.audio.unwrap_or(DEFAULT_AUDIO);
    let reactive = AudioReactive::from_input(audio);
    let beat = input
        .audio
        .map_or(frame.rem_euclid(40) == 0, |value| value.beat);
    let length = destination.pixel_count().max(1);
    effect.drop_wave_charge =
        (effect.drop_wave_charge + 0.006 + reactive.low * 0.018 + reactive.mid * 0.01).min(1.0);
    let speed_scale = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    let width = (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.12).max(1.0);
    let decay = effect.setting_number("decay", 50.0) / 100.0;
    if drop_hit(reactive, beat, 0.34, 0.72, audio.beat_strength) {
        effect.drop_wave_value = 1.0;
        effect.drop_wave_radius = 0.0;
        effect.drop_wave_speed = (0.75 + reactive.accent * 2.4) * speed_scale;
        effect.drop_wave_hue_seed = frame * 19 + (reactive.low * 120.0) as i64;
        effect.drop_wave_charge = 0.0;
    } else if effect.drop_wave_charge > 0.78 && frame.rem_euclid(7) == 0 {
        effect.drop_wave_value = effect.drop_wave_value.max(0.45);
        effect.drop_wave_speed = effect
            .drop_wave_speed
            .max((0.55 + reactive.drive() * 1.2) * speed_scale);
    }
    effect.drop_wave_radius += effect.drop_wave_speed;
    let flash = decay_step(&mut effect.drop_wave_value, 0.0, 0.02 + decay * 0.1);
    let center = length.saturating_sub(1) as f64 / 2.0;
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    destination.fill(Rgba::BLACK);
    for index in 0..destination.pixel_count() {
        let distance = (index as f64 - center).abs();
        if flash > 0.0 {
            let intensity = (1.0
                - (distance - effect.drop_wave_radius).abs()
                    / (width + effect.drop_wave_charge * width))
                .max(0.0);
            if intensity > 0.0 {
                let alpha = (intensity * (0.22 + flash * 0.78)).min(1.0);
                let hue = [0.0, 20.0, 38.0, 320.0]
                    [(effect.drop_wave_hue_seed + index as i64).rem_euclid(4) as usize];
                let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 60.0, palette);
                destination.set_pixel(
                    index,
                    Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
                );
            }
        } else if effect.drop_wave_charge > 0.35 {
            let pre = (1.0 - distance / center.max(1.0)).max(0.0);
            let alpha = (pre * (0.08 + effect.drop_wave_charge * 0.2)).min(1.0);
            if alpha > 0.0 {
                const CLUB_HUES: [f64; 7] = [330.0, 18.0, 48.0, 126.0, 190.0, 224.0, 286.0];
                let hue =
                    CLUB_HUES[(effect.drop_wave_hue_seed + index as i64).rem_euclid(7) as usize];
                let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, 48.0, palette);
                destination.set_pixel(
                    index,
                    Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
                );
            }
        }
    }
}

fn render_shockwave(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let speed_scale = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    let mut width = (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.14).max(1.0);
    let (hue, mid, audio) = if let Some(audio_input) = input.audio {
        let reactive = AudioReactive::from_input(audio_input);
        let release = 0.04 + reactive.high * 0.04;
        decay_step(&mut effect.shockwave_accent, reactive.accent, release);
        if audio_input.beat {
            effect.shockwave_radius = 0.0;
        } else {
            effect.shockwave_radius += reactive.speed(0.48, 1.7) * speed_scale;
            if effect.shockwave_radius > center + 8.0 {
                effect.shockwave_radius = 0.0;
            }
        }
        width += reactive.high * 2.0;
        (
            (170 + (reactive.low * 18.0) as i32 - (reactive.high * 10.0) as i32).rem_euclid(256),
            reactive.mid,
            Some(audio_input),
        )
    } else {
        effect.shockwave_radius = (frame as f64 * 0.32 * speed_scale).rem_euclid(center + 6.0);
        (188, 0.0, None)
    };
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    for index in 0..length {
        let distance = (index as f64 - center).abs();
        let ring = (1.0 - (distance - effect.shockwave_radius).abs() / width).max(0.0);
        let ambient = (1.0 - distance / center.max(1.0)).max(0.0) * 0.12;
        let (alpha, saturation, lightness) = if let Some(audio) = audio {
            let band = band_window(audio, index, length, 1);
            (
                (ambient + ring * 0.88 + effect.shockwave_accent * 0.05).min(1.0),
                95.0,
                (52.0 + mid * 10.0 + band * 8.0 + effect.shockwave_accent * 4.0).min(68.0) as i32
                    as f64,
            )
        } else {
            (ambient + ring * 0.88, 100.0, 58.0)
        };
        let [red, green, blue] = palette_hsl_to_rgb(hue as f64, saturation, lightness, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_center_burst(
    effect: &mut EffectInstance,
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
    let speed_scale = 0.25 + effect.setting_number("speed", 50.0) / 100.0 * 1.5;
    if beat && (reactive.low > 0.22 || reactive.accent > 0.46 || reactive.rms > 0.5) {
        effect.center_burst_radius = 0.0;
        effect.center_burst_speed = (0.9 + reactive.accent * 2.2) * speed_scale;
        effect.center_burst_width =
            (length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.1).max(1.0)
                + reactive.accent * 2.0;
        effect.center_burst_hue_seed = frame.wrapping_mul(23) + (reactive.low * 80.0) as i64;
        effect.center_burst_flash = (0.35 + reactive.accent * 0.65).min(1.0);
    } else if effect.center_burst_flash > 0.0 {
        effect.center_burst_speed = effect
            .center_burst_speed
            .max((0.75 + reactive.drive() * 1.1) * speed_scale);
    } else {
        effect.center_burst_radius += 0.3 + reactive.drive() * 0.4;
    }
    effect.center_burst_radius += effect.center_burst_speed;
    effect.center_burst_flash = (effect.center_burst_flash - 0.08).max(0.0);
    let center = length.saturating_sub(1) as f64 / 2.0;
    let width = effect.center_burst_width.max(1.0);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    destination.fill(Rgba::BLACK);
    for index in 0..destination.pixel_count() {
        let distance = (index as f64 - center).abs();
        let intensity = (1.0 - (distance - effect.center_burst_radius).abs() / width).max(0.0);
        if intensity <= 0.0 {
            continue;
        }
        let alpha = (intensity * (0.28 + effect.center_burst_flash * 0.72)).min(1.0);
        let hue_index = (effect.center_burst_hue_seed + index as i64)
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
    effect: &mut EffectInstance,
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
        effect.mirror_flash_value = (0.34 + reactive.accent * 0.66).min(1.0);
        effect.mirror_flash_hue_seed = frame.wrapping_mul(31) + (reactive.mid * 60.0) as i64;
        effect.mirror_flash_alternate = 1 - effect.mirror_flash_alternate;
    }
    let decay = effect.setting_number("decay", 50.0) / 100.0;
    effect.mirror_flash_value = (effect.mirror_flash_value - (0.02 + decay * 0.16)).max(0.0);
    let length = destination.pixel_count().max(1);
    let center = length.saturating_sub(1) as f64 / 2.0;
    let width = effect.setting_number("width", 30.0) / 100.0;
    let span = (center * (0.2 + width * 0.8) * (0.3 + reactive.drive() * 0.7)).max(1.0);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    destination.fill(Rgba::BLACK);
    for index in 0..length.div_ceil(2) {
        let distance = (index as f64 - center).abs();
        let intensity = (1.0 - distance / span).max(0.0) * effect.mirror_flash_value;
        if intensity <= 0.0 {
            continue;
        }
        let hue_seed = effect.mirror_flash_hue_seed
            + if effect.mirror_flash_alternate != 0 {
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
        let rgb = if effect.mirror_flash_alternate == 0 {
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
    effect: &mut EffectInstance,
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
        effect.spectrum_flash_value = 1.0;
    }
    let decay = effect.setting_number("decay", 50.0) / 100.0;
    effect.spectrum_flash_value = (effect.spectrum_flash_value - (0.02 + decay * 0.12)).max(0.0);
    effect.spectrum_flash_hue_phase =
        (effect.spectrum_flash_hue_phase + 0.35 + reactive.high * 0.9).rem_euclid(360.0);
    let length = destination.pixel_count();
    let blend = effect.setting_number("width", 30.0) / 100.0 * 0.5;
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    for index in 0..length {
        let ratio = if length <= 1 {
            0.0
        } else {
            index as f64 / (length - 1) as f64
        };
        let band_index = ((ratio * 8.0) as usize).min(7);
        let neighbor = (audio.bands[band_index.saturating_sub(1)]
            + audio.bands[(band_index + 1).min(7)])
            * 0.5;
        let band = audio.bands[band_index] * (1.0 - blend) + neighbor * blend;
        let mut hue = (effect.spectrum_flash_hue_phase + ratio * 300.0).rem_euclid(360.0);
        let mut lightness = 56.0;
        let mut alpha = (0.18 + band * 0.72).clamp(0.0, 1.0);
        if effect.spectrum_flash_value > 0.0 {
            let hue_seed = (effect.spectrum_flash_hue_phase / 8.0) as i64;
            let warm_index = (hue_seed + index as i64).rem_euclid(4) as usize;
            hue = f64::from(WARM_HUES[warm_index]);
            lightness = 60.0;
            alpha = (0.1 + effect.spectrum_flash_value * 0.9).min(1.0);
        }
        let [red, green, blue] = palette_hsl_to_rgb(hue, 100.0, lightness, palette);
        destination.set_pixel(
            index,
            Rgba::new(red, green, blue, alpha_byte(alpha, brightness)),
        );
    }
}

fn render_electric_storm(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let count = effect.setting_number("count", 4.0).round().max(0.0) as usize;
    let decay = 0.02 + effect.setting_number("decay", 50.0) * 0.0012;
    let reactive = input.audio.map(AudioReactive::from_input);
    if let Some(reactive) = reactive {
        let flash_decay = decay + reactive.high * 0.02;
        effect.electric_storm_flash =
            (effect.electric_storm_flash - flash_decay).max(reactive.accent);
        if input.audio.is_some_and(|audio| audio.beat) {
            effect.electric_storm_flash = (effect.electric_storm_flash + 0.5).min(1.0);
        }
        let max_streaks =
            count.min((1.0 + reactive.drive() * count as f64 + shimmer(reactive) * 2.0) as usize);
        let should_spawn = reactive.accent > 0.3 || effect.rng.random() < 0.15;
        if effect.electric_storm_streaks.len() < max_streaks.min(8) && should_spawn {
            push_storm_streak(effect, length, Some(reactive));
        }
    } else {
        let flash_rate = effect.setting_number("flash_rate", 50.0);
        let spawn_period = (8.0 - count as f64).round().max(1.0) as i64;
        if frame.rem_euclid(spawn_period) < 1 && effect.electric_storm_streaks.len() < count {
            push_storm_streak(effect, length, None);
        }
        let flash_period = (60.0 - flash_rate * 0.6).round().max(4.0) as i64;
        let on_frames = (flash_period as f64 * flash_rate / 100.0 * 0.08)
            .round()
            .max(1.0) as i64;
        if frame.rem_euclid(flash_period) < on_frames {
            effect.electric_storm_flash = 1.0;
        }
    }

    destination.clear();
    let flash_release = decay + reactive.map_or(0.0, |value| value.high * 0.02);
    effect.electric_storm_flash = (effect.electric_storm_flash - flash_release).max(0.0);
    let flash_threshold = if reactive.is_some() { 0.05 } else { 0.1 };
    let flash_scale = if reactive.is_some() { 0.3 } else { 0.4 };
    if effect.electric_storm_flash > flash_threshold {
        let rgb = palette_hsl_to_rgb(230.0, 80.0, 80.0, effect.palette());
        destination.fill(Rgba::new(
            rgb[0],
            rgb[1],
            rgb[2],
            alpha_byte(
                effect.electric_storm_flash * flash_scale,
                output_brightness(effect, input),
            ),
        ));
    }

    let streak_decay = decay + reactive.map_or(0.0, |value| value.high * 0.02);
    effect.electric_storm_streaks.retain_mut(|streak| {
        streak.position += streak.speed;
        streak.alpha = (streak.alpha - streak_decay).max(0.0);
        streak.alpha >= 0.01 && streak.position < length as f64 + 2.0
    });
    let brightness = output_brightness(effect, input);
    let palette = effect.palette();
    for streak in &effect.electric_storm_streaks {
        for offset in 0..streak.length {
            let pixel = streak.position as usize;
            let Some(pixel) = pixel.checked_sub(offset) else {
                continue;
            };
            if pixel >= length {
                continue;
            }
            let intensity = 1.0 - offset as f64 / streak.length.max(1) as f64;
            let rgb = palette_hsl_to_rgb(streak.hue as f64, 90.0, 75.0, palette);
            destination.set_pixel(
                pixel,
                Rgba::new(
                    rgb[0],
                    rgb[1],
                    rgb[2],
                    alpha_byte(intensity * streak.alpha, brightness),
                ),
            );
        }
    }
}

fn push_storm_streak(effect: &mut EffectInstance, length: usize, reactive: Option<AudioReactive>) {
    if effect.electric_storm_streaks.len() >= effect.electric_storm_streaks.capacity() {
        return;
    }
    let streak = if let Some(reactive) = reactive {
        StormStreak {
            position: effect.rng.randint(0, length.saturating_sub(1) as i32) as f64,
            length: 2 + (reactive.high * 6.0) as usize,
            speed: 0.8 + reactive.speed(0.5, 3.0),
            hue: 180 + (reactive.low * 60.0) as i32 + effect.rng.randint(-20, 20),
            alpha: 0.6 + reactive.accent * 0.4,
        }
    } else {
        StormStreak {
            position: effect.rng.randint(0, length.saturating_sub(1) as i32) as f64,
            length: 2 + effect.rng.randint(0, 4) as usize,
            speed: 1.0 + effect.rng.random() * 2.0,
            hue: 200 + effect.rng.randint(-30, 30),
            alpha: 0.8 + effect.rng.random() * 0.2,
        }
    };
    effect.electric_storm_streaks.push(streak);
}

fn render_firework_burst(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let center = length.saturating_sub(1) as f64 * 0.5;
    let spread = 0.5 + effect.setting_number("spread", 60.0) / 100.0;
    let count = effect.setting_number("count", 4.0).round().clamp(1.0, 12.0) as usize;
    let fade = effect.setting_number("fade_time", 50.0) / 100.0;
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    let clear = transparent_palette_rgb(palette);
    destination.fill(Rgba::new(clear[0], clear[1], clear[2], 0));

    if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat {
            effect.firework_hue = (effect.firework_hue + 41).rem_euclid(256);
            if effect.firework_bursts.len() == 12 {
                effect.firework_bursts.remove(0);
            }
            effect.firework_bursts.push(FireworkState {
                phase: 0.5,
                speed: (0.5 + reactive.speed(0.15, 2.5) * (0.8 + reactive.accent * 0.4)) * spread,
                strength: 0.7 + reactive.accent * 0.3,
                hue: effect.firework_hue,
            });
        }
        for burst in &mut effect.firework_bursts {
            burst.phase += burst.speed;
            burst.strength *= 0.90 + fade * 0.14;
        }
        effect
            .firework_bursts
            .retain(|burst| burst.strength > 0.02 && burst.phase < center + 6.0);
        while effect.firework_bursts.len() > count {
            effect.firework_bursts.remove(0);
        }
        let base_hue = reactive.hue_shift(frame, 0.25);
        let glow = reactive.rms * 0.08;
        for index in 0..length {
            let distance = (index as f64 - center).abs();
            let mut alpha = glow;
            let mut hue = base_hue;
            for burst in &effect.firework_bursts {
                if distance <= burst.phase {
                    let sparkle = (burst.phase - distance) / burst.phase.max(1.0);
                    let mut wave = (1.0 - sparkle * 1.8).max(0.0) * burst.strength;
                    let particle = (index * 7 + (burst.phase * 10.0) as usize * 13) % 7;
                    wave = (wave + (particle as f64 - 3.0) * 0.06).max(0.0);
                    let band = audio.bands[if length <= 1 {
                        0
                    } else {
                        (index * 8 / length).min(7)
                    }];
                    alpha = (alpha + wave + band * 0.2).min(1.0);
                    if wave > 0.0 {
                        hue = burst.hue;
                    }
                }
            }
            if alpha > 0.0 {
                let lightness = (45.0 + reactive.mid * 20.0 + reactive.high * 10.0) as i32;
                let rgb = palette_hsl_to_rgb(hue as f64, 100.0, lightness as f64, palette);
                destination.set_pixel(
                    index,
                    Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
                );
            }
        }
    } else {
        let radius = center + 4.0;
        let fade_alpha = (1.0 - frame.rem_euclid(36) as f64 / 36.0 * (1.0 - fade * 0.75)).max(0.25);
        let burst_id = frame.div_euclid(36);
        let seed = burst_id.wrapping_mul(1_103_515_245).wrapping_add(12_345);
        let hue = (seed.div_euclid(100).rem_euclid(256) + frame.wrapping_mul(2)).rem_euclid(256);
        for index in 0..length {
            let distance = (index as f64 - center).abs();
            let mut alpha: f64 = 0.0;
            for burst in 0..count {
                let phase = (frame as f64 * 0.25 * spread + burst as f64 * radius / count as f64)
                    .rem_euclid(radius.max(1.0));
                if distance <= phase {
                    let sparkle = (phase - distance) / phase.max(1.0);
                    let candidate = (1.0 - sparkle * 1.5).max(0.0);
                    let offset = (index * 7 + burst_id as usize * 13) % 5;
                    let variance = (offset as f64 - 2.0) * 0.04;
                    alpha = alpha.max((candidate + variance).max(0.0));
                }
            }
            if alpha > 0.0 {
                let rgb = palette_hsl_to_rgb(hue as f64, 100.0, 60.0, palette);
                destination.set_pixel(
                    index,
                    Rgba::new(
                        rgb[0],
                        rgb[1],
                        rgb[2],
                        alpha_byte(alpha * 0.85 * fade_alpha, brightness),
                    ),
                );
            }
        }
    }
}

fn render_lightning_strike(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let safe_length = length.max(1);
    let spread = effect.setting_number("spread", 60.0) / 100.0;
    let decay = effect.setting_number("decay", 50.0);
    let reactive = input.audio.map(AudioReactive::from_input);
    let audio_mode = reactive.is_some();
    if let Some(audio) = input.audio {
        let reactive = reactive.expect("audio mode has reactive input");
        if audio.beat {
            effect.lightning_flash = (effect.lightning_flash + 0.8).min(1.0);
            let center = (safe_length - 1) as f64 * 0.5;
            let offset = effect.rng.uniform(-1.0, 1.0);
            effect.lightning_position = python_round(center + offset * center * spread)
                .clamp(0, safe_length.saturating_sub(1) as i64)
                as usize;
            let configured_width =
                (safe_length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.12).max(2.0);
            effect.lightning_length =
                (configured_width + reactive.drive() * 4.0 + shimmer(reactive) * 3.0) as usize;
            effect.lightning_hue = (190 - (reactive.low * 40.0) as i32).rem_euclid(256);
        }
        effect.lightning_flash =
            (effect.lightning_flash - (0.015 + decay * 0.0004 + reactive.high * 0.025)).max(0.0);
    } else {
        if frame.rem_euclid(45) < 3 {
            effect.lightning_flash = 1.0;
            let center = (safe_length - 1) as f64 * 0.5;
            let offset = (frame.wrapping_mul(17).rem_euclid(101) as f64) / 100.0 * 2.0 - 1.0;
            effect.lightning_position = python_round(center + offset * center * spread)
                .clamp(0, safe_length.saturating_sub(1) as i64)
                as usize;
            effect.lightning_length =
                (safe_length as f64 * effect.setting_number("width", 30.0) / 100.0 * 0.12)
                    .round()
                    .max(2.0) as usize;
            effect.lightning_hue = frame.wrapping_mul(50).rem_euclid(256) as i32;
        }
        effect.lightning_flash = (effect.lightning_flash - (0.02 + decay * 0.0008)).max(0.0);
    }
    let all_flash = (effect.lightning_flash * 2.0 - 0.5).max(0.0);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    for index in 0..length {
        let distance = index.abs_diff(effect.lightning_position);
        if distance < effect.lightning_length
            && effect.lightning_flash > if audio_mode { 0.05 } else { 0.1 }
        {
            let intensity =
                (1.0 - distance as f64 / effect.lightning_length.max(1) as f64).max(0.0);
            let rgb = palette_hsl_to_rgb(effect.lightning_hue as f64, 80.0, 70.0, palette);
            destination.set_pixel(
                index,
                Rgba::new(
                    rgb[0],
                    rgb[1],
                    rgb[2],
                    alpha_byte(intensity * effect.lightning_flash, brightness),
                ),
            );
        } else if all_flash > 0.0 {
            let rgb = palette_hsl_to_rgb(effect.lightning_hue as f64, 50.0, 80.0, palette);
            destination.set_pixel(
                index,
                Rgba::new(
                    rgb[0],
                    rgb[1],
                    rgb[2],
                    alpha_byte(all_flash * 0.4, brightness),
                ),
            );
        } else {
            let clear = transparent_palette_rgb(palette);
            destination.set_pixel(index, Rgba::new(clear[0], clear[1], clear[2], 0));
        }
    }
}

fn render_pixel_explosion(
    effect: &mut EffectInstance,
    frame: i64,
    input: RenderingInput,
    destination: &mut FrameBuffer,
) {
    let length = destination.pixel_count();
    let count = effect.setting_number("count", 4.0).round().clamp(1.0, 12.0) as usize;
    let spread = effect.setting_number("spread", 60.0) / 100.0;
    let decay = effect.setting_number("decay", 50.0) / 100.0;
    let (speed, release) = if let Some(audio) = input.audio {
        let reactive = AudioReactive::from_input(audio);
        if audio.beat || reactive.accent > 0.5 {
            let burst_count =
                (1.0 + reactive.accent * 2.0 + reactive.drive() * count as f64) as usize;
            let burst_count = burst_count.min(count);
            for _ in 0..burst_count {
                if effect.pixel_bursts.len() == 12 {
                    effect.pixel_bursts.remove(0);
                }
                let center = (length as f64 - 1.0) * 0.5;
                let spread_px = center * spread;
                let position =
                    python_round(center + effect.rng.uniform(-spread_px, spread_px)) as f64;
                let hue = ((reactive.low * 90.0) as i64
                    + frame.wrapping_mul(6)
                    + i64::from(effect.rng.randint(0, 120)))
                .rem_euclid(256) as i32;
                effect.pixel_bursts.push(PixelBurstState {
                    position,
                    radius: 0.0,
                    hue,
                    alpha: 0.7 + reactive.accent * 0.3,
                });
            }
            while effect.pixel_bursts.len() > count {
                effect.pixel_bursts.remove(0);
            }
        }
        (
            (0.8 + reactive.high * 1.2) * (0.25 + spread * 1.5),
            0.015 + decay * 0.05,
        )
    } else {
        if frame.rem_euclid(12) < 2 {
            if effect.pixel_bursts.len() == 12 {
                effect.pixel_bursts.remove(0);
            }
            effect.pixel_bursts.push(PixelBurstState {
                position: effect.rng.randint(0, length.saturating_sub(1) as i32) as f64,
                radius: 0.0,
                hue: frame.wrapping_mul(17).rem_euclid(256) as i32,
                alpha: 1.0,
            });
            while effect.pixel_bursts.len() > count {
                effect.pixel_bursts.remove(0);
            }
        }
        (0.6, 0.015 + 0.5 * 0.05)
    };
    let max_position = length.saturating_sub(1) as f64;
    effect.pixel_bursts.retain_mut(|burst| {
        burst.radius += speed;
        burst.alpha = (burst.alpha - release).max(0.0);
        burst.alpha >= 0.01 && burst.radius <= max_position + 5.0
    });
    let width = (length as f64 * spread * 0.035).max(1.0);
    let palette = effect.palette();
    let brightness = output_brightness(effect, input);
    destination.clear();
    for burst in &effect.pixel_bursts {
        for index in 0..length {
            let distance = (index as f64 - burst.position).abs();
            let ring = (1.0 - (distance - burst.radius).abs() / width).max(0.0);
            let alpha = ring * burst.alpha;
            if alpha > 0.01 {
                let rgb = palette_hsl_to_rgb(burst.hue as f64, 100.0, 55.0, palette);
                destination.set_pixel(
                    index,
                    Rgba::new(rgb[0], rgb[1], rgb[2], alpha_byte(alpha, brightness)),
                );
            }
        }
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

fn drop_hit(
    reactive: AudioReactive,
    beat: bool,
    low_threshold: f64,
    accent_threshold: f64,
    beat_strength: f64,
) -> bool {
    let onset = if beat {
        beat_strength
    } else {
        reactive.rms * 0.18
    };
    beat && (reactive.low > low_threshold
        || reactive.accent > accent_threshold
        || onset > 0.58
        || reactive.rms > 0.8)
}

fn output_brightness(effect: &EffectInstance, input: RenderingInput) -> f64 {
    (input.brightness * effect.setting_number("intensity", 100.0) / 100.0).clamp(0.0, 1.0)
}

fn mapped_rgb(mut rgb: [u8; 3], palette: Palette) -> [u8; 3] {
    if let Palette::Custom(custom) = palette {
        return [
            ((custom >> 16) & 0xff) as u8,
            ((custom >> 8) & 0xff) as u8,
            (custom & 0xff) as u8,
        ];
    }
    map_rgb_palette(&mut rgb, palette);
    rgb
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
