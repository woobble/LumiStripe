use lumistripe_engine_audio::{
    AudioConfig, AudioProcessor, MusicStructurePhase, MusicStructureTracker, StructureUpdate,
};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

const FIXTURE_DIRECTORY: &str = "../../tests/fixtures/audio-reference-v1";

#[test]
fn rust_dsp_matches_fixed_python_native_c_reference_pcm() {
    let fixture_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(FIXTURE_DIRECTORY);
    let reference: Value = serde_json::from_slice(
        &fs::read(fixture_dir.join("reference.json")).expect("reference fixture exists"),
    )
    .expect("reference fixture is valid JSON");
    assert_eq!(reference["schema_version"], 1);
    let absolute_tolerance = number(&reference["tolerance"]["absolute"]);
    let relative_tolerance = number(&reference["tolerance"]["relative"]);
    let beat_time_tolerance = number(&reference["tolerance"]["beat_timestamps_seconds"]);

    for fixture in reference["fixtures"].as_array().expect("fixture list") {
        compare_fixture(
            fixture,
            &fixture_dir,
            absolute_tolerance,
            relative_tolerance,
            beat_time_tolerance,
        );
    }
}

fn compare_fixture(
    fixture: &Value,
    fixture_dir: &Path,
    absolute_tolerance: f64,
    relative_tolerance: f64,
    beat_time_tolerance: f64,
) {
    let name = fixture["name"].as_str().expect("fixture name");
    let pcm_path = fixture_dir.join(fixture["pcm_file"].as_str().expect("PCM path"));
    let pcm = fs::read(&pcm_path).expect("PCM fixture exists");
    assert_eq!(
        pcm.len(),
        fixture["sample_count"].as_u64().expect("sample count") as usize * 4,
        "{name}: PCM size"
    );
    let samples = pcm
        .chunks_exact(4)
        .map(|bytes| f32::from_le_bytes(bytes.try_into().expect("four byte sample")))
        .collect::<Vec<_>>();
    let feed_size = fixture["feed_size"].as_u64().expect("feed size") as usize;
    let sample_rate = fixture["sample_rate"].as_f64().expect("sample rate");
    let config: AudioConfig = serde_json::from_value(fixture["config"].clone())
        .unwrap_or_else(|error| panic!("{name}: invalid audio config: {error}"));
    let mut processor = AudioProcessor::new(config, sample_rate as f32)
        .unwrap_or_else(|error| panic!("{name}: processor initialization failed: {error}"));

    let expected_frames = fixture["frames"].as_array().expect("frame list");
    let expected_structures = fixture["structures"].as_array().expect("structure list");
    let mut structure_tracker = MusicStructureTracker::new();
    let mut structure_index = 0;
    let mut frame_index = 0;
    for offset in (0..samples.len()).step_by(feed_size) {
        let end = (offset + feed_size).min(samples.len());
        let capture_end = end as f64 / sample_rate;
        processor
            .feed_samples_with_observer(&samples[offset..end], capture_end, |frame, features| {
                let update = structure_tracker
                    .update(&features, frame.timestamp)
                    .unwrap_or_else(|error| panic!("{name}: structure tracker failed: {error}"));
                let expected = expected_structures.get(structure_index).unwrap_or_else(|| {
                    panic!("{name}: unexpected structure update {structure_index}")
                });
                compare_structure(expected, &update, name, structure_index);
                structure_index += 1;
            })
            .unwrap_or_else(|error| panic!("{name}: feeding PCM failed: {error}"));
        let actual = processor.frame();
        if actual.sequence > frame_index as u64 {
            let expected = expected_frames
                .get(frame_index)
                .unwrap_or_else(|| panic!("{name}: unexpected frame {}", actual.sequence));
            compare_frame(
                expected,
                &actual,
                &processor.features(),
                absolute_tolerance,
                relative_tolerance,
                name,
                frame_index,
            );
            frame_index += 1;
        }
    }
    assert_eq!(frame_index, expected_frames.len(), "{name}: frame count");
    assert_eq!(
        structure_index,
        expected_structures.len(),
        "{name}: structure count"
    );

    let expected_stats = &fixture["final_stats"];
    let stats = processor.stats();
    assert_eq!(
        stats.feed_count,
        expected_stats["feed_count"].as_u64().unwrap(),
        "{name}"
    );
    assert_eq!(
        stats.samples_seen,
        expected_stats["samples_seen"].as_u64().unwrap(),
        "{name}"
    );
    assert_eq!(
        stats.fft_count,
        expected_stats["fft_count"].as_u64().unwrap(),
        "{name}"
    );
    assert_eq!(
        stats.microphone_gate_open,
        expected_stats["microphone_gate_open"].as_bool().unwrap(),
        "{name}"
    );
    for (field, actual) in [
        ("sample_abs_sum", stats.sample_abs_sum),
        ("normalization_gain", stats.normalization_gain),
        ("input_rms", stats.input_rms),
        ("program_loudness", stats.program_loudness),
        ("musical_impact", stats.musical_impact),
        ("input_peak", stats.input_peak),
        ("clipping_fraction", stats.clipping_fraction),
    ] {
        assert_close(
            number(&expected_stats[field]),
            actual as f64,
            absolute_tolerance,
            relative_tolerance,
            &format!("{name}: final_stats.{field}"),
        );
    }

    let expected_events = fixture["beat_events"].as_array().expect("event list");
    let events = processor.drain_beat_events();
    assert_eq!(
        events.len(),
        expected_events.len(),
        "{name}: beat event count"
    );
    for (index, (event, expected)) in events.iter().zip(expected_events).enumerate() {
        assert_eq!(
            event.sequence,
            expected["sequence"].as_u64().unwrap(),
            "{name}: beat event {index} sequence"
        );
        assert_eq!(
            event.sample_position,
            expected["sample_position"].as_u64().unwrap(),
            "{name}: beat event {index} sample position"
        );
        assert_close(
            number(&expected["timestamp"]),
            event.timestamp,
            beat_time_tolerance,
            0.0,
            &format!("{name}: beat event {index} timestamp"),
        );
        assert_close(
            number(&expected["strength"]),
            event.strength as f64,
            absolute_tolerance,
            relative_tolerance,
            &format!("{name}: beat event {index} strength"),
        );
    }
}

