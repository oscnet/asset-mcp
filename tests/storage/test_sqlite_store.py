from __future__ import annotations

import sqlite3
from contextlib import closing
from decimal import Decimal

import pytest

from asset_mcp.domain.models import Asset
from asset_mcp.storage import PortfolioStore, default_database_path


def test_replace_current_assets_only_replaces_requested_source(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    store.replace_current_assets("binance", [_asset("binance", "BTC", "0.1", "6000")])
    store.replace_current_assets("okx", [_asset("okx", "ETH", "2", "6000")])

    store.replace_current_assets("binance", [_asset("binance", "USDT", "100", "100")])

    assets = store.load_current_assets()
    assert [(asset.source, asset.symbol) for asset in assets] == [
        ("binance", "USDT"),
        ("okx", "ETH"),
    ]
    assert assets[0].quantity == Decimal("100")
    assert assets[1].valueUsd == Decimal("6000")


def test_daily_snapshot_is_idempotent_and_immutable(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    first = [_asset("binance", "BTC", "0.1", "6000")]
    changed = [_asset("binance", "BTC", "0.2", "12000")]

    assert store.save_daily_snapshot("binance", first, snapshot_date="2026-09-09") is True
    assert store.save_daily_snapshot("binance", changed, snapshot_date="2026-09-09") is False

    snapshot = store.load_snapshot("2026-09-09")
    assert len(snapshot) == 1
    assert snapshot[0].quantity == Decimal("0.1")
    assert snapshot[0].valueUsd == Decimal("6000")

    with pytest.raises(sqlite3.IntegrityError, match="immutable snapshot"):
        with closing(sqlite3.connect(store.path)) as connection:
            with connection:
                connection.execute("UPDATE asset_snapshots SET payload = '{}' WHERE id = 1")


def test_load_stale_assets_preserves_values_and_marks_sync_status(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    original = _asset("okx", "ETH", "2", "6000")
    store.replace_current_assets("okx", [original])

    stale = store.load_current_assets(source="okx", sync_status="STALE")

    assert len(stale) == 1
    assert stale[0].valueUsd == Decimal("6000")
    assert stale[0].updatedAt == original.updatedAt
    assert stale[0].syncStatus == "STALE"


def test_store_creates_parent_directory_and_schema_version(tmp_path):
    database_path = tmp_path / "nested" / "portfolio.db"

    store = PortfolioStore(database_path)

    assert store.path == database_path
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_default_database_path_prefers_environment(monkeypatch, tmp_path):
    configured_path = tmp_path / "custom.db"
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(configured_path))

    assert default_database_path() == configured_path


def _asset(source: str, symbol: str, quantity: str, value_usd: str) -> Asset:
    """构造存储测试使用的精确资产。

    输入：来源、币种、十进制数量和美元价值字符串。
    输出：字段完整、金额使用 ``Decimal`` 且带统一维度的 ``Asset`` 测试对象。
    """
    return Asset(
        source=source,
        accountId=f"{source}-main",
        accountLabel=source.title(),
        category="crypto",
        symbol=symbol,
        quantity=quantity,
        currency=symbol,
        unitPriceUsd=Decimal(value_usd) / Decimal(quantity),
        valueUsd=value_usd,
        updatedAt="2026-09-09T08:00:00+00:00",
        wallet="trading",
        strategy="core",
        riskLevel="Medium",
        tags=("long-term",),
    )
