use crate::settings::{AudioTuningProfile, DashboardSettings};
use lumistripe_engine_audio::{AudioConfig, InputAudioDevice};
use lumistripe_engine_protocol::{AudioInputTransport, EngineAudioStatus};
use serde_json::{Value, json};

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct AudioStreamContext {
    pub source: String,
    pub sync_route: String,
    pub sync_offset_ms: f64,
    pub configured_input_noise_floor: Option<f64>,
    pub configured_noise_floor: f64,
}

pub(crate) fn engine_config(profile: &AudioTuningProfile, microphone: bool) -> AudioConfig {
    AudioConfig {
        microphone_enabled: microphone,
        input_noise_floor: profile.input_noise_floor as f32,
        noise_floor: profile.noise_floor as f32,
        rms_attack: profile.rms_attack as f32,
        rms_release: profile.rms_release as f32,
        band_attack: profile.band_attack as f32,
        band_release: profile.band_release as f32,
        beat_release: profile.beat_release as f32,
        target_level: profile.target_level as f32,
        music_threshold: if microphone {
            (profile.input_noise_floor * 2.0) as f32
        } else {
            AudioConfig::default().music_threshold
        },
        ..AudioConfig::default()
    }
}

pub(crate) fn tuning_value(profile: &AudioTuningProfile) -> Value {
    json!({
        "target_level": profile.target_level,
        "hardware_gain_target": profile.hardware_gain_target,
        "noise_floor": profile.noise_floor,
        "input_noise_floor": profile.input_noise_floor,
        "dynamic_response": profile.dynamic_response,
        "rms_attack": profile.rms_attack,
        "rms_release": profile.rms_release,
        "band_attack": profile.band_attack,
        "band_release": profile.band_release,
        "beat_release": profile.beat_release,
        "energy_threshold": profile.energy_threshold,
        "onset_threshold": profile.onset_threshold,
        "beat_density_threshold": profile.beat_density_threshold,
        "brightness_threshold": profile.brightness_threshold,
        "spectral_balance_ratio": profile.spectral_balance_ratio,
    })
}

pub(crate) fn sync_route(
    status: Option<&EngineAudioStatus>,
    active_source: &str,
    settings: &DashboardSettings,
) -> String {
    if status.is_some_and(|status| {
        status.active && status.input_transport == AudioInputTransport::DirectPcm
    }) {
        return "spotify_direct|librespot decoded PCM|default".to_owned();
    }
    let source = if status.is_some_and(|status| status.active) {
        match active_source {
            "demo" | "mic" | "bluetooth" | "spotify" => active_source,
            _ => "mic",
        }
    } else {
        "off"
    };
    let device = status
        .filter(|status| status.active)
        .and_then(|status| status.device.as_ref())
        .map(|device| device.name.as_str())
        .or(settings.selected_audio_device.as_deref())
        .unwrap_or(source);
    format!("{source}|{device}|default")
}

pub(crate) fn stream_context(
    settings: &DashboardSettings,
    configured_source: &str,
    active_source: &str,
    status: Option<&EngineAudioStatus>,
) -> AudioStreamContext {
    let route = sync_route(status, active_source, settings);
    let active_status = status.filter(|status| status.active);
    let active_device = active_status.and_then(|status| status.device.as_ref());
    let selected_name = match active_source {
        "bluetooth" | "spotify" => Some(active_source),
        _ => active_device
            .map(|device| device.name.as_str())
            .or(settings.selected_audio_device.as_deref()),
    };
    let profile = selected_name
        .and_then(|name| settings.audio_profiles.get(name))
        .cloned()
        .unwrap_or_default();
    let microphone_requested =
        active_source == "mic" || (configured_source == "auto" && active_source == "off");

    AudioStreamContext {
        source: configured_source.to_owned(),
        sync_offset_ms: settings
            .audio_sync_offsets_ms
            .get(&route)
            .copied()
            .unwrap_or_default(),
        sync_route: route,
        configured_input_noise_floor: microphone_requested.then_some(profile.input_noise_floor),
        configured_noise_floor: profile.noise_floor,
    }
}

