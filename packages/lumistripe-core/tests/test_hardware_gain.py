from __future__ import annotations

import logging

from lumistripe.audio import hardware_gain


def test_amixer_gain_discovery_and_write(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> str:
        commands.append(command)
        if command[-1] == "scontrols":
            return "Simple mixer control 'Mic Capture',0\n"
        if command[-2] == "get":
            return "  Capture [70%]"
        return ""

    monkeypatch.setattr(hardware_gain, "_find_alsa_card", lambda _: 1)
    monkeypatch.setattr(hardware_gain.shutil, "which", lambda _: "/usr/bin/amixer")
    monkeypatch.setattr(hardware_gain._PyAlsaBackend, "open", classmethod(lambda cls, card: None))
    controller = hardware_gain.HardwareGainController("USB Mic", runner=runner)

    assert controller.status.supported is True
    assert controller.status.backend == "amixer"
    assert controller.status.value == 0.7
    controller.set_normalized(0.4)
    assert commands[-1] == ("amixer", "-c", "1", "sset", "Mic Capture", "40%")
    assert "hardware gain applied" in caplog.text


def test_missing_alsa_card_falls_back_to_software(monkeypatch):
    monkeypatch.setattr(hardware_gain, "_find_alsa_card", lambda _: None)
    controller = hardware_gain.HardwareGainController("USB Mic")
    assert controller.status.supported is False
    assert controller.status.writable is False
    assert controller.set_normalized(0.8).supported is False


def test_device_endpoint_maps_directly_to_alsa_card():
    assert hardware_gain._find_alsa_card("USB PnP Sound Device: Audio (hw:1,0)") == 1


def test_pyalsa_write_falls_back_to_amixer(monkeypatch):
    commands: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]) -> str:
        commands.append(command)
        if command[-1] == "scontrols":
            return ""
        return ""

    class BrokenPyAlsa:
        status = hardware_gain.HardwareGainStatus(
            supported=True,
            writable=True,
            backend="pyalsaaudio",
            card=1,
            control="Mic",
            value=0.5,
        )

        def setvolume(self, percent: int) -> None:
            raise RuntimeError("No such channel [hw:1]")

    monkeypatch.setattr(hardware_gain, "_find_alsa_card", lambda _: 1)
    monkeypatch.setattr(hardware_gain.shutil, "which", lambda _: "/usr/bin/amixer")
    monkeypatch.setattr(hardware_gain._PyAlsaBackend, "open", classmethod(lambda cls, card: BrokenPyAlsa()))
    controller = hardware_gain.HardwareGainController("USB Mic", runner=runner)

    status = controller.set_normalized(0.4)
    assert status.backend == "amixer"
    assert status.value == 0.4
    assert commands[-1] == ("amixer", "-c", "1", "sset", "Mic", "40%")
