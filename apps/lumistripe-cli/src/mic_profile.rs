use lumistripe_engine_audio::AudioConfig;
use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::path::Path;

const MAX_PROFILE_BYTES: u64 = 65_536;

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct MicProfile {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(
        default,
        deserialize_with = "deserialize_device_patterns",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub device_patterns: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mic_noise_floor: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mic_target_level: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idle_threshold_scale: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub analysis: Option<MicAnalysisProfile>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct MicAnalysisProfile {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub drop_bass_threshold: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub drop_bass_delta_threshold: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub drop_onset_threshold: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub section_change_threshold: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub section_onset_threshold: Option<f32>,
}

impl MicProfile {
    pub fn load(identifier: &str) -> Result<Self, String> {
        if identifier == "pcm2902" {
            return Ok(Self {
                name: Some("pcm2902".to_owned()),
                device_patterns: vec![
                    "Texas Instruments PCM2902 Audio Codec".to_owned(),
                    "PCM2902".to_owned(),
                ],
                mic_noise_floor: Some(0.0137),
                mic_target_level: Some(0.403),
                idle_threshold_scale: Some(0.92),
                analysis: None,
            });
        }
        if identifier == "auto" {
            return Err("the auto mic profile must be resolved against an audio device".to_owned());
        }
        let path = Path::new(identifier);
        let mut file = std::fs::File::open(path)
            .map_err(|error| format!("read mic profile {}: {error}", path.display()))?;
        if !file
            .metadata()
            .map_err(|error| format!("inspect mic profile {}: {error}", path.display()))?
            .is_file()
        {
            return Err(format!(
                "mic profile must be a regular file no larger than {MAX_PROFILE_BYTES} bytes"
            ));
        }
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take(MAX_PROFILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| format!("read mic profile {}: {error}", path.display()))?;
        if bytes.len() as u64 > MAX_PROFILE_BYTES {
            return Err(format!(
                "mic profile must be no larger than {MAX_PROFILE_BYTES} bytes"
            ));
        }
        let profile: Self = serde_json::from_slice(&bytes)
            .map_err(|error| format!("invalid mic profile {}: {error}", path.display()))?;
        profile.validate()?;
        Ok(profile)
    }

    pub fn matching_device(device_name: &str) -> Option<Self> {
        let profile = Self::load("pcm2902").expect("the built-in profile is valid");
        profile
            .device_patterns
            .iter()
            .any(|pattern| device_name.to_lowercase().contains(&pattern.to_lowercase()))
            .then_some(profile)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.name.as_ref().is_some_and(|name| name.len() > 128) {
            return Err("mic profile name exceeds 128 bytes".to_owned());
        }
        if self.device_patterns.len() > 32
            || self
                .device_patterns
                .iter()
                .any(|pattern| pattern.is_empty() || pattern.len() > 256)
        {
            return Err("mic profile device patterns must contain at most 32 non-empty strings of at most 256 bytes".to_owned());
        }
        for (name, value) in [
            ("mic_noise_floor", self.mic_noise_floor),
            ("mic_target_level", self.mic_target_level),
        ] {
            if value.is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value)) {
                return Err(format!(
                    "mic profile {name} must be finite and between 0 and 1"
                ));
            }
        }
        if self
            .idle_threshold_scale
            .is_some_and(|scale| !scale.is_finite() || !(0.01..=10.0).contains(&scale))
        {
            return Err("mic profile idle_threshold_scale must be between 0.01 and 10".to_owned());
        }
        if let Some(analysis) = &self.analysis {
            for (name, value) in [
                ("drop_bass_threshold", analysis.drop_bass_threshold),
                (
                    "drop_bass_delta_threshold",
                    analysis.drop_bass_delta_threshold,
                ),
                ("drop_onset_threshold", analysis.drop_onset_threshold),
                (
                    "section_change_threshold",
                    analysis.section_change_threshold,
                ),
                ("section_onset_threshold", analysis.section_onset_threshold),
            ] {
                if value.is_some_and(|value| !value.is_finite() || !(0.0..=1.0).contains(&value)) {
                    return Err(format!(
                        "mic profile analysis.{name} must be finite and between 0 and 1"
                    ));
                }
            }
        }
        Ok(())
    }

    pub fn apply(&self, config: &mut AudioConfig) {
        if let Some(noise_floor) = self.mic_noise_floor {
            config.noise_floor = noise_floor;
        }
        if let Some(target_level) = self.mic_target_level {
            config.target_level = target_level;
        }
        if let Some(analysis) = &self.analysis {
            if let Some(value) = analysis.drop_bass_threshold {
                config.drop_bass_threshold = value;
            }
            if let Some(value) = analysis.drop_bass_delta_threshold {
                config.drop_bass_delta_threshold = value;
            }
            if let Some(value) = analysis.drop_onset_threshold {
                config.drop_onset_threshold = value;
            }
            if let Some(value) = analysis.section_change_threshold {
                config.section_change_threshold = value;
            }
            if let Some(value) = analysis.section_onset_threshold {
                config.section_onset_threshold = value;
            }
        }
    }

    pub fn from_effective_config(
        config: &AudioConfig,
        idle_threshold_scale: f64,
        device_name: Option<&str>,
    ) -> Self {
        Self {
            name: Some("custom".to_owned()),
            device_patterns: device_name.map_or_else(Vec::new, |name| vec![name.to_owned()]),
            mic_noise_floor: Some(config.noise_floor),
            mic_target_level: Some(config.target_level),
            idle_threshold_scale: Some(idle_threshold_scale),
            analysis: Some(MicAnalysisProfile {
                drop_bass_threshold: Some(config.drop_bass_threshold),
                drop_bass_delta_threshold: Some(config.drop_bass_delta_threshold),
                drop_onset_threshold: Some(config.drop_onset_threshold),
                section_change_threshold: Some(config.section_change_threshold),
                section_onset_threshold: Some(config.section_onset_threshold),
            }),
        }
    }

    pub fn write(&self, path: &Path) -> Result<(), String> {
        self.validate()?;
        let parent = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("create mic profile directory: {error}"))?;
        let encoded = serde_json::to_vec_pretty(self)
            .map_err(|error| format!("encode mic profile: {error}"))?;
        let mut temporary = tempfile::NamedTempFile::new_in(parent)
            .map_err(|error| format!("create temporary mic profile: {error}"))?;
        temporary
            .write_all(&encoded)
            .map_err(|error| format!("write temporary mic profile: {error}"))?;
        temporary
            .persist(path)
            .map_err(|error| format!("replace mic profile {}: {error}", path.display()))?;
        Ok(())
    }
}

