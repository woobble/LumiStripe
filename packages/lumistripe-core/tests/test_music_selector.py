import numpy as np

from lumistripe import AnimationClass, AnimationPlayer, MusicDrivenSelector, MusicFeatures
from lumistripe.audio import AudioState
from lumistripe.animation import CLASS_MAP


def _fast_party_features() -> MusicFeatures:
    return MusicFeatures(
        bpm=140.0,
        energy=0.95,
        bass=0.6,
        brightness=0.7,
        onset_strength=0.5,
        dynamic_range=0.6,
        beat=True,
        beat_strength=1.0,
        bands=(0.7, 0.6, 0.5, 0.5, 0.4, 0.7, 0.8, 0.9),
    )


def _quiet_features() -> MusicFeatures:
    return MusicFeatures(
        bpm=60.0,
        energy=0.12,
        bass=0.03,
        brightness=0.1,
        onset_strength=0.01,
        dynamic_range=0.01,
        beat=False,
        beat_strength=0.0,
        bands=(0.01, 0.01, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01),
    )


def _sustained_bass_led_features(*, beat: bool = False) -> MusicFeatures:
    return MusicFeatures(
        bpm=54.0,
        energy=0.36,
        bass=0.67,
        brightness=0.31,
        onset_strength=0.034,
        dynamic_range=0.52,
        beat=beat,
        beat_strength=0.35 if beat else 0.0,
        bands=(0.64, 0.70, 0.51, 0.12, 0.05, 0.06, 0.01, 0.0),
    )


def _silence_features() -> MusicFeatures:
    return MusicFeatures(
        bpm=60.0,
        energy=0.0,
        bass=0.0,
        brightness=0.0,
        onset_strength=0.0,
        dynamic_range=0.0,
        beat=False,
        beat_strength=0.0,
        bands=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _drop_like_chunk(frame: int, sample_rate: float = 44_100.0) -> np.ndarray:
    t = np.arange(1024, dtype=np.float32) / sample_rate
    bass_env = 1.0 if frame % 2 == 0 else 0.72
    bass = np.sin(2.0 * np.pi * 55.0 * t) * (0.18 * bass_env)
    sub = np.sin(2.0 * np.pi * 110.0 * t) * 0.08
    hat_amp = 0.06 if frame % 2 == 0 else 0.02
    hat = np.sin(2.0 * np.pi * 3_200.0 * t) * hat_amp
    shimmer = np.sin(2.0 * np.pi * 5_400.0 * t) * (0.03 if frame % 4 == 0 else 0.0)
    click = np.zeros_like(t)
    click[:48] = np.linspace(0.16, 0.0, 48, dtype=np.float32)
    return np.clip(bass + sub + hat + shimmer + click, -1.0, 1.0).astype(np.float32)


def test_selector_moves_to_high_energy_class() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    features = _fast_party_features()

    for _ in range(140):
        class_ = selector.update(player, features)

    assert class_ in {AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC}


def test_selector_prefers_ambient_or_calm_for_quiet_music() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.FAST_PARTY)
    hot = _fast_party_features()
    quiet = _quiet_features()

    for _ in range(40):
        selector.update(player, hot)

    for _ in range(200):
        class_ = selector.update(player, quiet)

    assert class_ in {AnimationClass.AMBIENT, AnimationClass.CALM, AnimationClass.GROOVY}


def test_selector_stays_in_class_with_consistent_input() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    groovy = MusicFeatures(
        bpm=110.0,
        energy=0.45,
        bass=0.35,
        brightness=0.35,
        onset_strength=0.12,
        dynamic_range=0.25,
        beat=True,
        beat_strength=0.5,
        bands=(0.4, 0.35, 0.3, 0.25, 0.2, 0.3, 0.35, 0.4),
    )

    for _ in range(100):
        class_ = selector.update(player, groovy)

    assert class_ is AnimationClass.GROOVY


