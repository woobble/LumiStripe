import numpy as np
import numpy.typing as npt

def correct_rgba_into(
    source: npt.NDArray[np.uint8],
    destination: npt.NDArray[np.uint8],
    correction: npt.NDArray[np.uint16],
) -> None: ...

def apply_alpha_scale_into(
    source: npt.NDArray[np.uint8],
    corrected: npt.NDArray[np.uint8],
    destination: npt.NDArray[np.uint8],
    scale: float,
) -> None: ...

def composite_screen_rgba_into(
    base: npt.NDArray[np.uint8],
    destination: npt.NDArray[np.uint8],
    base_intensity: float,
    overlay_1: npt.NDArray[np.uint8] | None,
    strength_1: float,
    overlay_2: npt.NDArray[np.uint8] | None,
    strength_2: float,
) -> None: ...

def render_tunnel_rings_into(
    destination: npt.NDArray[np.uint8],
    geometry: npt.NDArray[np.float64],
    colors: npt.NDArray[np.uint8],
    clear_color: npt.NDArray[np.uint8],
    ring_count: int,
    brightness: float,
) -> None: ...

def render_spectrum_flash_into(
    destination: npt.NDArray[np.uint8],
    bands: tuple[float, ...],
    width: float,
    hue_phase: float,
    flash: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_solid_rgba_into(
    destination: npt.NDArray[np.uint8],
    red: int,
    green: int,
    blue: int,
    alpha: int,
) -> None: ...

def render_pulse_into(
    destination: npt.NDArray[np.uint8],
    phase: float,
    hue: int,
    alpha: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_comet_into(
    destination: npt.NDArray[np.uint8],
    position: float,
    tail: float,
    shimmer: float,
    hue: float,
    intensity: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_dance_floor_into(
    destination: npt.NDArray[np.uint8],
    frame: int,
    segment_width: int,
    frequency: float,
    audio_mode: int,
    hue_shift: float,
    mid: float,
    high: float,
    drive: float,
    beat_boost: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_disco_sparkle_into(
    destination: npt.NDArray[np.uint8],
    intensities: npt.NDArray[np.float32],
    frame: int,
    audio_mode: int,
    density: int,
    accent_trigger: int,
    decay: float,
    variation: float,
    hue_rate: float,
    pulse: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_club_flash_into(
    destination: npt.NDArray[np.uint8],
    hue_seed: int,
    segment_start: int,
    segment_width: int,
    whole_strip: int,
    flash: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_beat_wave_into(
    destination: npt.NDArray[np.uint8],
    waves: npt.NDArray[np.float64],
    wave_count: int,
    hue: int,
    width: float,
    glow: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_beat_explosion_into(
    destination: npt.NDArray[np.uint8],
    frame: int,
    audio_mode: int,
    center: float,
    radius: float,
    width: float,
    hit: float,
    low: float,
    mid: float,
    high: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_firework_burst_into(
    destination: npt.NDArray[np.uint8],
    phases: npt.NDArray[np.float64],
    bursts: npt.NDArray[np.float64],
    phase_count: int,
    burst_count: int,
    frame: int,
    audio_mode: int,
    center: float,
    fade_alpha: float,
    glow: float,
    mid: float,
    high: float,
    brightness: float,
    bands: tuple[float, ...] | None,
    hue: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_color_burst_into(
    destination: npt.NDArray[np.uint8],
    bursts: npt.NDArray[np.float64],
    burst_count: int,
    palette: int,
    brightness: float,
    custom_rgb: int,
) -> None: ...

def render_plasma_rave_into(
    destination: npt.NDArray[np.uint8],
    frequency: float,
    time_one: float,
    time_two: float,
    time_three: float,
    hue_base: int,
    hue_scale: float,
    alpha_extra: float,
    brightness: float,
    palette: int,
    clamp_alpha: int,
    hue_base_before_truncation: int,
    custom_rgb: int,
) -> None: ...

def render_beat_ripple_into(
    destination: npt.NDArray[np.uint8],
    audio_mode: int,
    center: float,
    radius_or_offset: float,
    width: float,
    alpha_scale: float,
    brightness: float,
    hue: int,
    lightness: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_neon_confetti_into(
    destination: npt.NDArray[np.uint8],
    levels: npt.NDArray[np.float32],
    hues: npt.NDArray[np.int16],
    frame: int,
    fade_multiplier: float,
    spawn_threshold: float,
    high: float,
    spread: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_strobe_chase_into(
    destination: npt.NDArray[np.uint8],
    stride: int,
    phase: float,
    hue: int,
    frame: int,
    drive: float,
    flash: float,
    brightness: float,
    flash_active: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_rave_scanner_into(
    destination: npt.NDArray[np.uint8],
    phase: float,
    width: float,
    beam_brightness: float,
    brightness: float,
    hue: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_center_burst_into(
    destination: npt.NDArray[np.uint8],
    hue_seed: int,
    center: float,
    radius: float,
    width: float,
    flash: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_dual_laser_into(
    destination: npt.NDArray[np.uint8],
    position_a: float,
    position_b: float,
    width: float,
    alpha: float,
    brightness: float,
    hue_a: int,
    hue_b: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_mirror_flash_into(
    destination: npt.NDArray[np.uint8],
    hue_seed: int,
    center: float,
    span: float,
    flash: float,
    brightness: float,
    alternate: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_drop_wave_into(
    destination: npt.NDArray[np.uint8],
    hue_seed: int,
    center: float,
    radius: float,
    width: float,
    charge: float,
    flash: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_disco_comet_into(
    destination: npt.NDArray[np.uint8],
    phase: float,
    width: float,
    trail: float,
    hue: int,
    high: float,
    sparkle_threshold: int,
    frame: int,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_pixel_explosion_into(
    destination: npt.NDArray[np.uint8],
    bursts: npt.NDArray[np.float64],
    burst_count: int,
    width: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_lightning_strike_into(
    destination: npt.NDArray[np.uint8],
    strike_position: int,
    strike_length: int,
    hue: int,
    flash_alpha: float,
    all_flash: float,
    brightness: float,
    audio_mode: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_neon_storm_into(
    destination: npt.NDArray[np.uint8],
    levels: npt.NDArray[np.float32],
    hues: npt.NDArray[np.int16],
    positions: npt.NDArray[np.float64],
    drive: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_electric_storm_into(
    destination: npt.NDArray[np.uint8],
    geometry: npt.NDArray[np.float64],
    flash_alpha: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_hsl_solid_into(
    destination: npt.NDArray[np.uint8],
    hue: float,
    saturation: float,
    lightness: float,
    alpha: float,
    brightness: float,
) -> None: ...

def render_halves_rgba_into(
    destination: npt.NDArray[np.uint8],
    left_red: int,
    left_green: int,
    left_blue: int,
    left_alpha: int,
    right_red: int,
    right_green: int,
    right_blue: int,
    right_alpha: int,
) -> None: ...

def render_red_sweep_into(
    destination: npt.NDArray[np.uint8], start: int, width: int, on: bool, alpha: int
) -> None: ...

def render_red_chase_into(
    destination: npt.NDArray[np.uint8], period: int, block: int, phase: float, alpha: int
) -> None: ...

def render_drop_explosion_into(
    destination: npt.NDArray[np.uint8],
    frame: int,
    flash_alpha: float,
    tension: float,
    preview: float,
    audio_mode: bool,
    low: float,
    high: float,
    brightness: float,
) -> None: ...

def render_fire_into(
    destination: npt.NDArray[np.uint8],
    heat: npt.NDArray[np.float32],
    next_heat: npt.NDArray[np.float32],
    frame: int,
    cooling: float,
    sparking: float,
    drive: float,
    bass: float,
    shimmer: float,
    accent: float,
    brightness: float,
) -> None: ...

def render_color_wipe_into(
    destination: npt.NDArray[np.uint8],
    length: int,
    head: float,
    tail: float,
    hue: float,
    saturation: float,
    direction: int,
    local: float,
    boost: float,
    brightness: float,
) -> None: ...

def render_rainbow_pattern_into(
    destination: npt.NDArray[np.uint8],
    mode: int,
    frame: int,
    bands: tuple[float, ...] | None,
    spread: float,
    base_hue: float,
    saturation: float,
    alpha: float,
    low: float,
    mid: float,
    beat_pulse: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_theater_chase_into(
    destination: npt.NDArray[np.uint8],
    stride: int,
    phase: float,
    hue: float,
    drive: float,
    brightness: float,
    audio_mode: bool,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_laser_sweep_into(
    destination: npt.NDArray[np.uint8],
    position: float,
    width: float,
    hue: float,
    alpha: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_sinelon_into(
    destination: npt.NDArray[np.uint8],
    center: float,
    width: float,
    hue: int,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_peak_mirror_into(
    destination: npt.NDArray[np.uint8],
    levels: tuple[float, ...],
    center_flash: float,
    hue_shift: int,
    width: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_bouncing_ball_into(
    destination: npt.NDArray[np.uint8],
    position: float,
    radius: float,
    glow_width: float,
    glow: float,
    hue: int,
    brightness: float,
    audio_mode: bool,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_wave_into(
    destination: npt.NDArray[np.uint8],
    frame: int,
    audio_mode: bool,
    bands: tuple[float, ...] | None,
    frequency: float,
    speed: float,
    low: float,
    mid: float,
    high: float,
    accent: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_juggle_into(
    destination: npt.NDArray[np.uint8],
    positions: tuple[float, ...],
    radius: float,
    frame: int,
    hue_rate: float,
    brightness: float,
    audio_mode: bool,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_confetti_into(
    destination: npt.NDArray[np.uint8],
    intensities: npt.NDArray[np.float32],
    bands: tuple[float, ...] | None,
    frame: int,
    audio_mode: bool,
    fade: float,
    spread: float,
    trigger_rate: float,
    density: int,
    addition: float,
    accent: float,
    high: float,
    bloom: float,
    hue_shift: int,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_glow_rush_into(
    destination: npt.NDArray[np.uint8],
    position: float,
    width: float,
    hue: int,
    pulse: float,
    glow_width: float,
    brightness: float,
    audio_mode: bool,
    main_light: int,
    glow_alpha: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_dual_comet_into(
    destination: npt.NDArray[np.uint8],
    phase: float,
    width: float,
    alpha: float,
    separation: float,
    hue_a: int,
    hue_b: int,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_shockwave_into(
    destination: npt.NDArray[np.uint8],
    radius: float,
    width: float,
    accent: float,
    hue: int,
    mid: float,
    brightness: float,
    audio_mode: bool,
    bands: tuple[float, ...] | None,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_aurora_into(
    destination: npt.NDArray[np.uint8],
    frame: int,
    audio_mode: bool,
    bands: tuple[float, ...] | None,
    frequency: float,
    speed: float,
    low: float,
    mid: float,
    high: float,
    beat_glow: float,
    brightness: float,
    hue_offset: int,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_comet_storm_into(
    destination: npt.NDArray[np.uint8],
    geometry: npt.NDArray[np.float64],
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...

def render_twinkle_into(
    destination: npt.NDArray[np.uint8],
    levels: npt.NDArray[np.float32],
    hues: npt.NDArray[np.int16],
    frame: int,
    radius: int,
    bloom: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> None: ...
