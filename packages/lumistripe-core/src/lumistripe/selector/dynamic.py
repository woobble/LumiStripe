from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.base import AnimationPlayer
from ..audio import AudioFeatures
from .scoring import AnimationScoringEngine, DynamicSelectorConfig, SelectorDecision


@dataclass(frozen=True, slots=True)
class DynamicSelectorDiagnostics:
    current_name: str | None
    elapsed_s: float
    min_duration_remaining_s: float
    max_duration_remaining_s: float
    switch_cooldown_remaining_s: float
    drop_cooldown_remaining_s: float
    recent_names: tuple[str, ...]


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
    _last_update_at_s: float = 0.0

    def __post_init__(self) -> None:
        self.engine = AnimationScoringEngine(self.config)

    def reset(self) -> None:
        self.current_name = None
        self.last_switch_at_s = 0.0
        self.last_drop_switch_at_s = -9999.0
        self.recent_names.clear()
        self.last_decision = SelectorDecision(None, 0.0, None, False, "not_started")
        self._last_update_at_s = 0.0

    def diagnostics(self, *, now_s: float | None = None) -> DynamicSelectorDiagnostics:
        now = self._last_update_at_s if now_s is None else now_s
        elapsed = max(0.0, now - self.last_switch_at_s)
        drop_elapsed = max(0.0, now - self.last_drop_switch_at_s)
        return DynamicSelectorDiagnostics(
            current_name=self.current_name,
            elapsed_s=elapsed,
            min_duration_remaining_s=max(0.0, self.config.min_duration_s - elapsed),
            max_duration_remaining_s=max(0.0, self.config.max_duration_s - elapsed),
            switch_cooldown_remaining_s=max(
                0.0, self.config.switch_cooldown_s - elapsed
            ),
            drop_cooldown_remaining_s=max(
                0.0, self.config.drop_cooldown_s - drop_elapsed
            ),
            recent_names=tuple(self.recent_names),
        )

    def update(
        self,
        player: AnimationPlayer,
        features: AudioFeatures,
        *,
        now_s: float,
        quiet: bool = False,
        force_switch: bool = False,
    ) -> SelectorDecision:
        self._last_update_at_s = now_s
        current = player.name_at(player.current_index())
        candidates = [
            entry
            for entry in player.animations
            if entry.automatic
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
            elif bool(getattr(features, "drop_detected", False)):
                reason = "drop_hold"
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
