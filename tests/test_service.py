from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import asset_mcp.service as service_module
from asset_mcp.config import AppConfig, LoanAssetConfig
from asset_mcp.domain.models import AccountStatus, Asset, Position
from asset_mcp.providers.base import PartialAssetFetchError
from asset_mcp.service import AssetService
from asset_mcp.storage import PortfolioStore


def test_onchain_uses_longer_default_timeout_without_changing_custom_timeout():
    """输入默认与自定义 Provider 超时；输出仅默认 onchain 扩展到 90 秒。"""
    default_service = AssetService(AppConfig(), store=None)
    custom_service = AssetService(AppConfig(), provider_timeout_seconds=0.01, store=None)

    assert default_service._provider_timeout_for_source("onchain") == 90
    assert default_service._provider_timeout_for_source("binance") == 20
    assert custom_service._provider_timeout_for_source("onchain") == 0.01


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
async def test_loan_asset_reduces_net_worth_and_is_saved_in_daily_snapshot(monkeypatch, tmp_path):
    """输入 2 BTC 资产和借出 0.5 BTC；输出净值按 1.5 BTC 计算并保存借贷负快照。"""
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("binance", _StaticProvider([_priced_btc_asset()]))],
    )
    config = AppConfig(
        loanAssets=[LoanAssetConfig(borrower="张三", symbol="BTC", quantity=Decimal("0.5"))]
    )
    store = PortfolioStore(tmp_path / "portfolio.db")

    result = await AssetService(config, store=store).get_net_worth()

    by_source = {row["key"]: row["valueUsd"] for row in result["bySource"]}
    assert result["totalValueUsd"] == 75000
    assert by_source == {"binance": 100000, "loan": -25000}
    assert sum(asset.valueUsd for asset in store.load_snapshot(
        datetime.now(timezone.utc).date().isoformat()
    )) == 75000


@pytest.mark.asyncio
async def test_loan_asset_payload_exposes_borrower_and_negative_quantity(monkeypatch):
    """输入借贷配置与 BTC 报价；输出 MCP 资产明细包含借贷人和负数量。"""
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("binance", _StaticProvider([_priced_btc_asset()]))],
    )
    config = AppConfig(
        loanAssets=[LoanAssetConfig(borrower="张三", symbol="BTC", quantity=Decimal("0.5"))]
    )

    result = await AssetService(config, store=None).get_assets_payload()

    loan = next(asset for asset in result["assets"] if asset["source"] == "loan")
    assert loan["borrower"] == "张三"
    assert loan["symbol"] == "BTC"
    assert loan["quantity"] == -0.5
    assert loan["valueUsd"] == -25000


@pytest.mark.asyncio
async def test_loan_source_filter_fetches_prices_but_returns_only_liabilities(monkeypatch):
    """输入 loan 来源过滤；输出读取组合报价、仅返回借贷负资产。"""
    requested_sources = []

    def entries(_config, source=None):
        """输入配置和 Provider 过滤；输出 BTC 报价 Provider 并记录过滤值。"""
        requested_sources.append(source)
        return [("binance", _StaticProvider([_priced_btc_asset()]))]

    monkeypatch.setattr(service_module, "build_provider_entries", entries)
    config = AppConfig(
        loanAssets=[
            LoanAssetConfig(borrower="张三", symbol="BTC", quantity=Decimal("0.5"))
        ]
    )

    result = await AssetService(config, store=None).get_assets_payload(source="loan")

    assert requested_sources == [None]
    assert result["count"] == 1
    assert result["assets"][0]["source"] == "loan"


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


@pytest.mark.asyncio
async def test_onchain_zero_price_reuses_last_known_price_as_stale(monkeypatch, tmp_path):
    """输入实时余额更新但价格为零及旧缓存价格；输出按新数量重新估值并标记 STALE。"""
    store = PortfolioStore(tmp_path / "portfolio.db")
    store.replace_current_assets(
        "onchain",
        [_onchain_asset(quantity=1, unit_price=2000, value_usd=2000)],
    )
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            ("onchain", _StaticProvider([_onchain_asset(quantity=2, unit_price=0, value_usd=0)]))
        ],
    )

    result = await AssetService(AppConfig(), store=store).get_assets_payload(source="onchain")

    asset = result["assets"][0]
    assert result["ok"] is True
    assert result["partial"] is True
    assert asset["quantity"] == 2
    assert asset["unitPriceUsd"] == 2000
    assert asset["valueUsd"] == 4000
    assert asset["syncStatus"] == "STALE"
    assert asset["priceSource"] == "cached:onchain"
    assert store.load_current_assets("onchain")[0].syncStatus == "STALE"
    assert store.load_snapshot(datetime.now(timezone.utc).date().isoformat()) == []


