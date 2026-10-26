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
