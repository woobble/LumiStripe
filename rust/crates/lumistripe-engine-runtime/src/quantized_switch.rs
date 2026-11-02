use lumistripe_engine_protocol::{
    RenderAnimationChoice, RenderBeatClockState, RenderDynamicSelector, RenderSwitchQuantization,
};

const NS_PER_SECOND: f64 = 1_000_000_000.0;
const BEATS_PER_BAR: u64 = 4;
const MAX_LATENESS_NS: u64 = 50_000_000;

#[derive(Clone, Debug, Default)]
pub(crate) struct QuantizedSwitchState {
    pending: Option<PendingSwitch>,
}

#[derive(Clone, Debug)]
struct PendingSwitch {
    choice: RenderAnimationChoice,
    target_position: u64,
    last_position: u64,
    last_beat_index: u64,
    last_now_ns: u64,
    boundary_at_ns: u64,
}

#[derive(Clone, Copy, Debug)]
struct PositionData {
    position: u64,
    beat_index: u64,
    next_boundary_at_ns: u64,
    grid_period_ns: u64,
    beat_period_ns: u64,
}

impl QuantizedSwitchState {
    pub(crate) fn reset(&mut self) {
        self.pending = None;
    }

    /// Reconcile a new selector intent, if present, and return a due choice.
    /// `None` means no new intent was evaluated this tick; `Some(None)` clears
    /// a pending intent, and `Some(Some(choice))` updates or schedules one.
    pub(crate) fn update(
        &mut self,
        intent: Option<Option<RenderAnimationChoice>>,
        selector: RenderDynamicSelector,
        clock: RenderBeatClockState,
        now_ns: u64,
        transition_active: bool,
    ) -> Option<RenderAnimationChoice> {
        if selector.switch_quantization == RenderSwitchQuantization::Off {
            self.reset();
            return intent.flatten();
        }

        let Some(position) = position_data(selector, clock) else {
            self.reset();
            return None;
        };
        let offset_ns = selector.sync_offset_ns;

        if let Some(intent) = intent {
            match intent {
                Some(choice) => {
                    if let Some(pending) = self.pending.as_mut() {
                        pending.choice = choice;
                    } else {
                        self.pending = Some(schedule_next(choice, position, now_ns, offset_ns));
                    }
                }
                None => self.reset(),
            }
        }

        let Some(mut pending) = self.pending.take() else {
            return None;
        };
        let beat_delta = position.beat_index.saturating_sub(pending.last_beat_index);
        let regressed = now_ns < pending.last_now_ns
            || position.position < pending.last_position
            || position.beat_index < pending.last_beat_index
            || beat_delta > 1;
        if regressed {
            pending = schedule_next(pending.choice, position, now_ns, offset_ns);
        } else {
            pending.last_position = position.position;
            pending.last_beat_index = position.beat_index;
            pending.last_now_ns = now_ns;
            if position.position <= pending.target_position {
                let remaining = pending.target_position - position.position;
                pending.boundary_at_ns = if remaining == 0 {
                    position
                        .next_boundary_at_ns
                        .saturating_sub(position.grid_period_ns)
                } else {
                    position
                        .next_boundary_at_ns
                        .saturating_add((remaining - 1).saturating_mul(position.grid_period_ns))
                };
            }
        }

        let due_at_ns = apply_offset(pending.boundary_at_ns, offset_ns);
        if now_ns < due_at_ns {
            self.pending = Some(pending);
            return None;
        }
        let maximum_lateness_ns = (position.beat_period_ns
            / u64::try_from(selector.subdivisions_per_beat).unwrap_or(1)
            / 2)
        .min(MAX_LATENESS_NS);
        if now_ns.saturating_sub(due_at_ns) > maximum_lateness_ns || transition_active {
            self.pending = Some(schedule_next(pending.choice, position, now_ns, offset_ns));
            return None;
        }
        Some(pending.choice)
    }
}

