//! Real-time audio processing and music-structure analysis for the Rust engine.

mod calibration;
mod capture;
mod config;
mod dsp;
mod structure;

pub use calibration::{AudioCalibrationRecommendation, recommend_audio_calibration};
pub use capture::{
    AudioCapture, AudioCaptureError, AudioCaptureStats, CapturedAudioBatch, InputAudioDevice,
    enumerate_input_devices, monotonic_time_ns,
};
pub use config::{AUDIO_HOP_SIZE, AUDIO_SAMPLE_RATE, AUDIO_WINDOW_SIZE, AudioConfig};
pub use dsp::{
    AudioBeatEvent, AudioFrame, AudioProcessor, AudioProcessorError, AudioProcessorStats,
    MAX_AUDIO_BEAT_EVENTS, MusicFeatures,
};
pub use structure::{
    MusicStructurePhase, MusicStructureState, MusicStructureTracker, StructureUpdate,
};
