from collections.abc import Buffer

from numpy import uint8
from numpy.typing import NDArray

def frame_size(pixel_count: int, /) -> int: ...
def encode_into(pixels: NDArray[uint8], destination: Buffer, /) -> None: ...
