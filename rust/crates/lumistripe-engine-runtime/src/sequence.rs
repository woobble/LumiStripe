use lumistripe_engine_protocol::{
    RenderAnimationSequence, RenderBeatClockState, RenderSequencePlaybackState,
};

const BEATS_PER_BAR: u64 = 4;
const NS_PER_SECOND: f64 = 1_000_000_000.0;

#[derive(Clone, Copy)]
struct Timeline {
    bar_index: u64,
    bpm: f64,
    confidence: f64,
    next_bar_at_s: f64,
}

/// Per-output sequence progress, advanced only by the Rust frame scheduler.
#[derive(Clone, Debug, Default)]
pub(crate) struct SequenceCoordinator {
    anchor_beat_index: Option<u64>,
    session_active: bool,
    completed: bool,
    clock_valid: bool,
    current_step_index: Option<usize>,
    remaining_bars: Option<u32>,
    last_bar_index: Option<u64>,
    target_bar_index: Option<u64>,
    last_boundary_at_s: Option<f64>,
    next_transition_at_s: Option<f64>,
    pending_section_change_at_s: Option<f64>,
    last_now_s: Option<f64>,
    waiting_for_clock: bool,
}

impl SequenceCoordinator {
    pub(crate) fn update(
        &mut self,
        sequence: Option<&RenderAnimationSequence>,
        clock: RenderBeatClockState,
        now_ns: u64,
        sync_offset_ns: i64,
        confidence_threshold: f64,
        subdivisions_per_beat: usize,
        music_active: bool,
        section_change_at_ns: Option<u64>,
    ) -> Option<usize> {
        let Some(sequence) = sequence else {
            self.reset_session();
            return None;
        };
        if !music_active {
            self.reset_session();
            return None;
        }
        self.session_active = true;
        if self.completed {
            self.waiting_for_clock = false;
            return None;
        }

        let now_s = now_ns as f64 / NS_PER_SECOND;
        let sync_offset_s = sync_offset_ns as f64 / NS_PER_SECOND;
        let Some(timeline) = self.timeline(clock) else {
            self.clock_valid = false;
            self.next_transition_at_s = None;
            self.last_now_s = Some(now_s);
            self.waiting_for_clock = true;
            return None;
        };
        if timeline.confidence < confidence_threshold {
            self.clock_valid = false;
            self.next_transition_at_s = None;
            self.pending_section_change_at_s = None;
            self.last_now_s = Some(now_s);
            self.waiting_for_clock = true;
            return None;
        }

        if self.last_now_s.is_some_and(|last| now_s < last) {
            self.clock_valid = false;
        }
        if !self.clock_valid {
            self.clock_valid = true;
            self.reanchor(timeline, now_s, sync_offset_s);
            self.last_now_s = Some(now_s);
            self.waiting_for_clock = false;
            return None;
        }

        let bar_index = timeline.bar_index;
        if self.current_step_index.is_none() {
            let mut target = self.target_bar_index.unwrap_or_else(|| {
                self.schedule_start(timeline, now_s, sync_offset_s);
                self.target_bar_index.unwrap_or(bar_index.saturating_add(1))
            });
            if bar_index > target {
                self.schedule_start(timeline, now_s, sync_offset_s);
                target = self.target_bar_index.unwrap_or(bar_index.saturating_add(1));
            }
            let boundary_at = boundary_at_bar(timeline, target);
            let due_at = boundary_at + sync_offset_s;
            self.next_transition_at_s = Some(due_at);
            if now_s >= due_at {
                if now_s - due_at <= maximum_lateness_s(timeline.bpm, subdivisions_per_beat) {
                    self.start_step(sequence, target, boundary_at);
                    self.last_now_s = Some(now_s);
                    self.waiting_for_clock = false;
                    return Some(0);
                }
                self.schedule_start(timeline, now_s, sync_offset_s);
            }
            self.last_now_s = Some(now_s);
            self.waiting_for_clock = false;
            return None;
        }

        let Some(mut remaining_bars) = self.remaining_bars else {
            self.reset_session();
            return None;
        };
        let Some(mut last_bar_index) = self.last_bar_index else {
            self.reset_session();
            return None;
        };
        let Some(mut target_bar_index) = self.target_bar_index else {
            self.reset_session();
            return None;
        };
        if bar_index < last_bar_index {
            let before_activation_bar = bar_index == last_bar_index.saturating_sub(1)
                && self
                    .last_boundary_at_s
                    .is_some_and(|boundary| now_s < boundary);
            if !before_activation_bar {
                self.reanchor(timeline, now_s, sync_offset_s);
            }
        } else if bar_index > last_bar_index {
            let bar_delta = bar_index - last_bar_index;
            if bar_delta == 1 {
                remaining_bars = remaining_bars.saturating_sub(1);
                last_bar_index = bar_index;
                if remaining_bars == 0 {
                    target_bar_index = bar_index;
                }
                self.remaining_bars = Some(remaining_bars);
                self.last_bar_index = Some(last_bar_index);
                self.target_bar_index = Some(target_bar_index);
            } else {
                last_bar_index = bar_index;
                remaining_bars = remaining_bars.max(1);
                target_bar_index = bar_index.saturating_add(u64::from(remaining_bars));
                self.last_boundary_at_s = Some(boundary_at_bar(timeline, bar_index));
                self.remaining_bars = Some(remaining_bars);
                self.last_bar_index = Some(last_bar_index);
                self.target_bar_index = Some(target_bar_index);
            }
        }

        let target = self.target_bar_index.unwrap_or(target_bar_index);
        let boundary_at = boundary_at_bar(timeline, target);
        let due_at = boundary_at + sync_offset_s;
        self.next_transition_at_s = Some(due_at);
        if let Some(section_change_at_ns) = section_change_at_ns
            && self.remaining_bars == Some(0)
            && bar_index >= target
        {
            self.pending_section_change_at_s = Some(section_change_at_ns as f64 / NS_PER_SECOND);
        }
        if let Some(section_change_at_s) = self.pending_section_change_at_s {
            let section_due_at = section_change_at_s + sync_offset_s;
            if now_s >= section_due_at {
                if now_s - section_due_at <= maximum_lateness_s(timeline.bpm, subdivisions_per_beat)
                {
                    let next_index = self.advance_step(sequence, bar_index, section_change_at_s);
                    self.last_now_s = Some(now_s);
                    self.waiting_for_clock = false;
                    return next_index;
                }
                self.pending_section_change_at_s = None;
            } else {
                self.next_transition_at_s = Some(due_at.min(section_due_at));
            }
        }
        if now_s >= due_at {
            if now_s - due_at <= maximum_lateness_s(timeline.bpm, subdivisions_per_beat) {
                let next_index = self.advance_step(sequence, target, boundary_at);
                self.last_now_s = Some(now_s);
                self.waiting_for_clock = false;
                return next_index;
            }
            self.last_bar_index = Some(bar_index);
            self.remaining_bars = Some(1);
            self.target_bar_index = Some(bar_index.saturating_add(1));
            let (next_target, next_boundary) = next_future_bar_boundary(
                timeline,
                self.target_bar_index.unwrap_or(bar_index.saturating_add(1)),
                now_s,
                sync_offset_s,
            );
            self.target_bar_index = Some(next_target);
            self.remaining_bars = Some(
                next_target
                    .saturating_sub(bar_index)
                    .min(u64::from(u32::MAX)) as u32,
            );
            self.next_transition_at_s = Some(next_boundary + sync_offset_s);
        }
        if let Some(section_change_at_s) = self.pending_section_change_at_s {
            let section_due_at = section_change_at_s + sync_offset_s;
            if let Some(next_transition_at_s) = &mut self.next_transition_at_s {
                *next_transition_at_s = next_transition_at_s.min(section_due_at);
            }
        }
        self.last_now_s = Some(now_s);
        self.waiting_for_clock = false;
        None
    }