fn position_data(
    selector: RenderDynamicSelector,
    clock: RenderBeatClockState,
) -> Option<PositionData> {
    if clock.beat_index == 0
        || !clock.confidence.is_finite()
        || clock.confidence < selector.clock_confidence_threshold
        || !clock.bpm.is_finite()
        || clock.bpm <= 0.0
    {
        return None;
    }
    let next_beat_at_ns = clock.next_beat_at_ns?;
    let beat_period_ns = (60.0 * NS_PER_SECOND / clock.bpm)
        .round()
        .min(u64::MAX as f64) as u64;
    if beat_period_ns == 0 {
        return None;
    }
    let stride_beats = match selector.switch_quantization {
        RenderSwitchQuantization::Off => return None,
        RenderSwitchQuantization::Beat => 1,
        RenderSwitchQuantization::Bar => BEATS_PER_BAR,
        RenderSwitchQuantization::Phrase => {
            BEATS_PER_BAR.saturating_mul(selector.bars_per_phrase as u64)
        }
    };
    let zero_based_beat = clock.beat_index - 1;
    let position = match selector.switch_quantization {
        RenderSwitchQuantization::Beat => clock.beat_index,
        RenderSwitchQuantization::Bar => zero_based_beat / BEATS_PER_BAR + 1,
        RenderSwitchQuantization::Phrase => zero_based_beat / stride_beats + 1,
        RenderSwitchQuantization::Off => return None,
    };
    let beats_to_boundary = stride_beats - zero_based_beat % stride_beats;
    let next_boundary_at_ns = next_beat_at_ns.saturating_add(
        beats_to_boundary
            .saturating_sub(1)
            .saturating_mul(beat_period_ns),
    );
    Some(PositionData {
        position,
        beat_index: clock.beat_index,
        next_boundary_at_ns,
        grid_period_ns: beat_period_ns.saturating_mul(stride_beats),
        beat_period_ns,
    })
}

fn schedule_next(
    choice: RenderAnimationChoice,
    position: PositionData,
    now_ns: u64,
    offset_ns: i64,
) -> PendingSwitch {
    let mut target_position = position.position.saturating_add(1);
    let mut boundary_at_ns = position.next_boundary_at_ns;
    while apply_offset(boundary_at_ns, offset_ns) <= now_ns {
        target_position = target_position.saturating_add(1);
        boundary_at_ns = boundary_at_ns.saturating_add(position.grid_period_ns);
    }
    PendingSwitch {
        choice,
        target_position,
        last_position: position.position,
        last_beat_index: position.beat_index,
        last_now_ns: now_ns,
        boundary_at_ns,
    }
}

fn apply_offset(timestamp_ns: u64, offset_ns: i64) -> u64 {
    (i128::from(timestamp_ns) + i128::from(offset_ns)).clamp(0, i128::from(u64::MAX)) as u64
}

#[cfg(test)]
mod tests {
    use super::QuantizedSwitchState;
    use lumistripe_engine_protocol::{
        RenderAnimationChoice, RenderBeatClockState, RenderDynamicSelector,
        RenderSwitchQuantization,
    };
    use serde_json::Map;

    fn selector(switch_quantization: RenderSwitchQuantization) -> RenderDynamicSelector {
        RenderDynamicSelector {
            min_duration_ns: 0,
            max_duration_ns: 0,
            switch_cooldown_ns: 0,
            drop_cooldown_ns: 0,
            randomness: 0.0,
            history_size: 0,
            seed: Some(1),
            switch_margin: 0.0,
            switch_quantization,
            bars_per_phrase: 4,
            clock_confidence_threshold: 0.65,
            subdivisions_per_beat: 1,
            sync_offset_ns: 0,
            activity: None,
        }
    }

    fn choice(animation: &str) -> RenderAnimationChoice {
        RenderAnimationChoice {
            animation: animation.to_owned(),
            settings: Map::new(),
        }
    }

