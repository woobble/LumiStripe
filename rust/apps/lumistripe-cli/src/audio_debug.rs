use lumistripe_engine_protocol::{
    EngineAudioStatus, RenderSelectorDiagnostics, RenderSelectorScore, ResponseResult,
};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

const MAX_RECORDING_BYTES: u64 = 32 * 1024 * 1024;
const MAX_RECORDING_ROWS: usize = 100_000;

pub(crate) struct Recorder {
    writer: BufWriter<File>,
    label: Option<String>,
}

impl Recorder {
    pub(crate) fn create(path: &Path, label: Option<&str>) -> Result<Self, String> {
        if label.is_some_and(|value| value.len() > 256) {
            return Err("--label must not exceed 256 bytes".to_owned());
        }
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)
                .map_err(|error| format!("create audio recording directory: {error}"))?;
        }
        let file = File::create(path)
            .map_err(|error| format!("create audio recording {}: {error}", path.display()))?;
        Ok(Self {
            writer: BufWriter::new(file),
            label: label.map(ToOwned::to_owned),
        })
    }

    pub(crate) fn write(&mut self, mut row: Value) -> Result<(), String> {
        if let (Some(label), Some(object)) = (&self.label, row.as_object_mut()) {
            object.insert("label".to_owned(), Value::String(label.clone()));
        }
        serde_json::to_writer(&mut self.writer, &row)
            .map_err(|error| format!("encode audio recording row: {error}"))?;
        self.writer
            .write_all(b"\n")
            .and_then(|()| self.writer.flush())
            .map_err(|error| format!("write audio recording row: {error}"))
    }
}

#[derive(Serialize)]
struct DebugRow<'a> {
    elapsed_s: f64,
    source: &'a str,
    mode: &'static str,
    class: &'static str,
    animation: String,
    base_animation: String,
    effects: Vec<String>,
    music_active: bool,
    music_gate: String,
    fresh: bool,
    age_s: Option<f64>,
    callback_age_s: Option<f64>,
    sequence: u64,
    fft_count: u64,
    status_count: u64,
    normalization_gain: f32,
    input_rms: f32,
    input_peak: f32,
    clipping_fraction: f32,
    capture_callback_count: u64,
    capture_callback_errors: u64,
    capture_overflowed_batches: u64,
    capture_dropped_samples: u64,
    rms: f32,
    beat: bool,
    beat_strength: f32,
    bpm: f32,
    bpm_confidence: f32,
    energy: f32,
    volume: f32,
    energy_level: f32,
    bass: f32,
    brightness: f32,
    onset_strength: f32,
    dynamic_range: f32,
    bands: [f32; 8],
    bass_energy: f32,
    mid_energy: f32,
    treble_energy: f32,
    spectral_centroid: f32,
    spectral_flux: f32,
    beat_confidence: f32,
    rolling_loudness: f32,
    silence: bool,
    drop_detected: bool,
    section_change: bool,
    selector_reason: String,
    selector_should_switch: bool,
    selector_scores: Vec<RenderSelectorScore>,
    selector_diagnostics: Vec<RenderSelectorDiagnostics>,
    selected_animations: Vec<String>,
    cycling_active: Vec<bool>,
    effect_schedulers: Value,
}