    pub(crate) fn controls_playback(&self) -> bool {
        self.session_active && !self.completed
    }

    pub(crate) fn playback_state(
        &self,
        sequence: Option<&RenderAnimationSequence>,
    ) -> RenderSequencePlaybackState {
        let step = sequence.and_then(|sequence| {
            self.current_step_index
                .and_then(|index| sequence.steps.get(index))
        });
        RenderSequencePlaybackState {
            sequence_id: sequence.map(|sequence| sequence.id.clone()),
            active: step.is_some() && !self.completed,
            waiting_for_clock: self.waiting_for_clock,
            current_step_index: self.current_step_index,
            animation: step.map(|step| step.animation.clone()),
            remaining_bars: self.remaining_bars,
            next_transition_at_ns: self.next_transition_at_s.map(seconds_to_ns),
        }
    }

    fn timeline(&mut self, clock: RenderBeatClockState) -> Option<Timeline> {
        if clock.beat_index == 0 || !clock.bpm.is_finite() || clock.bpm <= 0.0 {
            return None;
        }
        let anchor = *self.anchor_beat_index.get_or_insert(clock.beat_index);
        let timeline_beat = clock.beat_index.saturating_sub(anchor).saturating_add(1);
        let zero_based_beat = timeline_beat.saturating_sub(1);
        let next_beat_at_s = clock.next_beat_at_ns? as f64 / NS_PER_SECOND;
        let beat_period_s = 60.0 / clock.bpm;
        let beats_to_bar = BEATS_PER_BAR - zero_based_beat % BEATS_PER_BAR;
        let next_bar_at_s = next_beat_at_s + (beats_to_bar - 1) as f64 * beat_period_s;
        Some(Timeline {
            bar_index: zero_based_beat / BEATS_PER_BAR + 1,
            bpm: clock.bpm,
            confidence: clock.confidence,
            next_bar_at_s,
        })
    }