    fn clock(beat_index: u64, now_ns: u64, confidence: f64) -> RenderBeatClockState {
        let next_beat_at_ns = beat_index * 500_000_000;
        RenderBeatClockState {
            sampled_at_ns: now_ns,
            bpm: 120.0,
            phase: 0.0,
            confidence,
            beat_index,
            next_beat_at_ns: Some(next_beat_at_ns),
            last_beat_at_ns: Some((beat_index - 1) * 500_000_000),
            last_event_sequence: 0,
        }
    }

    #[test]
    fn waits_for_the_next_beat_and_commits_at_its_boundary() {
        let mut switcher = QuantizedSwitchState::default();
        let config = selector(RenderSwitchQuantization::Beat);
        assert!(
            switcher
                .update(
                    Some(Some(choice("shockwave"))),
                    config,
                    clock(2, 800_000_000, 0.9),
                    800_000_000,
                    false,
                )
                .is_none()
        );
        assert!(
            switcher
                .update(None, config, clock(2, 990_000_000, 0.9), 990_000_000, false,)
                .is_none()
        );
        assert_eq!(
            switcher.update(
                None,
                config,
                clock(3, 1_000_000_000, 0.9),
                1_000_000_000,
                false,
            ),
            Some(choice("shockwave"))
        );
    }

    #[test]
    fn bar_and_phrase_modes_wait_for_their_musical_boundaries() {
        for (mode, current_beat, observed_at, boundary) in [
            (RenderSwitchQuantization::Bar, 2, 800_000_000, 2_000_000_000),
            (
                RenderSwitchQuantization::Phrase,
                2,
                800_000_000,
                8_000_000_000,
            ),
        ] {
            let mut switcher = QuantizedSwitchState::default();
            let config = selector(mode);
            assert!(
                switcher
                    .update(
                        Some(Some(choice("shockwave"))),
                        config,
                        clock(current_beat, observed_at, 0.9),
                        observed_at,
                        false,
                    )
                    .is_none()
            );
            let boundary_beat = boundary / 500_000_000 + 1;
            for beat_index in 3..=boundary_beat {
                let now_ns = (beat_index - 1) * 500_000_000;
                let switched =
                    switcher.update(None, config, clock(beat_index, now_ns, 0.9), now_ns, false);
                if beat_index == boundary_beat {
                    assert_eq!(switched, Some(choice("shockwave")));
                } else {
                    assert!(switched.is_none());
                }
            }
        }
    }

    #[test]
    fn offsets_and_active_transitions_defer_a_due_switch() {
        let mut switcher = QuantizedSwitchState::default();
        let config = RenderDynamicSelector {
            sync_offset_ns: -100_000_000,
            ..selector(RenderSwitchQuantization::Beat)
        };
        assert!(
            switcher
                .update(
                    Some(Some(choice("shockwave"))),
                    config,
                    clock(2, 950_000_000, 0.9),
                    950_000_000,
                    false,
                )
                .is_none()
        );
        assert!(
            switcher
                .update(
                    None,
                    config,
                    clock(3, 1_400_000_000, 0.9),
                    1_400_000_000,
                    true,
                )
                .is_none()
        );
        assert_eq!(
            switcher.update(
                None,
                config,
                clock(4, 1_900_000_000, 0.9),
                1_900_000_000,
                false,
            ),
            Some(choice("shockwave"))
        );
    }

    #[test]
    fn uncertain_clocks_clear_pending_switches() {
        let mut switcher = QuantizedSwitchState::default();
        let config = selector(RenderSwitchQuantization::Beat);
        assert!(
            switcher
                .update(
                    Some(Some(choice("shockwave"))),
                    config,
                    clock(2, 800_000_000, 0.9),
                    800_000_000,
                    false,
                )
                .is_none()
        );
        assert!(
            switcher
                .update(None, config, clock(2, 900_000_000, 0.2), 900_000_000, false,)
                .is_none()
        );
        assert!(
            switcher
                .update(
                    None,
                    config,
                    clock(3, 1_000_000_000, 0.9),
                    1_000_000_000,
                    false,
                )
                .is_none()
        );
    }
}
