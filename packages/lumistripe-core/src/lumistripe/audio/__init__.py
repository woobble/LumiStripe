"""Stable audio primitives with explicit capture, DSP, and device modules."""

# Keep ``importlib`` available as a patch point for applications and tests that
# provide a sounddevice-compatible implementation.
import importlib as importlib

from .calibration import calibrate_audio_input, recommend_audio_calibration
from .capture import AudioInput, BoundedAudioBatchBuffer
from .config import (
    AUDIO_FRESH_SECONDS,
    BAND_LIMITS_HZ,
    DEFAULT_SAMPLE_RATE,
    FFT_HOP_SIZE,
    FFT_SIZE,
    AudioAnalysis,
    AudioConfig,
    AudioNormalization,
    AudioSmoothing,
)
from .devices import list_input_device_details, list_input_devices
from .dsp import (
    AudioState,
    NativeFeaturesResult,
    NativeFrameResult,
    NativeStatsResult,
    _audio,
    features_from_frame,
)
from .hardware_gain import HardwareGainController, HardwareGainStatus
from .protocols import (
    AudioBatchBuffer,
    AudioProcessor,
    AudioSource,
    snapshot_from_source,
)
from .types import (
    NUM_BANDS,
    AudioCalibrationResult,
    AudioFeatures,
    AudioFrame,
    AudioInputDevice,
    AudioInputHealth,
    AudioProcessorStats,
    AudioSnapshot,
    BandTuple,
    MusicFeatures,
)

__all__ = [
    "AUDIO_FRESH_SECONDS",
    "BAND_LIMITS_HZ",
    "DEFAULT_SAMPLE_RATE",
    "FFT_HOP_SIZE",
    "FFT_SIZE",
    "NUM_BANDS",
    "AudioAnalysis",
    "AudioBatchBuffer",
    "AudioCalibrationResult",
    "AudioConfig",
    "AudioFeatures",
    "AudioFrame",
    "AudioInput",
    "AudioInputDevice",
    "AudioInputHealth",
    "AudioNormalization",
    "AudioProcessor",
    "AudioProcessorStats",
    "AudioSmoothing",
    "AudioSnapshot",
    "AudioSource",
    "AudioState",
    "BandTuple",
    "BoundedAudioBatchBuffer",
    "HardwareGainController",
    "HardwareGainStatus",
    "MusicFeatures",
    "NativeFeaturesResult",
    "NativeFrameResult",
    "NativeStatsResult",
    "_audio",
    "calibrate_audio_input",
    "features_from_frame",
    "list_input_device_details",
    "list_input_devices",
    "recommend_audio_calibration",
    "snapshot_from_source",
]
