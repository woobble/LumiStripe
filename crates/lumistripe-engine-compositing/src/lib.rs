//! RGBA layer compositing and timestamped scene transitions.

use lumistripe_engine_render_primitives::FrameBuffer;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransitionMode {
    Crossfade,
    FadeBlack,
    Wipe,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CompositeError {
    FrameLengthMismatch,
}

impl std::fmt::Display for CompositeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::FrameLengthMismatch => formatter.write_str("composite frame lengths differ"),
        }
    }
}

impl std::error::Error for CompositeError {}

/// Matches the scalar native C SCREEN compositor used by layered playback.
pub fn screen_composite(
    base: &FrameBuffer,
    destination: &mut FrameBuffer,
    base_intensity: f32,
    overlay_1: Option<(&FrameBuffer, f32)>,
    overlay_2: Option<(&FrameBuffer, f32)>,
) -> Result<(), CompositeError> {
    if base.pixel_count() != destination.pixel_count()
        || overlay_1.is_some_and(|(frame, _)| frame.pixel_count() != base.pixel_count())
        || overlay_2.is_some_and(|(frame, _)| frame.pixel_count() != base.pixel_count())
    {
        return Err(CompositeError::FrameLengthMismatch);
    }
    let base_bytes = base.as_bytes();
    let output = destination.as_bytes_mut();
    let first = overlay_1.map(|(frame, strength)| (frame.as_bytes(), strength));
    let second = overlay_2.map(|(frame, strength)| (frame.as_bytes(), strength));
    for index in 0..base.pixel_count() {
        let offset = index * 4;
        for channel in 0..3 {
            let mut composed = visible_channel(base_bytes, offset, channel) * base_intensity;
            if let Some((pixels, strength)) = first {
                if strength > 0.0 {
                    composed = screen_blend(
                        composed,
                        visible_channel(pixels, offset, channel) * strength,
                    );
                }
            }
            if let Some((pixels, strength)) = second {
                if strength > 0.0 {
                    composed = screen_blend(
                        composed,
                        visible_channel(pixels, offset, channel) * strength,
                    );
                }
            }
            let scaled = composed * 255.0;
            output[offset + channel] = if !(scaled > 0.0) {
                0
            } else if scaled >= 255.0 {
                255
            } else {
                scaled as u8
            };
        }
        output[offset + 3] = 255;
    }
    Ok(())
}

fn visible_channel(pixel: &[u8], offset: usize, channel: usize) -> f32 {
    (f32::from(pixel[offset + channel]) / 255.0) * (f32::from(pixel[offset + 3]) / 255.0)
}

fn screen_blend(composed: f32, overlay: f32) -> f32 {
    let base_remaining = 1.0 - composed;
    let overlay_remaining = 1.0 - overlay;
    let remaining = base_remaining * overlay_remaining;
    1.0 - remaining
}

pub fn apply_transition(
    source: &FrameBuffer,
    target: &FrameBuffer,
    destination: &mut FrameBuffer,
    mode: TransitionMode,
    amount: f64,
) -> Result<(), CompositeError> {
    if source.pixel_count() != target.pixel_count()
        || source.pixel_count() != destination.pixel_count()
    {
        return Err(CompositeError::FrameLengthMismatch);
    }
    let amount = amount.clamp(0.0, 1.0) as f32;
    let source_bytes = source.as_bytes();
    let target_bytes = target.as_bytes();
    let output = destination.as_bytes_mut();
    match mode {
        TransitionMode::Crossfade => {
            let outgoing = 1.0 - amount;
            for ((&from, &to), output) in
                source_bytes.iter().zip(target_bytes).zip(output.iter_mut())
            {
                let mixed = f32::from(from) * outgoing + f32::from(to) * amount;
                *output = mixed.clamp(0.0, 255.0) as u8;
            }
        }
        TransitionMode::FadeBlack => {
            let (frame, brightness) = if amount <= 0.5 {
                (source_bytes, 1.0 - amount * 2.0)
            } else {
                (target_bytes, (amount - 0.5) * 2.0)
            };
            for (input, output) in frame.chunks_exact(4).zip(output.chunks_exact_mut(4)) {
                for channel in 0..3 {
                    output[channel] =
                        (f32::from(input[channel]) * brightness).clamp(0.0, 255.0) as u8;
                }
                output[3] = input[3];
            }
        }
        TransitionMode::Wipe => {
            render_wipe(
                source_bytes,
                target_bytes,
                output,
                source.pixel_count(),
                amount,
            );
        }
    }
    Ok(())
}

