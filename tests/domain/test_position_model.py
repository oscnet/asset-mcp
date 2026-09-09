import json
from decimal import Decimal

import pytest

from asset_mcp.domain.models import Position


def test_position_normalizes_contract_amounts_to_decimal():
    position = _position()

    assert position.quantity == Decimal("0.5")
    assert position.entryPriceUsd == Decimal("60000")
    assert position.markPriceUsd == Decimal("62000")
    assert position.notionalUsd == Decimal("31000")
    assert position.unrealizedPnlUsd == Decimal("1000")
    assert position.leverage == Decimal("3")
    assert position.liquidationPriceUsd == Decimal("45000")
    assert position.marginUsd == Decimal("10000")


def test_position_supports_missing_liquidation_price_and_margin():
    position = _position(liquidationPriceUsd=None, marginUsd=None)

    assert position.liquidationPriceUsd is None
    assert position.marginUsd is None


def test_position_dict_is_json_compatible_and_keeps_dimensions():
    payload = json.loads(json.dumps(_position().to_dict()))

    assert payload["source"] == "binance"
    assert payload["side"] == "long"
    assert payload["accountType"] == "um_futures"
    assert payload["syncStatus"] == "FRESH"
    assert payload["notionalUsd"] == 31000.0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"side": "flat"}, "side"),
        ({"quantity": "-0.1"}, "quantity"),
        ({"leverage": "-1"}, "leverage"),
    ],
)
def test_position_rejects_invalid_direction_or_negative_magnitude(overrides, message):
    with pytest.raises(ValueError, match=message):
        _position(**overrides)


def _position(**overrides):
    values = {
        "source": "binance",
        "accountId": "binance-main",
        "accountLabel": "Binance Main",
        "symbol": "BTC",
        "instrument": "BTCUSDT",
        "side": "long",
        "quantity": "0.5",
        "entryPriceUsd": "60000",
        "markPriceUsd": "62000",
        "notionalUsd": "31000",
        "unrealizedPnlUsd": "1000",
        "leverage": "3",
        "liquidationPriceUsd": "45000",
        "marginUsd": "10000",
        "accountType": "um_futures",
        "updatedAt": "2026-09-09T00:00:00Z",
    }
    values.update(overrides)
    return Position(**values)