fn compare_structure(expected: &Value, actual: &StructureUpdate, fixture_name: &str, index: usize) {
    let phase = match actual.state.phase {
        MusicStructurePhase::Idle => "idle",
        MusicStructurePhase::Breakdown => "breakdown",
        MusicStructurePhase::Groove => "groove",
        MusicStructurePhase::Buildup => "buildup",
        MusicStructurePhase::Drop => "drop",
    };
    let prefix = format!("{fixture_name}: structure {index}");
    assert_eq!(phase, expected["phase"].as_str().unwrap(), "{prefix}.phase");
    assert_eq!(
        actual.state.drop_event,
        expected["drop_event"].as_bool().unwrap(),
        "{prefix}.drop_event"
    );
    assert_eq!(
        actual.phase_changed,
        expected["phase_changed"].as_bool().unwrap(),
        "{prefix}.phase_changed"
    );
    assert_close(
        number(&expected["confidence"]),
        actual.state.confidence,
        0.003,
        0.003,
        &format!("{prefix}.confidence"),
    );
    assert_close(
        number(&expected["buildup_progress"]),
        actual.state.buildup_progress,
        0.003,
        0.003,
        &format!("{prefix}.buildup_progress"),
    );
}

fn compare_frame(
    expected: &Value,
    actual: &lumistripe_engine_audio::AudioFrame,
    features: &lumistripe_engine_audio::MusicFeatures,
    absolute_tolerance: f64,
    relative_tolerance: f64,
    fixture_name: &str,
    index: usize,
) {
    let frame = &expected["frame"];
    let prefix = format!("{fixture_name}: frame {index}");
    for (field, actual) in [("rms", actual.rms), ("beat_strength", actual.beat_strength)] {
        assert_close(
            number(&frame[field]),
            actual as f64,
            absolute_tolerance,
            relative_tolerance,
            &format!("{prefix}.{field}"),
        );
    }
    assert_eq!(
        actual.beat,
        frame["beat"].as_bool().unwrap(),
        "{prefix}.beat"
    );
    assert_eq!(
        actual.sequence,
        frame["sequence"].as_u64().unwrap(),
        "{prefix}.sequence"
    );
    assert_eq!(
        actual.fresh,
        frame["fresh"].as_bool().unwrap(),
        "{prefix}.fresh"
    );
    assert_close(
        number(&frame["timestamp"]),
        actual.timestamp,
        1e-9,
        0.0,
        &format!("{prefix}.timestamp"),
    );
    compare_array(
        &frame["bands"],
        &actual.bands,
        absolute_tolerance,
        relative_tolerance,
        &format!("{prefix}.bands"),
    );

    let expected_features = &expected["features"];
    for (field, value) in [
        ("bpm", features.bpm),
        ("bpm_confidence", features.bpm_confidence),
        ("energy", features.energy),
        ("volume", features.volume),
        ("energy_level", features.energy_level),
        ("bass", features.bass),
        ("brightness", features.brightness),
        ("onset_strength", features.onset_strength),
        ("dynamic_range", features.dynamic_range),
        ("beat_strength", features.beat_strength),
        ("bass_energy", features.bass_energy),
        ("mid_energy", features.mid_energy),
        ("treble_energy", features.treble_energy),
        ("spectral_centroid", features.spectral_centroid),
        ("spectral_flux", features.spectral_flux),
        ("beat_confidence", features.beat_confidence),
        ("rolling_loudness", features.rolling_loudness),
        ("program_loudness", features.program_loudness),
        ("musical_impact", features.musical_impact),
    ] {
        assert_close(
            number(&expected_features[field]),
            value as f64,
            absolute_tolerance,
            relative_tolerance,
            &format!("{prefix}.features.{field}"),
        );
    }
    for field in ["beat", "silence", "drop_detected", "section_change"] {
        assert_eq!(
            features_bool(features, field),
            expected_features[field].as_bool().unwrap(),
            "{prefix}.features.{field}"
        );
    }
    compare_array(
        &expected_features["bands"],
        &features.bands,
        absolute_tolerance,
        relative_tolerance,
        &format!("{prefix}.features.bands"),
    );
}