pub(crate) fn debug_row(
    status: &EngineAudioStatus,
    health: &ResponseResult,
    elapsed_seconds: f64,
    source: &str,
    now_ns: u64,
) -> Value {
    let (animations, gates, cycling, effects, active_effects, selector_diagnostics) = match health {
        ResponseResult::Health {
            selected_animations,
            dynamic_gate_states,
            cycling_active,
            effect_schedulers,
            selector_diagnostics,
            ..
        } => (
            selected_animations.clone(),
            dynamic_gate_states.clone(),
            cycling_active.clone(),
            serde_json::to_value(effect_schedulers).unwrap_or(Value::Null),
            effect_schedulers
                .iter()
                .flat_map(|scheduler| scheduler.active.iter().map(|effect| effect.name.clone()))
                .collect(),
            selector_diagnostics.clone(),
        ),
        _ => (
            Vec::new(),
            Vec::new(),
            Vec::new(),
            Value::Null,
            Vec::new(),
            Vec::new(),
        ),
    };
    let selector_reason = selector_diagnostics
        .first()
        .map(|diagnostics| diagnostics.reason.clone())
        .unwrap_or_default();
    let selector_should_switch = selector_diagnostics
        .first()
        .is_some_and(|diagnostics| diagnostics.should_switch);
    let selector_scores = selector_diagnostics
        .first()
        .map(|diagnostics| diagnostics.scores.clone())
        .unwrap_or_default();
    let frame_age = status
        .last_frame_ns
        .map(|timestamp| now_ns.saturating_sub(timestamp) as f64 / 1_000_000_000.0);
    let callback_age = status
        .capture
        .last_callback_ns
        .map(|timestamp| now_ns.saturating_sub(timestamp) as f64 / 1_000_000_000.0);

    serde_json::to_value(DebugRow {
        elapsed_s: elapsed_seconds,
        source,
        mode: "dynamic",
        class: if status.features.silence {
            "idle"
        } else {
            "active"
        },
        animation: animations.first().cloned().unwrap_or_default(),
        base_animation: animations.first().cloned().unwrap_or_default(),
        effects: active_effects,
        music_active: !status.features.silence,
        music_gate: gates
            .first()
            .cloned()
            .unwrap_or_else(|| "unknown".to_owned()),
        fresh: status.frame.fresh,
        age_s: frame_age,
        callback_age_s: callback_age,
        sequence: status.frame.sequence,
        fft_count: status.processor.fft_count,
        status_count: status.audio_update_sequence,
        normalization_gain: status.processor.normalization_gain,
        input_rms: status.processor.input_rms,
        input_peak: status.processor.input_peak,
        clipping_fraction: status.processor.clipping_fraction,
        capture_callback_count: status.capture.callback_count,
        capture_callback_errors: status.capture.callback_errors,
        capture_overflowed_batches: status.capture.overflowed_batches,
        capture_dropped_samples: status.capture.dropped_samples,
        rms: status.frame.rms,
        beat: status.frame.beat,
        beat_strength: status.frame.beat_strength,
        bpm: status.features.bpm,
        bpm_confidence: status.features.bpm_confidence,
        energy: status.features.energy,
        volume: status.features.volume,
        energy_level: status.features.energy_level,
        bass: status.features.bass,
        brightness: status.features.brightness,
        onset_strength: status.features.onset_strength,
        dynamic_range: status.features.dynamic_range,
        bands: status.frame.bands,
        bass_energy: status.features.bass_energy,
        mid_energy: status.features.mid_energy,
        treble_energy: status.features.treble_energy,
        spectral_centroid: status.features.spectral_centroid,
        spectral_flux: status.features.spectral_flux,
        beat_confidence: status.features.beat_confidence,
        rolling_loudness: status.features.rolling_loudness,
        silence: status.features.silence,
        drop_detected: status.features.drop_detected,
        section_change: status.features.section_change,
        selector_reason,
        selector_should_switch,
        selector_scores,
        selector_diagnostics,
        selected_animations: animations,
        cycling_active: cycling,
        effect_schedulers: effects,
    })
    .expect("diagnostic row uses JSON-compatible engine values")
}

pub(crate) fn analyze(paths: &[PathBuf]) -> Result<String, String> {
    let mut rows = Vec::new();
    for path in paths {
        let metadata = fs::metadata(path)
            .map_err(|error| format!("read audio recording {}: {error}", path.display()))?;
        if metadata.len() > MAX_RECORDING_BYTES {
            return Err(format!(
                "audio recording {} exceeds the {MAX_RECORDING_BYTES}-byte limit",
                path.display()
            ));
        }
        let file = File::open(path)
            .map_err(|error| format!("open audio recording {}: {error}", path.display()))?;
        let reader = BufReader::new(file.take(MAX_RECORDING_BYTES + 1));
        for (index, line) in reader.lines().enumerate() {
            if rows.len() >= MAX_RECORDING_ROWS {
                return Err(format!(
                    "audio debug analysis exceeds the {MAX_RECORDING_ROWS}-row limit"
                ));
            }
            let line = line.map_err(|error| {
                format!(
                    "{}:{}: read audio debug row: {error}",
                    path.display(),
                    index + 1
                )
            })?;
            let value = serde_json::from_str::<Value>(&line).map_err(|_| {
                format!(
                    "{}:{}: invalid JSONL audio debug row",
                    path.display(),
                    index + 1
                )
            })?;
            if !value.is_object() {
                return Err(format!(
                    "{}:{}: audio debug row must be an object",
                    path.display(),
                    index + 1
                ));
            }
            rows.push(value);
        }
    }
    analyze_rows(&rows)
}

