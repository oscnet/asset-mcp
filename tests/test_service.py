from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

import asset_mcp.service as service_module
from asset_mcp.config import AppConfig
from asset_mcp.domain.models import AccountStatus, Asset, Position
from asset_mcp.service import AssetService
from asset_mcp.storage import PortfolioStore


@pytest.mark.asyncio
async def test_net_worth_returns_partial_result_when_provider_times_out(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            ("manual", _FastProvider()),
            ("ibkr", _BlockingProvider()),
        ],
    )

    service = AssetService(AppConfig(), provider_timeout_seconds=0.01)
    started = time.monotonic()

    result = await service.get_net_worth()

    assert time.monotonic() - started < 0.15
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["totalValueUsd"] == 100
    assert result["assetCount"] == 1
    assert result["providerErrors"] == [
        {
            "source": "ibkr",
            "code": "provider_timeout",
            "error": "Timeout",
            "message": "fetch_assets timed out after 0.01s",
            "retryable": True,
        }
    ]


@pytest.mark.asyncio
async def test_health_check_returns_partial_result_when_provider_times_out(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            ("manual", _FastProvider()),
            ("ibkr", _BlockingProvider()),
        ],
    )

    service = AssetService(AppConfig(), provider_timeout_seconds=0.01)

    result = await service.health_check_sources()

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["accounts"] == [
        {
            "source": "manual",
            "accountId": "manual-main",
            "accountLabel": "Manual",
            "enabled": True,
            "ok": True,
            "message": "ok",
        }
    ]
    assert result["providerErrors"][0]["source"] == "ibkr"
    assert result["providerErrors"][0]["code"] == "provider_timeout"
    assert result["providerErrors"][0]["error"] == "Timeout"
    assert result["providerErrors"][0]["retryable"] is True


@pytest.mark.asyncio
async def test_assets_payload_includes_partial_metadata(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            ("manual", _FastProvider()),
            ("ibkr", _BlockingProvider()),
        ],
    )

    service = AssetService(AppConfig(), provider_timeout_seconds=0.01)

    result = await service.get_assets_payload()

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["count"] == 1
    assert result["assets"][0]["accountId"] == "manual-main"
    assert result["providerErrors"][0]["source"] == "ibkr"
    assert result["providerErrors"][0]["code"] == "provider_timeout"


@pytest.mark.asyncio
async def test_failed_provider_uses_last_successful_assets_as_stale(monkeypatch, tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    providers = [
        ("manual", _FastProvider()),
        ("ibkr", _AssetProvider("ibkr", "AAPL", 200)),
    ]
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: providers,
    )
    service = AssetService(AppConfig(), provider_timeout_seconds=0.01, store=store)
    first = await service.get_assets_payload()
    assert first["ok"] is True

    providers[:] = [
        ("manual", _AssetProvider("manual", "USD", 150)),
        ("ibkr", _FailingProvider()),
    ]
    second = await service.get_assets_payload()

    by_source = {asset["source"]: asset for asset in second["assets"]}
    assert second["ok"] is False
    assert second["partial"] is True
    assert second["count"] == 2
    assert by_source["manual"]["valueUsd"] == 150
    assert by_source["manual"]["syncStatus"] == "FRESH"
    assert by_source["ibkr"]["valueUsd"] == 200
    assert by_source["ibkr"]["syncStatus"] == "STALE"


@pytest.mark.asyncio
async def test_failed_provider_without_cache_does_not_invent_zero_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("ibkr", _FailingProvider())],
    )
    store = PortfolioStore(tmp_path / "portfolio.db")

    result = await AssetService(AppConfig(), store=store).get_assets_payload()

    assert result["assets"] == []
    assert result["count"] == 0
    assert result["partial"] is True
    assert result["providerErrors"][0]["source"] == "ibkr"


def test_service_uses_default_store_for_normal_runtime(monkeypatch, tmp_path):
    database_path = tmp_path / "portfolio.db"
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(database_path))

    service = AssetService()

    assert service.store is not None
    assert service.store.path == database_path


@pytest.mark.asyncio
async def test_futures_positions_persist_and_fall_back_to_stale(monkeypatch, tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    providers = [
        ("manual", _FastProvider()),
        ("binance", _PositionProvider()),
    ]
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: providers,
    )
    service = AssetService(AppConfig(), store=store)

    first = await service.get_futures_positions()
    assert first["ok"] is True
    assert first["count"] == 1
    assert first["positions"][0]["syncStatus"] == "FRESH"

    providers[:] = [("binance", _FailingPositionProvider())]
    second = await service.get_futures_positions()

    assert second["ok"] is False
    assert second["partial"] is True
    assert second["count"] == 1
    assert second["positions"][0]["instrument"] == "BTCUSDT"
    assert second["positions"][0]["syncStatus"] == "STALE"
    assert second["providerErrors"][0]["source"] == "binance"
    today = datetime.now(timezone.utc).date().isoformat()
    assert len(store.load_position_snapshot(today)) == 1
    assert len(store.load_current_positions("binance")) == 1


