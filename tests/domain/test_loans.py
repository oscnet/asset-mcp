from __future__ import annotations

from decimal import Decimal

from asset_mcp.config import LoanAssetConfig
from asset_mcp.domain.loans import build_loan_assets
from asset_mcp.domain.models import Asset


def test_loan_asset_uses_fresh_matching_symbol_price_and_negative_quantity():
    """输入 BTC 借贷和实时 BTC 持仓报价；输出记录借贷人且数量、价值均为负数。"""
    loans = [LoanAssetConfig(borrower="张三", symbol="BTC", quantity=Decimal("0.25"))]
    pricing_assets = [
        _asset("binance", "BTC", 1, 60000, "FRESH"),
        _asset("onchain", "BTC", 1, 55000, "STALE"),
    ]

    assets = build_loan_assets(loans, pricing_assets, {})

    assert len(assets) == 1
    assert assets[0].source == "loan"
    assert assets[0].category == "liability"
    assert assets[0].borrower == "张三"
    assert assets[0].symbol == "BTC"
    assert assets[0].quantity == -0.25
    assert assets[0].unitPriceUsd == 60000
    assert assets[0].valueUsd == -15000
    assert assets[0].syncStatus == "FRESH"


def test_loan_asset_falls_back_to_configured_rate_and_marks_missing_price():
    """输入无对应持仓报价的两笔借贷；输出优先使用 rates，仍无价格时明确标记异常。"""
    loans = [
        LoanAssetConfig(borrower="李四", symbol="USDT", quantity=Decimal("100")),
        LoanAssetConfig(borrower="王五", symbol="UNKNOWN", quantity=Decimal("2")),
        LoanAssetConfig(borrower="停用", symbol="BTC", quantity=Decimal("1"), enabled=False),
    ]

    assets = build_loan_assets(loans, [], {"USDT": 1})

    assert [(asset.symbol, asset.valueUsd, asset.syncStatus) for asset in assets] == [
        ("USDT", -100, "FRESH"),
        ("UNKNOWN", 0, "ERROR"),
    ]


def test_loan_asset_uses_stale_price_only_when_no_fresh_price_exists():
    """输入仅有缓存报价的 ETH 借贷；输出按缓存价扣减并继承 STALE 状态。"""
    loans = [LoanAssetConfig(borrower="赵六", symbol="ETH", quantity=Decimal("2"))]

    assets = build_loan_assets(
        loans,
        [_asset("onchain", "ETH", 3, 2500, "STALE")],
        {},
    )

    assert assets[0].unitPriceUsd == 2500
    assert assets[0].valueUsd == -5000
    assert assets[0].syncStatus == "STALE"


def test_loan_asset_ignores_error_price_and_uses_configured_rate():
    """输入状态异常的资产价格及有效固定汇率；输出不采用异常报价并使用配置汇率。"""
    loans = [LoanAssetConfig(borrower="赵六", symbol="ETH", quantity=Decimal("2"))]

    assets = build_loan_assets(
        loans,
        [_asset("onchain", "ETH", 3, 2500, "ERROR")],
        {"ETH": 2000},
    )

    assert assets[0].unitPriceUsd == 2000
    assert assets[0].valueUsd == -4000
    assert assets[0].priceSource == "configured_rate"


def _asset(source: str, symbol: str, quantity: float, price: float, status: str) -> Asset:
    """输入来源、币种、数量、单价和状态；输出借贷估值使用的标准化测试资产。"""
    return Asset(
        source=source,
        accountId=f"{source}-main",
        accountLabel=source,
        category="crypto",
        symbol=symbol,
        quantity=quantity,
        currency=symbol,
        unitPriceUsd=price,
        valueUsd=quantity * price,
        updatedAt="2026-09-11T00:00:00Z",
        syncStatus=status,
    )
