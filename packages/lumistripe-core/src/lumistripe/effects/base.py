from ..animation.base import Animation
from ..audio.types import AudioFrame
from ..buffers import PixelBuffer


class Effect(Animation):
    """A reusable animation intended to render as a transparent overlay."""

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        """Render with an optional native kernel, returning whether it handled the frame."""

        return False
