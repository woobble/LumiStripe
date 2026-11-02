use lumistripe_engine_audio::{
    AUDIO_HOP_SIZE, AUDIO_SAMPLE_RATE, AudioConfig, AudioProcessor, MusicStructureTracker,
};

const RUN_SECONDS: usize = 180;
const RECONFIGURE_SECONDS: usize = 3;

#[test]
fn three_minutes_of_music_stays_finite_ordered_and_bounded_across_reconfiguration() {
    let total_samples = RUN_SECONDS * AUDIO_SAMPLE_RATE as usize;
    let reconfigure_samples = RECONFIGURE_SECONDS * AUDIO_SAMPLE_RATE as usize;
    let mut processor = AudioProcessor::new(AudioConfig::default(), AUDIO_SAMPLE_RATE).unwrap();
    let mut tracker = MusicStructureTracker::new();
    let mut block = [0.0_f32; AUDIO_HOP_SIZE];
    let mut frames_seen = 0_usize;
    let mut beat_events_seen = 0_usize;
    let mut last_frame_timestamp = 0.0_f64;
    let mut last_event_timestamp = 0.0_f64;
    let mut previous_config_segment = 0;

    for offset in (0..total_samples).step_by(AUDIO_HOP_SIZE) {
        let segment = offset / reconfigure_samples;
        if segment != previous_config_segment {
            let config = if segment % 2 == 0 {
                AudioConfig::default()
            } else {
                AudioConfig::raw()
            };
            processor = AudioProcessor::new(config, AUDIO_SAMPLE_RATE).unwrap();
            tracker = MusicStructureTracker::new();
            previous_config_segment = segment;
        }

        let end = (offset + AUDIO_HOP_SIZE).min(total_samples);
        let active_samples = end - offset;
        for (index, sample) in block[..active_samples].iter_mut().enumerate() {
            let position = offset + index;
            let seconds = position as f32 / AUDIO_SAMPLE_RATE;
            let envelope = match seconds as usize {
                60..=79 => 0.04 + ((seconds - 60.0) / 20.0) * 0.35,
                80..=119 => 0.38,
                120..=139 => 0.025,
                140.. => 0.42,
                _ => 0.14,
            };
            let beat_sample = position % (AUDIO_SAMPLE_RATE as usize / 2);
            let beat_seconds = beat_sample as f32 / AUDIO_SAMPLE_RATE;
            let kick = if beat_sample < AUDIO_SAMPLE_RATE as usize / 8 {
                0.8 * (-beat_seconds / 0.045).exp()
                    * (std::f32::consts::TAU * 52.0 * beat_seconds).sin()
            } else {
                0.0
            };
            *sample = envelope
                * (0.7 * (std::f32::consts::TAU * 80.0 * seconds).sin()
                    + 0.18 * (std::f32::consts::TAU * 1_600.0 * seconds).sin())
                + kick;
        }

        let capture_end = end as f64 / AUDIO_SAMPLE_RATE as f64;
        processor
            .feed_samples_with_observer(&block[..active_samples], capture_end, |frame, features| {
                assert!(frame.timestamp >= last_frame_timestamp);
                assert!(frame.rms.is_finite());
                assert!(frame.bands.iter().all(|band| band.is_finite()));
                assert!(features.bpm.is_finite());
                assert!(features.bpm_confidence.is_finite());
                assert!(features.spectral_centroid.is_finite());
                assert!(features.spectral_flux.is_finite());
                let update = tracker.update(&features, frame.timestamp).unwrap();
                assert!((0.0..=1.0).contains(&update.state.confidence));
                assert!((0.0..=1.0).contains(&update.state.buildup_progress));
                last_frame_timestamp = frame.timestamp;
                frames_seen += 1;
            })
            .unwrap();

        let events = processor.drain_beat_events();
        for event in events {
            assert!(event.timestamp >= last_event_timestamp);
            assert!(event.strength.is_finite());
            last_event_timestamp = event.timestamp;
            beat_events_seen += 1;
        }
    }

    let last_segment_boundary = ((total_samples - 1) / reconfigure_samples) * reconfigure_samples;
    let last_segment_start = last_segment_boundary.div_ceil(AUDIO_HOP_SIZE) * AUDIO_HOP_SIZE;
    let last_segment_samples = total_samples - last_segment_start;
    assert_eq!(processor.stats().samples_seen, last_segment_samples as u64);
    assert!(
        frames_seen > 10_000,
        "processed {frames_seen} analysis frames"
    );
    assert!(
        beat_events_seen > 100,
        "detected {beat_events_seen} beat events"
    );
    assert!(last_frame_timestamp >= RUN_SECONDS as f64 - 4.0);
}