def test_selector_keeps_sustained_bass_led_music_out_of_calm() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.FAST_PARTY)
    hot = _fast_party_features()
    sustained = _sustained_bass_led_features()

    for _ in range(40):
        selector.update(player, hot)

    for _ in range(200):
        class_ = selector.update(player, sustained)

    assert class_ in {AnimationClass.GROOVY, AnimationClass.FAST_PARTY, AnimationClass.BASS_HEAVY}
    assert class_ is not AnimationClass.CALM


def test_selector_prefers_groovy_or_fast_party_for_sparse_detected_beats() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    sustained_offbeat = _sustained_bass_led_features(beat=False)
    sustained_beat = _sustained_bass_led_features(beat=True)

    for frame in range(180):
        features = sustained_beat if frame % 4 == 0 else sustained_offbeat
        class_ = selector.update(player, features)

    assert class_ in {AnimationClass.GROOVY, AnimationClass.FAST_PARTY, AnimationClass.BASS_HEAVY}


def test_selector_can_switch_fast_party_to_bass_heavy_for_bass_dominant_music() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.FAST_PARTY)
    hot = _fast_party_features()
    sustained = _sustained_bass_led_features()

    for _ in range(40):
        selector.update(player, hot)

    for _ in range(220):
        class_ = selector.update(player, sustained)

    assert class_ is AnimationClass.BASS_HEAVY


def test_selector_prefers_bass_heavy_when_low_end_clearly_dominates() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    sustained_offbeat = _sustained_bass_led_features(beat=False)
    sustained_beat = _sustained_bass_led_features(beat=True)

    for frame in range(220):
        features = sustained_beat if frame % 5 == 0 else sustained_offbeat
        class_ = selector.update(player, features)

    assert class_ is AnimationClass.BASS_HEAVY


def test_selector_rotation_does_not_repeat_animation_immediately() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)
    quiet = _silence_features()

    seen: list[str] = []
    for _ in range(40):
        class_ = selector.update(player, quiet)
        _ = class_
        if selector.current_animation_name is not None:
            name = selector.current_animation_name
            if not seen or seen[-1] != name:
                seen.append(name)

    if len(seen) >= 3:
        assert len(set(seen[-3:])) == len(seen[-3:])


def test_auto_select_disabled_keeps_current_class() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)
    selector.set_auto_select(False)
    features = _fast_party_features()

    for _ in range(200):
        class_ = selector.update(player, features)

    assert class_ is AnimationClass.AMBIENT
    assert selector.current_class is AnimationClass.AMBIENT


def test_auto_select_re_enabled_picks_correct_class() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)
    features = _fast_party_features()

    selector.set_auto_select(False)
    for _ in range(100):
        selector.update(player, features)

    assert selector.current_class is AnimationClass.AMBIENT

    selector.set_auto_select(True)
    for _ in range(140):
        class_ = selector.update(player, features)

    assert class_ in {AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC}


def test_set_manual_class_switches_class_and_animation() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)

    selector.set_manual_class(player, AnimationClass.CHAOTIC)

    assert selector.current_class is AnimationClass.CHAOTIC
    assert selector.current_animation_name is not None
    assert player.index_of(selector.current_animation_name) == player.current_index()


def test_set_manual_class_stays_even_with_contrary_input() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.CHAOTIC)
    selector.set_auto_select(True)
    quiet = _silence_features()

    selector.set_manual_class(player, AnimationClass.AMBIENT)

    for _ in range(200):
        class_ = selector.update(player, quiet)

    assert class_ is AnimationClass.AMBIENT


def test_set_manual_animation_switches_to_named_animation() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)

    selector.set_manual_animation(player, "strobe")

    assert selector.current_animation_name == "strobe"
    assert player.current_index() == player.index_of("strobe")


def test_set_manual_animation_falls_back_to_first_class_in_map() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)

    selector.set_manual_animation(player, "shockwave")

    assert selector.current_class is AnimationClass.HARD_DROP


