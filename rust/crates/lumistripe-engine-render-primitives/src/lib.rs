//! Reusable RGBA buffers, color conversion, and typed audio render inputs.

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Rgba(pub [u8; 4]);

impl Rgba {
    pub const TRANSPARENT: Self = Self([0, 0, 0, 0]);
    pub const BLACK: Self = Self([0, 0, 0, 255]);

    pub const fn new(red: u8, green: u8, blue: u8, alpha: u8) -> Self {
        Self([red, green, blue, alpha])
    }
}

/// A fixed-size, contiguous RGBA frame. The allocation is retained across renders.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct FrameBuffer {
    bytes: Vec<u8>,
}

impl FrameBuffer {
    pub fn new(pixel_count: usize) -> Result<Self, FrameError> {
        let byte_count = pixel_count
            .checked_mul(4)
            .ok_or(FrameError::PixelCountOverflow)?;
        Ok(Self {
            bytes: vec![0; byte_count],
        })
    }

    pub fn pixel_count(&self) -> usize {
        self.bytes.len() / 4
    }

    pub fn from_bytes(bytes: Vec<u8>) -> Result<Self, FrameError> {
        if bytes.len() % 4 != 0 {
            return Err(FrameError::InvalidByteCount);
        }
        Ok(Self { bytes })
    }

    pub fn into_bytes(self) -> Vec<u8> {
        self.bytes
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn as_bytes_mut(&mut self) -> &mut [u8] {
        &mut self.bytes
    }

    pub fn pixel(&self, index: usize) -> Option<Rgba> {
        let start = index.checked_mul(4)?;
        let end = start.checked_add(4)?;
        let bytes = self.bytes.get(start..end)?;
        Some(Rgba([bytes[0], bytes[1], bytes[2], bytes[3]]))
    }

    pub fn set_pixel(&mut self, index: usize, pixel: Rgba) -> bool {
        let Some(start) = index.checked_mul(4) else {
            return false;
        };
        let Some(end) = start.checked_add(4) else {
            return false;
        };
        let Some(destination) = self.bytes.get_mut(start..end) else {
            return false;
        };
        destination.copy_from_slice(&pixel.0);
        true
    }

    pub fn fill(&mut self, pixel: Rgba) {
        for destination in self.bytes.chunks_exact_mut(4) {
            destination.copy_from_slice(&pixel.0);
        }
    }

    pub fn clear(&mut self) {
        self.bytes.fill(0);
    }
}

/// Precomputed mapping from a logical zone canvas to a physical output frame.
/// Mapping reuses its index table and does not allocate while copying frames.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ZoneMapping {
    logical_pixel_count: usize,
    physical_indices: Vec<usize>,
}

impl ZoneMapping {
    pub fn new(
        physical_pixel_count: usize,
        zone_count: usize,
        alternate: bool,
    ) -> Result<Self, ZoneMappingError> {
        if physical_pixel_count == 0 {
            return Err(ZoneMappingError::EmptyPhysicalOutput);
        }
        if zone_count == 0 || zone_count > physical_pixel_count {
            return Err(ZoneMappingError::InvalidZoneCount);
        }

        let base_length = physical_pixel_count / zone_count;
        let remainder = physical_pixel_count % zone_count;
        let logical_pixel_count = base_length + usize::from(remainder > 0);
        let mut physical_indices = Vec::with_capacity(physical_pixel_count);

        for zone_index in 0..zone_count {
            let zone_length = base_length + usize::from(zone_index < remainder);
            let reversed = alternate && zone_index % 2 == 1;
            if zone_length == 1 {
                physical_indices.push(0);
                continue;
            }
            let last_logical_index = (logical_pixel_count - 1) as f64;
            let last_zone_index = (zone_length - 1) as f64;
            for sample_index in 0..zone_length {
                let sample_index = if reversed {
                    zone_length - sample_index - 1
                } else {
                    sample_index
                };
                let position = sample_index as f64 * last_logical_index / last_zone_index;
                physical_indices.push(position.round_ties_even() as usize);
            }
        }

        Ok(Self {
            logical_pixel_count,
            physical_indices,
        })
    }

    pub fn logical_pixel_count(&self) -> usize {
        self.logical_pixel_count
    }

    pub fn physical_pixel_count(&self) -> usize {
        self.physical_indices.len()
    }

