from __future__ import annotations

import sqlite3
from contextlib import closing
from decimal import Decimal

import pytest

from asset_mcp.domain.models import Asset, Position
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_default_database_path_prefers_environment(monkeypatch, tmp_path):
    configured_path = tmp_path / "custom.db"
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(configured_path))

    assert default_database_path() == configured_path


def test_current_positions_round_trip_decimal_and_stale_status(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    store.replace_current_positions("binance", [_position("binance", "BTCUSDT")])

    positions = store.load_current_positions(source="binance", sync_status="STALE")

    assert len(positions) == 1
    assert positions[0].quantity == Decimal("0.123456789123456789")
    assert positions[0].notionalUsd == Decimal("7654.320980987654321")
    assert positions[0].liquidationPriceUsd == Decimal("45000.12345678")
    assert positions[0].syncStatus == "STALE"


def test_position_snapshot_is_independent_from_asset_snapshot(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    asset = _asset("binance", "BTC", "0.1", "6000")
    position = _position("binance", "BTCUSDT")

    assert store.save_daily_snapshot("binance", [asset], "2026-09-09") is True
    assert store.save_daily_position_snapshot("binance", [position], "2026-09-09") is True
    assert store.save_daily_position_snapshot("binance", [], "2026-09-09") is False

    positions = store.load_position_snapshot("2026-09-09", source="binance")
    assert len(positions) == 1
    assert positions[0].instrument == "BTCUSDT"
    assert positions[0].unrealizedPnlUsd == Decimal("123.456789")


def test_schema_v1_database_is_migrated_without_losing_existing_tables(tmp_path):
    database_path = tmp_path / "portfolio.db"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "CREATE TABLE current_assets (id INTEGER PRIMARY KEY, source TEXT, payload TEXT)"
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

    PortfolioStore(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "current_assets" in tables
    assert "current_positions" in tables
    assert "position_snapshots" in tables


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


def _position(source: str, instrument: str) -> Position:
    """构造存储测试使用的精确合约仓位。

    输入：交易所来源和合约代码。
    输出：包含高精度金额、杠杆、清算价与保证金的 ``Position`` 测试对象。
    """
    return Position(
        source=source,
        accountId=f"{source}-main",
        accountLabel=source.title(),
        symbol="BTC",
        instrument=instrument,
        side="long",
        quantity="0.123456789123456789",
        entryPriceUsd="60000.12345678",
        markPriceUsd="62000.87654321",
        notionalUsd="7654.320980987654321",
        unrealizedPnlUsd="123.456789",
        leverage="3",
        liquidationPriceUsd="45000.12345678",
        marginUsd="2551.440326995884773667",
        accountType="um_futures",
        updatedAt="2026-09-09T08:00:00+00:00",
    )
