from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.base import AnimationPlayer
from ..audio import AudioFeatures
from .metadata import animation_metadata
from .scoring import AnimationScoringEngine, DynamicSelectorConfig, SelectorDecision


@dataclass(slots=True)
class DynamicSelector:
    config: DynamicSelectorConfig = field(default_factory=DynamicSelectorConfig)
    engine: AnimationScoringEngine = field(init=False)
    current_name: str | None = None
    last_switch_at_s: float = 0.0
    last_drop_switch_at_s: float = -9999.0
    recent_names: list[str] = field(default_factory=list)
    last_decision: SelectorDecision = field(
        default_factory=lambda: SelectorDecision(None, 0.0, None, False, "not_started")
    )

    def __post_init__(self) -> None:
        self.engine = AnimationScoringEngine(self.config)

    def reset(self) -> None:
        self.current_name = None
        self.last_switch_at_s = 0.0
        self.last_drop_switch_at_s = -9999.0
        self.recent_names.clear()
        self.last_decision = SelectorDecision(None, 0.0, None, False, "not_started")

    def update(
        self,
        player: AnimationPlayer,
        features: AudioFeatures,
        *,
        now_s: float,
        quiet: bool = False,
        force_switch: bool = False,
    ) -> SelectorDecision:
        current = player.name_at(player.current_index())
        candidates = [
            entry
            for entry in player.animations
            if entry.automatic
            and (not quiet or animation_metadata(entry.animation).supports_silence)
        ]
        if self.current_name is None:
            self.current_name = current
            self.last_switch_at_s = now_s
            ranked = self.engine.rank(
                candidates,
                features,
                current_name=current,
                recent_names=tuple(self.recent_names),
            )
            best = ranked[0] if ranked else None
            selected = best.name if best is not None else current
            should_switch = selected is not None and selected != current
            if selected is not None and selected != current:
                index = player.index_of(selected)
                if index is not None:
                    player.set_index(index)
                    self._remember(current)
                    self.current_name = selected
            self.last_decision = SelectorDecision(
                selected_name=selected,
                selected_score=best.score if best is not None else 0.0,
                current_name=current,
                should_switch=should_switch,
                reason="initial" if should_switch else "initial_hold",
                scores=ranked[:5],
            )
            return self.last_decision

        ranked = self.engine.rank(
            candidates,
            features,
            current_name=current,
            recent_names=tuple(self.recent_names),
        )
        best = ranked[0] if ranked else None
        current_score = next((score for score in ranked if score.name == current), None)
        elapsed = now_s - self.last_switch_at_s
        cooldown_ready = elapsed >= self.config.switch_cooldown_s
        min_ready = elapsed >= self.config.min_duration_s
        max_due = elapsed >= self.config.max_duration_s
        drop_ready = (
            bool(getattr(features, "drop_detected", False))
            and min_ready
            and cooldown_ready
            and (now_s - self.last_drop_switch_at_s) >= self.config.drop_cooldown_s
        )

        should_switch = False
        reason = "hold"
        selected_name = current
        selected_score = current_score.score if current_score is not None else 0.0

        if best is None:
            reason = "no_candidates"
        elif current is None:
            should_switch = True
            selected_name = best.name
            selected_score = best.score
            reason = "initial"
        elif best.name == current:
            selected_name = current
            selected_score = best.score
            reason = "best_is_current"
        else:
            score_gap = best.score - (
                current_score.score if current_score is not None else 0.0
            )
            if force_switch:
                should_switch = True
                reason = "music_state"
            elif drop_ready and best.metadata.supports_drops:
                should_switch = True
                reason = "drop"
            elif (
                bool(getattr(features, "section_change", False))
                and min_ready
                and cooldown_ready
                and score_gap >= self.config.switch_margin * 0.5
            ):
                should_switch = True
                reason = "section_change"
            elif max_due and cooldown_ready:
                should_switch = True
                reason = "max_duration"
            elif (
                min_ready and cooldown_ready and score_gap >= self.config.switch_margin
            ):
                should_switch = True
                reason = f"score_gap={score_gap:0.2f}"
            selected_name = best.name if should_switch else current
            selected_score = best.score if should_switch else selected_score

        if should_switch and selected_name is not None:
            index = player.index_of(selected_name)
            if index is not None and index != player.current_index():
                previous = current
                player.set_index(index)
                self._remember(previous)
                self.current_name = selected_name
                self.last_switch_at_s = now_s
                if reason == "drop":
                    self.last_drop_switch_at_s = now_s

        self.last_decision = SelectorDecision(
            selected_name=selected_name,
            selected_score=selected_score,
            current_name=current,
            should_switch=should_switch,
            reason=reason,
            scores=ranked[:5],
        )
        return self.last_decision

    def _remember(self, name: str | None) -> None:
        if not name:
            return
        self.recent_names = [item for item in self.recent_names if item != name]
        self.recent_names.append(name)
        if len(self.recent_names) > self.config.history_size:
            self.recent_names = self.recent_names[-self.config.history_size :]
