/// Tuning recommendations derived from observed engine audio frames.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AudioCalibrationRecommendation {
    pub duration_seconds: f64,
    pub samples: usize,
    pub measured_floor: f64,
    pub measured_peak: f64,
    pub recommended_noise_floor: f64,
    pub recommended_target_level: f64,
    pub recommended_idle_threshold_scale: f64,
}

/// Recommend shared microphone tuning from the frame and music-energy values
/// already produced by the Rust audio processor.
pub fn recommend_audio_calibration(
    rms_values: &[f64],
    energy_values: &[f64],
    duration_seconds: f64,
) -> Result<AudioCalibrationRecommendation, String> {
    if rms_values.is_empty() {
        return Err("audio calibration requires at least one frame".to_owned());
    }
    if !duration_seconds.is_finite()
        || rms_values.iter().any(|value| !value.is_finite())
        || energy_values.iter().any(|value| !value.is_finite())
    {
        return Err("audio calibration values must be finite".to_owned());
    }

    let rms = rms_values
        .iter()
        .map(|value| value.clamp(0.0, 1.0))
        .collect::<Vec<_>>();
    let energy = energy_values
        .iter()
        .map(|value| value.clamp(0.0, 1.0))
        .collect::<Vec<_>>();
    let floor = percentile(&rms, 20.0);
    let peak = rms.iter().chain(energy.iter()).copied().fold(0.0, f64::max);
    let active = energy
        .iter()
        .copied()
        .filter(|value| *value >= (floor * 1.8).max(0.01))
        .collect::<Vec<_>>();
    let active_levels = if active.is_empty() {
        if energy.is_empty() { &rms } else { &energy }
    } else {
        &active
    };
    let active_level = percentile(active_levels, 80.0);
    let noise_floor = (floor * 1.6 + 0.004).clamp(0.003, 0.12);
    let target_level = (active_level * 1.25).max(0.24).clamp(0.24, 0.62);

    Ok(AudioCalibrationRecommendation {
        duration_seconds: duration_seconds.max(0.0),
        samples: rms.len(),
        measured_floor: floor,
        measured_peak: peak,
        recommended_noise_floor: noise_floor,
        recommended_target_level: target_level,
        recommended_idle_threshold_scale: (noise_floor / 0.015).max(0.5).clamp(0.5, 4.0),
    })
}

fn percentile(values: &[f64], percentile_value: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    if ordered.len() == 1 {
        return ordered[0];
    }
    let position = percentile_value.clamp(0.0, 100.0) / 100.0 * (ordered.len() - 1) as f64;
    let lower = position as usize;
    let upper = (lower + 1).min(ordered.len() - 1);
    ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower as f64)
}

#[cfg(test)]
mod tests {
    use super::recommend_audio_calibration;

    #[test]
    fn recommends_tuning_from_engine_audio_levels() {
        let result =
            recommend_audio_calibration(&[0.01, 0.02, 0.04, 0.08], &[0.02, 0.1, 0.2, 0.4], 4.0)
                .unwrap();
        assert_eq!(result.samples, 4);
        assert!((result.measured_floor - 0.016).abs() < 1.0e-12);
        assert!((result.measured_peak - 0.4).abs() < 1.0e-12);
        assert!((result.recommended_noise_floor - 0.0296).abs() < 1.0e-12);
        assert_eq!(result.duration_seconds, 4.0);
    }

    #[test]
    fn rejects_missing_or_non_finite_measurements() {
        assert!(recommend_audio_calibration(&[], &[], 1.0).is_err());
        assert!(recommend_audio_calibration(&[f64::NAN], &[], 1.0).is_err());
    }
}