@pytest.mark.asyncio
async def test_get_allocation_uses_normalized_assets_and_filters(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            ("manual", _AssetProvider("manual", "USD", 150)),
            ("ibkr", _AssetProvider("ibkr", "AAPL", 350)),
        ],
    )
    service = AssetService(AppConfig())

    result = await service.get_allocation(groupBy="source", source="manual")

    assert result["ok"] is True
    assert result["totalValueUsd"] == 150
    assert result["groups"] == [
        {"key": "manual", "valueUsd": 150.0, "percentage": 100.0, "assetCount": 1}
    ]


@pytest.mark.asyncio
async def test_get_risk_combines_asset_and_position_results(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("binance", _AssetAndPositionProvider())],
    )

    result = await AssetService(AppConfig()).get_risk()

    assert result["ok"] is True
    assert result["totalValueUsd"] == 10000
    assert result["custody"]["cexPercent"] == 100
    assert result["futures"]["grossNotionalUsd"] == 6200


class _FastProvider:
    async def fetch_assets(self) -> list[Asset]:
        return [
            Asset(
                source="manual",
                accountId="manual-main",
                accountLabel="Manual",
                category="cash",
                symbol="USD",
                quantity=100,
                currency="USD",
                unitPriceUsd=1,
                valueUsd=100,
                updatedAt="2026-05-31T00:00:00Z",
            )
        ]

    async def health_check(self) -> list[AccountStatus]:
        return [AccountStatus("manual", "manual-main", "Manual", True, True, "ok")]


class _BlockingProvider:
    async def fetch_assets(self) -> list[Asset]:
        time.sleep(0.2)
        return []

    async def health_check(self) -> list[AccountStatus]:
        time.sleep(0.2)
        return []


class _AssetProvider:
    def __init__(self, source: str, symbol: str, value_usd: int):
        self.source = source
        self.symbol = symbol
        self.value_usd = value_usd

    async def fetch_assets(self) -> list[Asset]:
        """返回单个确定性测试资产。

        输入：构造器保存的来源、代码和美元价值。
        输出：用于验证缓存替换行为的单元素 ``Asset`` 列表。
        """
        return [
            Asset(
                source=self.source,
                accountId=f"{self.source}-main",
                accountLabel=self.source.title(),
                category="cash" if self.symbol == "USD" else "stock",
                symbol=self.symbol,
                quantity=self.value_usd,
                currency="USD",
                unitPriceUsd=1,
                valueUsd=self.value_usd,
                updatedAt="2026-09-09T00:00:00Z",
            )
        ]

    async def health_check(self) -> list[AccountStatus]:
        """返回测试 Provider 健康状态。

        输入：无。
        输出：与构造器来源对应的单个成功 ``AccountStatus``。
        """
        return [
            AccountStatus(
                self.source,
                f"{self.source}-main",
                self.source.title(),
                True,
                True,
                "ok",
            )
        ]


class _FailingProvider:
    async def fetch_assets(self) -> list[Asset]:
        """模拟来源读取失败。

        输入：无。
        输出：不返回资产，固定抛出 ``RuntimeError`` 供失败隔离测试使用。
        """
        raise RuntimeError("provider unavailable")

    async def health_check(self) -> list[AccountStatus]:
        """模拟健康检查失败。

        输入：无。
        输出：不返回状态，固定抛出 ``RuntimeError``。
        """
        raise RuntimeError("provider unavailable")


class _PositionProvider:
    async def fetch_positions(self) -> list[Position]:
        """返回一个确定性 Binance 测试仓位。

        输入：无。
        输出：用于验证仓位持久化和 STALE 回退的单元素 ``Position`` 列表。
        """
        return [
            Position(
                source="binance",
                accountId="binance-main",
                accountLabel="Binance",
                symbol="BTC",
                instrument="BTCUSDT",
                side="long",
                quantity="0.1",
                entryPriceUsd="60000",
                markPriceUsd="62000",
                notionalUsd="6200",
                unrealizedPnlUsd="200",
                leverage="3",
                marginUsd="2066.66666667",
                liquidationPriceUsd="45000",
                accountType="um_futures",
                updatedAt="2026-09-09T00:00:00Z",
            )
        ]


class _FailingPositionProvider:
    async def fetch_positions(self) -> list[Position]:
        """模拟交易所仓位读取失败。

        输入：无。
        输出：不返回仓位，固定抛出 ``RuntimeError`` 供缓存回退测试使用。
        """
        raise RuntimeError("position provider unavailable")


class _AssetAndPositionProvider(_PositionProvider):
    async def fetch_assets(self) -> list[Asset]:
        """返回风险集成测试资产。

        输入：无。
        输出：价值 10000 美元的 Binance BTC 资产。
        """
        return [
            Asset(
                source="binance",
                accountId="binance-main",
                accountLabel="Binance",
                category="crypto",
                symbol="BTC",
                quantity=1,
                currency="BTC",
                unitPriceUsd=10000,
                valueUsd=10000,
                updatedAt="2026-09-09T00:00:00Z",
            )
        ]
