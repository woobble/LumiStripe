//! Typed view of the canonical Python animation and effect catalogs.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Number, Value};
use std::collections::BTreeMap;
use std::sync::OnceLock;

pub const CATALOG_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct AnimationCatalog {
    pub schema_version: u32,
    pub animations: Vec<AnimationDefinition>,
    pub effects: Vec<EffectDefinition>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct AnimationDefinition {
    pub name: String,
    pub renderer: String,
    pub frame_ms: u32,
    pub frames_per_cycle: u32,
    pub automatic: bool,
    pub display_name: String,
    pub role: String,
    pub metadata: Value,
    pub dynamic_metadata: Value,
    pub effect_metadata: Option<Value>,
    pub settings: Vec<AnimationSettingDefinition>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct AnimationSettingDefinition {
    pub id: String,
    pub label: String,
    #[serde(rename = "type")]
    pub setting_type: String,
    pub default: Value,
    pub description: String,
    pub minimum: Option<f64>,
    pub maximum: Option<f64>,
    pub step: Option<f64>,
    pub unit: Option<String>,
    #[serde(default)]
    pub choices: Vec<String>,
    pub visible_when_option: Option<String>,
    pub visible_when_value: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct EffectDefinition {
    pub name: String,
    pub category: String,
    pub duration_s: f64,
    pub strength: f64,
    pub preferred_band: String,
    pub drop_only: bool,
    pub blend_mode: String,
}

static CATALOG: OnceLock<AnimationCatalog> = OnceLock::new();

pub fn catalog() -> &'static AnimationCatalog {
    CATALOG.get_or_init(|| {
        let catalog: AnimationCatalog = serde_json::from_str(include_str!("../catalog.json"))
            .expect("generated Rust animation catalog must be valid JSON");
        catalog
            .validate()
            .expect("generated Rust animation catalog must validate");
        catalog
    })
}

impl AnimationCatalog {
    pub fn validate(&self) -> Result<(), CatalogError> {
        if self.schema_version != CATALOG_SCHEMA_VERSION {
            return Err(CatalogError::UnsupportedSchema(self.schema_version));
        }
        let mut names = std::collections::BTreeSet::new();
        for definition in &self.animations {
            if definition.name.is_empty() || !names.insert(definition.name.as_str()) {
                return Err(CatalogError::InvalidAnimationName(definition.name.clone()));
            }
            if definition.frame_ms == 0 || definition.frames_per_cycle == 0 {
                return Err(CatalogError::InvalidTiming(definition.name.clone()));
            }
            validate_setting_schema(&definition.name, &definition.settings)?;
        }
        let mut effect_names = std::collections::BTreeSet::new();
        for effect in &self.effects {
            if effect.name.is_empty() || !effect_names.insert(effect.name.as_str()) {
                return Err(CatalogError::InvalidEffectName(effect.name.clone()));
            }
            if effect.duration_s <= 0.0 || !(0.0 < effect.strength && effect.strength <= 1.0) {
                return Err(CatalogError::InvalidEffect(effect.name.clone()));
            }
        }
        Ok(())
    }

    pub fn animation(&self, name: &str) -> Option<&AnimationDefinition> {
        self.animations
            .iter()
            .find(|definition| definition.name == name)
    }

    pub fn effect(&self, name: &str) -> Option<&EffectDefinition> {
        self.effects
            .iter()
            .find(|definition| definition.name == name)
    }
}

impl AnimationDefinition {
    /// Validates caller-provided settings without allocating on valid input.
    pub fn validate_settings(
        &self,
        values: &Map<String, Value>,
        allow_partial: bool,
    ) -> Result<(), SettingError> {
        if let Some(unknown) = values.keys().find(|key| {
            !self
                .settings
                .iter()
                .any(|setting| setting.id.as_str() == key.as_str())
        }) {
            return Err(SettingError::Unknown(unknown.clone()));
        }
        for setting in &self.settings {
            let Some(value) = values.get(&setting.id) else {
                if !allow_partial {
                    return Err(SettingError::Missing(setting.id.clone()));
                }
                continue;
            };
            validate_setting_value(setting, value)?;
        }
        if self.renderer == "Pulse" {
            let minimum = values
                .get("minimum_intensity")
                .or_else(|| {
                    self.settings
                        .iter()
                        .find(|setting| setting.id == "minimum_intensity")
                        .map(|setting| &setting.default)
                })
                .and_then(Value::as_f64)
                .unwrap_or(22.0);
            let maximum = values
                .get("maximum_intensity")
                .or_else(|| {
                    self.settings
                        .iter()
                        .find(|setting| setting.id == "maximum_intensity")
                        .map(|setting| &setting.default)
                })
                .and_then(Value::as_f64)
                .unwrap_or(100.0);
            if minimum > maximum {
                return Err(SettingError::Invalid(
                    "minimum_intensity must not exceed maximum_intensity".to_owned(),
                ));
            }
        }
        Ok(())
    }

    /// Validates and fills catalog defaults using the same strict policy as Python.
    pub fn normalize_settings(
        &self,
        values: &Map<String, Value>,
        allow_partial: bool,
    ) -> Result<BTreeMap<String, Value>, SettingError> {
        self.validate_settings(values, allow_partial)?;
        let mut normalized = BTreeMap::new();
        for setting in &self.settings {
            let Some(value) = values.get(&setting.id) else {
                normalized.insert(setting.id.clone(), setting.default.clone());
                continue;
            };
            normalized.insert(setting.id.clone(), normalize_value(setting, value)?);
        }
        Ok(normalized)
    }
}

fn validate_setting_value(
    definition: &AnimationSettingDefinition,
    value: &Value,
) -> Result<(), SettingError> {
    match definition.setting_type.as_str() {
        "number" => {
            let number = value
                .as_f64()
                .filter(|_| !value.is_boolean())
                .ok_or_else(|| SettingError::Invalid(definition.id.clone()))?;
            if !number.is_finite()
                || definition.minimum.is_some_and(|minimum| number < minimum)
                || definition.maximum.is_some_and(|maximum| number > maximum)
            {
                return Err(SettingError::Invalid(definition.id.clone()));
            }
        }
        "boolean" if value.is_boolean() => {}
        "enum" => {
            let Some(encoded) = value.as_str() else {
                return Err(SettingError::Invalid(definition.id.clone()));
            };
            if !definition.choices.iter().any(|choice| choice == encoded) {
                return Err(SettingError::Invalid(definition.id.clone()));
            }
        }
        "color" => {
            let Some(encoded) = value.as_str() else {
                return Err(SettingError::Invalid(definition.id.clone()));
            };
            if encoded.len() != 7
                || !encoded.starts_with('#')
                || !encoded[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(SettingError::Invalid(definition.id.clone()));
            }
        }
        _ => return Err(SettingError::Invalid(definition.id.clone())),
    }
    Ok(())
}

fn validate_setting_schema(
    animation: &str,
    settings: &[AnimationSettingDefinition],
) -> Result<(), CatalogError> {
    let mut ids = std::collections::BTreeSet::new();
    for setting in settings {
        if setting.id.is_empty() || !ids.insert(setting.id.as_str()) {
            return Err(CatalogError::InvalidSettingId {
                animation: animation.to_owned(),
                setting: setting.id.clone(),
            });
        }
        if setting.setting_type == "number"
            && (setting.minimum.is_none()
                || setting.maximum.is_none()
                || setting.step.is_none()
                || setting.minimum > setting.maximum
                || setting.step.is_some_and(|step| step <= 0.0))
        {
            return Err(CatalogError::InvalidSettingSchema {
                animation: animation.to_owned(),
                setting: setting.id.clone(),
            });
        }
        if setting.setting_type == "enum" && setting.choices.is_empty() {
            return Err(CatalogError::InvalidSettingSchema {
                animation: animation.to_owned(),
                setting: setting.id.clone(),
            });
        }
    }
    Ok(())
}

fn normalize_value(
    definition: &AnimationSettingDefinition,
    value: &Value,
) -> Result<Value, SettingError> {
    match definition.setting_type.as_str() {
        "number" => {
            let number = value
                .as_f64()
                .filter(|_| !value.is_boolean())
                .ok_or_else(|| SettingError::Invalid(definition.id.clone()))?;
            if !number.is_finite()
                || definition.minimum.is_some_and(|minimum| number < minimum)
                || definition.maximum.is_some_and(|maximum| number > maximum)
            {
                return Err(SettingError::Invalid(definition.id.clone()));
            }
            let encoded = if number.fract() == 0.0
                && number >= i64::MIN as f64
                && number <= i64::MAX as f64
            {
                Number::from(number as i64)
            } else {
                Number::from_f64(number)
                    .ok_or_else(|| SettingError::Invalid(definition.id.clone()))?
            };
            Ok(Value::Number(encoded))
        }
        "boolean" if value.is_boolean() => Ok(value.clone()),
        "enum" => {
            let Some(encoded) = value.as_str() else {
                return Err(SettingError::Invalid(definition.id.clone()));
            };
            if !definition.choices.iter().any(|choice| choice == encoded) {
                return Err(SettingError::Invalid(definition.id.clone()));
            }
            Ok(value.clone())
        }
        "color" => {
            let Some(encoded) = value.as_str() else {
                return Err(SettingError::Invalid(definition.id.clone()));
            };
            if encoded.len() != 7
                || !encoded.starts_with('#')
                || !encoded[1..].bytes().all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(SettingError::Invalid(definition.id.clone()));
            }
            Ok(Value::String(encoded.to_ascii_uppercase()))
        }
        _ => Err(SettingError::Invalid(definition.id.clone())),
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CatalogError {
    UnsupportedSchema(u32),
    InvalidAnimationName(String),
    InvalidTiming(String),
    InvalidEffectName(String),
    InvalidEffect(String),
    InvalidSettingId { animation: String, setting: String },
    InvalidSettingSchema { animation: String, setting: String },
}

impl std::fmt::Display for CatalogError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "invalid animation catalog: {self:?}")
    }
}