    fn reset_session(&mut self) {
        self.session_active = false;
        self.completed = false;
        self.clock_valid = false;
        self.current_step_index = None;
        self.remaining_bars = None;
        self.last_bar_index = None;
        self.target_bar_index = None;
        self.last_boundary_at_s = None;
        self.next_transition_at_s = None;
        self.pending_section_change_at_s = None;
        self.last_now_s = None;
        self.waiting_for_clock = false;
    }

    fn reanchor(&mut self, timeline: Timeline, now_s: f64, sync_offset_s: f64) {
        self.pending_section_change_at_s = None;
        let bar_index = timeline.bar_index;
        self.last_bar_index = Some(bar_index);
        self.last_boundary_at_s = Some(boundary_at_bar(timeline, bar_index));
        if self.current_step_index.is_none() {
            self.schedule_start(timeline, now_s, sync_offset_s);
            return;
        }
        let remaining_bars = self.remaining_bars.unwrap_or_default().max(1);
        let target = bar_index.saturating_add(u64::from(remaining_bars));
        let (target, boundary) = next_future_bar_boundary(timeline, target, now_s, sync_offset_s);
        self.remaining_bars =
            Some(target.saturating_sub(bar_index).min(u64::from(u32::MAX)) as u32);
        self.target_bar_index = Some(target);
        self.next_transition_at_s = Some(boundary + sync_offset_s);
        self.clock_valid = true;
    }

    fn schedule_start(&mut self, timeline: Timeline, now_s: f64, sync_offset_s: f64) {
        let bar_index = timeline.bar_index;
        self.last_bar_index = Some(bar_index);
        let target = bar_index.saturating_add(1);
        let (target, boundary) = next_future_bar_boundary(timeline, target, now_s, sync_offset_s);
        self.target_bar_index = Some(target);
        self.next_transition_at_s = Some(boundary + sync_offset_s);
    }

    fn start_step(
        &mut self,
        sequence: &RenderAnimationSequence,
        bar_index: u64,
        boundary_at_s: f64,
    ) {
        self.pending_section_change_at_s = None;
        self.current_step_index = Some(0);
        self.remaining_bars = Some(sequence.steps[0].duration_bars);
        self.last_bar_index = Some(bar_index);
        self.target_bar_index =
            Some(bar_index.saturating_add(u64::from(sequence.steps[0].duration_bars)));
        self.last_boundary_at_s = Some(boundary_at_s);
    }

    fn advance_step(
        &mut self,
        sequence: &RenderAnimationSequence,
        boundary_bar_index: u64,
        boundary_at_s: f64,
    ) -> Option<usize> {
        self.pending_section_change_at_s = None;
        let current = self.current_step_index?;
        let mut next = current + 1;
        if next >= sequence.steps.len() {
            if !sequence.repeat {
                self.completed = true;
                self.remaining_bars = Some(0);
                self.target_bar_index = Some(boundary_bar_index);
                self.next_transition_at_s = None;
                return None;
            }
            next = 0;
        }
        self.current_step_index = Some(next);
        self.remaining_bars = Some(sequence.steps[next].duration_bars);
        self.last_bar_index = Some(boundary_bar_index);
        self.target_bar_index =
            Some(boundary_bar_index.saturating_add(u64::from(sequence.steps[next].duration_bars)));
        self.last_boundary_at_s = Some(boundary_at_s);
        Some(next)
    }
}

fn boundary_at_bar(timeline: Timeline, target_bar_index: u64) -> f64 {
    let period = 60.0 / timeline.bpm;
    let bar_duration = BEATS_PER_BAR as f64 * period;
    let current_bar_start = timeline.next_bar_at_s - bar_duration;
    current_bar_start + target_bar_index.saturating_sub(timeline.bar_index) as f64 * bar_duration
}