pub(crate) fn response(
    settings: &DashboardSettings,
    configured_source: &str,
    active_source: &str,
    status: Option<&EngineAudioStatus>,
    devices: &[InputAudioDevice],
    enumeration_error: Option<&str>,
) -> Value {
    let active_name = status
        .filter(|status| status.active)
        .and_then(|status| status.device.as_ref())
        .map(|device| device.name.as_str());
    let selected_name = match active_source {
        "bluetooth" | "spotify" => Some(active_source),
        _ => active_name.or(settings.selected_audio_device.as_deref()),
    };
    let active_device = status
        .filter(|status| status.active)
        .filter(|status| status.input_transport != AudioInputTransport::DirectPcm)
        .and_then(|status| status.device.as_ref())
        .map(|device| device.selector.as_str())
        .or_else(|| {
            selected_name.and_then(|name| {
                devices
                    .iter()
                    .find(|device| device.name == name || device.selector == name)
                    .map(|device| device.selector.as_str())
            })
        });
    let fallback_device = settings.selected_audio_device.as_deref().and_then(|name| {
        devices
            .iter()
            .find(|device| device.name == name || device.selector == name)
            .map(|device| device.selector.as_str())
            .or(Some(name))
    });
    let profile = selected_name
        .and_then(|name| settings.audio_profiles.get(name))
        .cloned()
        .unwrap_or_default();
    let route = sync_route(status, active_source, settings);
    let active_source = if status.is_some_and(|status| status.active) {
        active_source
    } else {
        "off"
    };
    let audio_error = status
        .and_then(|status| status.last_error.as_deref())
        .or(enumeration_error);
    let input_transport = status
        .filter(|status| status.active)
        .map_or(AudioInputTransport::Off, |status| status.input_transport);
    let input_sample_rate = status.map_or(0, |status| {
        status
            .device
            .as_ref()
            .map_or(status.pcm_ingress.sample_rate, |device| device.sample_rate)
    });
    let input_channels = status.map_or(0, |status| {
        status
            .device
            .as_ref()
            .map_or(status.pcm_ingress.channels, |device| device.channels)
    });
    json!({
        "source": configured_source,
        "active_source": active_source,
        "monitoring": status.is_some_and(|status| status.active) && active_source != "demo",
        "active_device": active_device,
        "fallback_device": fallback_device,
        "active_device_name": active_name,
        "input_transport": input_transport,
        "input_sample_rate": input_sample_rate,
        "input_channels": input_channels,
        "pcm_ingress": status.map(|status| status.pcm_ingress).unwrap_or_default(),
        "dsp_processing_p50_ns": status.map_or(0, |status| status.dsp_processing_p50_ns),
        "dsp_processing_p95_ns": status.map_or(0, |status| status.dsp_processing_p95_ns),
        "last_feature_timestamp_ns": status.and_then(|status| status.last_frame_ns),
        "devices": devices.iter().map(|device| json!({
            "selector": device.selector,
            "name": device.name,
            "settings": tuning_value(settings.audio_profiles.get(&device.name).unwrap_or(&AudioTuningProfile::default())),
        })).collect::<Vec<_>>(),
        "settings": tuning_value(&profile),
        "configured_noise_floor": profile.noise_floor,
        "sync_route": route,
        "sync_offset_ms": settings.audio_sync_offsets_ms.get(&route).copied().unwrap_or_default(),
        "hardware_gain_supported": false,
        "hardware_gain_writable": false,
        "hardware_gain_backend": null,
        "hardware_gain_control": null,
        "hardware_gain_value": null,
        "hardware_gain_error": null,
        "bluetooth": {
            "available": false,
            "powered": false,
            "adapter_alias": null,
            "scanning": false,
            "streaming": false,
            "devices": [],
            "connected_inputs": [],
            "connected_outputs": [],
            "connected_device": null,
            "input_source": null,
            "output_devices": [],
            "default_sink": null,
            "output_volume": null,
            "output_muted": false,
            "output_ready": false,
            "capabilities": {"operations": [], "max_inputs": 1, "max_outputs": 1},
            "operation": null,
            "operation_id": null,
            "operation_state": "idle",
            "error": null,
        },
        "spotify": {
            "configured": false,
            "connected": false,
            "logged_in": false,
            "is_active": false,
            "device_name": null,
            "status": "idle",
            "track": null,
            "position_ms": 0,
            "duration_ms": null,
            "volume": 0,
            "shuffle": false,
            "repeat": "off",
            "error": null,
        },
        "error": audio_error,
    })
}

#[cfg(test)]
mod tests {
    use super::{engine_config, response, stream_context};
    use crate::settings::{AudioTuningProfile, DashboardSettings};
    use lumistripe_engine_audio::InputAudioDevice;
    use lumistripe_engine_protocol::{AudioInputTransport, EngineAudioStatus};

    #[test]
    fn microphone_audio_config_uses_the_saved_profile() {
        let profile = AudioTuningProfile {
            target_level: 0.52,
            input_noise_floor: 0.02,
            noise_floor: 0.04,
            rms_attack: 0.6,
            band_release: 0.3,
            ..Default::default()
        };
        let config = engine_config(&profile, true);
        assert_eq!(config.target_level, 0.52);
        assert_eq!(config.input_noise_floor, 0.02);
        assert_eq!(config.noise_floor, 0.04);
        assert_eq!(config.rms_attack, 0.6);
        assert_eq!(config.band_release, 0.3);
        assert_eq!(config.music_threshold, 0.04);
        assert!(config.microphone_enabled);
        assert!(config.validate().is_ok());
    }

