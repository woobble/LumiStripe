from __future__ import annotations

import importlib
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HardwareGainStatus:
    supported: bool = False
    writable: bool = False
    backend: str | None = None
    card: int | None = None
    control: str | None = None
    value: float | None = None
    error: str | None = None


class HardwareGainController:
    """Best-effort ALSA capture gain controller.

    Hardware gain is deliberately a coarse, explicit operation. The audio DSP
    remains responsible for fast normalization while this controller is used
    during device selection, calibration, and settings apply.
    """

    _CONTROL_HINTS = ("mic", "capture", "input", "adc", "boost")
    _PERCENT_RE = re.compile(r"\[(\d+)%\]")

    def __init__(self, device_name: str, *, runner=None) -> None:
        self.device_name = device_name
        self._run = runner or self._run_command
        self._pyalsa: _PyAlsaBackend | None = None
        self._status = self._discover()
        logger.info("hardware gain initialization: device=%r status=%s", device_name, self._status)

    @property
    def status(self) -> HardwareGainStatus:
        return self._status

    def refresh(self) -> HardwareGainStatus:
        self._status = self._discover()
        return self._status

    def set_normalized(self, value: float) -> HardwareGainStatus:
        value = min(1.0, max(0.0, float(value)))
        status = self._status
        if not status.supported or not status.writable or status.card is None or not status.control:
            return status
        percent = round(value * 100.0)
        try:
            if status.backend == "pyalsaaudio" and self._pyalsa is not None:
                try:
                    self._pyalsa.setvolume(percent)
                except Exception as pyalsa_exc:  # noqa: BLE001 - optional backend exceptions vary by ALSA binding
                    # pyalsaaudio can discover a mixer but fail to write it on
                    # some USB ALSA cards (often reporting ``No such channel
                    # [hw:N]``).  Keep amixer as a reliable write fallback.
                    logger.debug(
                        "pyalsaaudio hardware gain write failed; trying amixer: %s",
                        pyalsa_exc,
                    )
                    self._run(("amixer", "-c", str(status.card), "sset", status.control, f"{percent}%"))
                    self._status = replace(status, backend="amixer", value=value, error=None)
                    logger.info(
                        "hardware gain applied via amixer fallback: device=%r card=%s control=%r value=%.3f",
                        self.device_name,
                        status.card,
                        status.control,
                        value,
                    )
                    return self._status
            else:
                self._run(("amixer", "-c", str(status.card), "sset", status.control, f"{percent}%"))
            self._status = replace(status, value=value, error=None)
            logger.info(
                "hardware gain applied: device=%r backend=%s card=%s control=%r value=%.3f",
                self.device_name,
                status.backend,
                status.card,
                status.control,
                value,
            )
        except (OSError, RuntimeError) as exc:
            self._status = replace(status, error=str(exc))
            logger.warning(
                "hardware gain apply failed: device=%r backend=%s card=%s control=%r value=%.3f error=%s",
                self.device_name,
                status.backend,
                status.card,
                status.control,
                value,
                exc,
            )
        return self._status

    def _discover(self) -> HardwareGainStatus:
        card = _find_alsa_card(self.device_name)
        if card is None:
            logger.debug("hardware gain unavailable: device=%r no matching ALSA card", self.device_name)
            return HardwareGainStatus(error="No ALSA card matches the selected input device.")
        pyalsa = _PyAlsaBackend.open(card)
        if pyalsa is not None:
            self._pyalsa = pyalsa
            return pyalsa.status
        if shutil.which("amixer") is None:
            logger.debug("hardware gain unavailable: device=%r card=%s amixer missing", self.device_name, card)
            return HardwareGainStatus(card=card, error="amixer is not installed; hardware gain is unavailable.")
        try:
            output = self._run(("amixer", "-c", str(card), "scontrols"))
            controls = _parse_controls(output)
            control = _select_control(controls)
            if control is None:
                logger.debug("hardware gain unavailable: device=%r card=%s no matching mixer control", self.device_name, card)
                return HardwareGainStatus(card=card, error="No writable capture gain control was found.")
            current = self._run(("amixer", "-c", str(card), "get", control))
            value = _parse_percent(current)
            return HardwareGainStatus(
                supported=True,
                writable=True,
                backend="amixer",
                card=card,
                control=control,
                value=value,
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("hardware gain discovery failed: device=%r card=%s error=%s", self.device_name, card, exc)
            return HardwareGainStatus(card=card, error=str(exc))

    @staticmethod
    def _run_command(command: tuple[str, ...]) -> str:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "ALSA command failed").strip())
        return result.stdout


class _PyAlsaBackend:
    def __init__(self, alsaaudio, card: int, control: str, mixer) -> None:
        self._alsaaudio = alsaaudio
        self._mixer = mixer
        self.status = HardwareGainStatus(
            supported=True,
            writable=True,
            backend="pyalsaaudio",
            card=card,
            control=control,
            value=_parse_pyalsa_percent(mixer, alsaaudio),
        )

    @classmethod
    def open(cls, card: int) -> _PyAlsaBackend | None:
        try:
            alsaaudio = importlib.import_module("alsaaudio")
            names = alsaaudio.mixers(cardindex=card)
            control = _select_control(tuple(str(name) for name in names))
            if control is None:
                return None
            mixer = alsaaudio.Mixer(control, cardindex=card)
            return cls(alsaaudio, card, control, mixer)
        except (ImportError, OSError, RuntimeError, TypeError):
            return None

    def setvolume(self, percent: int) -> None:
        self._mixer.setvolume(percent, self._alsaaudio.PCM_CAPTURE)


def _parse_pyalsa_percent(mixer, alsaaudio) -> float | None:
    try:
        values = mixer.getvolume(alsaaudio.PCM_CAPTURE)
        return (float(values[0]) / 100.0) if values else None
    except (OSError, RuntimeError, TypeError, AttributeError):
        return None


def _find_alsa_card(device_name: str) -> int | None:
    endpoint = re.search(r"\bhw:(\d+),\d+\b", device_name, flags=re.IGNORECASE)
    if endpoint is not None:
        return int(endpoint.group(1))
    cards = Path("/proc/asound/cards")
    if not cards.exists():
        return None
    lowered = device_name.casefold()
    for line in cards.read_text(errors="replace").splitlines():
        match = re.match(r"\s*(\d+)\s+\[(.*?)\].*", line)
        if match and (lowered in line.casefold() or match.group(2).casefold() in lowered):
            return int(match.group(1))
    return None


def _parse_controls(output: str) -> tuple[str, ...]:
    controls: list[str] = []
    for match in re.finditer(r"Simple mixer control '([^']+)'", output):
        controls.append(match.group(1))
    return tuple(controls)


def _select_control(controls: tuple[str, ...]) -> str | None:
    for control in controls:
        lowered = control.casefold()
        if any(hint in lowered for hint in HardwareGainController._CONTROL_HINTS):
            return control
    return controls[0] if controls else None


def _parse_percent(output: str) -> float | None:
    match = HardwareGainController._PERCENT_RE.search(output)
    return None if match is None else int(match.group(1)) / 100.0


__all__ = ["HardwareGainController", "HardwareGainStatus"]
