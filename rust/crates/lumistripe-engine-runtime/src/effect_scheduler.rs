use lumistripe_engine_animation_definitions::{EffectDefinition, catalog};
use lumistripe_engine_effects::PythonRandom;
use lumistripe_engine_protocol::{
    RenderAudioInput, RenderBeatClockState, RenderEffect, RenderEffectChoice,
    RenderEffectPlaybackState, RenderEffectSchedulerConfig, RenderEffectSchedulerState,
    RenderMusicStructure, RenderMusicStructurePhase,
};

const NS_PER_SECOND: f64 = 1_000_000_000.0;
const MAX_PENDING_BEATS: usize = 64;
const MAX_RECENT_GRID_INDICES: usize = 256;
const MAX_LATENESS_NS: u64 = 50_000_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Category {
    Rhythmic,
    Accent,
}

impl Category {
    fn as_str(self) -> &'static str {
        match self {
            Self::Rhythmic => "rhythmic",
            Self::Accent => "accent",
        }
    }
}

#[derive(Clone, Debug)]
struct ActiveEffect {
    name: String,
    settings: serde_json::Map<String, serde_json::Value>,
    category: Category,
    started_at_ns: u64,
    intensity: f64,
    trigger_beat_strength: Option<f64>,
}

#[derive(Clone, Copy, Debug)]
struct PendingBeat {
    due_at_ns: u64,
    strength: f64,
    grid_index: Option<i128>,
    has_event: bool,
}

#[derive(Clone, Copy, Debug, Default)]
struct StructureModifiers {
    output_scale: f64,
    effect_scale: f64,
    threshold_scale: f64,
}

