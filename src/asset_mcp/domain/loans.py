from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from asset_mcp.config.models import LoanAssetConfig
from asset_mcp.domain.models import Asset, SyncStatus, decimal_amount, utc_now_iso


def build_loan_assets(
    loans: Sequence[LoanAssetConfig],
    pricing_assets: Sequence[Asset],
    fallback_rates: Mapping[str, float],
) -> list[Asset]:
    """把借贷配置转换为可参与净值计算的负资产。

    输入：借贷配置、当前组合资产和可选固定美元汇率。每笔借贷数量必须为正数；估值
    优先使用同币种 FRESH 资产的数量加权单价，其次使用 STALE 报价，最后使用
    ``rates.SYMBOL``。输出：每笔启用借贷对应一个数量及价值均为负数的 ``Asset``，
    包含借贷人；完全没有价格时价值为零并标记 ``ERROR``。输入对象不会被修改。
    """
    now = utc_now_iso()
    assets: list[Asset] = []
    normalized_rates = {
        str(symbol).upper(): decimal_amount(rate) for symbol, rate in fallback_rates.items()
    }
    for index, loan in enumerate(loans, start=1):
        if not loan.enabled:
            continue
        symbol = loan.symbol.upper()
        unit_price, sync_status, price_source = _loan_price(
            symbol,
            pricing_assets,
            normalized_rates,
        )
        unit_price = round(unit_price, 8)
        quantity = -decimal_amount(loan.quantity)
        assets.append(
            Asset(
                source="loan",
                accountId=f"loan-{index}",
                accountLabel=loan.borrower,
                category="liability",
                symbol=symbol,
                name=f"{loan.borrower} · {symbol} 借贷",
                quantity=quantity,
                currency=symbol,
                unitPriceUsd=unit_price,
                valueUsd=round(quantity * unit_price, 8),
                updatedAt=now,
                rawSource="loan_config",
                accountType="liability",
                location="loan",
                priceSource=price_source,
                syncStatus=sync_status,
                borrower=loan.borrower,
            )
        )
    return assets


def _loan_price(
    symbol: str,
    pricing_assets: Sequence[Asset],
    fallback_rates: Mapping[str, Decimal],
) -> tuple[Decimal, SyncStatus, str]:
    """选择一笔借贷的美元单价及同步状态。

    输入：大写资产代码、当前非借贷资产和规范化固定汇率。输出：单价、同步状态和价格
    来源；同状态下按正持仓数量加权，优先实时报价，再用缓存报价或固定汇率，无价格时
    返回 ``(0, ERROR, unavailable)``。
    """
    matching = [
        asset
        for asset in pricing_assets
        if asset.source != "loan"
        and asset.symbol.upper() == symbol
        and asset.quantity > 0
        and asset.unitPriceUsd > 0
        and asset.syncStatus != "ERROR"
    ]
    fresh = [asset for asset in matching if asset.syncStatus == "FRESH"]
    candidates = fresh or matching
    if candidates:
        total_quantity = sum((asset.quantity for asset in candidates), start=Decimal("0"))
        weighted_value = sum(
            (asset.quantity * asset.unitPriceUsd for asset in candidates),
            start=Decimal("0"),
        )
        status: SyncStatus = "FRESH" if fresh else "STALE"
        return (
            weighted_value / total_quantity,
            status,
            f"portfolio:{candidates[0].source}",
        )
    rate = fallback_rates.get(symbol, Decimal("0"))
    if rate > 0:
        return rate, "FRESH", "configured_rate"
    return Decimal("0"), "ERROR", "unavailable"
