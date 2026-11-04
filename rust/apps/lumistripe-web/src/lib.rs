//! Native Axum control plane for LumiStripe.

#![recursion_limit = "256"]

#[allow(dead_code)]
mod api_schema;
mod app;
mod audio;
mod auth;
mod bluetooth;
#[cfg(all(feature = "bluez", target_os = "linux"))]
mod bluetooth_bluer;
mod bluetooth_models;
mod bluetooth_process;
mod bluetooth_providers;
mod config;
mod control;
mod error;
mod network;
#[cfg(all(feature = "pipewire", target_os = "linux"))]
mod pipewire_bindings;
mod preview;
mod settings;
mod spotify;
mod state;
mod streams;
mod system_stats;
mod updates;

pub use app::{ApiDoc, WebApplication};
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