def test_set_manual_animation_invalidates_stale_class_queue() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)

    selector.set_manual_class(player, AnimationClass.AMBIENT)
    selector.set_manual_animation(player, "shockwave")

    hard_drop_names = [
        entry.animation.name
        for entry in player.animations
        if selector.current_class in CLASS_MAP.get(entry.animation.name, ())
    ]
    chosen = selector._choose_class_animation(hard_drop_names)

    assert chosen in hard_drop_names
    assert chosen != "shockwave"


def test_selector_enters_idle_on_silence() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    silence = _silence_features()

    for _ in range(selector.config.idle_enter_frames):
        selector.update(player, silence)

    assert selector.idle_active is True


def test_selector_defaults_match_retuned_idle_baseline() -> None:
    config = MusicDrivenSelector().config
    assert config.idle_enter_frames == 60
    assert config.idle_energy_threshold == 0.03
    assert config.idle_onset_threshold == 0.025
    assert config.idle_beat_density_threshold == 0.05
    assert config.idle_brightness_threshold == 0.08


def test_selector_idle_rotates_only_calm_or_ambient_animations() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    silence = _silence_features()

    for _ in range(selector.config.idle_enter_frames):
        selector.update(player, silence)

    assert selector.idle_active is True

    seen: set[str] = set()
    for _ in range(500):
        selector.update(player, silence)
        if selector.idle_active and selector.current_animation_name is not None:
            seen.add(selector.current_animation_name)

    assert seen
    assert all(
        AnimationClass.AMBIENT in CLASS_MAP.get(name, ()) or AnimationClass.CALM in CLASS_MAP.get(name, ())
        for name in seen
    )


def test_selector_exits_idle_immediately_when_music_returns() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)
    silence = _silence_features()
    active = _fast_party_features()

    for _ in range(selector.config.idle_enter_frames):
        selector.update(player, silence)

    assert selector.idle_active is True

    selector.update(player, active)

    assert selector.idle_active is False


def test_selector_quiet_music_does_not_favor_bass_heavy() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.BASS_HEAVY)
    quiet = _quiet_features()

    for _ in range(200):
        class_ = selector.update(player, quiet)

    assert class_ is not AnimationClass.BASS_HEAVY


def test_selector_treats_drop_like_audio_as_groovy_or_higher() -> None:
    player = AnimationPlayer.party()
    state = AudioState()
    active_features: MusicFeatures | None = None
    best_activity_score = -1.0

    for frame in range(40):
        state.feed_samples(_drop_like_chunk(frame))
        features = state.music_features()
        activity_score = features.brightness + features.onset_strength + (0.25 if features.beat else 0.0)
        if activity_score > best_activity_score:
            best_activity_score = activity_score
            active_features = features

    assert active_features is not None

    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    for _ in range(140):
        class_ = selector.update(player, active_features)

    assert class_ in {
        AnimationClass.GROOVY,
        AnimationClass.FAST_PARTY,
        AnimationClass.CHAOTIC,
        AnimationClass.HARD_DROP,
        AnimationClass.BASS_HEAVY,
        AnimationClass.VOCAL_POP,
    }
    assert class_ is not AnimationClass.CALM


def test_new_club_animations_are_classified_for_selector_rotation() -> None:
    expected = {
        "club_flash": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
        "color_burst": (AnimationClass.FAST_PARTY, AnimationClass.GROOVY),
        "disco_comet": (AnimationClass.FAST_PARTY, AnimationClass.GROOVY),
        "rave_scanner": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
        "neon_confetti": (AnimationClass.FAST_PARTY, AnimationClass.GROOVY),
        "strobe_chase": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
        "center_burst": (AnimationClass.GROOVY, AnimationClass.BASS_HEAVY),
        "mirror_flash": (AnimationClass.GROOVY, AnimationClass.BASS_HEAVY),
        "spectrum_flash": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC, AnimationClass.BASS_HEAVY),
        "drop_wave": (AnimationClass.BASS_HEAVY, AnimationClass.HARD_DROP),
    }

    for name, classes in expected.items():
        assert CLASS_MAP[name] == classes