    pub fn map_frame(
        &self,
        logical: &FrameBuffer,
        physical: &mut FrameBuffer,
    ) -> Result<(), ZoneMappingError> {
        if logical.pixel_count() != self.logical_pixel_count
            || physical.pixel_count() != self.physical_indices.len()
        {
            return Err(ZoneMappingError::FrameSizeMismatch);
        }
        for (destination_index, source_index) in self.physical_indices.iter().enumerate() {
            let source_start = source_index * 4;
            let destination_start = destination_index * 4;
            physical.as_bytes_mut()[destination_start..destination_start + 4]
                .copy_from_slice(&logical.as_bytes()[source_start..source_start + 4]);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ZoneMappingError {
    EmptyPhysicalOutput,
    InvalidZoneCount,
    FrameSizeMismatch,
}

impl std::fmt::Display for ZoneMappingError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyPhysicalOutput => formatter.write_str("physical output must contain pixels"),
            Self::InvalidZoneCount => {
                formatter.write_str("zone count must be within the physical output length")
            }
            Self::FrameSizeMismatch => {
                formatter.write_str("zone mapping frame size does not match its geometry")
            }
        }
    }
}

impl std::error::Error for ZoneMappingError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FrameError {
    PixelCountOverflow,
    InvalidByteCount,
}

impl std::fmt::Display for FrameError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::PixelCountOverflow => formatter.write_str("RGBA frame size overflow"),
            Self::InvalidByteCount => {
                formatter.write_str("RGBA frame byte length is not divisible by four")
            }
        }
    }
}

impl std::error::Error for FrameError {}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum Palette {
    #[default]
    Rainbow,
    Neon,
    Fire,
    Custom(u32),
}

impl Palette {
    pub fn parse(name: &str, custom_rgb: u32) -> Option<Self> {
        match name {
            "rainbow" => Some(Self::Rainbow),
            "neon" => Some(Self::Neon),
            "fire" => Some(Self::Fire),
            "custom" if custom_rgb <= 0x00ff_ffff => Some(Self::Custom(custom_rgb)),
            _ => None,
        }
    }