fn next_future_bar_boundary(
    timeline: Timeline,
    mut target_bar_index: u64,
    now_s: f64,
    sync_offset_s: f64,
) -> (u64, f64) {
    let mut boundary = boundary_at_bar(timeline, target_bar_index);
    let due_at = boundary + sync_offset_s;
    if due_at <= now_s {
        let bar_duration = 60.0 / timeline.bpm * BEATS_PER_BAR as f64;
        let skipped = ((now_s - due_at) / bar_duration).floor() as u64 + 1;
        target_bar_index = target_bar_index.saturating_add(skipped);
        boundary += skipped as f64 * bar_duration;
    }
    (target_bar_index, boundary)
}

fn maximum_lateness_s(bpm: f64, subdivisions_per_beat: usize) -> f64 {
    let subdivisions = subdivisions_per_beat.max(1) as f64;
    (60.0 / bpm / subdivisions / 2.0).min(0.05)
}

fn seconds_to_ns(seconds: f64) -> u64 {
    (seconds.max(0.0) * NS_PER_SECOND)
        .round()
        .min(u64::MAX as f64) as u64
}

#[cfg(test)]
mod tests {
    use super::SequenceCoordinator;
    use lumistripe_engine_protocol::{
        RenderAnimationSequence, RenderAnimationSequenceStep, RenderBeatClockState,
    };

    fn sequence(repeat: bool, durations: &[u32]) -> RenderAnimationSequence {
        RenderAnimationSequence {
            id: "test".into(),
            repeat,
            steps: durations
                .iter()
                .enumerate()
                .map(|(index, duration_bars)| RenderAnimationSequenceStep {
                    animation: format!("animation-{index}"),
                    duration_bars: *duration_bars,
                })
                .collect(),
        }
    }

    fn clock(beat_index: u64, next_beat_at_s: f64, confidence: f64) -> RenderBeatClockState {
        RenderBeatClockState {
            sampled_at_ns: 0,
            bpm: 120.0,
            phase: 0.0,
            confidence,
            beat_index,
            next_beat_at_ns: Some((next_beat_at_s * 1_000_000_000.0) as u64),
            last_beat_at_ns: None,
            last_event_sequence: 0,
        }
    }

    fn update(
        coordinator: &mut SequenceCoordinator,
        sequence: &RenderAnimationSequence,
        beat_index: u64,
        next_beat_at_s: f64,
        now_s: f64,
        confidence: f64,
        offset_ns: i64,
    ) -> Option<usize> {
        update_with_section(
            coordinator,
            sequence,
            beat_index,
            next_beat_at_s,
            now_s,
            confidence,
            offset_ns,
            None,
        )
    }

    fn update_with_section(
        coordinator: &mut SequenceCoordinator,
        sequence: &RenderAnimationSequence,
        beat_index: u64,
        next_beat_at_s: f64,
        now_s: f64,
        confidence: f64,
        offset_ns: i64,
        section_change_at_s: Option<f64>,
    ) -> Option<usize> {
        coordinator.update(
            Some(sequence),
            clock(beat_index, next_beat_at_s, confidence),
            (now_s * 1_000_000_000.0) as u64,
            offset_ns,
            0.65,
            1,
            true,
            section_change_at_s.map(|timestamp| (timestamp * 1_000_000_000.0) as u64),
        )
    }