@pytest.mark.asyncio
async def test_onchain_unknown_zero_price_is_error_and_not_snapshotted(monkeypatch, tmp_path):
    """输入首次出现且无历史价格的链上资产；输出保留数量、标记 ERROR 且不写每日快照。"""
    store = PortfolioStore(tmp_path / "portfolio.db")
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            ("onchain", _StaticProvider([_onchain_asset(quantity=3, unit_price=0, value_usd=0)]))
        ],
    )

    result = await AssetService(AppConfig(), store=store).get_assets_payload(source="onchain")

    asset = result["assets"][0]
    assert result["ok"] is True
    assert result["partial"] is True
    assert asset["quantity"] == 3
    assert asset["unitPriceUsd"] == 0
    assert asset["syncStatus"] == "ERROR"
    assert asset["priceSource"] == "unavailable"
    assert store.load_snapshot(datetime.now(timezone.utc).date().isoformat()) == []


@pytest.mark.asyncio
async def test_partial_onchain_fetch_keeps_fresh_and_failed_address_cache(monkeypatch, tmp_path):
    """输入一个成功地址和一个失败地址缓存；输出 FRESH 与 STALE 并存及部分错误。"""
    store = PortfolioStore(tmp_path / "portfolio.db")
    stale_wallet = "ethereum:0x0000...0001"
    fresh_wallet = "ethereum:0x0000...0002"
    store.replace_current_assets(
        "onchain",
        [_onchain_asset(quantity=1, unit_price=2000, value_usd=2000, wallet=stale_wallet)],
    )
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [
            (
                "onchain",
                _PartialOnchainProvider(
                    [_onchain_asset(quantity=2, unit_price=2100, value_usd=4200, wallet=fresh_wallet)],
                    {("wallet-main", stale_wallet)},
                ),
            )
        ],
    )

    result = await AssetService(AppConfig(), store=store).get_assets_payload(source="onchain")

    by_wallet = {asset["wallet"]: asset for asset in result["assets"]}
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["count"] == 2
    assert by_wallet[fresh_wallet]["syncStatus"] == "FRESH"
    assert by_wallet[stale_wallet]["syncStatus"] == "STALE"
    assert result["providerErrors"][0]["code"] == "provider_partial"
    assert result["providerErrors"][0]["retryable"] is True
    assert {asset.wallet for asset in store.load_current_assets("onchain")} == {
        stale_wallet,
        fresh_wallet,
    }
    assert store.load_snapshot(datetime.now(timezone.utc).date().isoformat()) == []


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


@pytest.mark.asyncio
async def test_get_portfolio_overview_fetches_each_live_dataset_once(monkeypatch):
    provider = _CountingAssetAndPositionProvider()
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("binance", provider)],
    )

    result = await AssetService(AppConfig()).get_portfolio_overview()

    assert provider.asset_fetches == 1
    assert provider.position_fetches == 1
    assert result["dashboard"]["totalValueUsd"] == 10000
    assert result["risk"]["futures"]["grossNotionalUsd"] == 6200
    assert result["assets"][0]["symbol"] == "BTC"
    assert result["partial"] is False


@pytest.mark.asyncio
async def test_configured_tags_are_applied_without_polluting_provider_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("binance", _AssetAndPositionProvider())],
    )
    config = AppConfig(
        assetTags={"BTC": ("Core",)},
        accountTags={"binance-main": ("Personal",)},
        walletTags={"binance-main/spot": ("Trading",)},
    )

    store = PortfolioStore(tmp_path / "portfolio.db")
    result = await AssetService(config, store=store).get_portfolio_overview()

    assert result["assets"][0]["tags"] == ("Core", "Personal", "Trading")
    assert store.load_current_assets("binance")[0].tags == ()


