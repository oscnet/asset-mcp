from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from asset_mcp.domain.models import Asset, Position, json_number, sum_value_usd

STABLECOINS = {
    "USD",
    "USDT",
    "USDC",
    "BUSD",
    "FDUSD",
    "TUSD",
    "DAI",
    "USDP",
    "USD1",
    "USDE",
}
CEX_SOURCES = {"binance", "okx"}


def build_risk(assets: list[Asset], positions: list[Position]) -> dict[str, Any]:
    """计算个人组合的确定性核心风险指标。

    输入：统一资产和合约仓位列表，金额必须已经规范为 Decimal。
    输出：币种/来源集中度、稳定币、托管方式、合约毛净敞口及规则型警告；
    所有百分比以当前资产净值为分母，空组合安全返回零值。
    """
    total = sum_value_usd(assets)
    asset_values = _group_values(assets, "symbol")
    source_values = _group_values(assets, "source")
    top_asset, top_asset_value = _top_group(asset_values)
    top_source, top_source_value = _top_group(source_values)
    stablecoin_value = sum(
        (asset.valueUsd for asset in assets if asset.symbol.upper() in STABLECOINS),
        start=Decimal("0"),
    )
    cex_value = sum(
        (asset.valueUsd for asset in assets if asset.source in CEX_SOURCES),
        start=Decimal("0"),
    )
    self_custody_value = sum(
        (asset.valueUsd for asset in assets if asset.source == "onchain"),
        start=Decimal("0"),
    )
    gross_notional = sum(
        (position.notionalUsd for position in positions),
        start=Decimal("0"),
    )
    net_notional = sum(
        (
            position.notionalUsd if position.side == "long" else -position.notionalUsd
            for position in positions
        ),
        start=Decimal("0"),
    )
    margin = sum(
        (position.marginUsd or Decimal("0") for position in positions),
        start=Decimal("0"),
    )
    pnl = sum(
        (position.unrealizedPnlUsd for position in positions),
        start=Decimal("0"),
    )
    max_leverage = max(
        (position.leverage for position in positions),
        default=Decimal("0"),
    )
    top_asset_percent = _percentage(top_asset_value, total)
    top_source_percent = _percentage(top_source_value, total)
    cex_percent = _percentage(cex_value, total)
    warnings: list[dict[str, str]] = []
    if len(asset_values) > 1 and top_asset_percent > Decimal("50"):
        warnings.append(_warning("asset_concentration", "high", "Top asset exceeds 50%."))
    if len(source_values) > 1 and cex_percent > Decimal("50"):
        warnings.append(_warning("cex_concentration", "high", "CEX custody exceeds 50%."))
    if max_leverage >= Decimal("5"):
        warnings.append(_warning("high_leverage", "high", "A position uses at least 5x leverage."))
    if any(item.syncStatus != "FRESH" for item in [*assets, *positions]):
        warnings.append(
            _warning("stale_data", "medium", "Risk metrics include stale source data.")
        )

    return {
        "totalValueUsd": json_number(total),
        "concentration": {
            "topAsset": top_asset,
            "topAssetValueUsd": json_number(top_asset_value),
            "topAssetPercent": json_number(top_asset_percent),
            "topSource": top_source,
            "topSourceValueUsd": json_number(top_source_value),
            "topSourcePercent": json_number(top_source_percent),
        },
        "stablecoin": {
            "valueUsd": json_number(stablecoin_value),
            "percent": json_number(_percentage(stablecoin_value, total)),
        },
        "custody": {
            "cexValueUsd": json_number(cex_value),
            "cexPercent": json_number(cex_percent),
            "selfCustodyValueUsd": json_number(self_custody_value),
            "selfCustodyPercent": json_number(_percentage(self_custody_value, total)),
        },
        "futures": {
            "positionCount": len(positions),
            "grossNotionalUsd": json_number(gross_notional),
            "netNotionalUsd": json_number(net_notional),
            "marginUsd": json_number(margin),
            "unrealizedPnlUsd": json_number(pnl),
            "maxLeverage": json_number(max_leverage),
            "grossExposurePercent": json_number(_percentage(gross_notional, total)),
        },
        "warnings": warnings,
    }


def _group_values(assets: Iterable[Asset], field_name: str) -> dict[str, Decimal]:
    """按单值字段汇总资产美元价值。

    输入：资产可迭代对象和 ``Asset`` 字段名。
    输出：非空字段字符串到 Decimal 总值的映射。
    """
    values: dict[str, Decimal] = {}
    for asset in assets:
        key = str(getattr(asset, field_name) or "").strip()
        if key:
            values[key] = values.get(key, Decimal("0")) + asset.valueUsd
    return values


def _top_group(values: dict[str, Decimal]) -> tuple[str | None, Decimal]:
    """选择最大风险集中项。

    输入：分组名到金额的映射。
    输出：按金额降序、名称升序确定的首项；空映射返回 ``(None, 0)``。
    """
    if not values:
        return None, Decimal("0")
    return sorted(values.items(), key=lambda item: (-item[1], item[0]))[0]


def _percentage(value: Decimal, total: Decimal) -> Decimal:
    """安全计算百分比。

    输入：Decimal 分子和总值。
    输出：八位小数百分比；总值非正时返回零。
    """
    return round(value * 100 / total, 8) if total > 0 else Decimal("0")


def _warning(code: str, severity: str, message: str) -> dict[str, str]:
    """构造稳定风险警告。

    输入：机器代码、严重级别和英文确定性说明。
    输出：适合 MCP JSON 传输的警告字典。
    """
    return {"code": code, "severity": severity, "message": message}
