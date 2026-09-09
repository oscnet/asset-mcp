import json
from decimal import Decimal

import pytest

from asset_mcp.domain.models import Asset, decimal_amount, sum_value_usd


def test_asset_normalizes_amounts_to_decimal_without_float_artifacts():
    asset = _asset(quantity=0.1 + 0.2, unit_price="0.1", value="0.03")

    assert asset.quantity == Decimal("0.3")
    assert asset.unitPriceUsd == Decimal("0.1")
    assert asset.valueUsd == Decimal("0.03")


def test_sum_value_usd_is_exact_for_decimal_inputs():
    assets = [
        _asset(quantity="1", unit_price="0.1", value="0.1"),
        _asset(quantity="1", unit_price="0.2", value="0.2"),
    ]

    assert sum_value_usd(assets) == Decimal("0.3")


def test_asset_dict_preserves_existing_json_number_contract():
    payload = _asset(quantity="1.25", unit_price="2.5", value="3.125").to_dict()

    assert json.loads(json.dumps(payload))["valueUsd"] == 3.125
    assert isinstance(payload["quantity"], float)


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", object()])
def test_decimal_amount_rejects_non_finite_or_invalid_values(invalid):
    with pytest.raises(ValueError, match="finite decimal"):
        decimal_amount(invalid)


def _asset(quantity, unit_price, value):
    return Asset(
        source="manual",
        accountId="manual-main",
        accountLabel="Manual",
        category="cash",
        symbol="USD",
        quantity=quantity,
        currency="USD",
        unitPriceUsd=unit_price,
        valueUsd=value,
        updatedAt="2026-09-09T00:00:00Z",
    )