impl StructureModifiers {
    fn for_state(state: Option<RenderMusicStructure>) -> Self {
        let Some(state) = state.filter(|state| state.confidence >= 0.6) else {
            return Self {
                output_scale: 1.0,
                effect_scale: 1.0,
                threshold_scale: 1.0,
            };
        };
        match state.phase {
            RenderMusicStructurePhase::Idle | RenderMusicStructurePhase::Drop => Self {
                output_scale: 1.0,
                effect_scale: 1.0,
                threshold_scale: 1.0,
            },
            RenderMusicStructurePhase::Breakdown => Self {
                output_scale: 0.65,
                effect_scale: 0.65,
                threshold_scale: 1.25,
            },
            RenderMusicStructurePhase::Buildup => {
                let progress = state.buildup_progress.clamp(0.0, 1.0);
                let scale = 0.65 + 0.35 * progress;
                Self {
                    output_scale: scale,
                    effect_scale: scale,
                    threshold_scale: 1.0 - 0.2 * progress,
                }
            }
            RenderMusicStructurePhase::Groove => Self {
                output_scale: 1.0,
                effect_scale: 1.0,
                threshold_scale: 1.0,
            },
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct EffectScheduleCoordinator {
    config: Option<RenderEffectSchedulerConfig>,
    rng: PythonRandom,
    active: Vec<ActiveEffect>,
    recent: Vec<String>,
    last_rhythmic_at_ns: Option<u64>,
    last_accent_at_ns: Option<u64>,
    last_beat_event_sequence: u64,
    pending_beats: Vec<PendingBeat>,
    pending_drop_accent_at_ns: Option<u64>,
    drop_event_seen: bool,
    last_drop_accent_status: String,
    last_drop_accent_reason: Option<String>,
    last_clock_subdivision_scheduled: Option<i128>,
    recent_grid_indices: Vec<i128>,
    sync_offset_ns: i64,
    late_ticks_dropped: u64,
    late_ticks_coalesced: u64,
    beat_lateness_samples_ns: Vec<u64>,
    rhythmic_result: String,
    accent_result: String,
}

impl Default for EffectScheduleCoordinator {
    fn default() -> Self {
        Self {
            config: None,
            rng: PythonRandom::from_u64(0),
            active: Vec::with_capacity(2),
            recent: Vec::with_capacity(4),
            last_rhythmic_at_ns: None,
            last_accent_at_ns: None,
            last_beat_event_sequence: 0,
            pending_beats: Vec::with_capacity(MAX_PENDING_BEATS),
            pending_drop_accent_at_ns: None,
            drop_event_seen: false,
            last_drop_accent_status: "idle".to_owned(),
            last_drop_accent_reason: None,
            last_clock_subdivision_scheduled: None,
            recent_grid_indices: Vec::with_capacity(MAX_RECENT_GRID_INDICES),
            sync_offset_ns: 0,
            late_ticks_dropped: 0,
            late_ticks_coalesced: 0,
            beat_lateness_samples_ns: Vec::with_capacity(256),
            rhythmic_result: "not-evaluated".to_owned(),
            accent_result: "not-evaluated".to_owned(),
        }
    }
}

impl EffectScheduleCoordinator {
    pub(crate) fn update(
        &mut self,
        config: Option<&RenderEffectSchedulerConfig>,
        audio: Option<&RenderAudioInput>,
        structure: Option<RenderMusicStructure>,
        beat_clock: RenderBeatClockState,
        now_ns: u64,
        music_active: bool,
        transition_active: bool,
    ) -> (f64, f64, Vec<RenderEffect>, RenderEffectSchedulerState) {
        let Some(config) = config else {
            self.reset();
            return (1.0, 0.65, Vec::new(), RenderEffectSchedulerState::default());
        };
        if self.config.as_ref() != Some(config) {
            self.configure(config);
        }
        let modifiers = StructureModifiers::for_state(structure);
        let effect_features = audio
            .and_then(|input| input.effect_features)
            .unwrap_or_default();
        let selector_features = audio
            .and_then(|input| input.selector_features)
            .unwrap_or_default();
        let frame_beat = audio.is_some_and(|input| input.beat);
        let frame_beat_strength = audio.map_or(0.0, |input| input.beat_strength);
        let beat_event_stream = audio.is_some_and(|input| input.beat_event_stream);
        let sync_offset_ns = config.sync_offset_ns;
        if sync_offset_ns != self.sync_offset_ns {
            let delta = sync_offset_ns.saturating_sub(self.sync_offset_ns);
            for pending in &mut self.pending_beats {
                pending.due_at_ns = add_signed(pending.due_at_ns, delta);
            }
            self.pending_drop_accent_at_ns = self
                .pending_drop_accent_at_ns
                .map(|due| add_signed(due, delta));
            self.sync_offset_ns = sync_offset_ns;
        }

        self.active.retain(|effect| {
            now_ns.saturating_sub(effect.started_at_ns) < definition_duration_ns(&effect.name)
        });
        for effect in &mut self.active {
            effect.trigger_beat_strength = None;
        }

        if !music_active {
            self.active.clear();
            self.pending_beats.clear();
            self.pending_drop_accent_at_ns = None;
            self.drop_event_seen = false;
            self.rhythmic_result = "below-threshold".to_owned();
            self.accent_result = "below-threshold".to_owned();
            let state = self.health(config, now_ns, modifiers);
            return (1.0, config.max_overlay_strength, Vec::new(), state);
        }
        if transition_active {
            self.active.clear();
        }

        self.ingest_beats(config, audio, beat_clock, now_ns);
        let lateness_ns = maximum_lateness_ns(beat_clock, config.subdivisions_per_beat);
        let drop_event = structure.is_some_and(|state| state.drop_event);
        if drop_event && !self.drop_event_seen {
            self.schedule_drop_accent(config, structure, beat_clock, now_ns);
        }
        self.drop_event_seen = drop_event;
        let drop_accent_due = self.drop_accent_due(now_ns, lateness_ns);
        let (due_beat, predicted_beat) = self.due_beat(now_ns, lateness_ns);
        let due_strength = due_beat.map_or(0.0, |beat| beat.strength);
        let event_due = due_beat.is_some();
        let legacy_beat = !beat_event_stream
            && audio.is_some_and(|input| input.beat_events.is_empty())
            && (frame_beat || selector_features.beat);
        let beat = event_due || legacy_beat;
        let beat_strength = frame_beat_strength
            .max(effect_features.beat_strength)
            .max(selector_features.beat_confidence)
            .max(due_strength);
        let impact = effect_features.musical_impact;
        let response = config.dynamic_response;
        let impact_threshold = (0.85 - response * 0.25) * modifiers.threshold_scale;
        let onset_threshold = config.onset_threshold * modifiers.threshold_scale;
        let allow_triggers = !transition_active;

        let rhythmic_trigger = beat
            && beat_strength >= config.beat_confidence_threshold
            && (impact >= impact_threshold
                || (predicted_beat && beat_clock.confidence >= config.clock_confidence_threshold));
        let rhythmic_result = self.trigger_result(
            Category::Rhythmic,
            rhythmic_trigger,
            now_ns,
            config.rhythmic_cooldown_ns,
            config.max_active_effects,
            allow_triggers,
        );
        if rhythmic_result == "activated" {
            let trigger_strength = frame_beat_strength
                .max(effect_features.beat_strength)
                .max(selector_features.onset_strength)
                .max(due_strength);
            self.activate(
                config,
                Category::Rhythmic,
                &selector_features,
                audio,
                frame_beat || event_due,
                frame_beat_strength.max(if event_due { due_strength } else { 0.0 }),
                frame_beat_strength.max(if event_due { due_strength } else { 0.0 }),
                trigger_strength,
                impact,
                now_ns,
                false,
            );
            self.last_rhythmic_at_ns = Some(now_ns);
        }
        self.rhythmic_result = if rhythmic_result == "activated"
            && !self
                .active
                .iter()
                .any(|effect| effect.category == Category::Rhythmic)
        {
            "no-candidate".to_owned()
        } else {
            rhythmic_result
        };

        if drop_accent_due {
            let accent_result = self.trigger_result(
                Category::Accent,
                true,
                now_ns,
                config.accent_cooldown_ns,
                config.max_active_effects,
                allow_triggers,
            );
            let activated = if accent_result == "activated" {
                let strength = frame_beat_strength
                    .max(effect_features.beat_strength)
                    .max(selector_features.onset_strength)
                    .max(0.9);
                let active = self.activate(
                    config,
                    Category::Accent,
                    &selector_features,
                    audio,
                    frame_beat,
                    0.9,
                    frame_beat_strength.max(0.9),
                    strength,
                    impact,
                    now_ns,
                    true,
                );
                self.last_accent_at_ns = Some(now_ns);
                active
            } else {
                false
            };
            self.accent_result = if accent_result == "activated" && !activated {
                "no-candidate".to_owned()
            } else {
                accent_result.clone()
            };
            if activated {
                self.last_drop_accent_status = "activated".to_owned();
                self.last_drop_accent_reason = None;
            } else {
                self.last_drop_accent_status = "skipped".to_owned();
                self.last_drop_accent_reason = Some(drop_accent_reason(&self.accent_result));
            }
        } else {
            let drop_in_progress = structure
                .is_some_and(|state| state.phase == RenderMusicStructurePhase::Drop)
                || self.pending_drop_accent_at_ns.is_some();
            let accent_trigger = !drop_in_progress
                && impact >= impact_threshold
                && selector_features.onset_strength >= onset_threshold
                && (structure.is_none() || !selector_features.drop_detected);
            let accent_result = self.trigger_result(
                Category::Accent,
                accent_trigger,
                now_ns,
                config.accent_cooldown_ns,
                config.max_active_effects,
                allow_triggers,
            );
            if accent_result == "activated" {
                let trigger_strength = frame_beat_strength
                    .max(effect_features.beat_strength)
                    .max(selector_features.onset_strength);
                let activated = self.activate(
                    config,
                    Category::Accent,
                    &selector_features,
                    audio,
                    frame_beat,
                    frame_beat_strength,
                    frame_beat_strength,
                    trigger_strength,
                    impact,
                    now_ns,
                    false,
                );
                self.last_accent_at_ns = Some(now_ns);
                self.accent_result = if activated {
                    "activated".to_owned()
                } else {
                    "no-candidate".to_owned()
                };
            } else {
                self.accent_result = accent_result;
            }
        }

        let base = base_intensity(response, impact, modifiers);
        let effects = self.render_effects(modifiers.effect_scale);
        let state = self.health(config, now_ns, modifiers);
        (base, config.max_overlay_strength, effects, state)
    }

    fn configure(&mut self, config: &RenderEffectSchedulerConfig) {
        self.reset();
        self.config = Some(config.clone());
        self.sync_offset_ns = config.sync_offset_ns;
        self.rng = PythonRandom::from_u64(config.seed.unwrap_or(0));
    }

    pub(crate) fn reset(&mut self) {
        *self = Self::default();
    }

    fn ingest_beats(
        &mut self,
        config: &RenderEffectSchedulerConfig,
        audio: Option<&RenderAudioInput>,
        clock: RenderBeatClockState,
        now_ns: u64,
    ) {
        if let Some(audio) = audio {
            for event in &audio.beat_events {
                if event.sequence <= self.last_beat_event_sequence {
                    continue;
                }
                self.last_beat_event_sequence = event.sequence;
                let grid_index = self.event_grid_index(event.timestamp_ns, clock, config);
                if let Some(grid_index) = grid_index {
                    if self.recent_grid_indices.contains(&grid_index) {
                        if let Some(prediction) = self.pending_beats.iter_mut().find(|pending| {
                            pending.grid_index == Some(grid_index) && !pending.has_event
                        }) {
                            prediction.due_at_ns =
                                add_signed(event.timestamp_ns, config.sync_offset_ns);
                            prediction.strength = event.strength;
                            prediction.has_event = true;
                        }
                    } else {
                        self.remember_grid_index(grid_index);
                        self.enqueue_beat(PendingBeat {
                            due_at_ns: add_signed(event.timestamp_ns, config.sync_offset_ns),
                            strength: event.strength,
                            grid_index: Some(grid_index),
                            has_event: true,
                        });
                    }
                } else {
                    self.enqueue_beat(PendingBeat {
                        due_at_ns: add_signed(event.timestamp_ns, config.sync_offset_ns),
                        strength: event.strength,
                        grid_index: None,
                        has_event: true,
                    });
                }
            }
        }

        if clock.bpm <= 0.0
            || !clock.bpm.is_finite()
            || clock.last_beat_at_ns.is_none()
            || clock.confidence < config.clock_confidence_threshold
        {
            return;
        }
        let division = config.subdivisions_per_beat as i128;
        let position = ((clock.phase * division as f64) as i128).clamp(0, division - 1);
        let next_subdivision = i128::from(clock.beat_index) * division + position + 1;
        let first = self
            .last_clock_subdivision_scheduled
            .map_or(next_subdivision, |last| last.saturating_add(1));
        let first = if next_subdivision.saturating_sub(first) >= 64 {
            next_subdivision - 63
        } else {
            first
        };
        for grid_index in first..=next_subdivision {
            if self.recent_grid_indices.contains(&grid_index) {
                continue;
            }
            let Some(target_at_ns) = self.clock_grid_time(clock, grid_index, division) else {
                continue;
            };
            self.remember_grid_index(grid_index);
            self.enqueue_beat(PendingBeat {
                due_at_ns: add_signed(target_at_ns, config.sync_offset_ns),
                strength: 0.8,
                grid_index: Some(grid_index),
                has_event: false,
            });
        }
        self.last_clock_subdivision_scheduled = Some(
            self.last_clock_subdivision_scheduled
                .map_or(next_subdivision, |last| last.max(next_subdivision)),
        );
        let _ = now_ns;
    }

    fn schedule_drop_accent(
        &mut self,
        config: &RenderEffectSchedulerConfig,
        structure: Option<RenderMusicStructure>,
        clock: RenderBeatClockState,
        now_ns: u64,
    ) {
        if self.pending_drop_accent_at_ns.is_some() {
            return;
        }
        if structure.is_none_or(|state| state.confidence < 0.6) {
            self.last_drop_accent_status = "skipped".to_owned();
            self.last_drop_accent_reason = Some("structure-confidence-low".to_owned());
            return;
        }
        if clock.bpm <= 0.0 || !clock.bpm.is_finite() || clock.next_beat_at_ns.is_none() {
            self.last_drop_accent_status = "skipped".to_owned();
            self.last_drop_accent_reason = Some("beat-clock-unavailable".to_owned());
            return;
        }
        if clock.confidence < config.clock_confidence_threshold {
            self.last_drop_accent_status = "skipped".to_owned();
            self.last_drop_accent_reason = Some("beat-clock-uncertain".to_owned());
            return;
        }
        let period_ns = (60.0 / clock.bpm * NS_PER_SECOND).round() as u64;
        let mut due = add_signed(
            clock.next_beat_at_ns.unwrap_or(now_ns),
            config.sync_offset_ns,
        );
        if due <= now_ns && period_ns > 0 {
            let missed = (now_ns - due) / period_ns + 1;
            due = due.saturating_add(missed.saturating_mul(period_ns));
        }
        self.pending_drop_accent_at_ns = Some(due);
        self.last_drop_accent_status = "pending".to_owned();
        self.last_drop_accent_reason = None;
    }

    fn drop_accent_due(&mut self, now_ns: u64, lateness_ns: u64) -> bool {
        let Some(due) = self.pending_drop_accent_at_ns.filter(|due| *due <= now_ns) else {
            return false;
        };
        self.pending_drop_accent_at_ns = None;
        if now_ns.saturating_sub(due) > lateness_ns {
            self.late_ticks_dropped = self.late_ticks_dropped.saturating_add(1);
            self.last_drop_accent_status = "skipped".to_owned();
            self.last_drop_accent_reason = Some("late".to_owned());
            return false;
        }
        true
    }

    fn due_beat(&mut self, now_ns: u64, lateness_ns: u64) -> (Option<PendingBeat>, bool) {
        self.pending_beats.sort_by_key(|beat| beat.due_at_ns);
        let due_count = self
            .pending_beats
            .iter()
            .take_while(|beat| beat.due_at_ns <= now_ns)
            .count();
        if due_count == 0 {
            return (None, false);
        }
        let due: Vec<_> = self.pending_beats.drain(..due_count).collect();
        let fresh: Vec<_> = due
            .iter()
            .copied()
            .filter(|beat| now_ns.saturating_sub(beat.due_at_ns) <= lateness_ns)
            .collect();
        self.late_ticks_dropped = self
            .late_ticks_dropped
            .saturating_add((due.len() - fresh.len()) as u64);
        if fresh.len() > 1 {
            self.late_ticks_coalesced = self
                .late_ticks_coalesced
                .saturating_add((fresh.len() - 1) as u64);
        }
        let Some(beat) = fresh.last().copied() else {
            return (None, false);
        };
        self.beat_lateness_samples_ns
            .push(now_ns.saturating_sub(beat.due_at_ns));
        if self.beat_lateness_samples_ns.len() > 256 {
            self.beat_lateness_samples_ns.remove(0);
        }
        (Some(beat), !beat.has_event)
    }

    fn trigger_result(
        &self,
        category: Category,
        triggered: bool,
        now_ns: u64,
        cooldown_ns: u64,
        max_active: usize,
        allow_triggers: bool,
    ) -> String {
        if !allow_triggers {
            return "transition".to_owned();
        }
        if !triggered {
            return "below-threshold".to_owned();
        }
        if self.active.iter().any(|effect| effect.category == category) {
            return "category-active".to_owned();
        }
        if self.active.len() >= max_active {
            return "capacity".to_owned();
        }
        let last = match category {
            Category::Rhythmic => self.last_rhythmic_at_ns,
            Category::Accent => self.last_accent_at_ns,
        };
        if last.is_some_and(|last| now_ns.saturating_sub(last) < cooldown_ns) {
            return "cooldown".to_owned();
        }
        "activated".to_owned()
    }

    #[allow(clippy::too_many_arguments)]
    fn activate(
        &mut self,
        config: &RenderEffectSchedulerConfig,
        category: Category,
        features: &lumistripe_engine_protocol::RenderSelectorFeatures,
        audio: Option<&RenderAudioInput>,
        score_beat: bool,
        score_beat_strength: f64,
        frame_beat_strength: f64,
        trigger_strength: f64,
        impact: f64,
        now_ns: u64,
        drop_only: bool,
    ) -> bool {
        let mut selected: Option<(&RenderEffectChoice, &EffectDefinition, f64)> = None;
        for choice in &config.choices {
            let Some(definition) = catalog().effect(&choice.name) else {
                continue;
            };
            if definition.category != category.as_str()
                || definition.drop_only != drop_only && drop_only
                || !drop_only && category == Category::Accent && definition.drop_only
            {
                continue;
            }
            if drop_only && !definition.drop_only {
                continue;
            }
            let score = if drop_only {
                definition.strength
            } else {
                self.score(definition, features, audio, score_beat, score_beat_strength)
            };
            if selected.is_none_or(|(_, _, best)| score > best) {
                selected = Some((choice, definition, score));
            }
        }
        let Some((choice, _definition, _)) = selected else {
            return false;
        };
        let mut actual_trigger_strength = frame_beat_strength
            .max(features.onset_strength)
            .max(trigger_strength);
        if features.drop_detected {
            actual_trigger_strength = actual_trigger_strength.max(0.9);
        }
        let intensity = if drop_only {
            1.0
        } else {
            (impact * actual_trigger_strength).clamp(0.0, 1.0)
        };
        let trigger_beat_strength = Some(actual_trigger_strength.min(1.0));
        self.active.push(ActiveEffect {
            name: choice.name.clone(),
            settings: choice.settings.clone(),
            category,
            started_at_ns: now_ns,
            intensity,
            trigger_beat_strength,
        });
        if self.recent.len() == 4 {
            self.recent.remove(0);
        }
        self.recent.push(choice.name.clone());
        true
    }

    fn score(
        &mut self,
        definition: &EffectDefinition,
        features: &lumistripe_engine_protocol::RenderSelectorFeatures,
        audio: Option<&RenderAudioInput>,
        frame_beat: bool,
        frame_beat_strength: f64,
    ) -> f64 {
        let low = clamp01(if features.bass_energy != 0.0 {
            features.bass_energy
        } else {
            features.bass
        });
        let bands = audio.map_or([0.0; 8], |input| input.bands);
        let mid = clamp01(if features.mid_energy != 0.0 {
            features.mid_energy
        } else {
            (bands[2] + bands[3] + bands[4]) / 3.0
        });
        let high = clamp01(if features.treble_energy != 0.0 {
            features.treble_energy
        } else {
            (bands[5] + bands[6] + bands[7]) / 3.0
        });
        let brightness = clamp01(features.brightness);
        let onset = clamp01(features.onset_strength);
        let energy = clamp01(features.energy);
        let drive = clamp01(energy * 0.48 + low * 0.28 + mid * 0.18 + onset * 0.18);
        let beat_strength = frame_beat_strength.max(features.beat_confidence);
        let accent = if frame_beat || features.beat {
            clamp01(beat_strength.max(energy * 0.5).max(onset * 0.8))
        } else {
            clamp01(beat_strength * 0.5 + onset * 0.5 + energy * 0.18)
        };
        let activity = clamp01(drive * 0.5 + accent * 0.24 + brightness * 0.16 + high * 0.1);
        let preferred = match definition.preferred_band.as_str() {
            "bass" => low,
            "mid" => mid,
            "treble" => high,
            _ => activity,
        };
        preferred + self.rng.uniform(0.0, 0.3)
            - if self.recent.iter().any(|name| name == &definition.name) {
                0.4
            } else {
                0.0
            }
    }

    fn render_effects(&self, effect_scale: f64) -> Vec<RenderEffect> {
        self.active
            .iter()
            .map(|effect| RenderEffect {
                name: effect.name.clone(),
                settings: effect.settings.clone(),
                intensity: (effect.intensity * effect_scale).clamp(0.0, 1.0),
                trigger_beat: effect.trigger_beat_strength.is_some(),
                trigger_beat_strength: effect.trigger_beat_strength.unwrap_or(0.0),
            })
            .collect()
    }

    fn health(
        &self,
        config: &RenderEffectSchedulerConfig,
        now_ns: u64,
        modifiers: StructureModifiers,
    ) -> RenderEffectSchedulerState {
        let active = self
            .active
            .iter()
            .filter_map(|effect| {
                let definition = catalog().effect(&effect.name)?;
                let duration_ns = definition_duration_ns(&effect.name);
                let elapsed_ns = now_ns.saturating_sub(effect.started_at_ns);
                let remaining_ns = duration_ns.saturating_sub(elapsed_ns);
                Some(RenderEffectPlaybackState {
                    name: effect.name.clone(),
                    category: effect.category.as_str().to_owned(),
                    strength: definition.strength * effect.intensity * modifiers.effect_scale,
                    elapsed_ns,
                    remaining_ns,
                    progress: (elapsed_ns as f64 / duration_ns as f64).clamp(0.0, 1.0),
                })
            })
            .collect();
        let p95 = if self.beat_lateness_samples_ns.is_empty() {
            0
        } else {
            let mut values = self.beat_lateness_samples_ns.clone();
            values.sort_unstable();
            values[((values.len() - 1) as f64 * 0.95) as usize]
        };
        RenderEffectSchedulerState {
            active,
            rhythmic_cooldown_remaining_ns: cooldown_remaining(
                now_ns,
                self.last_rhythmic_at_ns,
                config.rhythmic_cooldown_ns,
            ),
            accent_cooldown_remaining_ns: cooldown_remaining(
                now_ns,
                self.last_accent_at_ns,
                config.accent_cooldown_ns,
            ),
            rhythmic_result: self.rhythmic_result.clone(),
            accent_result: self.accent_result.clone(),
            drop_accent_status: self.last_drop_accent_status.clone(),
            drop_accent_reason: self.last_drop_accent_reason.clone(),
            late_ticks_dropped: self.late_ticks_dropped,
            late_ticks_coalesced: self.late_ticks_coalesced,
            beat_lateness_p95_ns: p95,
        }
    }

    fn enqueue_beat(&mut self, beat: PendingBeat) {
        if self.pending_beats.len() == MAX_PENDING_BEATS {
            self.pending_beats.remove(0);
        }
        self.pending_beats.push(beat);
        self.pending_beats.sort_by_key(|item| item.due_at_ns);
    }

    fn remember_grid_index(&mut self, grid_index: i128) {
        if self.recent_grid_indices.contains(&grid_index) {
            return;
        }
        if self.recent_grid_indices.len() == MAX_RECENT_GRID_INDICES {
            self.recent_grid_indices.remove(0);
        }
        self.recent_grid_indices.push(grid_index);
    }

    fn event_grid_index(
        &self,
        timestamp_ns: u64,
        clock: RenderBeatClockState,
        config: &RenderEffectSchedulerConfig,
    ) -> Option<i128> {
        let last_beat_at_ns = clock.last_beat_at_ns?;
        if clock.confidence < config.clock_confidence_threshold || clock.bpm <= 0.0 {
            return None;
        }
        let division = config.subdivisions_per_beat as i128;
        let subdivision_ns = 60.0 / clock.bpm * NS_PER_SECOND / division as f64;
        let position = (timestamp_ns as f64 - last_beat_at_ns as f64) / subdivision_ns;
        let rounded = position.round_ties_even();
        if (position - rounded).abs() > 0.35 {
            return None;
        }
        Some(i128::from(clock.beat_index) * division + rounded as i128)
    }

    fn clock_grid_time(
        &self,
        clock: RenderBeatClockState,
        grid_index: i128,
        division: i128,
    ) -> Option<u64> {
        let last_beat_at_ns = clock.last_beat_at_ns?;
        let period_ns = 60.0 / clock.bpm * NS_PER_SECOND;
        let target_beat = grid_index.div_euclid(division);
        let subdivision = grid_index.rem_euclid(division);
        let beat_delta = target_beat - i128::from(clock.beat_index);
        let timestamp = last_beat_at_ns as f64
            + beat_delta as f64 * period_ns
            + subdivision as f64 * period_ns / division as f64;
        (timestamp.is_finite() && timestamp >= 0.0 && timestamp <= u64::MAX as f64)
            .then_some(timestamp.round() as u64)
    }
}

fn definition_duration_ns(name: &str) -> u64 {
    catalog().effect(name).map_or(0, |definition| {
        (definition.duration_s * NS_PER_SECOND).round() as u64
    })
}

fn maximum_lateness_ns(clock: RenderBeatClockState, subdivisions: usize) -> u64 {
    let beat_subdivision_ns = if clock.bpm.is_finite() && clock.bpm > 0.0 {
        60.0 / clock.bpm * NS_PER_SECOND / subdivisions.max(1) as f64 / 2.0
    } else {
        MAX_LATENESS_NS as f64
    };
    beat_subdivision_ns.min(MAX_LATENESS_NS as f64).max(0.0) as u64
}

fn base_intensity(response: f64, musical_impact: f64, modifiers: StructureModifiers) -> f64 {
    let quiet_floor = 0.65 - response * 0.4;
    (quiet_floor + (1.0 - quiet_floor) * musical_impact.clamp(0.0, 1.0)) * modifiers.output_scale
}

fn clamp01(value: f64) -> f64 {
    if value.is_finite() {
        value.clamp(0.0, 1.0)
    } else {
        0.0
    }
}

fn add_signed(value: u64, offset: i64) -> u64 {
    if offset >= 0 {
        value.saturating_add(offset as u64)
    } else {
        value.saturating_sub(offset.unsigned_abs())
    }
}

fn cooldown_remaining(now_ns: u64, last_at_ns: Option<u64>, cooldown_ns: u64) -> u64 {
    last_at_ns.map_or(0, |last| {
        cooldown_ns.saturating_sub(now_ns.saturating_sub(last))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use lumistripe_engine_protocol::{
        RenderEffectChoice, RenderEffectFeatures, RenderSelectorFeatures,
    };

    fn config(names: &[&str]) -> RenderEffectSchedulerConfig {
        RenderEffectSchedulerConfig {
            beat_confidence_threshold: 0.5,
            onset_threshold: 0.5,
            rhythmic_cooldown_ns: 2_000_000_000,
            accent_cooldown_ns: 2_000_000_000,
            max_active_effects: 2,
            max_overlay_strength: 0.65,
            subdivisions_per_beat: 1,
            clock_confidence_threshold: 0.7,
            seed: Some(7),
            sync_offset_ns: 0,
            dynamic_response: 0.5,
            choices: names
                .iter()
                .map(|name| RenderEffectChoice {
                    name: (*name).to_owned(),
                    settings: serde_json::Map::new(),
                })
                .collect(),
        }
    }

    fn audio(beat: bool, strength: f64, impact: f64) -> RenderAudioInput {
        RenderAudioInput {
            rms: 0.5,
            bands: [0.5; 8],
            beat,
            beat_strength: strength,
            feature_timestamp_ns: None,
            feature_sequence: Some(1),
            fallback_bpm: Some(120.0),
            beat_events: Vec::new(),
            structure_events: Vec::new(),
            selector_features: Some(RenderSelectorFeatures {
                silence: false,
                beat,
                beat_confidence: strength,
                ..RenderSelectorFeatures::default()
            }),
            effect_features: Some(RenderEffectFeatures {
                beat_strength: strength,
                musical_impact: impact,
            }),
            beat_event_stream: false,
        }
    }

    fn clock(next_beat_at_ns: Option<u64>, confidence: f64) -> RenderBeatClockState {
        RenderBeatClockState {
            sampled_at_ns: 1_000_000_000,
            bpm: 120.0,
            phase: 0.0,
            confidence,
            beat_index: 0,
            next_beat_at_ns,
            last_beat_at_ns: None,
            last_event_sequence: 0,
        }
    }

    fn structure(phase: RenderMusicStructurePhase, drop_event: bool) -> RenderMusicStructure {
        RenderMusicStructure {
            phase,
            confidence: 0.95,
            buildup_progress: 0.5,
            drop_event,
        }
    }

    #[test]
    fn rhythmic_effect_expires_and_respects_its_cooldown() {
        let config = config(&["hard_beat"]);
        let audio = audio(true, 0.95, 1.0);
        let mut scheduler = EffectScheduleCoordinator::default();

        let (_, _, first_effects, first_state) = scheduler.update(
            Some(&config),
            Some(&audio),
            None,
            clock(None, 0.0),
            100_000_000,
            true,
            false,
        );
        assert_eq!(first_state.rhythmic_result, "activated");
        assert_eq!(first_effects.len(), 1);
        assert!(first_effects[0].trigger_beat);
        assert_eq!(first_effects[0].name, "hard_beat");

        let (_, _, active_effects, active_state) = scheduler.update(
            Some(&config),
            Some(&audio),
            None,
            clock(None, 0.0),
            200_000_000,
            true,
            false,
        );
        assert_eq!(active_state.rhythmic_result, "category-active");
        assert_eq!(active_effects.len(), 1);
        assert!(!active_effects[0].trigger_beat);

        let (_, _, expired_effects, cooldown_state) = scheduler.update(
            Some(&config),
            Some(&audio),
            None,
            clock(None, 0.0),
            700_000_000,
            true,
            false,
        );
        assert!(expired_effects.is_empty());
        assert_eq!(cooldown_state.rhythmic_result, "cooldown");
        assert!(cooldown_state.rhythmic_cooldown_remaining_ns > 0);
    }

    #[test]
    fn breakdown_scales_output_intensity_and_thresholds() {
        let config = config(&["hard_beat"]);
        let audio = audio(true, 0.95, 0.95);
        let mut scheduler = EffectScheduleCoordinator::default();

        let (breakdown_base, _, breakdown_effects, _) = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(structure(RenderMusicStructurePhase::Breakdown, false)),
            clock(None, 0.0),
            100_000_000,
            true,
            false,
        );
        let (groove_base, _, groove_effects, _) = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(structure(RenderMusicStructurePhase::Groove, false)),
            clock(None, 0.0),
            200_000_000,
            true,
            false,
        );

        assert!((breakdown_base - groove_base * 0.65).abs() < 1e-12);
        assert_eq!(breakdown_effects.len(), 1);
        assert_eq!(groove_effects.len(), 1);
        assert!(
            (breakdown_effects[0].intensity - groove_effects[0].intensity * 0.65).abs() < 1e-12
        );
    }

    #[test]
    fn drop_event_schedules_one_accent_at_the_next_beat() {
        let config = config(&["drop_explosion"]);
        let audio = audio(false, 0.0, 0.0);
        let drop = structure(RenderMusicStructurePhase::Drop, true);
        let mut scheduler = EffectScheduleCoordinator::default();

        let (_, _, before_due, pending_state) = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(drop),
            clock(Some(1_500_000_000), 0.95),
            1_000_000_000,
            true,
            false,
        );
        assert!(before_due.is_empty());
        assert_eq!(pending_state.drop_accent_status, "pending");

        let (_, _, due_effects, due_state) = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(drop),
            clock(Some(1_500_000_000), 0.95),
            1_500_000_000,
            true,
            false,
        );
        assert_eq!(due_state.drop_accent_status, "activated");
        assert_eq!(due_effects.len(), 1);
        assert_eq!(due_effects[0].name, "drop_explosion");

        let (_, _, still_active, held_state) = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(drop),
            clock(Some(2_000_000_000), 0.95),
            2_000_000_000,
            true,
            false,
        );
        assert_eq!(held_state.drop_accent_status, "activated");
        assert_eq!(still_active.len(), 1);
    }