impl std::error::Error for CatalogError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SettingError {
    Unknown(String),
    Missing(String),
    Invalid(String),
}

impl std::fmt::Display for SettingError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unknown(name) => write!(formatter, "unknown animation setting: {name}"),
            Self::Missing(name) => write!(formatter, "missing animation setting: {name}"),
            Self::Invalid(name) => write!(formatter, "invalid value for animation setting: {name}"),
        }
    }
}

impl std::error::Error for SettingError {}

#[cfg(test)]
mod tests {
    use super::catalog;
    use serde_json::{Map, Value, json};

    #[test]
    fn generated_catalog_contains_every_animation_and_effect_definition() {
        let catalog = catalog();
        assert_eq!(catalog.animations.len(), 54);
        assert_eq!(catalog.effects.len(), 19);
        assert!(catalog.animation("rainbow_cycle").is_some());
        assert!(catalog.animation("red_blackout_strobe").is_some());
        assert!(catalog.effect("beat_tunnel").is_some());
        assert!(catalog.effect("drop_wave").is_some());
    }

    #[test]
    fn setting_validation_fills_defaults_and_normalizes_colors() {
        let definition = catalog().animation("rainbow").unwrap();
        let mut values = Map::new();
        values.insert("palette".into(), json!("custom"));
        values.insert("custom_color".into(), json!("#abcdef"));
        let normalized = definition.normalize_settings(&values, true).unwrap();
        assert_eq!(
            normalized.get("palette"),
            Some(&Value::String("custom".into()))
        );
        assert_eq!(
            normalized.get("custom_color"),
            Some(&Value::String("#ABCDEF".into()))
        );
        assert_eq!(normalized.get("speed").and_then(Value::as_i64), Some(50));
    }

    #[test]
    fn pulse_minimum_must_not_exceed_its_maximum() {
        let definition = catalog().animation("pulse").unwrap();
        let mut values = Map::new();
        values.insert("minimum_intensity".into(), json!(90));
        values.insert("maximum_intensity".into(), json!(50));
        assert!(definition.normalize_settings(&values, true).is_err());
    }
}
