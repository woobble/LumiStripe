"""Audio capture boundary.

Keeping capture behind this module makes it possible for applications to use
the DSP value objects without importing sounddevice until capture is needed.
"""

from . import AudioInput

__all__ = ["AudioInput"]
