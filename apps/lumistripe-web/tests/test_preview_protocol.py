from __future__ import annotations

import json
from pathlib import Path

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