fn features_bool(features: &lumistripe_engine_audio::MusicFeatures, field: &str) -> bool {
    match field {
        "beat" => features.beat,
        "silence" => features.silence,
        "drop_detected" => features.drop_detected,
        "section_change" => features.section_change,
        _ => unreachable!("test field is known"),
    }
}

fn compare_array<const N: usize>(
    expected: &Value,
    actual: &[f32; N],
    absolute_tolerance: f64,
    relative_tolerance: f64,
    context: &str,
) {
    let expected = expected.as_array().expect("expected array");
    assert_eq!(expected.len(), N, "{context}: length");
    for (index, (expected, actual)) in expected.iter().zip(actual.iter()).enumerate() {
        assert_close(
            number(expected),
            *actual as f64,
            absolute_tolerance,
            relative_tolerance,
            &format!("{context}[{index}]"),
        );
    }
}

fn number(value: &Value) -> f64 {
    value.as_f64().expect("numeric reference value")
}

fn assert_close(
    expected: f64,
    actual: f64,
    absolute_tolerance: f64,
    relative_tolerance: f64,
    context: &str,
) {
    let tolerance = absolute_tolerance.max(expected.abs() * relative_tolerance);
    assert!(
        (expected - actual).abs() <= tolerance,
        "{context}: expected {expected:.10}, got {actual:.10}, tolerance {tolerance:.3e}"
    );
}