fn render_wipe(source: &[u8], target: &[u8], output: &mut [u8], length: usize, amount: f32) {
    if amount <= 0.0 {
        output.copy_from_slice(source);
        return;
    }
    if amount >= 1.0 {
        output.copy_from_slice(target);
        return;
    }
    let feather = (length as f64 * 0.02).round_ties_even().max(1.0).min(8.0) as usize;
    let boundary = f64::from(amount) * length as f64;
    let feather_start = boundary - feather as f64 / 2.0;
    let feather_end = boundary + feather as f64 / 2.0;
    let blend_start = ((feather_start - 0.5).floor() + 1.0).clamp(0.0, length as f64) as usize;
    let blend_end = (feather_end - 0.5)
        .ceil()
        .clamp(blend_start as f64, length as f64) as usize;
    output.copy_from_slice(source);
    output[..blend_start * 4].copy_from_slice(&target[..blend_start * 4]);
    for pixel in blend_start..blend_end {
        let incoming = ((feather_end - (pixel as f64 + 0.5)) / feather as f64).clamp(0.0, 1.0);
        let outgoing = 1.0 - incoming;
        for channel in 0..4 {
            let offset = pixel * 4 + channel;
            output[offset] =
                (f64::from(source[offset]) * outgoing + f64::from(target[offset]) * incoming) as u8;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{TransitionMode, apply_transition, screen_composite};
    use lumistripe_engine_render_primitives::{FrameBuffer, Rgba};

    #[test]
    fn screen_composition_respects_rgba_alpha_strength_and_layer_order() {
        let mut base = FrameBuffer::new(1).unwrap();
        base.set_pixel(0, Rgba::new(120, 0, 0, 128));
        let mut first = FrameBuffer::new(1).unwrap();
        first.set_pixel(0, Rgba::new(0, 160, 0, 128));
        let mut second = FrameBuffer::new(1).unwrap();
        second.set_pixel(0, Rgba::new(0, 0, 200, 255));
        let mut output = FrameBuffer::new(1).unwrap();
        screen_composite(
            &base,
            &mut output,
            0.8,
            Some((&first, 0.5)),
            Some((&second, 0.25)),
        )
        .unwrap();
        assert_eq!(output.pixel(0).unwrap().0[3], 255);
        assert!(output.pixel(0).unwrap().0[0] > 0);
        assert!(output.pixel(0).unwrap().0[1] > 0);
        assert!(output.pixel(0).unwrap().0[2] > 0);
    }

    #[test]
    fn transitions_keep_a_reusable_destination_and_fade_keeps_alpha() {
        let mut from = FrameBuffer::new(2).unwrap();
        let mut to = FrameBuffer::new(2).unwrap();
        let mut out = FrameBuffer::new(2).unwrap();
        from.fill(Rgba::new(200, 100, 50, 33));
        to.fill(Rgba::new(0, 20, 80, 210));
        let address = out.as_bytes().as_ptr();
        apply_transition(&from, &to, &mut out, TransitionMode::FadeBlack, 0.5).unwrap();
        assert_eq!(out.pixel(0).unwrap(), Rgba::new(0, 0, 0, 33));
        assert_eq!(out.as_bytes().as_ptr(), address);
        apply_transition(&from, &to, &mut out, TransitionMode::Crossfade, 1.0).unwrap();
        assert_eq!(out, to);
    }

    #[test]
    fn wipe_uses_a_soft_boundary_and_exact_endpoints() {
        let mut from = FrameBuffer::new(16).unwrap();
        let mut to = FrameBuffer::new(16).unwrap();
        let mut out = FrameBuffer::new(16).unwrap();
        from.fill(Rgba::new(255, 0, 0, 255));
        to.fill(Rgba::new(0, 0, 255, 255));
        apply_transition(&from, &to, &mut out, TransitionMode::Wipe, 0.0).unwrap();
        assert_eq!(out, from);
        apply_transition(&from, &to, &mut out, TransitionMode::Wipe, 1.0).unwrap();
        assert_eq!(out, to);
    }

    #[test]
    fn transition_modes_match_controlled_python_golden_frames() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../golden_transitions.json")).unwrap();
        assert_eq!(fixture["schema_version"], 1);
        let pixel_count = fixture["pixel_count"].as_u64().unwrap() as usize;
        let decode_hex = |field: &str| {
            fixture[field]
                .as_str()
                .unwrap()
                .as_bytes()
                .chunks_exact(2)
                .map(|digits| u8::from_str_radix(std::str::from_utf8(digits).unwrap(), 16).unwrap())
                .collect::<Vec<_>>()
        };
        let source = FrameBuffer::from_bytes(decode_hex("source_hex")).unwrap();
        let target = FrameBuffer::from_bytes(decode_hex("target_hex")).unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let mode = match case["mode"].as_str().unwrap() {
                "crossfade" => TransitionMode::Crossfade,
                "fade_black" => TransitionMode::FadeBlack,
                "wipe" => TransitionMode::Wipe,
                unsupported => panic!("unexpected transition mode {unsupported}"),
            };
            let amount = case["amount"].as_f64().unwrap();
            let expected = case["expected_hex"]
                .as_str()
                .unwrap()
                .as_bytes()
                .chunks_exact(2)
                .map(|digits| u8::from_str_radix(std::str::from_utf8(digits).unwrap(), 16).unwrap())
                .collect::<Vec<_>>();
            let mut output = FrameBuffer::new(pixel_count).unwrap();
            apply_transition(&source, &target, &mut output, mode, amount).unwrap();
            assert_eq!(
                output.as_bytes(),
                expected,
                "mode {:?} at {amount}",
                case["mode"]
            );
        }
    }
}