fn analyze_rows(rows: &[Value]) -> Result<String, String> {
    if rows.is_empty() {
        return Err("audio debug analysis requires at least one recorded row".to_owned());
    }
    let row_refs = rows.iter().collect::<Vec<_>>();
    let mut groups: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for row in rows {
        let label = row
            .get("label")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("-");
        groups.entry(label.to_owned()).or_default().push(row);
    }
    let classes = groups
        .iter()
        .map(|(label, values)| (label.clone(), classify(values)))
        .collect::<BTreeMap<_, _>>();
    let idle = groups
        .iter()
        .filter(|(label, _)| classes[*label] == "idle")
        .flat_map(|(_, values)| values.iter().copied())
        .collect::<Vec<_>>();
    let active = groups
        .iter()
        .filter(|(label, _)| classes[*label] == "active")
        .flat_map(|(_, values)| values.iter().copied())
        .collect::<Vec<_>>();
    let recommendations = if active.is_empty() {
        row_refs.as_slice()
    } else {
        active.as_slice()
    };
    let rms = unit_numbers(recommendations, "rms");
    let noise_rms = unit_numbers(
        if idle.is_empty() {
            row_refs.as_slice()
        } else {
            idle.as_slice()
        },
        "rms",
    );
    let energy = unit_numbers(recommendations, "energy");
    let loudness = unit_numbers(recommendations, "rolling_loudness");
    let gains = numbers(recommendations, "normalization_gain");
    let all_gains = numbers(&row_refs, "normalization_gain");
    let bpm_values = numbers(&row_refs, "bpm")
        .into_iter()
        .filter(|value| *value > 0.0)
        .collect::<Vec<_>>();
    let floor = if idle.is_empty() {
        percentile(&noise_rms, 20.0)
    } else {
        percentile(&noise_rms, 99.0)
    };
    let peak = numbers(&row_refs, "rms")
        .into_iter()
        .chain(numbers(&row_refs, "energy"))
        .chain(numbers(&row_refs, "rolling_loudness"))
        .fold(0.0, f64::max);
    let active_source = if !energy.is_empty() {
        &energy
    } else if !loudness.is_empty() {
        &loudness
    } else {
        &rms
    };
    let active_threshold = (floor * 1.8).max(0.01);
    let active_levels = active_source
        .iter()
        .copied()
        .filter(|value| *value >= active_threshold)
        .collect::<Vec<_>>();
    let active_level = percentile(
        if active_levels.is_empty() {
            active_source
        } else {
            &active_levels
        },
        80.0,
    );
    let noise_floor =
        ((floor * if idle.is_empty() { 1.6 } else { 2.0 }) + 0.004).clamp(0.003, 0.12);
    let target_level = (active_level * 1.25).max(0.24).clamp(0.24, 0.62);
    let idle_scale = (noise_floor / 0.015).max(0.5).clamp(0.5, 4.0);
    let stale_rate =
        row_refs.iter().filter(|row| row["fresh"] == false).count() as f64 / rows.len() as f64;
    let silence_rate = bool_rate(&row_refs, "silence");
    let beat_rate = bool_rate(&row_refs, "beat");
    let drop_rate = bool_rate(&row_refs, "drop_detected");
    let section_rate = bool_rate(&row_refs, "section_change");
    let labels = groups
        .keys()
        .filter(|value| value.as_str() != "-")
        .cloned()
        .collect::<Vec<_>>();
    let mut warnings = Vec::new();
    if stale_rate > 0.1 {
        warnings.push(format!(
            "{:.0}% stale frames; check audio device stability or callback load",
            stale_rate * 100.0
        ));
    }
    if silence_rate > 0.8 {
        warnings.push(format!(
            "{:.0}% silence frames; mic may be too quiet or noise floor too high",
            silence_rate * 100.0
        ));
    }
    if beat_rate < 0.005 && peak > 0.05 {
        warnings.push("almost no beats detected despite active audio".to_owned());
    }
    if !gains.is_empty() && percentile(&gains, 95.0) >= 4.5 * 0.95 {
        warnings.push(
            "active-capture normalization gain is often near maximum; input may still be quiet"
                .to_owned(),
        );
    }
    if !all_gains.is_empty() && percentile(&all_gains, 5.0) <= 0.35 * 1.05 {
        warnings.push("some captures have normalization gain near minimum; check for clipping or abrupt loud input".to_owned());
    }
    if drop_rate > 0.08 {
        warnings.push(format!(
            "{:.0}% drop frames; drop detection may be too sensitive for this setup",
            drop_rate * 100.0
        ));
    }

    let mut lines = vec![
        format!("Audio debug rows: {}", rows.len()),
        format!(
            "Labels: {}",
            if labels.is_empty() {
                "-".to_owned()
            } else {
                labels.join(", ")
            }
        ),
        format!(
            "Classified labels: active={} idle={} quiet={}",
            classes.values().filter(|class| **class == "active").count(),
            classes.values().filter(|class| **class == "idle").count(),
            classes.values().filter(|class| **class == "quiet").count()
        ),
        format!(
            "RMS floor/p80/peak: {floor:0.4} / {:0.4} / {peak:0.4}",
            percentile(&rms, 80.0)
        ),
        format!(
            "Beat/drop/section/silence rates: {:.1}% / {:.1}% / {:.1}% / {:.1}%",
            beat_rate * 100.0,
            drop_rate * 100.0,
            section_rate * 100.0,
            silence_rate * 100.0
        ),
        if bpm_values.is_empty() {
            "BPM median: -".to_owned()
        } else {
            format!("BPM median: {:0.0}", percentile(&bpm_values, 50.0))
        },
        "Per-label summary:".to_owned(),
    ];
    for (label, label_rows) in &groups {
        let label_rms = unit_numbers(label_rows, "rms");
        let label_gains = numbers(label_rows, "normalization_gain");
        let label_bpm = numbers(label_rows, "bpm")
            .into_iter()
            .filter(|value| *value > 0.0)
            .collect::<Vec<_>>();
        lines.push(format!(
            "  {label}: class={} rows={} rms20={:0.4} rms80={:0.4} gain50={:0.2} gain95={:0.2} silence={:.1}% beat={:.1}% drop={:.1}% section={:.1}% bass80={:0.3} mid80={:0.3} treble80={:0.3} flux80={:0.3} bpm50={:0.0}",
            classes[label], label_rows.len(), percentile(&label_rms, 20.0), percentile(&label_rms, 80.0), percentile(&label_gains, 50.0), percentile(&label_gains, 95.0), bool_rate(label_rows, "silence") * 100.0, bool_rate(label_rows, "beat") * 100.0, bool_rate(label_rows, "drop_detected") * 100.0, bool_rate(label_rows, "section_change") * 100.0, percentile(&numbers(label_rows, "bass_energy"), 80.0), percentile(&numbers(label_rows, "mid_energy"), 80.0), percentile(&numbers(label_rows, "treble_energy"), 80.0), percentile(&numbers(label_rows, "spectral_flux"), 80.0), percentile(&label_bpm, 50.0)
        ));
    }
    lines.extend([
        "Recommended shared AudioConfig values:".to_owned(),
        format!("  noise_floor: {noise_floor:0.4}"),
        format!("  target_level: {target_level:0.3}"),
        format!("  idle_threshold_scale: {idle_scale:0.2}"),
        if warnings.is_empty() {
            "Warnings: none".to_owned()
        } else {
            "Warnings:".to_owned()
        },
    ]);
    lines.extend(warnings.into_iter().map(|warning| format!("  - {warning}")));
    Ok(lines.join("\n"))
}

