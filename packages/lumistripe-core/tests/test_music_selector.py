from lumistripe import AnimationClass, AnimationPlayer, AudioFrame, MusicDrivenSelector, MusicFeatures
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
        energy=0.02,
        bass=0.01,
        brightness=0.1,
        onset_strength=0.01,
        dynamic_range=0.01,
        beat=False,
        beat_strength=0.0,
        bands=(0.01, 0.01, 0.02, 0.02, 0.01, 0.01, 0.01, 0.01),
    )


def test_selector_moves_to_high_energy_class() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.GROOVY)
    features = _fast_party_features()

    for _ in range(140):
        class_ = selector.update(player, features)

    assert class_ in {AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC}


def test_selector_moves_to_ambient_for_low_energy_input() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.FAST_PARTY)
    hot = _fast_party_features()
    quiet = _quiet_features()

    for _ in range(40):
        selector.update(player, hot)

    seen_ambient = False
    for _ in range(200):
        class_ = selector.update(player, quiet)
        if class_ is AnimationClass.AMBIENT:
            seen_ambient = True

    assert seen_ambient is True


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


def test_selector_rotation_does_not_repeat_animation_immediately() -> None:
    player = AnimationPlayer.party()
    selector = MusicDrivenSelector(current_class=AnimationClass.AMBIENT)
    quiet = _quiet_features()

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
    quiet = _quiet_features()

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