fn deserialize_device_patterns<'de, D>(deserializer: D) -> Result<Vec<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Patterns {
        One(String),
        Many(Vec<String>),
    }
    Ok(match Patterns::deserialize(deserializer)? {
        Patterns::One(pattern) => vec![pattern],
        Patterns::Many(patterns) => patterns,
    })
}

#[cfg(test)]
mod tests {
    use super::MicProfile;
    use lumistripe_engine_audio::AudioConfig;

    #[test]
    fn built_in_profile_matches_pcm2902_device_names_and_updates_audio_config() {
        let profile = MicProfile::matching_device("USB Texas Instruments PCM2902 Audio Codec")
            .expect("built-in device profile should match");
        let mut config = AudioConfig::default();
        profile.apply(&mut config);
        assert_eq!(config.noise_floor, 0.0137);
        assert_eq!(config.target_level, 0.403);
        assert_eq!(profile.idle_threshold_scale, Some(0.92));
    }

    #[test]
    fn profile_validation_rejects_non_finite_and_out_of_range_values() {
        let bad_scale: MicProfile = serde_json::from_str(r#"{"idle_threshold_scale":0}"#).unwrap();
        assert!(bad_scale.validate().is_err());
        let bad_analysis: MicProfile =
            serde_json::from_str(r#"{"analysis":{"drop_onset_threshold":2}}"#).unwrap();
        assert!(bad_analysis.validate().is_err());
    }

    #[test]
    fn python_profile_json_loads_with_analysis_thresholds() {
        let profile: MicProfile = serde_json::from_str(
            r#"{"name":"usb","device_patterns":["USB mic"],"mic_noise_floor":0.02,"analysis":{"drop_bass_threshold":0.5}}"#,
        )
        .unwrap();
        profile.validate().unwrap();
        let mut config = AudioConfig::default();
        profile.apply(&mut config);
        assert_eq!(config.noise_floor, 0.02);
        assert_eq!(config.drop_bass_threshold, 0.5);
    }

    #[test]
    fn profile_writer_atomically_round_trips_single_string_device_patterns() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("profile.json");
        let profile: MicProfile = serde_json::from_str(
            r#"{"name":"usb","device_patterns":"USB mic","mic_noise_floor":0.02}"#,
        )
        .unwrap();
        profile.write(&path).unwrap();
        let loaded = MicProfile::load(path.to_str().unwrap()).unwrap();
        assert_eq!(loaded.device_patterns, ["USB mic"]);
        assert_eq!(loaded.mic_noise_floor, Some(0.02));
    }
}