fn classify(rows: &[&Value]) -> &'static str {
    let rms = unit_numbers(rows, "rms");
    let silence = bool_rate(rows, "silence");
    let rms_p80 = percentile(&rms, 80.0);
    if silence >= 0.8 && rms_p80 < 0.01 {
        "idle"
    } else if silence <= 0.5 && rms_p80 >= 0.02 {
        "active"
    } else {
        "quiet"
    }
}

fn numbers(rows: &[&Value], key: &str) -> Vec<f64> {
    rows.iter()
        .filter_map(|row| row.get(key).and_then(Value::as_f64))
        .filter(|value| value.is_finite())
        .collect()
}

fn unit_numbers(rows: &[&Value], key: &str) -> Vec<f64> {
    numbers(rows, key)
        .into_iter()
        .map(|value| value.clamp(0.0, 1.0))
        .collect()
}

fn bool_rate(rows: &[&Value], key: &str) -> f64 {
    if rows.is_empty() {
        0.0
    } else {
        rows.iter()
            .filter(|row| row.get(key) == Some(&Value::Bool(true)))
            .count() as f64
            / rows.len() as f64
    }
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
    let rank = (ordered.len() - 1) as f64 * percentile_value.clamp(0.0, 100.0) / 100.0;
    let lower = rank as usize;
    let upper = (lower + 1).min(ordered.len() - 1);
    ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower as f64)
}

#[cfg(test)]
mod tests {
    use super::{Recorder, analyze, analyze_rows, debug_row};
    use lumistripe_engine_protocol::{EngineAudioStatus, ResponseResult};
    use serde_json::json;

