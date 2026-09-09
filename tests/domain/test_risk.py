from __future__ import annotations

from asset_mcp.domain.models import Asset, Position
from asset_mcp.domain.risk import build_risk


def test_build_risk_calculates_concentration_custody_and_futures_exposure():
    assets = [
        _asset("binance", "BTC", 6000),
        _asset("okx", "USDT", 2000),
        _asset("onchain", "ETH", 2000),
    ]
    positions = [
        _position("binance", "BTCUSDT", "long", 3000, 600, 200, 3),
        _position("okx", "ETH-USDT-SWAP", "short", 1000, 200, -50, 5),
    ]

    result = build_risk(assets, positions)

    assert result["totalValueUsd"] == 10000
    assert result["concentration"]["topAsset"] == "BTC"
    assert result["concentration"]["topAssetPercent"] == 60
    assert result["concentration"]["topSource"] == "binance"
    assert result["stablecoin"] == {"valueUsd": 2000.0, "percent": 20.0}
    assert result["custody"]["cexPercent"] == 80
    assert result["custody"]["selfCustodyPercent"] == 20
    assert result["futures"] == {
        "positionCount": 2,
        "grossNotionalUsd": 4000.0,
        "netNotionalUsd": 2000.0,
        "marginUsd": 800.0,
        "unrealizedPnlUsd": 150.0,
        "maxLeverage": 5.0,
        "grossExposurePercent": 40.0,
    }
    assert {warning["code"] for warning in result["warnings"]} == {
        "asset_concentration",
        "cex_concentration",
        "high_leverage",
    }


def test_build_risk_handles_empty_portfolio_without_division_error():
    result = build_risk([], [])

    assert result["totalValueUsd"] == 0
    assert result["concentration"]["topAsset"] is None
    assert result["stablecoin"]["percent"] == 0
    assert result["futures"]["grossExposurePercent"] == 0
    assert result["warnings"] == []


def test_build_risk_warns_when_any_input_is_stale():
    stale_asset = Asset(**{**_asset("okx", "USDT", 100).__dict__, "syncStatus": "STALE"})

    result = build_risk([stale_asset], [])

    assert result["warnings"] == [
        {
            "code": "stale_data",
            "severity": "medium",
            "message": "Risk metrics include stale source data.",
        }
    ]


def _asset(source: str, symbol: str, value_usd: int) -> Asset:
    """构造风险测试资产。

    输入：来源、资产代码和美元价值。
    输出：数量为 1、具备默认统一维度的 ``Asset``。
    """
    return Asset(
        source=source,
        accountId=f"{source}-main",
        accountLabel=source.title(),
        category="crypto",
        symbol=symbol,
        quantity=1,
        currency=symbol,
        unitPriceUsd=value_usd,
        valueUsd=value_usd,
        updatedAt="2026-09-09T00:00:00Z",
    )


def _position(
    source: str,
    instrument: str,
    side: str,
    notional_usd: int,
    margin_usd: int,
    pnl_usd: int,
    leverage: int,
) -> Position:
    """构造风险测试合约仓位。

    输入：来源、合约、方向、名义价值、保证金、未实现盈亏和杠杆。
    输出：可参与毛/净敞口计算的 ``Position``。
    """
    return Position(
        source=source,
        accountId=f"{source}-main",
        accountLabel=source.title(),
        symbol=instrument.split("-", 1)[0].removesuffix("USDT"),
        instrument=instrument,
        side=side,
        quantity=1,
        entryPriceUsd=1,
        markPriceUsd=1,
        notionalUsd=notional_usd,
        unrealizedPnlUsd=pnl_usd,
        leverage=leverage,
        marginUsd=margin_usd,
        accountType="futures",
        updatedAt="2026-09-09T00:00:00Z",
    )