    fn index(self) -> i32 {
        match self {
            Self::Rainbow => 0,
            Self::Neon => 1,
            Self::Fire => 2,
            Self::Custom(_) => 3,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AudioInput {
    pub rms: f64,
    pub bands: [f64; 8],
    pub beat: bool,
    pub beat_strength: f64,
}

impl Default for AudioInput {
    fn default() -> Self {
        Self {
            rms: 0.0,
            bands: [0.0; 8],
            beat: false,
            beat_strength: 0.0,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct RenderingInput {
    pub timestamp_ns: u64,
    pub delta_ns: u64,
    pub brightness: f64,
    pub audio: Option<AudioInput>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct AudioReactive {
    pub rms: f64,
    pub accent: f64,
    pub low: f64,
    pub mid: f64,
    pub high: f64,
}

impl AudioReactive {
    pub fn from_input(audio: AudioInput) -> Self {
        let rms = finite_or_zero(audio.rms);
        let beat_strength = finite_or_zero(audio.beat_strength);
        let bands = audio.bands.map(finite_or_zero);
        Self {
            rms,
            accent: clamp01(if audio.beat {
                beat_strength.max(rms * 0.55)
            } else {
                rms * 0.2
            }),
            low: clamp01((bands[0] + bands[1]) * 0.5),
            mid: clamp01((bands[2] + bands[3] + bands[4]) / 3.0),
            high: clamp01((bands[5] + bands[6] + bands[7]) / 3.0),
        }
    }

    pub fn drive(self) -> f64 {
        clamp01(self.rms * 0.5 + self.low * 0.3 + self.mid * 0.2)
    }

    pub fn speed(self, base: f64, range: f64) -> f64 {
        base + self.drive() * range + self.accent * range * 0.35
    }

    pub fn beat_pulse(self, floor: f64, amount: f64) -> f64 {
        clamp01(floor.max(self.accent * amount + self.rms * amount * 0.25))
    }

    pub fn hue_shift(self, frame: i64, base_rate: f64) -> i32 {
        ((frame as f64 * (base_rate + self.high * 3.0 + self.accent * 6.0)) as i64).rem_euclid(256)
            as i32
    }
}

pub fn band_window(audio: AudioInput, index: usize, length: usize, span: usize) -> f64 {
    if length <= 1 {
        return audio.bands[0];
    }
    let position = index as f64 * 8.0 / (length - 1) as f64;
    let center = position as usize;
    let start = center.saturating_sub(span);
    let end = (center + span + 1).min(8);
    if start >= end {
        return audio.bands[center.min(7)];
    }
    audio.bands[start..end].iter().sum::<f64>() / (end - start) as f64
}

/// HSL conversion matches `hsl_to_rgb` in the existing native C renderer.
pub fn hsl_to_rgb(hue: f64, saturation: f64, lightness: f64) -> [u8; 3] {
    let hue = hue.rem_euclid(360.0);
    let h = hue / 360.0;
    let s = clamp(saturation, 0.0, 100.0) / 100.0;
    let l = clamp(lightness, 0.0, 100.0) / 100.0;
    if s == 0.0 {
        let gray = (l * 255.0) as u8;
        return [gray; 3];
    }
    let q = if l < 0.5 {
        l * (1.0 + s)
    } else {
        l + s - l * s
    };
    let p = 2.0 * l - q;
    let mut rgb = [0; 3];
    for (channel, offset) in [1.0 / 3.0, 0.0, -1.0 / 3.0].into_iter().enumerate() {
        let mut t = h + offset;
        if t < 0.0 {
            t += 1.0;
        } else if t > 1.0 {
            t -= 1.0;
        }
        let value = if t < 1.0 / 6.0 {
            p + (q - p) * 6.0 * t
        } else if t < 1.0 / 2.0 {
            q
        } else if t < 2.0 / 3.0 {
            p + (q - p) * 6.0 * (2.0 / 3.0 - t)
        } else {
            p
        };
        rgb[channel] = (value * 255.0).clamp(0.0, 255.0) as u8;
    }
    rgb
}

pub fn palette_hsl_to_rgb(hue: f64, saturation: f64, lightness: f64, palette: Palette) -> [u8; 3] {
    if let Palette::Custom(rgb) = palette {
        return [
            ((rgb >> 16) & 0xff) as u8,
            ((rgb >> 8) & 0xff) as u8,
            (rgb & 0xff) as u8,
        ];
    }
    let mut rgb = hsl_to_rgb(hue, saturation, lightness);
    map_palette(&mut rgb, palette.index());
    rgb
}

pub fn map_rgb_palette(rgb: &mut [u8; 3], palette: Palette) {
    map_palette(rgb, palette.index());
}

pub fn catalog_palette_hsl_to_rgb(
    mut hue: f64,
    saturation: f64,
    lightness: f64,
    palette: Palette,
) -> [u8; 3] {
    match palette {
        Palette::Neon => {
            const HUES: [f64; 7] = [300.0, 160.0, 52.0, 206.0, 12.0, 268.0, 132.0];
            hue = HUES[((hue / 45.0).rem_euclid(7.0).floor() as usize) % HUES.len()];
        }
        Palette::Fire => {
            const HUES: [f64; 5] = [0.0, 10.0, 22.0, 35.0, 48.0];
            hue = HUES[((hue / 55.0).rem_euclid(5.0).floor() as usize) % HUES.len()];
        }
        Palette::Rainbow | Palette::Custom(_) => {}
    }
    palette_hsl_to_rgb(
        hue,
        saturation,
        lightness,
        match palette {
            Palette::Custom(rgb) => Palette::Custom(rgb),
            _ => Palette::Rainbow,
        },
    )
}

fn map_palette(rgb: &mut [u8; 3], palette: i32) {
    if palette == 0 || palette == 3 {
        return;
    }
    let [red, green, blue] = rgb.map(|value| f64::from(value) / 255.0);
    let maximum = red.max(green).max(blue);
    let minimum = red.min(green).min(blue);
    let delta = maximum - minimum;
    let saturation = if maximum == 0.0 { 0.0 } else { delta / maximum };
    let mut hue = 0.0;
    if delta != 0.0 {
        let red_component = (maximum - red) / delta;
        let green_component = (maximum - green) / delta;
        let blue_component = (maximum - blue) / delta;
        if maximum == red {
            hue = blue_component - green_component;
        } else if maximum == green {
            hue = 2.0 + red_component - blue_component;
        } else {
            hue = 4.0 + green_component - red_component;
        }
        hue /= 6.0;
        if hue < 0.0 {
            hue += 1.0;
        }
        hue *= 360.0;
    }
    const NEON_HUES: [f64; 7] = [300.0, 160.0, 52.0, 206.0, 12.0, 268.0, 132.0];
    const FIRE_HUES: [f64; 5] = [0.0, 10.0, 22.0, 35.0, 48.0];
    let slot = if palette == 1 {
        (hue / 45.0) as usize % NEON_HUES.len()
    } else {
        (hue / 55.0) as usize % FIRE_HUES.len()
    };
    let mapped_hue = if palette == 1 {
        NEON_HUES[slot]
    } else {
        FIRE_HUES[slot]
    };
    let mapped_saturation = (saturation * 100.0) as f64;
    let mapped_lightness = 36.0 + maximum * 24.0;
    *rgb = hsl_to_rgb(mapped_hue, mapped_saturation, mapped_lightness);
}

pub fn alpha_byte(alpha: f64, brightness: f64) -> u8 {
    (clamp(alpha, 0.0, 1.0) * clamp(brightness, 0.0, 1.0) * 255.0) as u8
}

pub fn clamp01(value: f64) -> f64 {
    clamp(value, 0.0, 1.0)
}

fn clamp(value: f64, minimum: f64, maximum: f64) -> f64 {
    value.max(minimum).min(maximum)
}

fn finite_or_zero(value: f64) -> f64 {
    if value.is_finite() { value } else { 0.0 }
}

#[cfg(test)]
mod tests {
    use super::{
        AudioInput, AudioReactive, FrameBuffer, Palette, Rgba, ZoneMapping, hsl_to_rgb,
        palette_hsl_to_rgb,
    };

    #[test]
    fn reusable_rgba_frame_keeps_its_allocation_and_bounds_checks_pixels() {
        let mut frame = FrameBuffer::new(3).unwrap();
        let address = frame.as_bytes().as_ptr();
        assert!(frame.set_pixel(1, Rgba::new(1, 2, 3, 4)));
        assert_eq!(frame.pixel(1), Some(Rgba::new(1, 2, 3, 4)));
        assert!(!frame.set_pixel(3, Rgba::BLACK));
        frame.fill(Rgba::new(5, 6, 7, 8));
        assert_eq!(frame.as_bytes().as_ptr(), address);
        assert_eq!(frame.as_bytes(), &[5, 6, 7, 8, 5, 6, 7, 8, 5, 6, 7, 8]);
    }

    #[test]
    fn hsl_and_palette_conversion_match_native_c_truncation() {
        assert_eq!(hsl_to_rgb(0.0, 100.0, 50.0), [255, 0, 0]);
        assert_eq!(hsl_to_rgb(120.0, 100.0, 50.0), [0, 255, 0]);
        assert_eq!(hsl_to_rgb(240.0, 100.0, 50.0), [0, 0, 255]);
        assert_eq!(
            palette_hsl_to_rgb(0.0, 100.0, 50.0, Palette::Custom(0x12_34_56)),
            [0x12, 0x34, 0x56]
        );
    }

    #[test]
    fn reactive_audio_inputs_use_the_python_band_and_drive_rules() {
        let input = AudioInput {
            rms: 0.4,
            bands: [0.2, 0.4, 0.3, 0.6, 0.9, 0.1, 0.3, 0.5],
            beat: true,
            beat_strength: 0.7,
        };
        let reactive = AudioReactive::from_input(input);
        assert!((reactive.low - 0.3).abs() < 1e-12);
        assert!((reactive.mid - 0.6).abs() < 1e-12);
        assert!((reactive.high - 0.3).abs() < 1e-12);
        assert_eq!(reactive.accent, 0.7);
        assert!((reactive.drive() - 0.41).abs() < 1e-12);
    }

    #[test]
    fn zone_mapping_matches_python_round_to_even_and_alternating_zones() {
        let repeat = ZoneMapping::new(10, 3, false).unwrap();
        let alternate = ZoneMapping::new(10, 3, true).unwrap();
        let mut logical = FrameBuffer::new(4).unwrap();
        for index in 0..4 {
            logical.set_pixel(index, Rgba::new(index as u8, 0, 0, 255));
        }
        let mut repeat_output = FrameBuffer::new(10).unwrap();
        let mut alternate_output = FrameBuffer::new(10).unwrap();
        repeat.map_frame(&logical, &mut repeat_output).unwrap();
        alternate
            .map_frame(&logical, &mut alternate_output)
            .unwrap();

        assert_eq!(repeat.logical_pixel_count(), 4);
        assert_eq!(
            repeat_output
                .as_bytes()
                .chunks_exact(4)
                .map(|p| p[0])
                .collect::<Vec<_>>(),
            [0, 1, 2, 3, 0, 2, 3, 0, 2, 3]
        );
        assert_eq!(
            alternate_output
                .as_bytes()
                .chunks_exact(4)
                .map(|p| p[0])
                .collect::<Vec<_>>(),
            [0, 1, 2, 3, 3, 2, 0, 0, 2, 3]
        );
    }

    #[test]
    fn zone_mapping_reuses_preallocated_index_geometry() {
        let mapping = ZoneMapping::new(7, 4, true).unwrap();
        let indices = mapping.physical_indices.as_ptr();
        let mut logical = FrameBuffer::new(2).unwrap();
        logical.set_pixel(0, Rgba::new(0, 9, 10, 255));
        logical.set_pixel(1, Rgba::new(1, 9, 10, 255));
        let mut physical = FrameBuffer::new(7).unwrap();
        mapping.map_frame(&logical, &mut physical).unwrap();
        assert_eq!(mapping.physical_indices.as_ptr(), indices);
        assert_eq!(
            physical
                .as_bytes()
                .chunks_exact(4)
                .map(|p| p[0])
                .collect::<Vec<_>>(),
            [0, 1, 1, 0, 0, 1, 0]
        );
    }
}
