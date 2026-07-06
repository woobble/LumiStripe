from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TextIO

from lumistripe import (
    AnimationClass,
    AnimationPlayer,
    AutoSelectorConfig,
    AudioAnalysis,
    AudioConfig,
    AudioCalibrationResult,
    AudioFrame,
    AudioInput,
    AudioNormalization,
    AudioSnapshot,
    AudioSmoothing,
    CLASS_MAP,
    Config,
    Controller,
    GPIOStripe,
    DJModeSelector,
    MultiController,
    MusicDrivenSelector,
    MusicFeatures,
    MusicSelectorConfig,
    MicProfile,
    Stripe,
    calibrate_audio_input,
    features_from_frame,
    list_input_device_details,
    load_mic_profile,
    write_mic_profile,
)
from .encoder import ControlEvent, EncoderBackend, EncoderPins, NullEncoderBackend, build_encoder_backend

MIN_FRAME_SECONDS = 0.016
STATUS_INTERVAL_SECONDS = 0.25
DEFAULT_IDLE_THRESHOLD_SCALE = 1.0
BRIGHTNESS_STEP = 0.05


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


class AudioDebugRecorder:
    def __init__(self, path: Path, *, label: str | None = None) -> None:
        self.path = path
        self.label = label
        self._file: TextIO | None = None

    def __enter__(self) -> AudioDebugRecorder:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def write(self, row: dict[str, object]) -> None:
        if self._file is None:
            raise RuntimeError("audio debug recorder is not open")
        if self.label:
            row = {"label": self.label, **row}
        self._file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
        self._file.write("\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is None:
            return
        self._file.close()
        self._file = None


class RuntimeMode(str, Enum):
    MANUAL = "manual"
    DEMO = "demo"
    MIC = "mic"
    DJ = "dj"


def demo_frame(frame: int) -> AudioFrame:
    frames_per_beat = 25
    frames_per_measure = frames_per_beat * 4

    measure_pos = frame % frames_per_measure
    beat_idx = measure_pos // frames_per_beat
    phase = (measure_pos % frames_per_beat) / frames_per_beat

    decay = pow(2.718281828, -phase * 5.0)
    fast = pow(2.718281828, -phase * 12.0)
    slow = pow(2.718281828, -phase * 2.0)

    kick = fast if beat_idx in (0, 2) else 0.001
    snare = decay * 0.8 if beat_idx in (1, 3) else 0.001
    bass_harmonic = (1.0, 1.0, 1.0, 0.75)[(frame // frames_per_measure) % 4]
    bass = slow * 0.6 * bass_harmonic

    pos_in_beat = measure_pos % frames_per_beat
    hat = 0.35 if pos_in_beat < 2 or 12 <= pos_in_beat < 14 else 0.001
    accent = 0.2 if 62 <= measure_pos < 64 else 0.0

    bands = (
        min(kick, 1.0),
        min(bass, 1.0),
        min(snare * 0.3 + kick * 0.15, 1.0),
        min(snare, 1.0),
        min(snare * 0.5 + hat * 0.3, 1.0),
        min(hat + accent, 1.0),
        min((hat + accent) * 0.4, 1.0),
        min((hat + accent) * 0.15, 1.0),
    )
    rms = min((sum(bands) / len(bands)) ** 0.5, 1.0)
    beat = beat_idx == 0
    beat_strength = 0.7 + kick * 0.3 if beat else 0.0
    return AudioFrame(rms=rms, bands=bands, beat=beat, beat_strength=beat_strength, sequence=frame + 1, timestamp=time.monotonic(), fresh=True)


@dataclass(slots=True)
class HeadlessApp:
    controller: Controller
    pixel_count: int
    mode: RuntimeMode = RuntimeMode.MANUAL
    audio_device: str | None = None
    mic_target_level: float = field(default_factory=lambda: AudioNormalization().target_level)
    mic_noise_floor: float = field(default_factory=lambda: AudioSmoothing().noise_floor)
    audio_analysis: AudioAnalysis = field(default_factory=AudioAnalysis)
    idle_enter_frames: int = field(default_factory=lambda: MusicSelectorConfig().idle_enter_frames)
    idle_threshold_scale: float = DEFAULT_IDLE_THRESHOLD_SCALE
    audio_debug_verbose: bool = False
    audio_calibration: AudioCalibrationResult | None = None
    animation_name: str | None = None
    quiet: bool = False
    gpio_backend_label: str | None = None
    auto_selector_config: AutoSelectorConfig = field(default_factory=AutoSelectorConfig)
    debug_selector: bool = False
    encoder_backend: EncoderBackend = field(default_factory=NullEncoderBackend)
    player: AnimationPlayer = field(init=False)
    running: bool = field(init=False, default=True)
    audio_input: AudioInput | None = field(init=False, default=None)
    audio_status: str = field(init=False, default="No audio source active.")
    audio_error: str | None = field(init=False, default=None)
    audio_frame: AudioFrame = field(init=False, default_factory=AudioFrame)
    music_features: MusicFeatures = field(init=False, default_factory=MusicFeatures)
    audio_snapshot: AudioSnapshot = field(init=False, default_factory=AudioSnapshot.silence)
    demo_tick: int = field(init=False, default=0)
    selector: MusicDrivenSelector | None = field(init=False, default=None)
    dj_selector: DJModeSelector | None = field(init=False, default=None)
    _status_enabled: bool = field(init=False, default=False)
    _status_tty: bool = field(init=False, default=False)
    _status_lines: int = field(init=False, default=0)
    _last_status_at: float = field(init=False, default=0.0)
    _last_debug_class: str = field(init=False, default="-")
    _auto_select_enabled: bool = field(init=False, default=True)

    def __post_init__(self) -> None:
        self.player = AnimationPlayer.party()
        self.player.set_brightness(1.0)
        if self.animation_name is not None:
            index = self.player.index_of(self.animation_name)
            if index is None:
                raise ValueError(f"unknown animation: {self.animation_name}")
            self.player.set_index(index)
        self._status_enabled = not self.quiet
        self._status_tty = self._status_enabled and sys.stdout.isatty()
        self.set_mode(self.mode)

    @property
    def current_animation_label(self) -> str:
        raw = self.player.name_at(self.player.current_index()) or "?"
        classes = CLASS_MAP.get(raw, ())
        label = raw.upper()
        if classes:
            tags = ", ".join(c.value.upper() for c in classes)
            label = f"{label}  [{tags}]"
        return label

    @property
    def mode_label(self) -> str:
        return self.mode.value.upper()

    @property
    def class_label(self) -> str:
        if self.selector is None:
            return "DJ" if self.dj_selector is not None else "-"
        if self.selector.idle_active:
            return "IDLE"
        return self.selector.current_class.value.upper()

    def analysis_text(self) -> str:
        frame = self.audio_frame
        feat = self.music_features
        beat = "YES" if frame.beat else "NO"
        bands = " ".join(f"{value:0.2f}" for value in frame.bands)
        return (
            f"RMS: {frame.rms:0.3f}    BEAT: {beat}    BPM: {feat.bpm:3.0f}\n"
            f"BRIGHT: {feat.brightness:0.2f}    ONSET: {feat.onset_strength:0.3f}    DYN: {feat.dynamic_range:0.3f}\n"
            f"BANDS: {bands}"
        )

    @property
    def mic_tuning_label(self) -> str:
        label = (
            f"target={self.mic_target_level:0.2f} "
            f"noise={self.mic_noise_floor:0.3f} "
            f"idle={self.idle_enter_frames}f "
            f"scale={self.idle_threshold_scale:0.2f}"
        )
        if self.audio_calibration is not None:
            label = f"{label} calibrated={self.audio_calibration.samples}f"
        return label

    @property
    def brightness_label(self) -> str:
        return f"{self.player.brightness:0.2f}"

    def set_mode(self, mode: RuntimeMode) -> None:
        self._close_audio_input()
        self.player.clear_audio_snapshot()
        self.mode = mode
        self.audio_error = None
        self.audio_frame = AudioFrame()
        self.music_features = MusicFeatures()
        self.audio_snapshot = AudioSnapshot.silence()
        self.selector = None
        self.dj_selector = None

        if mode is RuntimeMode.MANUAL:
            self.audio_status = "No audio source active."
            return

        if mode is RuntimeMode.DEMO:
            self.demo_tick = 0
            self.audio_status = "Using internal demo beat."
            self.player.set_audio_snapshot(self._demo_snapshot)
            return

        try:
            audio_config = _build_audio_config(
                target_level=self.mic_target_level,
                noise_floor=self.mic_noise_floor,
                analysis=self.audio_analysis,
            )
            self.audio_input = (
                AudioInput.with_device_config(self.audio_device, audio_config)
                if self.audio_device
                else AudioInput.with_config(audio_config)
            )
        except RuntimeError as exc:
            self.mode = RuntimeMode.MANUAL
            self.audio_error = str(exc)
            self.audio_status = "Microphone unavailable."
            return

        self.audio_status = f"Input: {self.audio_input.device_name()}"
        if mode is RuntimeMode.DJ:
            self.dj_selector = DJModeSelector(self.auto_selector_config)
        else:
            self.selector = MusicDrivenSelector(
                config=_build_selector_config(
                    idle_enter_frames=self.idle_enter_frames,
                    idle_threshold_scale=self.idle_threshold_scale,
                ),
                current_class=AnimationClass.GROOVY,
            )
            self.selector.set_auto_select(self._auto_select_enabled)
        self.player.set_audio_snapshot(self._mic_snapshot)

    def _demo_snapshot(self) -> AudioFrame:
        frame = demo_frame(self.demo_tick)
        self.demo_tick += 1
        self.audio_frame = frame
        self.music_features = features_from_frame(frame)
        self.audio_snapshot = AudioSnapshot.from_parts(frame, self.music_features)
        return frame

    def _mic_snapshot(self) -> AudioFrame:
        return self.audio_snapshot.frame

    def _close_audio_input(self) -> None:
        if self.audio_input is None:
            return
        self.audio_input.close()
        self.audio_input = None

    def step(self) -> float:
        if self.mode in {RuntimeMode.MIC, RuntimeMode.DJ} and self.audio_input is not None:
            audio_frame = self.audio_input.read()
            health = self._audio_health()
            self.audio_frame = audio_frame
            self.music_features = self.audio_input.read_features() if audio_frame.fresh else MusicFeatures()
            self.audio_snapshot = (
                AudioSnapshot.from_parts(audio_frame, self.music_features, health)
                if audio_frame.fresh
                else AudioSnapshot.silence(frame=audio_frame, health=health)
            )
            if self.selector is not None:
                self.selector.update(self.player, self.audio_snapshot.features)
                self.player.audio_enabled = not self.selector.idle_active
            if self.dj_selector is not None:
                self.dj_selector.update(self.player, self.audio_snapshot.features, now_s=time.monotonic())
                self.player.audio_enabled = not self.audio_snapshot.silence
        delay = max(self.player.step(self.controller), MIN_FRAME_SECONDS)
        if self.mode is RuntimeMode.MANUAL:
            self.audio_frame = AudioFrame()
            self.music_features = MusicFeatures()
            self.audio_snapshot = AudioSnapshot.silence()
        return delay

    def apply_control_events(self, events: list[ControlEvent]) -> None:
        for event in events:
            self.apply_control_event(event)

    def apply_control_event(self, event: ControlEvent) -> None:
        if event.kind == "rotate" and event.source == "encoder1":
            self._handle_animation_rotation(event.value)
            return
        if event.kind == "rotate" and event.source == "encoder2":
            self.player.set_brightness(self.player.brightness + (BRIGHTNESS_STEP * event.value))
            return
        if event.kind == "press" and event.source == "encoder1":
            self._cycle_mode()
            return
        if event.kind == "press" and event.source == "encoder2":
            self._set_auto_select(not self._auto_select_enabled)

    def _handle_animation_rotation(self, step: int) -> None:
        self._set_auto_select(False)
        if step > 0:
            for _ in range(step):
                self.player.next()
            return
        for _ in range(abs(step)):
            self.player.prev()

    def _cycle_mode(self) -> None:
        if self.mode is RuntimeMode.MANUAL:
            self.set_mode(RuntimeMode.DEMO)
        elif self.mode is RuntimeMode.DEMO:
            self.set_mode(RuntimeMode.MIC)
        elif self.mode is RuntimeMode.MIC:
            self.set_mode(RuntimeMode.DJ)
        else:
            self.set_mode(RuntimeMode.MANUAL)

    def _set_auto_select(self, enabled: bool) -> None:
        self._auto_select_enabled = enabled
        if self.selector is not None:
            self.selector.set_auto_select(enabled)

    def run(self, *, frame_limit: int | None = None) -> None:
        frames_run = 0
        next_frame_at = time.monotonic()
        try:
            while self.running:
                self.apply_control_events(self.encoder_backend.read_events())
                now = time.monotonic()
                if now < next_frame_at:
                    time.sleep(next_frame_at - now)
                    continue
                next_frame_at = now + self.step()
                self._render_status(now)
                frames_run += 1
                if frame_limit is not None and frames_run >= frame_limit:
                    break
        except KeyboardInterrupt:
            self.running = False
        finally:
            self.controller.clear()
            self.controller.force_flush()
            self._finish_status()
            self.encoder_backend.close()
            self._close_audio_input()

    def _render_status(self, now: float) -> None:
        if not self._status_enabled:
            return
        if not self._status_due(now):
            return
        block = self._status_block()
        if self._status_tty:
            self._write_live_status(block)
            return
        print(block, flush=True)
        self._last_status_at = now

    def _status_block(self) -> str:
        auto_status = "ON" if self._auto_select_enabled else "OFF"
        lines = [
            f"ANIM: {self.current_animation_label}",
            f"MODE: {self.mode_label}  |  AUTO: {auto_status}  |  OUT: {self.brightness_label}",
            f"SOURCE: {self.audio_status}",
            f"CLASS: {self.class_label}",
            self.analysis_text(),
        ]
        if self.gpio_backend_label:
            lines.insert(3, f"GPIO: {self.gpio_backend_label}")
        if self.mode in {RuntimeMode.MIC, RuntimeMode.DJ}:
            lines.insert(4, f"MIC: {self.mic_tuning_label}")
            health = self._audio_health_summary()
            if health:
                lines.insert(5, f"HEALTH: {health}")
        if self.dj_selector is not None and self.dj_selector.last_decision.scores:
            lines.append(f"SELECTOR: {self._selector_top_summary()} reason={self.dj_selector.last_decision.reason}")
        if self.audio_error:
            lines.append(f"ERROR: {self.audio_error}")
        return "\n".join(lines)

    def _write_live_status(self, block: str) -> None:
        lines = block.count("\n") + 1
        if self._status_lines:
            sys.stdout.write(f"\x1b[{self._status_lines}F")
        sys.stdout.write("\x1b[J")
        sys.stdout.write(block)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_lines = lines
        self._last_status_at = time.monotonic()

    def _status_due(self, now: float) -> bool:
        return self._last_status_at == 0.0 or (now - self._last_status_at) >= STATUS_INTERVAL_SECONDS

    def _finish_status(self) -> None:
        if not self._status_enabled or not self._status_tty:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._status_lines = 0

    def debug_header(self) -> str:
        verbose = " verbose=ON" if self.audio_debug_verbose else ""
        return f"AUDIO-DEBUG SOURCE={self.audio_status} MIC={self.mic_tuning_label}{verbose}"

    def _selector_scores(self) -> dict[str, float]:
        if self.dj_selector is not None:
            return {score.name: score.score for score in self.dj_selector.last_decision.scores}
        if self.selector is None:
            return {}
        return {class_.value: score for class_, score in self.selector._class_scores().items()}

    def _selector_top_summary(self) -> str:
        scores = self._selector_scores()
        if not scores:
            return "TOP=-"
        top = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]
        formatted = ",".join(f"{name}:{score:0.2f}" for name, score in top)
        return f"TOP={formatted}"

    def _selector_verbose_summary(self) -> str:
        if self.selector is None:
            if self.dj_selector is not None:
                decision = self.dj_selector.last_decision
                reasons = ";".join(
                    f"{score.name}:{score.score:0.2f}[{'/'.join(score.reasons[:3])}]"
                    for score in decision.scores
                )
                return f"SEL=reason:{decision.reason},switch:{decision.should_switch} SCORES={reasons or '-'}"
            return "SEL=-"
        sel = self.selector
        scores = ",".join(
            f"{name}:{score:0.2f}" for name, score in sorted(self._selector_scores().items(), key=lambda item: item[0])
        )
        return (
            f"SEL=energy_short:{sel.energy_short:0.3f},"
            f"energy_long:{sel.energy_long:0.3f},"
            f"beat_density:{sel.beat_density:0.3f},"
            f"brightness:{sel.brightness_smooth:0.3f},"
            f"onset:{sel.onset_smooth:0.3f},"
            f"bass:{sel.bass_short:0.3f},"
            f"mid:{sel.mid_short:0.3f},"
            f"high:{sel.high_short:0.3f} "
            f"SCORES={scores}"
        )

    def debug_transition_line(self, elapsed: float, previous_class: str, current_class: str) -> str:
        score_snapshot = ",".join(
            f"{name}:{score:0.2f}" for name, score in sorted(self._selector_scores().items(), key=lambda item: item[1], reverse=True)
        ) or "-"
        return (
            f"t={elapsed:0.2f}s "
            f"TRANSITION CLASS={previous_class}->{current_class} "
            f"{self._selector_top_summary()} "
            f"SCORES={score_snapshot}"
        )

    def debug_log_line(self, elapsed: float) -> str:
        frame = self.audio_frame
        feat = self.music_features
        snapshot = self.audio_snapshot
        beat = "YES" if frame.beat else "NO"
        fresh = "YES" if frame.fresh else "NO"
        idle = "YES" if self.selector is not None and self.selector.idle_active else "NO"
        auto = "DJ" if self.dj_selector is not None else ("ON" if self.selector is not None and self.selector.auto_select else "OFF")
        anim = self.player.name_at(self.player.current_index()) or "?"
        bands = ",".join(f"{value:0.2f}" for value in frame.bands)
        silence = "YES" if feat.silence else "NO"
        drop = "YES" if feat.drop_detected else "NO"
        section = "YES" if feat.section_change else "NO"
        health = self._audio_health()
        age = health.last_frame_age if health is not None else None
        stats = health.processor if health is not None else None
        age_text = "-" if age is None else f"{age:0.3f}"
        fft_count = stats.fft_count if stats is not None else 0
        gain = stats.normalization_gain if stats is not None else 1.0
        status_count = health.status_count if health is not None else 0
        line = (
            f"t={elapsed:0.2f}s "
            f"SRC={self.audio_status} "
            f"CLASS={self.class_label} "
            f"ANIM={anim.upper()} "
            f"AUTO={auto} "
            f"IDLE={idle} "
            f"FRESH={fresh} "
            f"AGE={age_text} "
            f"SEQ={frame.sequence} "
            f"FFT={fft_count} "
            f"GAIN={gain:0.2f} "
            f"STATUS={status_count} "
            f"BASS={feat.bass_energy:0.2f} "
            f"MID={feat.mid_energy:0.2f} "
            f"TREBLE={feat.treble_energy:0.2f} "
            f"CENTROID={feat.spectral_centroid:0.2f} "
            f"FLUX={feat.spectral_flux:0.2f} "
            f"LOUD={feat.rolling_loudness:0.2f} "
            f"SILENCE={silence} "
            f"DROP={drop} "
            f"SECTION={section} "
            f"DRIVE={snapshot.drive:0.2f} "
            f"ACCENT={snapshot.accent:0.2f} "
            f"ACTIVITY={snapshot.activity:0.2f} "
            f"RMS={frame.rms:0.3f} "
            f"BEAT={beat} "
            f"BPM={feat.bpm:0.0f} "
            f"BRIGHT={feat.brightness:0.2f} "
            f"ONSET={feat.onset_strength:0.3f} "
            f"DYN={feat.dynamic_range:0.3f} "
            f"BANDS={bands} "
            f"{self._selector_top_summary()}"
        )
        if self.audio_debug_verbose or self.debug_selector:
            line = f"{line} {self._selector_verbose_summary()}"
        return line

    def audio_debug_record(self, elapsed: float) -> dict[str, object]:
        frame = self.audio_frame
        feat = self.music_features
        snapshot = self.audio_snapshot
        health = self._audio_health()
        stats = health.processor if health is not None else None
        decision = self.dj_selector.last_decision if self.dj_selector is not None else None
        return {
            "elapsed_s": round(elapsed, 6),
            "source": self.audio_status,
            "mode": self.mode.value,
            "class": self.class_label,
            "animation": self.player.name_at(self.player.current_index()) or "",
            "auto": "dj" if self.dj_selector is not None else ("on" if self.selector is not None and self.selector.auto_select else "off"),
            "fresh": frame.fresh,
            "age_s": None if health is None else health.last_frame_age,
            "callback_age_s": None if health is None else health.last_callback_age,
            "sequence": frame.sequence,
            "fft_count": 0 if stats is None else stats.fft_count,
            "status_count": 0 if health is None else health.status_count,
            "normalization_gain": 1.0 if stats is None else stats.normalization_gain,
            "rms": frame.rms,
            "beat": frame.beat,
            "beat_strength": frame.beat_strength,
            "bpm": feat.bpm,
            "bpm_confidence": feat.bpm_confidence,
            "energy": feat.energy,
            "volume": feat.volume,
            "energy_level": feat.energy_level,
            "bass": feat.bass,
            "brightness": feat.brightness,
            "onset_strength": feat.onset_strength,
            "dynamic_range": feat.dynamic_range,
            "bands": list(frame.bands),
            "bass_energy": feat.bass_energy,
            "mid_energy": feat.mid_energy,
            "treble_energy": feat.treble_energy,
            "spectral_centroid": feat.spectral_centroid,
            "spectral_flux": feat.spectral_flux,
            "beat_confidence": feat.beat_confidence,
            "rolling_loudness": feat.rolling_loudness,
            "silence": feat.silence,
            "drop_detected": feat.drop_detected,
            "section_change": feat.section_change,
            "drive": snapshot.drive,
            "accent": snapshot.accent,
            "activity": snapshot.activity,
            "selector_reason": "" if decision is None else decision.reason,
            "selector_should_switch": False if decision is None else decision.should_switch,
            "selector_scores": self._selector_scores(),
        }

    def _audio_health(self):
        if self.audio_input is None or not hasattr(self.audio_input, "health"):
            return None
        return self.audio_input.health()

    def _audio_health_summary(self) -> str:
        health = self._audio_health()
        if health is None:
            return ""
        age = "-" if health.last_frame_age is None else f"{health.last_frame_age:0.3f}s"
        callback_age = "-" if health.last_callback_age is None else f"{health.last_callback_age:0.3f}s"
        return (
            f"fresh={'YES' if self.audio_frame.fresh else 'NO'} "
            f"age={age} cb_age={callback_age} "
            f"seq={self.audio_frame.sequence} fft={health.processor.fft_count} "
            f"gain={health.processor.normalization_gain:0.2f} status={health.status_count}"
        )

    def run_audio_debug(
        self,
        *,
        frame_limit: int | None = None,
        duration_s: float | None = None,
        recorder: AudioDebugRecorder | None = None,
    ) -> None:
        started_at = time.monotonic()
        frames_run = 0
        next_frame_at = started_at
        self._last_debug_class = self.class_label
        print(self.debug_header(), flush=True)
        try:
            while self.running:
                now = time.monotonic()
                if now < next_frame_at:
                    time.sleep(next_frame_at - now)
                    continue
                next_frame_at = now + self.step()
                current_class = self.class_label
                if current_class != self._last_debug_class:
                    print(self.debug_transition_line(now - started_at, self._last_debug_class, current_class), flush=True)
                    self._last_debug_class = current_class
                elapsed = now - started_at
                print(self.debug_log_line(elapsed), flush=True)
                if recorder is not None:
                    recorder.write(self.audio_debug_record(elapsed))
                frames_run += 1
                if frame_limit is not None and frames_run >= frame_limit:
                    break
                if duration_s is not None and elapsed >= duration_s:
                    break
        except KeyboardInterrupt:
            self.running = False
        finally:
            self.controller.clear()
            self.controller.force_flush()
            self.encoder_backend.close()
            self._close_audio_input()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lumistripe")
    parser.add_argument("--pixels", type=int, default=80, help="Number of LEDs per stripe")
    parser.add_argument(
        "--mode",
        type=_parse_mode,
        default=RuntimeMode.MANUAL,
        help="Startup mode: manual, demo, mic, or dj",
    )
    parser.add_argument(
        "--auto-selector",
        choices=("dj",),
        help="Enable an automatic selector mode; currently equivalent to --mode dj",
    )
    parser.add_argument("--animation", help="Startup animation name for manual mode")
    parser.add_argument(
        "--audio-device",
        help="Input device index or substring match for the device name when using mic mode",
    )
    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--audio-debug",
        action="store_true",
        help="Run audio-only mic analysis and selector logging without GPIO output",
    )
    parser.add_argument(
        "--audio-debug-verbose",
        action="store_true",
        help="Include selector internals and full class scores in audio debug logs",
    )
    parser.add_argument(
        "--audio-debug-record",
        type=Path,
        metavar="PATH",
        help="Write structured audio debug rows as JSONL",
    )
    parser.add_argument(
        "--audio-debug-duration",
        type=_positive_float,
        metavar="SECONDS",
        help="Stop audio debug capture after the given duration",
    )
    parser.add_argument(
        "--audio-debug-label",
        help="Attach a song or test label to recorded audio debug rows",
    )
    parser.add_argument(
        "--analyze-audio-debug",
        type=Path,
        nargs="+",
        metavar="PATH",
        help="Analyze one or more JSONL audio debug recordings and print tuning suggestions",
    )
    parser.add_argument(
        "--debug-selector",
        action="store_true",
        help="Include selector decision details in runtime/debug output",
    )
    parser.add_argument(
        "--calibrate-audio",
        type=_positive_float,
        metavar="SECONDS",
        help="Measure the selected audio input and print recommended mic tuning flags",
    )
    parser.add_argument(
        "--auto-calibrate-audio",
        type=_positive_float,
        metavar="SECONDS",
        help="Measure the selected audio input and apply recommended mic tuning before mic mode starts",
    )
    parser.add_argument(
        "--mic-target-level",
        type=_positive_float,
        default=AudioNormalization().target_level,
        help="Normalized input level target for mic mode calibration",
    )
    parser.add_argument(
        "--mic-noise-floor",
        type=_non_negative_float,
        default=AudioSmoothing().noise_floor,
        help="Noise floor threshold for mic mode calibration",
    )
    parser.add_argument(
        "--mic-profile",
        help='Mic profile name, JSON path, or "auto" for device-name matching',
    )
    parser.add_argument(
        "--write-mic-profile",
        type=Path,
        metavar="PATH",
        help="Write the effective mic tuning/profile to a JSON file and exit",
    )
    parser.add_argument(
        "--idle-enter-frames",
        type=_positive_int,
        default=MusicSelectorConfig().idle_enter_frames,
        help="Consecutive low-activity frames before idle activates in mic mode",
    )
    parser.add_argument(
        "--idle-threshold-scale",
        type=_positive_float,
        default=DEFAULT_IDLE_THRESHOLD_SCALE,
        help="Scale factor applied to mic idle activity thresholds",
    )
    parser.add_argument("--dj-min-duration", type=_positive_float, default=AutoSelectorConfig().min_duration_s)
    parser.add_argument("--dj-max-duration", type=_positive_float, default=AutoSelectorConfig().max_duration_s)
    parser.add_argument("--dj-switch-cooldown", type=_positive_float, default=AutoSelectorConfig().switch_cooldown_s)
    parser.add_argument("--dj-drop-cooldown", type=_positive_float, default=AutoSelectorConfig().drop_cooldown_s)
    parser.add_argument("--dj-randomness", type=_non_negative_float, default=AutoSelectorConfig().randomness)
    parser.add_argument("--dj-history-size", type=_positive_int, default=AutoSelectorConfig().history_size)
    parser.add_argument("--dj-seed", type=int)
    parser.add_argument("--quiet", action="store_true", help="Disable runtime status output")
    parser.add_argument("--chip", default="/dev/gpiochip0", help="Primary GPIO chip path")
    parser.add_argument("--data-pin", type=int, default=14, help="Primary stripe data pin")
    parser.add_argument("--clock-pin", type=int, default=15, help="Primary stripe clock pin")
    parser.add_argument("--chip-2", help="Secondary GPIO chip path")
    parser.add_argument("--data-pin-2", type=int, help="Secondary stripe data pin")
    parser.add_argument("--clock-pin-2", type=int, help="Secondary stripe clock pin")
    parser.add_argument("--encoder-chip", help="GPIO chip path for encoder inputs")
    parser.add_argument("--encoder1-a", type=int, help="Encoder 1 A phase input pin")
    parser.add_argument("--encoder1-b", type=int, help="Encoder 1 B phase input pin")
    parser.add_argument("--encoder1-button", type=int, help="Encoder 1 button input pin")
    parser.add_argument("--encoder2-a", type=int, help="Encoder 2 A phase input pin")
    parser.add_argument("--encoder2-b", type=int, help="Encoder 2 B phase input pin")
    parser.add_argument("--encoder2-button", type=int, help="Encoder 2 button input pin")
    return parser


def build_output_controller(args: argparse.Namespace) -> Controller:
    _ensure_gpio_ready(args.chip)
    primary = GPIOStripe(
        Config(chip=args.chip, gpio_data=args.data_pin, gpio_clock=args.clock_pin, consumer="lumistripe"),
        args.pixels,
    )
    if not _secondary_stripe_requested(args):
        return primary
    if args.data_pin_2 is None or args.clock_pin_2 is None:
        raise ValueError("secondary stripe requires both --data-pin-2 and --clock-pin-2")
    secondary_chip = args.chip_2 or args.chip
    _ensure_gpio_ready(secondary_chip)
    secondary = GPIOStripe(
        Config(
            chip=secondary_chip,
            gpio_data=args.data_pin_2,
            gpio_clock=args.clock_pin_2,
            consumer="lumistripe_2",
        ),
        args.pixels,
    )
    return MultiController([primary, secondary])


def gpio_backend_label(controller: Controller) -> str | None:
    if isinstance(controller, MultiController):
        labels = [gpio_backend_label(child) for child in controller.controllers]
        compact = [label for label in labels if label]
        return ", ".join(compact) if compact else None
    label = getattr(controller, "gpio_backend_label", None)
    return str(label) if label else None


def build_runtime_encoder_backend(args: argparse.Namespace) -> EncoderBackend:
    encoder1 = _encoder_pins_from_args(args, "encoder1")
    encoder2 = _encoder_pins_from_args(args, "encoder2")
    if encoder1 is None and encoder2 is None:
        return NullEncoderBackend()
    encoder_chip = args.encoder_chip or args.chip
    _ensure_gpio_input_ready(encoder_chip)
    return build_encoder_backend(
        encoder_chip,
        encoder1=encoder1,
        encoder2=encoder2,
    )


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.audio_analysis = AudioAnalysis()
    if args.auto_selector == "dj":
        args.mode = RuntimeMode.DJ
    if args.analyze_audio_debug:
        print(analyze_audio_debug(args.analyze_audio_debug))
        return
    if args.list_audio_devices:
        for device in list_input_device_details():
            print(f"{device.index}: {device.name}")
        return
    try:
        if args.calibrate_audio is not None:
            result = calibrate_audio_input(duration=args.calibrate_audio, device_pattern=args.audio_device)
            print(_calibration_text(result))
            return
        if args.mic_profile is not None:
            profile = _load_mic_profile_for_args(args)
            _apply_mic_profile(args, profile, raw_argv)
        calibration = None
        if args.auto_calibrate_audio is not None and (args.audio_debug or args.mode in {RuntimeMode.MIC, RuntimeMode.DJ}):
            calibration = calibrate_audio_input(duration=args.auto_calibrate_audio, device_pattern=args.audio_device)
            _apply_calibration(args, calibration)
        if args.write_mic_profile is not None:
            write_mic_profile(args.write_mic_profile, _effective_mic_profile(args))
            print(f"Wrote mic profile: {args.write_mic_profile}")
            return
        if args.audio_debug:
            app = HeadlessApp(
                controller=Stripe(args.pixels),
                pixel_count=args.pixels,
                mode=args.mode if args.mode is RuntimeMode.DJ else RuntimeMode.MIC,
                audio_device=args.audio_device,
                mic_target_level=args.mic_target_level,
                mic_noise_floor=args.mic_noise_floor,
                audio_analysis=args.audio_analysis,
                idle_enter_frames=args.idle_enter_frames,
                idle_threshold_scale=args.idle_threshold_scale,
                audio_debug_verbose=args.audio_debug_verbose,
                audio_calibration=calibration,
                animation_name=args.animation,
                quiet=True,
                auto_selector_config=_build_auto_selector_config(args),
                debug_selector=args.debug_selector,
            )
            if args.audio_debug_record is None and args.audio_debug_duration is None:
                app.run_audio_debug()
            elif args.audio_debug_record is None:
                app.run_audio_debug(duration_s=args.audio_debug_duration)
            else:
                with AudioDebugRecorder(args.audio_debug_record, label=args.audio_debug_label) as recorder:
                    app.run_audio_debug(duration_s=args.audio_debug_duration, recorder=recorder)
            return
        controller = build_output_controller(args)
        encoder_backend = build_runtime_encoder_backend(args)
        HeadlessApp(
            controller=controller,
            pixel_count=args.pixels,
            mode=args.mode,
            audio_device=args.audio_device,
            mic_target_level=args.mic_target_level,
            mic_noise_floor=args.mic_noise_floor,
            audio_analysis=args.audio_analysis,
            idle_enter_frames=args.idle_enter_frames,
            idle_threshold_scale=args.idle_threshold_scale,
            audio_calibration=calibration,
            animation_name=args.animation,
            quiet=args.quiet,
            gpio_backend_label=gpio_backend_label(controller),
            auto_selector_config=_build_auto_selector_config(args),
            debug_selector=args.debug_selector,
            encoder_backend=encoder_backend,
        ).run()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc


def _secondary_stripe_requested(args: argparse.Namespace) -> bool:
    return args.chip_2 is not None or args.data_pin_2 is not None or args.clock_pin_2 is not None


def _ensure_gpio_ready(chip: str) -> None:
    if importlib.util.find_spec("gpiod") is None:
        raise RuntimeError("GPIO runtime unavailable: install lumistripe-core[gpio] to use the headless CLI")
    chip_path = Path(chip)
    if not chip_path.exists():
        raise RuntimeError(f'GPIO chip "{chip}" was not found. Check that GPIO is enabled and the device path is correct.')
    if not chip_path.is_char_device():
        raise RuntimeError(f'GPIO chip "{chip}" is not a character device. Check the configured GPIO chip path.')
    if not os.access(chip_path, os.R_OK | os.W_OK):
        raise RuntimeError(
            f'permission denied for GPIO chip "{chip}". '
            "Add your user to the gpio group or run with appropriate permissions."
        )


def _ensure_gpio_input_ready(chip: str) -> None:
    if importlib.util.find_spec("gpiod") is None:
        raise RuntimeError("GPIO runtime unavailable: install lumistripe-core[gpio] to use the headless CLI")
    chip_path = Path(chip)
    if not chip_path.exists():
        raise RuntimeError(f'GPIO chip "{chip}" was not found. Check that GPIO is enabled and the device path is correct.')
    if not chip_path.is_char_device():
        raise RuntimeError(f'GPIO chip "{chip}" is not a character device. Check the configured GPIO chip path.')
    if not os.access(chip_path, os.R_OK):
        raise RuntimeError(
            f'permission denied for GPIO chip "{chip}". '
            "Add your user to the gpio group or run with appropriate permissions."
        )


def _parse_mode(value: str) -> RuntimeMode:
    try:
        return RuntimeMode(value.lower())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid mode: {value}") from exc


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _apply_calibration(args: argparse.Namespace, result: AudioCalibrationResult) -> None:
    args.mic_target_level = result.recommended_target_level
    args.mic_noise_floor = result.recommended_noise_floor
    args.idle_threshold_scale = result.recommended_idle_threshold_scale


def _load_mic_profile_for_args(args: argparse.Namespace) -> MicProfile:
    device_name = _selected_audio_device_name(args.audio_device) if args.mic_profile == "auto" else None
    return load_mic_profile(args.mic_profile, device_name=device_name)


def _selected_audio_device_name(pattern: str | None) -> str | None:
    try:
        devices = list_input_device_details()
    except RuntimeError:
        return None
    if not devices:
        return None
    if pattern is None:
        return devices[0].name
    pattern_text = str(pattern)
    if pattern_text.isdigit():
        index = int(pattern_text)
        for device in devices:
            if device.index == index:
                return device.name
        return None
    lowered = pattern_text.casefold()
    for device in devices:
        if lowered in device.name.casefold():
            return device.name
    return None


def _apply_mic_profile(args: argparse.Namespace, profile: MicProfile, raw_argv: list[str]) -> None:
    args.audio_analysis = profile.analysis
    if profile.mic_target_level is not None and not _option_provided(raw_argv, "--mic-target-level"):
        args.mic_target_level = profile.mic_target_level
    if profile.mic_noise_floor is not None and not _option_provided(raw_argv, "--mic-noise-floor"):
        args.mic_noise_floor = profile.mic_noise_floor
    if profile.idle_threshold_scale is not None and not _option_provided(raw_argv, "--idle-threshold-scale"):
        args.idle_threshold_scale = profile.idle_threshold_scale


def _option_provided(argv: list[str], option: str) -> bool:
    prefix = f"{option}="
    return any(value == option or value.startswith(prefix) for value in argv)


def _effective_mic_profile(args: argparse.Namespace) -> MicProfile:
    device_name = _selected_audio_device_name(args.audio_device)
    patterns = (device_name,) if device_name else ()
    return MicProfile(
        name="custom",
        device_patterns=patterns,
        mic_noise_floor=args.mic_noise_floor,
        mic_target_level=args.mic_target_level,
        idle_threshold_scale=args.idle_threshold_scale,
        analysis=args.audio_analysis,
    )


def _calibration_text(result: AudioCalibrationResult) -> str:
    return "\n".join(
        [
            "Audio calibration complete.",
            f"  samples: {result.samples}",
            f"  measured floor: {result.measured_floor:0.4f}",
            f"  measured peak: {result.measured_peak:0.4f}",
            "Recommended flags:",
            f"  --mic-noise-floor {result.recommended_noise_floor:0.4f}",
            f"  --mic-target-level {result.recommended_target_level:0.3f}",
            f"  --idle-threshold-scale {result.recommended_idle_threshold_scale:0.2f}",
        ]
    )


def _read_audio_debug_rows(paths: list[Path]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSONL audio debug row") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: audio debug row must be an object")
                rows.append(row)
    return rows


def _float_field(rows: list[dict[str, object]], name: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def _bool_rate(rows: list[dict[str, object]], name: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get(name) is True) / len(rows)


def _audio_debug_label(row: dict[str, object]) -> str:
    label = row.get("label")
    return str(label) if label else "-"


def _audio_debug_classification(rows: list[dict[str, object]]) -> str:
    rms = [_clamp(value, 0.0, 1.0) for value in _float_field(rows, "rms")]
    silence_rate = _bool_rate(rows, "silence")
    rms_p80 = _percentile(rms, 80.0)
    if silence_rate >= 0.8 and rms_p80 < 0.01:
        return "idle"
    if silence_rate <= 0.5 and rms_p80 >= 0.02:
        return "active"
    return "quiet"


def _format_audio_debug_summary(label: str, rows: list[dict[str, object]]) -> str:
    rms = [_clamp(value, 0.0, 1.0) for value in _float_field(rows, "rms")]
    gains = _float_field(rows, "normalization_gain")
    bpm_values = [value for value in _float_field(rows, "bpm") if value > 0.0]
    classification = _audio_debug_classification(rows)
    return (
        f"  {label}: class={classification} rows={len(rows)} "
        f"rms20={_percentile(rms, 20.0):0.4f} rms80={_percentile(rms, 80.0):0.4f} "
        f"gain50={_percentile(gains, 50.0):0.2f} gain95={_percentile(gains, 95.0):0.2f} "
        f"silence={_bool_rate(rows, 'silence'):0.1%} beat={_bool_rate(rows, 'beat'):0.1%} "
        f"drop={_bool_rate(rows, 'drop_detected'):0.1%} section={_bool_rate(rows, 'section_change'):0.1%} "
        f"bass80={_percentile(_float_field(rows, 'bass_energy'), 80.0):0.3f} "
        f"mid80={_percentile(_float_field(rows, 'mid_energy'), 80.0):0.3f} "
        f"treble80={_percentile(_float_field(rows, 'treble_energy'), 80.0):0.3f} "
        f"flux80={_percentile(_float_field(rows, 'spectral_flux'), 80.0):0.3f} "
        f"bpm50={_percentile(bpm_values, 50.0):0.0f}"
    )


def analyze_audio_debug(paths: list[Path]) -> str:
    rows = _read_audio_debug_rows(paths)
    if not rows:
        raise ValueError("audio debug analysis requires at least one recorded row")

    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(_audio_debug_label(row), []).append(row)

    classified = {label: _audio_debug_classification(label_rows) for label, label_rows in groups.items()}
    idle_rows = [row for label, label_rows in groups.items() if classified[label] == "idle" for row in label_rows]
    active_rows = [row for label, label_rows in groups.items() if classified[label] == "active" for row in label_rows]
    recommendation_rows = active_rows or rows

    rms = [_clamp(value, 0.0, 1.0) for value in _float_field(recommendation_rows, "rms")]
    noise_rms = [_clamp(value, 0.0, 1.0) for value in _float_field(idle_rows or rows, "rms")]
    energy = [_clamp(value, 0.0, 1.0) for value in _float_field(recommendation_rows, "energy")]
    loudness = [_clamp(value, 0.0, 1.0) for value in _float_field(recommendation_rows, "rolling_loudness")]
    gains = _float_field(recommendation_rows, "normalization_gain")
    all_gains = _float_field(rows, "normalization_gain")
    bpm_values = [value for value in _float_field(rows, "bpm") if value > 0.0]

    floor = _percentile(noise_rms, 99.0) if idle_rows else _percentile(rms, 20.0)
    active_source = energy or loudness or rms
    active_threshold = max(floor * 1.8, 0.01)
    active = [value for value in active_source if value >= active_threshold]
    active_level = _percentile(active or active_source, 80.0)
    peak = max(_float_field(rows, "rms") + _float_field(rows, "energy") + _float_field(rows, "rolling_loudness"), default=0.0)

    noise_floor = _clamp((floor * 2.0 + 0.004) if idle_rows else (floor * 1.6 + 0.004), 0.003, 0.12)
    target_level = _clamp(max(active_level * 1.25, 0.24), 0.24, 0.62)
    idle_scale = _clamp(max(noise_floor / AudioSmoothing().noise_floor, 0.5), 0.5, 4.0)

    stale_rate = sum(1 for row in rows if row.get("fresh") is False) / len(rows)
    silence_rate = _bool_rate(rows, "silence")
    beat_rate = _bool_rate(rows, "beat")
    drop_rate = _bool_rate(rows, "drop_detected")
    section_rate = _bool_rate(rows, "section_change")

    warnings: list[str] = []
    if stale_rate > 0.1:
        warnings.append(f"{stale_rate:.0%} stale frames; check audio device stability or callback load")
    if silence_rate > 0.8:
        warnings.append(f"{silence_rate:.0%} silence frames; mic may be too quiet or noise floor too high")
    if beat_rate < 0.005 and peak > 0.05:
        warnings.append("almost no beats detected despite active audio")
    if gains and _percentile(gains, 95.0) >= AudioNormalization().max_gain * 0.95:
        warnings.append("active-capture normalization gain is often near maximum; input may still be quiet")
    if all_gains and _percentile(all_gains, 5.0) <= AudioNormalization().min_gain * 1.05:
        warnings.append("some captures have normalization gain near minimum; check for clipping or abrupt loud input")
    if drop_rate > 0.08:
        warnings.append(f"{drop_rate:.0%} drop frames; drop detection may be too sensitive for this setup")

    labels = sorted({str(row["label"]) for row in rows if row.get("label")})
    label_text = ", ".join(labels) if labels else "-"
    lines = [
        f"Audio debug rows: {len(rows)}",
        f"Labels: {label_text}",
        f"Classified labels: active={sum(1 for value in classified.values() if value == 'active')} "
        f"idle={sum(1 for value in classified.values() if value == 'idle')} "
        f"quiet={sum(1 for value in classified.values() if value == 'quiet')}",
        f"RMS floor/p80/peak: {floor:0.4f} / {_percentile(rms, 80.0):0.4f} / {peak:0.4f}",
        f"Beat/drop/section/silence rates: {beat_rate:0.1%} / {drop_rate:0.1%} / {section_rate:0.1%} / {silence_rate:0.1%}",
        f"BPM median: {_percentile(bpm_values, 50.0):0.0f}" if bpm_values else "BPM median: -",
        "Per-label summary:",
        *(_format_audio_debug_summary(label, groups[label]) for label in sorted(groups)),
        "Recommended flags:",
        f"  --mic-noise-floor {noise_floor:0.4f}",
        f"  --mic-target-level {target_level:0.3f}",
        f"  --idle-threshold-scale {idle_scale:0.2f}",
    ]
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in warnings)
    else:
        lines.append("Warnings: none")
    return "\n".join(lines)


def _encoder_pins_from_args(args: argparse.Namespace, prefix: str) -> EncoderPins | None:
    a = getattr(args, f"{prefix}_a")
    b = getattr(args, f"{prefix}_b")
    button = getattr(args, f"{prefix}_button")
    if a is None and b is None and button is None:
        return None
    if a is None or b is None or button is None:
        raise ValueError(f"{prefix} requires all of --{prefix}-a, --{prefix}-b, and --{prefix}-button")
    return EncoderPins(a=a, b=b, button=button)


def _build_audio_config(*, target_level: float, noise_floor: float, analysis: AudioAnalysis | None = None) -> AudioConfig:
    return AudioConfig(
        smoothing=AudioSmoothing(noise_floor=noise_floor),
        normalization=AudioNormalization(target_level=target_level),
        analysis=analysis or AudioAnalysis(),
    )


def _build_auto_selector_config(args: argparse.Namespace) -> AutoSelectorConfig:
    return AutoSelectorConfig(
        min_duration_s=args.dj_min_duration,
        max_duration_s=args.dj_max_duration,
        switch_cooldown_s=args.dj_switch_cooldown,
        drop_cooldown_s=args.dj_drop_cooldown,
        randomness=args.dj_randomness,
        history_size=args.dj_history_size,
        seed=args.dj_seed,
    )


def _build_selector_config(*, idle_enter_frames: int, idle_threshold_scale: float) -> MusicSelectorConfig:
    defaults = MusicSelectorConfig()
    return MusicSelectorConfig(
        class_dwell_frames=defaults.class_dwell_frames,
        animation_dwell_frames=defaults.animation_dwell_frames,
        confidence_threshold=defaults.confidence_threshold,
        feature_attack=defaults.feature_attack,
        feature_release=defaults.feature_release,
        idle_enter_frames=idle_enter_frames,
        idle_energy_threshold=defaults.idle_energy_threshold * idle_threshold_scale,
        idle_onset_threshold=defaults.idle_onset_threshold * idle_threshold_scale,
        idle_beat_density_threshold=defaults.idle_beat_density_threshold * idle_threshold_scale,
        idle_brightness_threshold=defaults.idle_brightness_threshold * idle_threshold_scale,
    )
