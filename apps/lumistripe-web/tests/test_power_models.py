import pytest
from lumistripe_web.models import (
    PowerBudgetState,
    PowerOutputState,
    StripeOutputConfig,
    StripeTopology,
    StripeTopologyRequest,
)
from lumistripe_web.runtime.outputs import power_budget_state_from_engine
from lumistripe_web.settings import StripeOutputSettings, StripeTopologySettings
from pydantic import BaseModel, ValidationError


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "model,base,fields",
    [
        (
            StripeOutputConfig,
            {"id": "left", "name": "Left", "pixels": 10},
            ("voltage_v", "full_white_current_a", "power_limit_watts"),
        ),
        (
            StripeTopology,
            {},
            (
                "power_budget_watts",
                "supply_capacity_watts",
                "pi_reserve_watts",
            ),
        ),
        (
            StripeTopologyRequest,
            {"layout": "mirrored", "outputs": ()},
            (
                "power_budget_watts",
                "supply_capacity_watts",
                "pi_reserve_watts",
            ),
        ),
        (
            PowerOutputState,
            {"output_id": "left", "estimated_watts": 1.0, "applied_scale": 1.0},
            ("estimated_watts", "limit_watts", "applied_scale"),
        ),
        (
            PowerBudgetState,
            {},
            (
                "budget_watts",
                "estimated_watts",
                "applied_scale",
                "supply_capacity_watts",
                "pi_reserve_watts",
                "available_led_watts",
            ),
        ),
    ],
)
def test_power_api_models_reject_nonfinite_values(
    invalid: float,
    model: type[BaseModel],
    base: dict[str, object],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        with pytest.raises(ValidationError):
            model.model_validate({**base, field: invalid})


def test_rust_power_telemetry_maps_to_existing_dashboard_state() -> None:
    topology = StripeTopologySettings(
        layout="independent",
        outputs=(
            StripeOutputSettings(
                id="left",
                name="Left",
                pixels=10,
                spi_device="/dev/spidev0.0",
                power_limit_watts=4.0,
            ),
            StripeOutputSettings(
                id="right", name="Right", pixels=12, spi_device="/dev/spidev0.1"
            ),
        ),
        power_budget_enabled=True,
        power_budget_watts=5.0,
    )

    state = power_budget_state_from_engine(
        topology,
        estimated_watts=7.0,
        applied_scale=0.5,
        output_watts=(6.0, 1.0),
        limiting_output_index=0,
    )

    assert state.enabled is True
    assert state.estimated_watts == 7.0
    assert state.applied_scale == 0.5
    assert state.limiting_output_id == "left"
    assert [output.output_id for output in state.outputs] == ["left", "right"]
    assert [output.estimated_watts for output in state.outputs] == [6.0, 1.0]
    assert state.outputs[0].limit_watts == 4.0


def test_rust_power_telemetry_rejects_topology_mismatches() -> None:
    topology = StripeTopologySettings(
        outputs=(StripeOutputSettings(id="left", name="Left", pixels=10),)
    )
    with pytest.raises(ValueError, match="does not match"):
        power_budget_state_from_engine(
            topology,
            estimated_watts=1.0,
            applied_scale=1.0,
            output_watts=(),
            limiting_output_index=None,
        )
    with pytest.raises(ValueError, match="outside"):
        power_budget_state_from_engine(
            topology,
            estimated_watts=1.0,
            applied_scale=1.0,
            output_watts=(1.0,),
            limiting_output_index=1,
        )
