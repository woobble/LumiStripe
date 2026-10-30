import pytest
from lumistripe_web.models import (
    PowerBudgetState,
    PowerOutputState,
    StripeOutputConfig,
    StripeTopology,
    StripeTopologyRequest,
)
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
