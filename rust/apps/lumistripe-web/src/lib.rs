//! Native Axum control plane for LumiStripe.

#![recursion_limit = "256"]

mod app;
mod audio;
mod auth;
mod config;
mod control;
mod error;
mod preview;
mod settings;
mod state;
mod streams;

pub use app::WebApplication;
pub use auth::PairingAuth;
pub use config::{AppConfig, CliOptions, EngineOutputOptions, OutputBackendArg};
pub use control::ControlPlane;
pub use error::ApiError;
pub use settings::{
    AudioTuningProfile, ColorCorrection, DashboardSettings, MusicIdleMode, MusicIdleSettings,
    PersistedPlaybackMode, SavedAnimationSequence, SavedAnimationSequenceStep, SettingsError,
    SettingsStore, StripeBackend, StripeLayout, StripeOutputSettings, StripeTopologySettings,
    SwitchQuantization, TransitionEffect, TransitionSettings, ZonePattern,
};
pub use state::{ControlModel, PlaybackMode};