    #[test]
    fn debug_rows_keep_engine_audio_and_selector_telemetry() {
        let status = EngineAudioStatus::default();
        let health: ResponseResult = serde_json::from_value(json!({
            "type": "health",
            "status": "ready",
            "configured": true,
            "output_count": 1,
            "pixel_counts": [8],
            "frames_rendered": 0,
            "last_sequence": null,
            "dropped_frames": 0,
            "failed_frames": 0,
            "selected_animations": ["aurora_wave"],
            "cycling_active": [true],
            "dynamic_gate_states": ["music"],
            "selector_diagnostics": [{
                "output_index": 0,
                "current_animation": "aurora_wave",
                "candidate_animation": "aurora_wave",
                "selected_animation": "aurora_wave",
                "should_switch": false,
                "reason": "best_is_current",
                "scores": [{
                    "animation": "aurora_wave",
                    "score": 1.25,
                    "reasons": ["weight=0.70", "energy=1.00"]
                }],
                "elapsed_ns": 1000000000,
                "min_duration_remaining_ns": 11000000000_u64,
                "max_duration_remaining_ns": 44000000000_u64,
                "switch_cooldown_remaining_ns": 7000000000_u64,
                "drop_cooldown_remaining_ns": 0,
                "recent_names": ["shockwave"]
            }],
            "effect_schedulers": [{
                "active": [{
                    "name": "beat_wave",
                    "category": "rhythmic",
                    "strength": 0.5,
                    "elapsed_ns": 100,
                    "remaining_ns": 200,
                    "progress": 0.3
                }],
                "rhythmic_cooldown_remaining_ns": 0,
                "accent_cooldown_remaining_ns": 0,
                "rhythmic_result": "triggered",
                "accent_result": "not-evaluated",
                "drop_accent_status": "idle",
                "late_ticks_dropped": 0,
                "late_ticks_coalesced": 0,
                "beat_lateness_p95_ns": 0
            }],
            "last_error": null,
            "output_rate": 0.0,
            "frame_latency_ms": null,
            "queue_latency_ms": null,
            "transmission_ms": null,
            "frame_latency_p50_ms": null,
            "frame_latency_p95_ms": null,
            "frame_latency_p99_ms": null,
            "sequential_skew_ms": null,
            "last_successful_update_epoch_ms": null
        }))
        .unwrap();
        let row = debug_row(&status, &health, 1.0, "USB Mic", 2_000_000_000);
        assert_eq!(row["source"], "USB Mic");
        assert_eq!(row["animation"], "aurora_wave");
        assert_eq!(row["selected_animations"][0], "aurora_wave");
        assert_eq!(row["effects"][0], "beat_wave");
        assert_eq!(row["selector_reason"], "best_is_current");
        assert_eq!(row["selector_should_switch"], false);
        assert_eq!(row["selector_scores"][0]["reasons"][0], "weight=0.70");
    }

    #[test]
    fn analyzer_reports_recommendations_and_warnings_for_json_rows() {
        let rows = (0..20)
            .map(|index| {
                let active = index >= 10;
                json!({
                    "label": if active { "music" } else { "quiet" },
                    "fresh": true,
                    "rms": if active { 0.1 } else { 0.002 },
                    "energy": if active { 0.3 } else { 0.0 },
                    "rolling_loudness": if active { 0.2 } else { 0.0 },
                    "normalization_gain": 1.0,
                    "beat": active,
                    "silence": !active,
                    "drop_detected": false,
                    "section_change": false,
                    "bpm": if active { 120.0 } else { 0.0 },
                    "bass_energy": 0.5,
                    "mid_energy": 0.3,
                    "treble_energy": 0.2,
                    "spectral_flux": 0.1
                })
            })
            .collect::<Vec<_>>();
        let summary = analyze_rows(&rows).unwrap();
        assert!(summary.contains("Audio debug rows: 20"));
        assert!(summary.contains("Recommended shared AudioConfig values:"));
        assert!(summary.contains("music: class=active"));
    }

    #[test]
    fn recorder_writes_analyzable_jsonl_with_a_label() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("nested/audio.jsonl");
        let mut recorder = Recorder::create(&path, Some("demo session")).unwrap();
        recorder
            .write(json!({
                "fresh": true,
                "rms": 0.1,
                "energy": 0.3,
                "rolling_loudness": 0.2,
                "normalization_gain": 1.0,
                "beat": true,
                "silence": false,
                "drop_detected": false,
                "section_change": false,
                "bpm": 120.0
            }))
            .unwrap();
        drop(recorder);

        let report = analyze(&[path]).unwrap();
        assert!(report.contains("Labels: demo session"));
        assert!(report.contains("Audio debug rows: 1"));
    }
}
