//! Versioned settings imported from the Python dashboard settings format.

use lumistripe_engine_animation_definitions::catalog;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use tempfile::NamedTempFile;
use thiserror::Error;

pub const SETTINGS_VERSION: u64 = 12;
pub const MAX_SAVED_ANIMATION_SEQUENCES: usize = 20;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PersistedPlaybackMode {
    Solid,
    #[default]
    Static,
    Cycling,
    Dynamic,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum MusicIdleMode {
    #[default]
    Off,
    Solid,
    Animation,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum StripeLayout {
    #[default]
    Mirrored,
    Continuous,
    Independent,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum StripeBackend {
    #[default]
    Spi,
    Gpio,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum ZonePattern {
    #[default]
    Repeat,
    Alternate,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum SwitchQuantization {
    #[default]
    Off,
    Beat,
    Bar,
    Phrase,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum TransitionEffect {
    #[default]
    Crossfade,
    FadeBlack,
    Wipe,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct DashboardSettings {
    pub application_name: String,
    pub spotify_device_name: String,
    pub music_idle: BTreeMap<String, MusicIdleSettings>,
    #[serde(rename = "color_correction")]
    pub color_corrections: BTreeMap<String, ColorCorrection>,
    pub selected_audio_device: Option<String>,
    pub audio_profiles: BTreeMap<String, AudioTuningProfile>,
    pub audio_sync_offsets_ms: BTreeMap<String, f64>,
    #[serde(rename = "stripes")]
    pub stripe_topology: Option<StripeTopologySettings>,
    pub restore_last_state: bool,
    pub switch_quantization: SwitchQuantization,
    pub bars_per_phrase: u8,
    pub animation_sequences: Vec<SavedAnimationSequence>,
    pub active_sequence_id: Option<String>,
    pub remembered_playback: StartupPlaybackSettings,
    pub transition_settings: TransitionSettings,
    pub animation_settings: BTreeMap<String, BTreeMap<String, Map<String, Value>>>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for DashboardSettings {
    fn default() -> Self {
        Self {
            application_name: "LumiStripe".to_owned(),
            spotify_device_name: "LumiStripe".to_owned(),
            music_idle: BTreeMap::new(),
            color_corrections: BTreeMap::from([
                ("primary".to_owned(), ColorCorrection::default()),
                ("secondary".to_owned(), ColorCorrection::default()),
            ]),
            selected_audio_device: None,
            audio_profiles: BTreeMap::new(),
            audio_sync_offsets_ms: BTreeMap::new(),
            stripe_topology: None,
            restore_last_state: false,
            switch_quantization: SwitchQuantization::Off,
            bars_per_phrase: 8,
            animation_sequences: Vec::new(),
            active_sequence_id: None,
            remembered_playback: StartupPlaybackSettings::default(),
            transition_settings: TransitionSettings::default(),
            animation_settings: BTreeMap::new(),
            extra: BTreeMap::new(),
        }
    }
}

impl DashboardSettings {
    fn decode(payload: &Value, version: u64, path: &Path) -> Result<Self, SettingsError> {
        let mut effective = payload.clone();
        let object = effective
            .as_object_mut()
            .ok_or_else(|| SettingsError::Invalid {
                path: path.to_path_buf(),
                message: "settings root must be a JSON object".to_owned(),
            })?;
        object.remove("version");
        for (introduced, fields) in [
            (2, &["selected_audio_device", "audio_profiles"][..]),
            (3, &["stripes"][..]),
            (4, &["restore_last_state", "remembered_playback"][..]),
            (6, &["audio_sync_offsets_ms"][..]),
            (7, &["switch_quantization", "bars_per_phrase"][..]),
            (8, &["animation_sequences", "active_sequence_id"][..]),
            (9, &["transition_settings"][..]),
            (11, &["animation_settings"][..]),
        ] {
            if version < introduced {
                for field in fields {
                    object.remove(*field);
                }
            }
        }

        let mut settings: Self =
            serde_json::from_value(effective).map_err(|error| SettingsError::Invalid {
                path: path.to_path_buf(),
                message: format!("settings contain an invalid value: {error}"),
            })?;
        settings.validate(path)?;
        Ok(settings)
    }

    fn validate(&mut self, path: &Path) -> Result<(), SettingsError> {
        validate_name(&self.application_name, "application_name", path)?;
        validate_name(&self.spotify_device_name, "spotify_device_name", path)?;

        if self.selected_audio_device.as_deref() == Some("") {
            self.selected_audio_device = None;
        }
        for (profile, correction) in &self.color_corrections {
            if profile.trim().is_empty() {
                return invalid(path, "color_correction profile names must not be empty");
            }
            let _ = correction;
        }
        for (device, profile) in &self.audio_profiles {
            if device.is_empty() {
                return invalid(path, "audio profile names must not be empty");
            }
            profile.validate(path, device)?;
        }
        for (route, offset) in &self.audio_sync_offsets_ms {
            if route.is_empty() {
                return invalid(path, "audio sync route keys must not be empty");
            }
            if !offset.is_finite() || !(-500.0..=500.0).contains(offset) {
                return invalid(
                    path,
                    format!("audio sync offset for {route:?} must be between -500 and 500 ms"),
                );
            }
        }
        if let Some(topology) = &self.stripe_topology {
            topology.validate_at(path)?;
        }
        if !matches!(self.bars_per_phrase, 4 | 8 | 16) {
            return invalid(path, "bars_per_phrase must be 4, 8, or 16");
        }
        if self.animation_sequences.len() > MAX_SAVED_ANIMATION_SEQUENCES {
            return invalid(
                path,
                format!("at most {MAX_SAVED_ANIMATION_SEQUENCES} animation sequences may be saved"),
            );
        }
        let mut sequence_ids = BTreeSet::new();
        for sequence in &mut self.animation_sequences {
            sequence.id = sequence.id.trim().to_owned();
            if sequence.id.is_empty() || sequence.id.chars().count() > 64 {
                return invalid(
                    path,
                    "animation sequence IDs must contain 1 to 64 characters",
                );
            }
            if !sequence_ids.insert(sequence.id.as_str()) {
                return invalid(
                    path,
                    format!("duplicate animation sequence ID {:?}", sequence.id),
                );
            }
            if sequence.name.trim().is_empty() || sequence.name.trim().chars().count() > 80 {
                return invalid(
                    path,
                    "animation sequence names must contain 1 to 80 characters",
                );
            }
            if !(1..=32).contains(&sequence.steps.len()) {
                return invalid(
                    path,
                    "animation sequence steps must contain between 1 and 32 items",
                );
            }
            for (index, step) in sequence.steps.iter().enumerate() {
                if step.animation_name.trim().is_empty()
                    || step.animation_name.trim().chars().count() > 100
                {
                    return invalid(
                        path,
                        format!(
                            "sequence step {} animation_name must contain 1 to 100 characters",
                            index + 1
                        ),
                    );
                }
                if !matches!(step.duration_bars, 1 | 2 | 4 | 8) {
                    return invalid(
                        path,
                        format!(
                            "sequence step {} duration_bars must be 1, 2, 4, or 8",
                            index + 1
                        ),
                    );
                }
                let definition = catalog().animation(&step.animation_name).ok_or_else(|| {
                    SettingsError::Invalid {
                        path: path.to_path_buf(),
                        message: format!(
                            "sequence step animation {:?} is unknown",
                            step.animation_name
                        ),
                    }
                })?;
                let dynamic = &definition.dynamic_metadata;
                let eligible = definition.automatic
                    && definition.role == "base"
                    && dynamic.get("role").and_then(Value::as_str) == Some("base")
                    && dynamic.get("dynamic_safe").and_then(Value::as_bool) == Some(true)
                    && definition.effect_metadata.is_none();
                if !eligible {
                    return invalid(
                        path,
                        format!(
                            "animation {:?} is not eligible for a Dynamic sequence",
                            step.animation_name
                        ),
                    );
                }
            }
        }
        if self
            .active_sequence_id
            .as_deref()
            .is_some_and(|id| !sequence_ids.contains(id.trim()))
        {
            return invalid(path, "active_sequence_id must identify a saved sequence");
        }
        if let Some(active_id) = &mut self.active_sequence_id {
            *active_id = active_id.trim().to_owned();
        }
        for (scope, animations) in &mut self.animation_settings {
            if scope.trim().is_empty() {
                return invalid(path, "animation settings scopes must not be empty");
            }
            let mut normalized = BTreeMap::new();
            for (name, values) in animations.iter() {
                if name.is_empty() {
                    return invalid(
                        path,
                        "animation names in animation_settings must not be empty",
                    );
                }
                let definition = catalog()
                    .animation(name)
                    .filter(|definition| !definition.settings.is_empty())
                    .ok_or_else(|| SettingsError::Invalid {
                        path: path.to_path_buf(),
                        message: format!("animation {name:?} has no configurable settings"),
                    })?;
                let values = definition
                    .normalize_settings(values, true)
                    .map_err(|error| SettingsError::Invalid {
                        path: path.to_path_buf(),
                        message: format!("animation settings for {scope:?}/{name:?}: {error}"),
                    })?;
                normalized.insert(name.clone(), values.into_iter().collect());
            }
            *animations = normalized;
        }
        for (scope, idle) in &self.music_idle {
            if scope.trim().is_empty() {
                return invalid(path, "music idle scopes must not be empty");
            }
            idle.validate(path)?;
        }
        self.remembered_playback.validate(path)?;
        self.transition_settings.validate(path)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(default)]
#[schema(as = MusicIdleState)]
pub struct MusicIdleSettings {
    #[schema(schema_with = crate::api_schema::music_idle_mode_schema)]
    pub mode: MusicIdleMode,
    #[schema(default = "#2060FF", pattern = "^#[0-9A-Fa-f]{6}$")]
    pub color: String,
    #[schema(default = "")]
    pub animation: String,
    #[schema(minimum = 0.0, maximum = 1.0, default = 1.0)]
    pub brightness: f64,
    #[serde(flatten)]
    #[schema(ignore)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for MusicIdleSettings {
    fn default() -> Self {
        Self {
            mode: MusicIdleMode::Off,
            color: "#2060FF".to_owned(),
            animation: String::new(),
            brightness: 1.0,
            extra: BTreeMap::new(),
        }
    }
}

impl MusicIdleSettings {
    fn validate(&self, path: &Path) -> Result<(), SettingsError> {
        validate_color(&self.color, "music idle color", path)?;
        validate_unit(self.brightness, "music idle brightness", path)?;
        if !self.animation.is_empty() && catalog().animation(&self.animation).is_none() {
            return invalid(
                path,
                format!("unknown music idle animation {:?}", self.animation),
            );
        }
        if self.mode == MusicIdleMode::Animation && self.animation.is_empty() {
            return invalid(
                path,
                "music idle animation is required when mode is animation",
            );
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default)]
pub struct ColorCorrection {
    pub red: u8,
    pub green: u8,
    pub blue: u8,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for ColorCorrection {
    fn default() -> Self {
        Self {
            red: 255,
            green: 255,
            blue: 255,
            extra: BTreeMap::new(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(default)]
#[schema(as = AudioTuningValues)]
pub struct AudioTuningProfile {
    #[schema(minimum = 0.1, maximum = 0.8, default = 0.36)]
    pub target_level: f64,
    #[schema(minimum = 0.0, maximum = 1.0)]
    pub hardware_gain_target: Option<f64>,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.015)]
    pub noise_floor: f64,
    #[schema(minimum = 0.0, maximum = 0.5, default = 0.003)]
    pub input_noise_floor: f64,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.65)]
    pub dynamic_response: f64,
    #[schema(minimum = 0.01, maximum = 1.0, default = 0.45)]
    pub rms_attack: f64,
    #[schema(minimum = 0.01, maximum = 1.0, default = 0.12)]
    pub rms_release: f64,
    #[schema(minimum = 0.01, maximum = 1.0, default = 0.4)]
    pub band_attack: f64,
    #[schema(minimum = 0.01, maximum = 1.0, default = 0.1)]
    pub band_release: f64,
    #[schema(minimum = 0.01, maximum = 1.0, default = 0.18)]
    pub beat_release: f64,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.03)]
    pub energy_threshold: f64,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.025)]
    pub onset_threshold: f64,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.05)]
    pub beat_density_threshold: f64,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.08)]
    pub brightness_threshold: f64,
    #[schema(minimum = 0.0, maximum = 1.0, default = 0.35)]
    pub spectral_balance_ratio: f64,
    #[serde(flatten)]
    #[schema(ignore)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for AudioTuningProfile {
    fn default() -> Self {
        Self {
            target_level: 0.36,
            hardware_gain_target: None,
            noise_floor: 0.015,
            input_noise_floor: 0.003,
            dynamic_response: 0.65,
            rms_attack: 0.45,
            rms_release: 0.12,
            band_attack: 0.4,
            band_release: 0.1,
            beat_release: 0.18,
            energy_threshold: 0.03,
            onset_threshold: 0.025,
            beat_density_threshold: 0.05,
            brightness_threshold: 0.08,
            spectral_balance_ratio: 0.35,
            extra: BTreeMap::new(),
        }
    }
}

impl AudioTuningProfile {
    fn validate(&self, path: &Path, name: &str) -> Result<(), SettingsError> {
        let prefix = format!("audio profile {name:?}");
        validate_range(
            self.target_level,
            0.1,
            0.8,
            &format!("{prefix}.target_level"),
            path,
        )?;
        if let Some(value) = self.hardware_gain_target {
            validate_range(
                value,
                0.0,
                1.0,
                &format!("{prefix}.hardware_gain_target"),
                path,
            )?;
        }
        for (field, value) in [
            ("noise_floor", self.noise_floor),
            ("dynamic_response", self.dynamic_response),
            ("energy_threshold", self.energy_threshold),
            ("onset_threshold", self.onset_threshold),
            ("beat_density_threshold", self.beat_density_threshold),
            ("brightness_threshold", self.brightness_threshold),
            ("spectral_balance_ratio", self.spectral_balance_ratio),
        ] {
            validate_range(value, 0.0, 1.0, &format!("{prefix}.{field}"), path)?;
        }
        validate_range(
            self.input_noise_floor,
            0.0,
            0.5,
            &format!("{prefix}.input_noise_floor"),
            path,
        )?;
        for (field, value) in [
            ("rms_attack", self.rms_attack),
            ("rms_release", self.rms_release),
            ("band_attack", self.band_attack),
            ("band_release", self.band_release),
            ("beat_release", self.beat_release),
        ] {
            validate_range(value, 0.01, 1.0, &format!("{prefix}.{field}"), path)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct StripeTopologySettings {
    pub layout: StripeLayout,
    pub outputs: Vec<StripeOutputSettings>,
    pub power_budget_enabled: bool,
    pub power_budget_watts: Option<f64>,
    pub shared_supply_enabled: bool,
    pub supply_capacity_watts: f64,
    pub pi_reserve_watts: f64,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for StripeTopologySettings {
    fn default() -> Self {
        Self {
            layout: StripeLayout::Mirrored,
            outputs: Vec::new(),
            power_budget_enabled: false,
            power_budget_watts: None,
            shared_supply_enabled: false,
            supply_capacity_watts: 200.0,
            pi_reserve_watts: 15.0,
            extra: BTreeMap::new(),
        }
    }
}

impl StripeTopologySettings {
    pub fn validate(&self) -> Result<(), String> {
        self.validate_at(Path::new("<stripe topology>"))
            .map_err(|error| match error {
                SettingsError::Invalid { message, .. } => message,
                error => error.to_string(),
            })
    }

    fn validate_at(&self, path: &Path) -> Result<(), SettingsError> {
        if self.outputs.len() > 2 {
            return invalid(path, "at most two stripes can be configured");
        }
        validate_positive(self.supply_capacity_watts, "supply_capacity_watts", path)?;
        if !self.pi_reserve_watts.is_finite() || self.pi_reserve_watts < 0.0 {
            return invalid(path, "pi_reserve_watts must be finite and not negative");
        }
        if let Some(watts) = self.power_budget_watts {
            validate_positive(watts, "power_budget_watts", path)?;
        }
        if self.power_budget_enabled && self.power_budget_watts.is_none() {
            return invalid(path, "an enabled power budget requires a watt limit");
        }
        let mut ids = BTreeSet::new();
        let mut spi_devices = BTreeSet::new();
        let mut gpio_pins = BTreeSet::new();
        for (index, output) in self.outputs.iter().enumerate() {
            output.validate(path, index)?;
            if !ids.insert(output.id.as_str()) {
                return invalid(path, "stripe ids must be unique");
            }
            match output.backend {
                StripeBackend::Spi if !spi_devices.insert(output.spi_device.as_str()) => {
                    return invalid(path, "SPI devices must be unique");
                }
                StripeBackend::Gpio => {
                    if !gpio_pins.insert((output.chip.as_str(), output.data_pin))
                        || !gpio_pins.insert((output.chip.as_str(), output.clock_pin))
                    {
                        return invalid(path, "GPIO pins must be unique");
                    }
                }
                StripeBackend::Spi => {}
            }
        }
        Ok(())
    }

    pub fn available_led_watts(&self) -> f64 {
        (self.supply_capacity_watts - self.pi_reserve_watts).max(0.0)
    }

    pub fn effective_power_budget_watts(&self) -> Option<f64> {
        let mut limits = Vec::with_capacity(2);
        if self.power_budget_enabled {
            if let Some(watts) = self.power_budget_watts {
                limits.push(watts);
            }
        }
        if self.shared_supply_enabled {
            limits.push(self.available_led_watts());
        }
        limits.into_iter().min_by(f64::total_cmp)
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(default)]
#[schema(as = StripeOutputConfig)]
pub struct StripeOutputSettings {
    #[schema(required = true, min_length = 1, max_length = 64)]
    pub id: String,
    #[schema(required = true, min_length = 1, max_length = 40)]
    pub name: String,
    #[schema(required = true, minimum = 1, maximum = 4096)]
    pub pixels: usize,
    #[schema(default = "spi")]
    pub backend: StripeBackend,
    #[schema(default = false)]
    pub reversed: bool,
    #[schema(minimum = 1, maximum = 4096, default = 1)]
    pub zone_count: usize,
    #[schema(default = "repeat")]
    pub zone_pattern: ZonePattern,
    #[schema(min_length = 1, default = "/dev/spidev0.0")]
    pub spi_device: String,
    #[schema(minimum = 1, maximum = 32000000, default = 1000000)]
    pub spi_speed_hz: u32,
    #[schema(min_length = 1, default = "/dev/gpiochip0")]
    pub chip: String,
    #[schema(minimum = 0, default = 10)]
    pub data_pin: u32,
    #[schema(minimum = 0, default = 11)]
    pub clock_pin: u32,
    #[schema(minimum = 0.0, default = 5.0)]
    pub voltage_v: f64,
    #[schema(minimum = 0.0, default = 0.06)]
    pub full_white_current_a: f64,
    #[serde(default)]
    pub power_limit_watts: Option<f64>,
    #[serde(flatten)]
    #[schema(ignore)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for StripeOutputSettings {
    fn default() -> Self {
        Self {
            id: "default".to_owned(),
            name: "Stripe".to_owned(),
            pixels: 80,
            backend: StripeBackend::Spi,
            reversed: false,
            zone_count: 1,
            zone_pattern: ZonePattern::Repeat,
            spi_device: "/dev/spidev0.0".to_owned(),
            spi_speed_hz: 1_000_000,
            chip: "/dev/gpiochip0".to_owned(),
            data_pin: 10,
            clock_pin: 11,
            voltage_v: 5.0,
            full_white_current_a: 0.06,
            power_limit_watts: None,
            extra: BTreeMap::new(),
        }
    }
}

impl StripeOutputSettings {
    fn validate(&self, path: &Path, index: usize) -> Result<(), SettingsError> {
        let label = format!("stripe {}", index + 1);
        if self.id.trim().is_empty() || self.name.trim().is_empty() {
            return invalid(path, format!("{label} ID and name must not be empty"));
        }
        if self.id.chars().count() > 64 {
            return invalid(
                path,
                format!("{label} ID must contain at most 64 characters"),
            );
        }
        if self.name.chars().count() > 40 {
            return invalid(
                path,
                format!("{label} name must contain at most 40 characters"),
            );
        }
        if !(1..=4096).contains(&self.pixels) {
            return invalid(path, format!("{label} pixels must be between 1 and 4096"));
        }
        if !(1..=self.pixels).contains(&self.zone_count) {
            return invalid(
                path,
                format!("{label} zone_count must be between one and its pixel count"),
            );
        }
        if !(1..=32_000_000).contains(&self.spi_speed_hz) {
            return invalid(
                path,
                format!("{label} SPI speed must be between 1 and 32000000"),
            );
        }
        if self.data_pin == self.clock_pin {
            return invalid(
                path,
                format!("{label} GPIO data and clock pins must differ"),
            );
        }
        validate_positive(self.voltage_v, &format!("{label} voltage_v"), path)?;
        validate_positive(
            self.full_white_current_a,
            &format!("{label} full_white_current_a"),
            path,
        )?;
        if let Some(watts) = self.power_limit_watts {
            validate_positive(watts, &format!("{label} power_limit_watts"), path)?;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct StartupPlaybackSettings {
    pub mode: PersistedPlaybackMode,
    pub solid_color: String,
    pub animation: String,
    pub brightness: f64,
    pub blackout: bool,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for StartupPlaybackSettings {
    fn default() -> Self {
        Self {
            mode: PersistedPlaybackMode::Static,
            solid_color: "#7C3AED".to_owned(),
            animation: String::new(),
            brightness: 1.0,
            blackout: false,
            extra: BTreeMap::new(),
        }
    }
}

impl StartupPlaybackSettings {
    fn validate(&self, path: &Path) -> Result<(), SettingsError> {
        validate_color(&self.solid_color, "remembered_playback.solid_color", path)?;
        validate_unit(self.brightness, "remembered_playback.brightness", path)
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(default)]
#[schema(as = TransitionSettingsResponse)]
pub struct TransitionSettings {
    #[schema(default = true)]
    pub enabled: bool,
    #[schema(minimum = 100, maximum = 1500, default = 300)]
    pub duration_ms: u16,
    #[schema(schema_with = crate::api_schema::transition_effect_schema)]
    pub effect: TransitionEffect,
    #[serde(flatten)]
    #[schema(ignore)]
    pub extra: BTreeMap<String, Value>,
}

impl Default for TransitionSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            duration_ms: 300,
            effect: TransitionEffect::Crossfade,
            extra: BTreeMap::new(),
        }
    }
}

impl TransitionSettings {
    fn validate(&self, path: &Path) -> Result<(), SettingsError> {
        if !(100..=1500).contains(&self.duration_ms) {
            return invalid(path, "transition duration must be between 100 and 1500 ms");
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
#[schema(as = AnimationSequenceContract)]
pub struct SavedAnimationSequence {
    #[schema(min_length = 1, max_length = 64)]
    pub id: String,
    #[schema(min_length = 1, max_length = 80)]
    pub name: String,
    pub repeat: bool,
    #[schema(min_items = 1, max_items = 32)]
    pub steps: Vec<SavedAnimationSequenceStep>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, utoipa::ToSchema)]
#[serde(deny_unknown_fields)]
#[schema(as = AnimationSequenceStepContract)]
pub struct SavedAnimationSequenceStep {
    #[schema(min_length = 1, max_length = 100)]
    pub animation_name: String,
    #[schema(schema_with = crate::api_schema::duration_sequence_bars_schema)]
    pub duration_bars: u8,
}

#[derive(Debug, Error)]
pub enum SettingsError {
    #[error("could not read settings at {path}: {source}")]
    Read {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("settings at {path} are invalid JSON: {source}")]
    Json {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("settings at {path} are invalid: {message}")]
    Invalid { path: PathBuf, message: String },
    #[error("could not persist settings at {path}: {source}")]
    Write {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("could not encode settings: {0}")]
    Encode(#[from] serde_json::Error),
}

#[derive(Debug)]
pub struct SettingsStore {
    path: PathBuf,
    payload: Value,
    settings: DashboardSettings,
}

impl SettingsStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SettingsError> {
        let path = path.as_ref().to_path_buf();
        let (payload, version) = match fs::read(&path) {
            Ok(bytes) => {
                let payload: Value =
                    serde_json::from_slice(&bytes).map_err(|source| SettingsError::Json {
                        path: path.clone(),
                        source,
                    })?;
                let object = payload.as_object().ok_or_else(|| SettingsError::Invalid {
                    path: path.clone(),
                    message: "settings root must be a JSON object".to_owned(),
                })?;
                let version = object
                    .get("version")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| SettingsError::Invalid {
                        path: path.clone(),
                        message: format!(
                            "version must be an integer from 1 through {SETTINGS_VERSION}"
                        ),
                    })?;
                if !(1..=SETTINGS_VERSION).contains(&version) {
                    return invalid(
                        &path,
                        format!(
                            "unsupported version {version}; supported versions are 1 through {SETTINGS_VERSION}"
                        ),
                    );
                }
                (payload, version)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                (json!({"version": SETTINGS_VERSION}), SETTINGS_VERSION)
            }
            Err(source) => {
                return Err(SettingsError::Read {
                    path: path.clone(),
                    source,
                });
            }
        };
        let settings = DashboardSettings::decode(&payload, version, &path)?;
        Ok(Self {
            path,
            payload,
            settings,
        })
    }

    pub fn settings(&self) -> &DashboardSettings {
        &self.settings
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Replace one versioned top-level category while retaining every other
    /// field from the source file. The complete candidate is validated before
    /// the durable file or in-memory snapshot changes.
    pub fn replace_category(&mut self, category: &str, value: Value) -> Result<(), SettingsError> {
        self.replace_categories([(category.to_owned(), value)])
    }

    pub fn replace_categories(
        &mut self,
        updates: impl IntoIterator<Item = (String, Value)>,
    ) -> Result<(), SettingsError> {
        let (candidate_payload, candidate) = self.candidate_with_categories(updates)?;
        write_atomic(&self.path, &candidate_payload)?;
        self.payload = candidate_payload;
        self.settings = candidate;
        Ok(())
    }

    pub fn validate_categories(
        &self,
        updates: impl IntoIterator<Item = (String, Value)>,
    ) -> Result<(), SettingsError> {
        self.candidate_with_categories(updates).map(|_| ())
    }

    fn candidate_with_categories(
        &self,
        updates: impl IntoIterator<Item = (String, Value)>,
    ) -> Result<(Value, DashboardSettings), SettingsError> {
        const CATEGORIES: &[&str] = &[
            "application_name",
            "spotify_device_name",
            "music_idle",
            "color_correction",
            "selected_audio_device",
            "audio_profiles",
            "audio_sync_offsets_ms",
            "stripes",
            "restore_last_state",
            "switch_quantization",
            "bars_per_phrase",
            "animation_sequences",
            "active_sequence_id",
            "remembered_playback",
            "transition_settings",
            "animation_settings",
        ];
        let mut candidate_payload = self.payload.clone();
        let object = candidate_payload
            .as_object_mut()
            .ok_or_else(|| SettingsError::Invalid {
                path: self.path.clone(),
                message: "settings root must be a JSON object".to_owned(),
            })?;
        for (category, value) in updates {
            if !CATEGORIES.contains(&category.as_str()) {
                return invalid(
                    &self.path,
                    format!("{category:?} is not a writable settings category"),
                );
            }
            object.insert(category, value);
        }
        object.insert("version".to_owned(), json!(SETTINGS_VERSION));
        let candidate =
            DashboardSettings::decode(&candidate_payload, SETTINGS_VERSION, &self.path)?;
        Ok((candidate_payload, candidate))
    }
}

fn write_atomic(path: &Path, payload: &Value) -> Result<(), SettingsError> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|source| SettingsError::Write {
        path: path.to_path_buf(),
        source,
    })?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(|source| SettingsError::Write {
        path: path.to_path_buf(),
        source,
    })?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|source| SettingsError::Write {
                path: path.to_path_buf(),
                source,
            })?;
    }
    let encoded = serde_json::to_vec_pretty(payload)?;
    temporary
        .write_all(&encoded)
        .and_then(|()| temporary.write_all(b"\n"))
        .and_then(|()| temporary.flush())
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|source| SettingsError::Write {
            path: path.to_path_buf(),
            source,
        })?;
    temporary
        .persist(path)
        .map_err(|error| SettingsError::Write {
            path: path.to_path_buf(),
            source: error.error,
        })?;
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(|source| SettingsError::Write {
            path: path.to_path_buf(),
            source,
        })?;
    Ok(())
}

fn validate_name(value: &str, name: &str, path: &Path) -> Result<(), SettingsError> {
    let length = value.trim().chars().count();
    if length == 0 || length > 64 || value.chars().any(|character| character.is_control()) {
        return invalid(
            path,
            format!(
                "{name} must be a non-empty name of at most 64 characters without control characters"
            ),
        );
    }
    Ok(())
}

fn validate_color(value: &str, name: &str, path: &Path) -> Result<(), SettingsError> {
    if value.len() != 7
        || !value.starts_with('#')
        || !value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
    {
        return invalid(
            path,
            format!("{name} must be a six-digit hex color such as #2060FF"),
        );
    }
    Ok(())
}

fn validate_unit(value: f64, name: &str, path: &Path) -> Result<(), SettingsError> {
    validate_range(value, 0.0, 1.0, name, path)
}

fn validate_positive(value: f64, name: &str, path: &Path) -> Result<(), SettingsError> {
    if !value.is_finite() || value <= 0.0 {
        return invalid(path, format!("{name} must be finite and greater than zero"));
    }
    Ok(())
}

fn validate_range(
    value: f64,
    minimum: f64,
    maximum: f64,
    name: &str,
    path: &Path,
) -> Result<(), SettingsError> {
    if !value.is_finite() || !(minimum..=maximum).contains(&value) {
        return invalid(
            path,
            format!("{name} must be between {minimum} and {maximum}"),
        );
    }
    Ok(())
}

fn invalid<T>(path: &Path, message: impl Into<String>) -> Result<T, SettingsError> {
    Err(SettingsError::Invalid {
        path: path.to_path_buf(),
        message: message.into(),
    })
}

#[cfg(test)]
mod tests {
    use super::{
        DashboardSettings, SETTINGS_VERSION, SettingsError, SettingsStore, StripeLayout,
        TransitionEffect, write_atomic,
    };
    use serde_json::json;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn loads_legacy_versions_with_python_compatible_defaults() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("settings.json");
        fs::write(
            &path,
            r#"{"version":4,"application_name":"Studio","color_correction":{"primary":{"red":240,"green":250,"blue":255}},"restore_last_state":true,"remembered_playback":{"mode":"dynamic","brightness":0.7}}"#,
        )
        .unwrap();

        let store = SettingsStore::open(&path).unwrap();
        assert_eq!(store.settings().application_name, "Studio");
        assert!(store.settings().restore_last_state);
        assert_eq!(store.settings().bars_per_phrase, 8);
        assert_eq!(store.settings().transition_settings.duration_ms, 300);
        assert_eq!(store.settings().remembered_playback.brightness, 0.7);
        assert_eq!(store.settings().color_corrections["primary"].red, 240);
    }

    #[test]
    fn loads_complete_python_settings_and_preserves_unrelated_fields_on_update() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("settings.json");
        let mut payload = json!({
            "version": SETTINGS_VERSION,
            "application_name": "Studio",
            "spotify_device_name": "LumiStripe",
            "music_idle": {"shared": {"mode":"solid", "color":"#123456", "animation":"", "brightness":0.4}},
            "color_correction": {"primary":{"red":120,"green":240,"blue":255}},
            "selected_audio_device": "USB microphone",
            "audio_profiles": {"USB microphone":{"target_level":0.5,"input_noise_floor":0.02}},
            "audio_sync_offsets_ms": {"bluetooth":-23.5},
            "stripes": {"layout":"continuous", "outputs":[{"id":"left","name":"Left","pixels":80,"backend":"spi","spi_device":"/dev/spidev0.0","zone_count":2,"zone_pattern":"alternate"}], "power_budget_enabled":true,"power_budget_watts":90.0},
            "restore_last_state": true,
            "switch_quantization": "bar",
            "bars_per_phrase": 4,
            "animation_sequences": [{"id":"night","name":"Night","repeat":true,"steps":[{"animation_name":"rainbow_cycle","duration_bars":2}]}],
            "active_sequence_id":"night",
            "remembered_playback":{"mode":"dynamic","solid_color":"#7C3AED","animation":"rainbow_cycle","brightness":0.7,"blackout":false},
            "transition_settings":{"enabled":false,"duration_ms":600,"effect":"wipe"},
            "animation_settings":{"shared":{"rainbow_cycle":{"speed":1.5}}},
            "future_feature":{"keep":true}
        });
        fs::write(&path, serde_json::to_vec(&payload).unwrap()).unwrap();

        let mut store = SettingsStore::open(&path).unwrap();
        assert_eq!(
            store.settings().stripe_topology.as_ref().unwrap().layout,
            StripeLayout::Continuous
        );
        assert_eq!(
            store.settings().transition_settings.effect,
            TransitionEffect::Wipe
        );
        assert_eq!(
            store.settings().active_sequence_id.as_deref(),
            Some("night")
        );
        assert_eq!(
            store.settings().audio_profiles["USB microphone"].target_level,
            0.5
        );
        store
            .replace_category("application_name", json!("LumiStripe Studio"))
            .unwrap();

        payload = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert_eq!(payload["application_name"], "LumiStripe Studio");
        assert_eq!(payload["future_feature"]["keep"], true);
        assert_eq!(
            payload["stripes"]["outputs"][0]["zone_pattern"],
            "alternate"
        );
        assert_eq!(payload["audio_sync_offsets_ms"]["bluetooth"], -23.5);
    }

    #[test]
    fn rejects_invalid_and_future_settings_without_replacing_the_source_file() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("settings.json");
        let original = br#"{"version":12,"bars_per_phrase":5}"#;
        fs::write(&path, original).unwrap();
        assert!(matches!(
            SettingsStore::open(&path),
            Err(SettingsError::Invalid { .. })
        ));
        assert_eq!(fs::read(&path).unwrap(), original);

        fs::write(&path, br#"{"version":13}"#).unwrap();
        assert!(SettingsStore::open(&path).is_err());
        fs::write(&path, b"{\"version\":12").unwrap();
        assert!(matches!(
            SettingsStore::open(&path),
            Err(SettingsError::Json { .. })
        ));
    }

    #[test]
    fn invalid_category_update_keeps_current_file_and_snapshot() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("settings.json");
        fs::write(&path, r#"{"version":12,"application_name":"Studio"}"#).unwrap();
        let mut store = SettingsStore::open(&path).unwrap();
        let original = fs::read(&path).unwrap();
        assert!(store.replace_category("bars_per_phrase", json!(3)).is_err());
        assert_eq!(store.settings().application_name, "Studio");
        assert_eq!(fs::read(path).unwrap(), original);
    }

    #[test]
    fn atomic_write_uses_private_permissions_and_reports_rename_failures() {
        let directory = tempdir().unwrap();
        let file_path = directory.path().join("settings.json");
        write_atomic(&file_path, &json!({"version":12})).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&file_path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }

        let destination_directory = directory.path().join("destination");
        fs::create_dir(&destination_directory).unwrap();
        assert!(write_atomic(&destination_directory, &json!({"version":12})).is_err());
        assert!(destination_directory.is_dir());
    }

    #[test]
    fn default_settings_round_trip_with_current_version() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("settings.json");
        let settings = DashboardSettings::default();
        assert_eq!(settings.bars_per_phrase, 8);
        let mut store = SettingsStore::open(&path).unwrap();
        store
            .replace_category(
                "transition_settings",
                serde_json::to_value(settings.transition_settings).unwrap(),
            )
            .unwrap();
        let reloaded = SettingsStore::open(path).unwrap();
        assert_eq!(reloaded.settings().transition_settings.duration_ms, 300);
    }
}