    #[test]
    fn audio_response_uses_supported_microphone_state_and_saved_sync_route() {
        let mut settings = DashboardSettings {
            selected_audio_device: Some("USB mic".to_owned()),
            ..Default::default()
        };
        settings
            .audio_sync_offsets_ms
            .insert("off|USB mic|default".to_owned(), -24.5);
        let response = response(&settings, "auto", "off", None, &[], None);
        assert_eq!(response["source"], "auto");
        assert_eq!(response["active_source"], "off");
        assert_eq!(response["monitoring"], false);
        assert_eq!(response["fallback_device"], "USB mic");
        assert_eq!(response["sync_route"], "off|USB mic|default");
        assert_eq!(response["sync_offset_ms"], -24.5);
        assert_eq!(response["hardware_gain_supported"], false);
        assert_eq!(response["hardware_gain_value"], serde_json::Value::Null);
    }

    #[test]
    fn audio_response_reports_demo_source_without_claiming_microphone_monitoring() {
        let mut settings = DashboardSettings::default();
        settings
            .audio_sync_offsets_ms
            .insert("demo|demo|default".to_owned(), 18.0);
        let status = EngineAudioStatus {
            active: true,
            ..Default::default()
        };

        let response = response(&settings, "demo", "demo", Some(&status), &[], None);

        assert_eq!(response["source"], "demo");
        assert_eq!(response["active_source"], "demo");
        assert_eq!(response["monitoring"], false);
        assert_eq!(response["sync_route"], "demo|demo|default");
        assert_eq!(response["sync_offset_ms"], 18.0);
    }

    #[test]
    fn direct_spotify_pcm_uses_a_stable_route_and_reports_its_format() {
        let mut settings = DashboardSettings {
            selected_audio_device: Some("Spotify monitor".to_owned()),
            ..Default::default()
        };
        settings
            .audio_sync_offsets_ms
            .insert("spotify|Spotify monitor|default".to_owned(), 35.0);
        settings.audio_sync_offsets_ms.insert(
            "spotify_direct|librespot decoded PCM|default".to_owned(),
            -12.5,
        );
        let status = EngineAudioStatus {
            active: true,
            input_transport: AudioInputTransport::DirectPcm,
            pcm_ingress: lumistripe_engine_audio::PcmIngressStats {
                enabled: true,
                playback_state: lumistripe_engine_audio::PcmPlaybackState::Playing,
                sample_rate: 44_100,
                channels: 2,
                ..Default::default()
            },
            ..Default::default()
        };

        let response = response(&settings, "spotify", "spotify", Some(&status), &[], None);
        let context = stream_context(&settings, "spotify", "spotify", Some(&status));

        assert_eq!(response["input_transport"], "direct_pcm");
        assert_eq!(response["input_sample_rate"], 44_100);
        assert_eq!(response["input_channels"], 2);
        assert_eq!(response["active_device"], serde_json::Value::Null);
        assert_eq!(
            response["sync_route"],
            "spotify_direct|librespot decoded PCM|default"
        );
        assert_eq!(response["sync_offset_ms"], -12.5);
        assert_eq!(context.sync_offset_ms, -12.5);
        assert_eq!(
            settings.audio_sync_offsets_ms["spotify|Spotify monitor|default"],
            35.0
        );
    }

    #[test]
    fn audio_stream_context_uses_the_active_device_profile_for_auto_source() {
        let mut settings = DashboardSettings {
            selected_audio_device: Some("USB mic".to_owned()),
            ..Default::default()
        };
        settings.audio_profiles.insert(
            "USB mic".to_owned(),
            AudioTuningProfile {
                noise_floor: 0.04,
                input_noise_floor: 0.02,
                ..Default::default()
            },
        );
        settings
            .audio_sync_offsets_ms
            .insert("mic|USB mic|default".to_owned(), -24.5);
        let status = EngineAudioStatus {
            active: true,
            device: Some(InputAudioDevice {
                selector: "alsa:USB".to_owned(),
                host: "ALSA".to_owned(),
                name: "USB mic".to_owned(),
                is_default: false,
                sample_rate: 48_000,
                channels: 1,
            }),
            ..Default::default()
        };

        let context = stream_context(&settings, "auto", "mic", Some(&status));

        assert_eq!(context.source, "auto");
        assert_eq!(context.sync_route, "mic|USB mic|default");
        assert_eq!(context.sync_offset_ms, -24.5);
        assert_eq!(context.configured_input_noise_floor, Some(0.02));
        assert_eq!(context.configured_noise_floor, 0.04);
    }
}