@pytest.mark.asyncio
async def test_run_scenario_combines_live_assets_and_positions(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "build_provider_entries",
        lambda config, source=None: [("binance", _AssetAndPositionProvider())],
    )

    result = await AssetService(AppConfig()).run_scenario({"BTC": -10})

    assert result["ok"] is True
    assert result["currentValueUsd"] == 10000
    assert result["spotImpactUsd"] == -1000
    assert result["futuresImpactUsd"] == -620
    assert result["estimatedValueUsd"] == 8380


@pytest.mark.asyncio
async def test_get_history_reads_daily_snapshot_series(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio.db")
    store.save_daily_snapshot(
        "manual",
        [_AssetProvider("manual", "USD", 150).fetch_asset()],
        "2026-09-09",
    )

    result = await AssetService(AppConfig(), store=store).get_history(
        days=7,
        asOf="2026-09-09",
    )

    assert result["days"] == 7
    assert result["count"] == 1
    assert result["points"][0]["totalValueUsd"] == 150


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


class _StaticProvider:
    def __init__(self, assets: list[Asset]):
        """输入固定资产列表；输出供 Service 持久化测试使用的静态 Provider。"""
        self.assets = assets

    async def fetch_assets(self) -> list[Asset]:
        """输入无；输出构造器保存的资产副本。"""
        return list(self.assets)

    async def health_check(self) -> list[AccountStatus]:
        """输入无；输出单个健康的链上测试账户状态。"""
        return [AccountStatus("onchain", "wallet-main", "Wallet", True, True, "ok")]


def _priced_btc_asset() -> Asset:
    """输入无；输出 2 BTC、单价 50,000 美元的确定性报价资产。"""
    return Asset(
        source="binance",
        accountId="binance-main",
        accountLabel="Binance",
        category="crypto",
        symbol="BTC",
        quantity=2,
        currency="BTC",
        unitPriceUsd=50000,
        valueUsd=100000,
        updatedAt="2026-09-11T00:00:00Z",
    )


class _PartialOnchainProvider(_StaticProvider):
    def __init__(self, assets: list[Asset], failed_scopes: set[tuple[str, str]]):
        """输入成功资产和失败地址范围；输出会抛出部分读取异常的链上测试 Provider。"""
        super().__init__(assets)
        self.failed_scopes = failed_scopes

    async def fetch_assets(self) -> list[Asset]:
        """输入无；输出通过异常携带成功资产及失败地址范围。"""
        raise PartialAssetFetchError(
            assets=self.assets,
            failed_scopes=self.failed_scopes,
            message="1 of 2 addresses failed: JsonRpcError",
        )


def _onchain_asset(
    quantity: int,
    unit_price: int,
    value_usd: int,
    wallet: str = "ethereum:0x0000...0001",
) -> Asset:
    """输入数量、美元单价和美元价值；输出具有稳定缓存匹配键的 ETH 测试资产。"""
    return Asset(
        source="onchain",
        accountId="wallet-main",
        accountLabel="Wallet",
        category="crypto",
        symbol="ETH",
        quantity=quantity,
        currency="ETH",
        unitPriceUsd=unit_price,
        valueUsd=value_usd,
        updatedAt="2026-09-10T00:00:00Z",
        rawSource="onchain_native_balance",
        wallet=wallet,
        chain="ethereum",
    )


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
        return [self.fetch_asset()]

    def fetch_asset(self) -> Asset:
        """同步构造单个确定性测试资产。

        输入：构造器保存的来源、代码和价值。
        输出：供异步 Provider 和直接存储夹具复用的 ``Asset``。
        """
        return Asset(
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
                wallet="spot",
            )
        ]


class _CountingAssetAndPositionProvider(_AssetAndPositionProvider):
    def __init__(self):
        self.asset_fetches = 0
        self.position_fetches = 0

    async def fetch_assets(self) -> list[Asset]:
        """输入无；输出测试资产，并记录实时资产读取次数。"""
        self.asset_fetches += 1
        return await super().fetch_assets()

    async def fetch_positions(self) -> list[Position]:
        """输入无；输出测试仓位，并记录实时仓位读取次数。"""
        self.position_fetches += 1
        return await super().fetch_positions()
