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


def test_estimate_frame_power_uint8_path_matches_integer_duty_reference() -> None:
    pixels = np.asarray(
        [[255, 0, 128, 255], [24, 180, 90, 127], [10, 20, 30, 0]],
        dtype=np.uint8,
    )
    weighted_channels = sum(sum(int(channel) for channel in row[:3]) * int(row[3]) for row in pixels)
    expected = weighted_channels / (3 * 255 * 255) * 0.06 * 5.0

    assert estimate_frame_power(
        pixels, voltage_v=5.0, full_white_current_a=0.06
    ) == pytest.approx(expected)


def test_estimate_frame_power_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="voltage"):
        estimate_frame_power(frame(1, 2, 3, 255), voltage_v=0.0, full_white_current_a=0.06)
    with pytest.raises(ValueError, match="current"):
        estimate_frame_power(frame(1, 2, 3, 255), voltage_v=5.0, full_white_current_a=0.0)
    with pytest.raises(ValueError, match="RGBA"):
        estimate_frame_power(np.zeros((2, 3), dtype=np.uint8), voltage_v=5.0, full_white_current_a=0.06)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("parameter", ["voltage_v", "full_white_current_a"])
def test_estimate_frame_power_rejects_nonfinite_calibration(
    invalid: float, parameter: str
) -> None:
    values = {"voltage_v": 5.0, "full_white_current_a": 0.06}
    values[parameter] = invalid
    with pytest.raises(ValueError, match="finite"):
        estimate_frame_power(frame(255, 255, 255, 255), **values)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_estimate_frame_power_rejects_nonfinite_pixel_channels(invalid: float) -> None:
    pixels = np.asarray([[invalid, 255.0, 255.0, 255.0]])
    with pytest.raises(ValueError, match="finite"):
        estimate_frame_power(pixels, voltage_v=5.0, full_white_current_a=0.06)


def test_estimate_frame_power_rejects_nonfinite_result() -> None:
    with pytest.raises(ValueError, match="finite numeric range"):
        estimate_frame_power(
            frame(255, 255, 255, 255),
            voltage_v=1e308,
            full_white_current_a=1e308,
        )


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("watts", float("nan")),
        ("watts", float("inf")),
        ("watts", float("-inf")),
        ("limit_watts", float("nan")),
        ("limit_watts", float("inf")),
        ("limit_watts", float("-inf")),
        ("applied_scale", float("nan")),
        ("applied_scale", float("inf")),
        ("applied_scale", float("-inf")),
    ],
)
def test_output_power_estimate_rejects_nonfinite_values(
    field: str, invalid: float
) -> None:
    values: dict[str, float] = {"watts": 1.0, "applied_scale": 1.0}
    values[field] = invalid
    with pytest.raises(ValueError):
        OutputPowerEstimate("left", **values)


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


def test_zero_watt_budget_scales_led_output_to_zero() -> None:
    result = apply_power_budget(
        (OutputPowerEstimate("left", watts=10.0),),
        enabled=True,
        budget_watts=0.0,
    )
    assert result.applied_scale == 0.0
    assert result.estimated_watts == 10.0


@pytest.mark.parametrize("budget", [-1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_budget_is_rejected(budget: float) -> None:
    with pytest.raises(ValueError, match="negative"):
        apply_power_budget(
            (OutputPowerEstimate("left", watts=10.0),),
            enabled=True,
            budget_watts=budget,
        )


def test_total_power_overflow_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite numeric range"):
        apply_power_budget(
            (
                OutputPowerEstimate("left", watts=1e308),
                OutputPowerEstimate("right", watts=1e308),
            ),
            enabled=False,
            budget_watts=None,
        )


def test_enabled_budget_requires_positive_limit() -> None:
    with pytest.raises(ValueError, match="requires"):
        apply_power_budget(
            (OutputPowerEstimate("left", watts=1.0),),
            enabled=True,
            budget_watts=None,
        )