    #[test]
    fn late_drop_accent_is_counted_and_skipped() {
        let config = config(&["drop_explosion"]);
        let audio = audio(false, 0.0, 0.0);
        let drop = structure(RenderMusicStructurePhase::Drop, true);
        let mut scheduler = EffectScheduleCoordinator::default();

        let _ = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(drop),
            clock(Some(1_500_000_000), 0.95),
            1_000_000_000,
            true,
            false,
        );
        let (_, _, effects, state) = scheduler.update(
            Some(&config),
            Some(&audio),
            Some(drop),
            clock(Some(1_500_000_000), 0.95),
            1_600_000_000,
            true,
            false,
        );

        assert!(effects.is_empty());
        assert_eq!(state.late_ticks_dropped, 1);
        assert_eq!(state.drop_accent_status, "skipped");
        assert_eq!(state.drop_accent_reason.as_deref(), Some("late"));
    }

    #[test]
    fn controlled_three_minute_music_run_keeps_scheduler_state_bounded() {
        let mut config = config(&["hard_beat", "club_flash", "drop_explosion"]);
        config.rhythmic_cooldown_ns = 1_000_000_000;
        config.accent_cooldown_ns = 1_000_000_000;
        let mut scheduler = EffectScheduleCoordinator::default();
        let mut rhythmic_activations = 0;
        let mut drop_activations = 0;
        let mut previous_drop_status = String::new();
        let frame_period_ns = 1_000_000_000 / 60;
        let frame_count = (180 * 1_000_000_000u64 / frame_period_ns) as usize;

        for frame in 0..frame_count {
            let now_ns = frame as u64 * frame_period_ns;
            let beat_period_ns = 500_000_000;
            let beat_index = now_ns / beat_period_ns;
            let last_beat_at_ns = beat_index * beat_period_ns;
            let drop_event = frame % (60 * 30) == 60 * 2 + 15;
            let beat = now_ns == last_beat_at_ns;
            let structure = RenderMusicStructure {
                phase: if drop_event {
                    RenderMusicStructurePhase::Drop
                } else {
                    match (frame / (60 * 15)) % 3 {
                        0 => RenderMusicStructurePhase::Groove,
                        1 => RenderMusicStructurePhase::Breakdown,
                        _ => RenderMusicStructurePhase::Buildup,
                    }
                },
                confidence: 0.95,
                buildup_progress: 0.5,
                drop_event,
            };
            let clock = RenderBeatClockState {
                sampled_at_ns: now_ns,
                bpm: 120.0,
                phase: (now_ns - last_beat_at_ns) as f64 / beat_period_ns as f64,
                confidence: 0.95,
                beat_index,
                next_beat_at_ns: Some(last_beat_at_ns + beat_period_ns),
                last_beat_at_ns: Some(last_beat_at_ns),
                last_event_sequence: 0,
            };
            let frame_audio = audio(beat, if beat { 0.95 } else { 0.0 }, 0.95);
            let (_, _, _, state) = scheduler.update(
                Some(&config),
                Some(&frame_audio),
                Some(structure),
                clock,
                now_ns,
                true,
                false,
            );
            rhythmic_activations += usize::from(state.rhythmic_result == "activated");
            if state.drop_accent_status == "activated" && previous_drop_status != "activated" {
                drop_activations += 1;
            }
            previous_drop_status.clone_from(&state.drop_accent_status);

            assert!(scheduler.active.len() <= config.max_active_effects);
            assert!(scheduler.pending_beats.len() <= MAX_PENDING_BEATS);
            assert!(scheduler.recent_grid_indices.len() <= MAX_RECENT_GRID_INDICES);
            assert!(scheduler.beat_lateness_samples_ns.len() <= 256);
        }

        assert!(rhythmic_activations >= 100);
        assert!(drop_activations >= 5);
    }
}

fn drop_accent_reason(result: &str) -> String {
    match result {
        "cooldown" => "accent-cooldown",
        "category-active" => "accent-already-active",
        "capacity" => "effect-capacity",
        "transition" => "transition-in-progress",
        "no-candidate" => "drop-effect-unavailable",
        "below-threshold" => "below-threshold",
        _ => "not-evaluated",
    }
    .to_owned()
}
