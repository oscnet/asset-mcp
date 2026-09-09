from __future__ import annotations

from decimal import Decimal
from typing import Any

from asset_mcp.domain.models import Asset, Position, decimal_amount, json_number, sum_value_usd


def run_scenario(
    assets: list[Asset],
    positions: list[Position],
    shocks: dict[str, Any],
) -> dict[str, Any]:
    """运行现货与线性合约价格冲击情景。

    输入：统一资产、合约仓位，以及标的代码到百分比冲击的映射；冲击范围为
    -100% 到 +1000%，代码不区分大小写。
    输出：当前/估算净值、损益、回撤、现货/合约影响和按标的明细；模型只做线性估算，
    不模拟爆仓、资金费率、滑点和期权非线性。
    """
    normalized_shocks = _normalize_shocks(shocks)
    impacts: dict[str, dict[str, Decimal]] = {}
    for asset in assets:
        symbol = asset.symbol.upper()
        shock = normalized_shocks.get(symbol, Decimal("0")) / 100
        bucket = impacts.setdefault(
            symbol,
            {"spot": Decimal("0"), "futures": Decimal("0")},
        )
        bucket["spot"] += asset.valueUsd * shock
    for position in positions:
        symbol = position.symbol.upper()
        shock = normalized_shocks.get(symbol, Decimal("0")) / 100
        direction = Decimal("1") if position.side == "long" else Decimal("-1")
        bucket = impacts.setdefault(
            symbol,
            {"spot": Decimal("0"), "futures": Decimal("0")},
        )
        bucket["futures"] += direction * position.notionalUsd * shock

    spot_impact = sum((row["spot"] for row in impacts.values()), start=Decimal("0"))
    futures_impact = sum(
        (row["futures"] for row in impacts.values()),
        start=Decimal("0"),
    )
    total_impact = spot_impact + futures_impact
    current_value = sum_value_usd(assets)
    estimated_value = current_value + total_impact
    estimated_loss = -total_impact
    drawdown = (
        estimated_loss * 100 / current_value
        if current_value > 0
        else Decimal("0")
    )
    by_asset = [
        {
            "symbol": symbol,
            "shockPercent": json_number(normalized_shocks.get(symbol, Decimal("0"))),
            "spotImpactUsd": json_number(round(row["spot"], 8)),
            "futuresImpactUsd": json_number(round(row["futures"], 8)),
            "totalImpactUsd": json_number(round(row["spot"] + row["futures"], 8)),
        }
        for symbol, row in impacts.items()
        if row["spot"] != 0 or row["futures"] != 0
    ]
    by_asset.sort(key=lambda row: (-abs(row["totalImpactUsd"]), row["symbol"]))
    return {
        "currentValueUsd": json_number(round(current_value, 8)),
        "estimatedValueUsd": json_number(round(estimated_value, 8)),
        "estimatedLossUsd": json_number(round(estimated_loss, 8)),
        "drawdownPercent": json_number(round(drawdown, 8)),
        "spotImpactUsd": json_number(round(spot_impact, 8)),
        "futuresImpactUsd": json_number(round(futures_impact, 8)),
        "byAsset": by_asset,
        "assumptions": [
            "Linear price shocks only.",
            "Liquidation, funding, fees, slippage, and option convexity are excluded.",
        ],
    }


def _normalize_shocks(shocks: dict[str, Any]) -> dict[str, Decimal]:
    """验证并规范化价格冲击映射。

    输入：资产代码到 DecimalLike 百分比的字典。
    输出：大写代码到 Decimal 的新字典；空代码、非法数字或超出范围时抛出
    ``ValueError``。
    """
    normalized: dict[str, Decimal] = {}
    for raw_symbol, raw_shock in shocks.items():
        symbol = str(raw_symbol).strip().upper()
        try:
            shock = decimal_amount(raw_shock)
        except ValueError as exc:
            raise ValueError(f"invalid shock for {symbol or 'unknown'}") from exc
        if not symbol or shock < Decimal("-100") or shock > Decimal("1000"):
            raise ValueError(f"invalid shock for {symbol or 'unknown'}")
        normalized[symbol] = shock
    return normalized