    #[test]
    fn starts_and_repeats_on_estimated_bar_boundaries() {
        let sequence = sequence(true, &[2, 1]);
        let mut coordinator = SequenceCoordinator::default();
        assert_eq!(
            update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, 0),
            None
        );
        assert_eq!(
            coordinator
                .playback_state(Some(&sequence))
                .next_transition_at_ns,
            Some(2_000_000_000)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 5, 2.5, 2.0, 0.9, 0),
            Some(0)
        );
        assert_eq!(
            coordinator.playback_state(Some(&sequence)).remaining_bars,
            Some(2)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 9, 4.5, 4.0, 0.9, 0),
            None
        );
        assert_eq!(
            coordinator.playback_state(Some(&sequence)).remaining_bars,
            Some(1)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 13, 6.5, 6.0, 0.9, 0),
            Some(1)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 17, 8.5, 8.0, 0.9, 0),
            Some(0)
        );
    }

    #[test]
    fn section_change_rescues_a_missed_boundary_only_after_step_duration() {
        let sequence = sequence(true, &[2, 1]);
        let mut coordinator = SequenceCoordinator::default();
        update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, 0);
        assert_eq!(
            update(&mut coordinator, &sequence, 5, 2.5, 2.0, 0.9, 0),
            Some(0)
        );

        assert_eq!(
            update_with_section(&mut coordinator, &sequence, 9, 4.5, 4.0, 0.9, 0, Some(4.0),),
            None
        );
        assert_eq!(
            coordinator.playback_state(Some(&sequence)).remaining_bars,
            Some(1)
        );
        assert_eq!(
            update_with_section(&mut coordinator, &sequence, 13, 6.5, 6.1, 0.9, 0, Some(6.1),),
            Some(1)
        );
    }

    #[test]
    fn section_change_waits_for_positive_sync_offset() {
        let sequence = sequence(true, &[2, 1]);
        let mut coordinator = SequenceCoordinator::default();
        let offset_ns = 100_000_000;
        update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, offset_ns);
        assert_eq!(
            update(&mut coordinator, &sequence, 5, 2.5, 2.1, 0.9, offset_ns),
            Some(0)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 9, 4.5, 4.1, 0.9, offset_ns),
            None
        );

        assert_eq!(
            update_with_section(
                &mut coordinator,
                &sequence,
                13,
                6.5,
                6.16,
                0.9,
                offset_ns,
                Some(6.16),
            ),
            None
        );
        assert_eq!(
            coordinator
                .playback_state(Some(&sequence))
                .next_transition_at_ns,
            Some(6_260_000_000)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 13, 6.5, 6.2, 0.9, offset_ns),
            None
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 13, 6.5, 6.26, 0.9, offset_ns),
            Some(1)
        );
    }

    #[test]
    fn non_repeating_sequence_releases_playback_when_complete() {
        let sequence = sequence(false, &[1]);
        let mut coordinator = SequenceCoordinator::default();
        update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, 0);
        assert_eq!(
            update(&mut coordinator, &sequence, 5, 2.5, 2.0, 0.9, 0),
            Some(0)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 9, 4.5, 4.0, 0.9, 0),
            None
        );
        assert!(!coordinator.controls_playback());
        assert_eq!(
            coordinator.playback_state(Some(&sequence)).remaining_bars,
            Some(0)
        );
    }

    #[test]
    fn confidence_loss_freezes_progress_and_reanchors() {
        let sequence = sequence(true, &[4]);
        let mut coordinator = SequenceCoordinator::default();
        update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, 0);
        update(&mut coordinator, &sequence, 5, 2.5, 2.0, 0.9, 0);
        assert_eq!(
            update(&mut coordinator, &sequence, 17, 8.5, 8.0, 0.2, 0),
            None
        );
        assert!(
            coordinator
                .playback_state(Some(&sequence))
                .waiting_for_clock
        );
        assert_eq!(
            coordinator.playback_state(Some(&sequence)).remaining_bars,
            Some(4)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 17, 8.5, 8.0, 0.9, 0),
            None
        );
        assert!(
            !coordinator
                .playback_state(Some(&sequence))
                .waiting_for_clock
        );
        assert_eq!(
            coordinator
                .playback_state(Some(&sequence))
                .next_transition_at_ns,
            Some(16_000_000_000)
        );
    }

    #[test]
    fn negative_sync_offset_can_start_before_the_estimated_bar() {
        let sequence = sequence(true, &[1, 1]);
        let mut coordinator = SequenceCoordinator::default();
        update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, -85_000_000);
        assert_eq!(
            coordinator
                .playback_state(Some(&sequence))
                .next_transition_at_ns,
            Some(1_915_000_000)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 1, 0.5, 1.92, 0.9, -85_000_000),
            Some(0)
        );
        assert_eq!(
            update(&mut coordinator, &sequence, 5, 2.5, 3.92, 0.9, -85_000_000),
            Some(1)
        );
    }

    #[test]
    fn stalled_scheduler_does_not_catch_up_multiple_bars() {
        let sequence = sequence(true, &[2, 1]);
        let mut coordinator = SequenceCoordinator::default();
        update(&mut coordinator, &sequence, 1, 0.5, 0.0, 0.9, 0);
        update(&mut coordinator, &sequence, 5, 2.5, 2.0, 0.9, 0);
        assert_eq!(
            update(&mut coordinator, &sequence, 17, 8.5, 8.0, 0.9, 0),
            None
        );
        assert_eq!(
            coordinator.playback_state(Some(&sequence)).remaining_bars,
            Some(2)
        );
        assert_eq!(
            coordinator
                .playback_state(Some(&sequence))
                .next_transition_at_ns,
            Some(12_000_000_000)
        );
    }
}
