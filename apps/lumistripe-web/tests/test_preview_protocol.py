from __future__ import annotations

import json
from pathlib import Path

import pytest
from lumistripe_web.api.protocols.preview import encode_preview_frame
from lumistripe_web.runtime import PreviewFrame


def test_preview_v1_matches_shared_golden_packet() -> None:
    fixture_path = Path(__file__).parents[3] / "docs" / "protocols" / "preview-v1.json"
    fixture = json.loads(fixture_path.read_text())
    frame = PreviewFrame(
        sequence=fixture["sequence"],
        outputs=tuple(bytes.fromhex(output) for output in fixture["outputs"]),
    )

    assert encode_preview_frame(frame).hex() == fixture["packet"]


def test_preview_encoder_rejects_more_than_two_outputs() -> None:
    frame = PreviewFrame(sequence=0, outputs=(b"", b"", b""))

    with pytest.raises(ValueError, match="more than two outputs"):
        encode_preview_frame(frame)


def test_preview_encoder_rejects_more_than_4096_pixels() -> None:
    frame = PreviewFrame(sequence=0, outputs=(bytes(4097 * 4),))

    with pytest.raises(ValueError, match="more than 4096 pixels"):
        encode_preview_frame(frame)


def test_preview_encoder_rejects_incomplete_rgba_pixels() -> None:
    frame = PreviewFrame(sequence=0, outputs=(b"\x00\x01\x02",))

    with pytest.raises(ValueError, match="complete RGBA pixels"):
        encode_preview_frame(frame)


def test_preview_encoder_accepts_documented_limits() -> None:
    frame = PreviewFrame(
        sequence=0,
        outputs=(bytes(4096 * 4), bytes(4096 * 4)),
    )

    assert len(encode_preview_frame(frame)) == 12 + 2 * (4 + 4096 * 4)
