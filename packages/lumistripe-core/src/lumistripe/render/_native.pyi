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
