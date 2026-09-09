from __future__ import annotations

from decimal import Decimal

import pytest

from asset_mcp.domain.models import Asset, Position
from asset_mcp.domain.scenario import run_scenario


def test_scenario_combines_spot_and_directional_futures_impact():
    assets = [_asset("BTC", 6000), _asset("ETH", 4000)]
    positions = [
        _position("BTC", "long", 3000),
        _position("ETH", "short", 1000),
    ]

    result = run_scenario(assets, positions, {"BTC": -20, "ETH": -30})

    assert result["currentValueUsd"] == 10000
    assert result["estimatedValueUsd"] == 7300
    assert result["estimatedLossUsd"] == 2700
    assert result["drawdownPercent"] == 27
    assert result["spotImpactUsd"] == -2400
    assert result["futuresImpactUsd"] == -300
    assert result["byAsset"][0]["symbol"] == "BTC"
    assert result["byAsset"][0]["totalImpactUsd"] == -1800


def test_scenario_leaves_unmentioned_assets_unchanged():
    result = run_scenario([_asset("BTC", 100), _asset("USDT", 50)], [], {"BTC": 10})

    assert result["estimatedValueUsd"] == 160
    assert result["estimatedLossUsd"] == -10
    assert result["drawdownPercent"] == -6.66666667


@pytest.mark.parametrize("shock", [-100.0001, 1000.0001, "bad"])
def test_scenario_rejects_invalid_shocks(shock):
    with pytest.raises(ValueError, match="shock"):
        run_scenario([], [], {"BTC": shock})


def _asset(symbol: str, value_usd: int) -> Asset:
    """构造情景测试现货资产。

    输入：资产代码和当前美元价值。
    输出：来自 Binance 的标准化 ``Asset``。
    """
    return Asset(
        source="binance",
        accountId="binance-main",
        accountLabel="Binance",
        category="crypto",
        symbol=symbol,
        quantity=1,
        currency=symbol,
        unitPriceUsd=value_usd,
        valueUsd=value_usd,
        updatedAt="2026-09-09T00:00:00Z",
    )


def _position(symbol: str, side: str, notional_usd: int) -> Position:
    """构造情景测试线性合约仓位。

    输入：标的代码、多空方向和美元名义价值。
    输出：杠杆为 1 的标准化 ``Position``。
    """
    return Position(
        source="binance",
        accountId="binance-main",
        accountLabel="Binance",
        symbol=symbol,
        instrument=f"{symbol}USDT",
        side=side,
        quantity=1,
        entryPriceUsd=1,
        markPriceUsd=1,
        notionalUsd=notional_usd,
        unrealizedPnlUsd=0,
        leverage=1,
        accountType="um_futures",
        updatedAt="2026-09-09T00:00:00Z",
    )
