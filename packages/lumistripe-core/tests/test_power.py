import numpy as np
import pytest
from lumistripe import OutputPowerEstimate, apply_power_budget, estimate_frame_power


def frame(*values: int) -> np.ndarray:
    return np.asarray(values, dtype=np.uint8).reshape((-1, 4))


def test_estimate_frame_power_uses_rgb_duty_and_alpha() -> None:
    assert estimate_frame_power(
        frame(255, 255, 255, 255), voltage_v=5.0, full_white_current_a=0.06
    ) == pytest.approx(0.3)
    assert estimate_frame_power(
        frame(255, 0, 0, 128), voltage_v=5.0, full_white_current_a=0.06
    ) == pytest.approx(0.05, rel=0.01)
    assert estimate_frame_power(
        frame(0, 0, 0, 255), voltage_v=5.0, full_white_current_a=0.06
    ) == pytest.approx(0.0)


def test_estimate_frame_power_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="voltage"):
        estimate_frame_power(frame(1, 2, 3, 255), voltage_v=0.0, full_white_current_a=0.06)
    with pytest.raises(ValueError, match="current"):
        estimate_frame_power(frame(1, 2, 3, 255), voltage_v=5.0, full_white_current_a=0.0)
    with pytest.raises(ValueError, match="RGBA"):
        estimate_frame_power(np.zeros((2, 3), dtype=np.uint8), voltage_v=5.0, full_white_current_a=0.06)


def test_apply_power_budget_uses_tightest_global_or_output_limit() -> None:
    result = apply_power_budget(
        (
            OutputPowerEstimate("left", watts=10.0, limit_watts=8.0),
            OutputPowerEstimate("right", watts=4.0),
        ),
        enabled=True,
        budget_watts=20.0,
    )
    assert result.estimated_watts == pytest.approx(14.0)
    assert result.applied_scale == pytest.approx(0.8)
    assert result.limiting_output_id == "left"
    assert all(output.applied_scale == pytest.approx(0.8) for output in result.outputs)


def test_disabled_budget_reports_estimate_without_scaling() -> None:
    result = apply_power_budget(
        (OutputPowerEstimate("left", watts=10.0, limit_watts=1.0),),
        enabled=False,
        budget_watts=None,
    )
    assert result.applied_scale == 1.0
    assert result.estimated_watts == 10.0
    assert result.limiting_output_id is None


def test_enabled_budget_requires_positive_limit() -> None:
    with pytest.raises(ValueError, match="requires"):
        apply_power_budget(
            (OutputPowerEstimate("left", watts=1.0),),
            enabled=True,
            budget_watts=None,
        )
