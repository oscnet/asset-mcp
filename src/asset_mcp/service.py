from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import queue
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from asset_mcp.domain.aggregation import (
    build_allocation,
    build_dashboard_data,
    build_net_worth,
    filter_assets,
)
from asset_mcp.config import AppConfig, load_config
from asset_mcp.domain.models import AccountStatus, Asset, Position
from asset_mcp.domain.risk import build_risk
from asset_mcp.domain.scenario import run_scenario as calculate_scenario
from asset_mcp.providers.base import AssetProvider, AssetScope, PartialAssetFetchError
from asset_mcp.providers.registry import PROVIDER_FACTORIES, build_provider_entries
from asset_mcp.storage import PortfolioStore, default_database_path

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 20.0
DEFAULT_ONCHAIN_PROVIDER_TIMEOUT_SECONDS = 90.0
PROCESS_ISOLATED_SOURCES = {"longbridge", "moomoo"}


class ProviderErrorCode(str, Enum):
    TIMEOUT = "provider_timeout"
    PROVIDER_PARTIAL = "provider_partial"
    PROVIDER_EXCEPTION = "provider_exception"
    PROVIDER_EXITED = "provider_exited"


@dataclass(frozen=True)
class _ProviderError:
    source: str
    code: ProviderErrorCode
    error: str
    message: str
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "code": self.code.value,
            "error": self.error,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class _FetchAssetsResult:
    assets: list[Asset]
    provider_errors: list[_ProviderError]

    @property
    def partial(self) -> bool:
        return bool(self.provider_errors) or any(
            asset.syncStatus != "FRESH" for asset in self.assets
        )


@dataclass(frozen=True)
class _FetchPositionsResult:
    positions: list[Position]
    provider_errors: list[_ProviderError]

    @property
    def partial(self) -> bool:
        return bool(self.provider_errors) or any(
            position.syncStatus != "FRESH" for position in self.positions
        )


@dataclass(frozen=True)
class _ProviderActionResult:
    source: str
    items: list[Any]
    error: _ProviderError | None = None
    failed_scopes: frozenset[AssetScope] = frozenset()


class AssetService:
    def __init__(
        self,
        config: AppConfig | None = None,
        provider_timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        store: PortfolioStore | None = None,
    ):
        """创建只读资产聚合服务。

        输入：可选应用配置、单来源超时秒数和 SQLite 仓库；不传仓库时保持原有的
        无状态行为，传入仓库时保存成功数据并为失败来源回退到 STALE 缓存。
        输出：可执行资产、净值、Dashboard 和健康检查查询的 ``AssetService``。
        """
        self.config = config
        self.provider_timeout_seconds = provider_timeout_seconds
        self.store = (
            PortfolioStore(default_database_path())
            if store is None and config is None
            else store
        )

    async def get_assets(
        self,
        source: str | None = None,
        accountId: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        result = await self._fetch_assets_result(source=source)
        if result.provider_errors:
            _raise_provider_errors(result.provider_errors)
        assets = filter_assets(result.assets, source, accountId, category)
        return [asset.to_dict() for asset in assets]

    async def get_assets_payload(
        self,
        source: str | None = None,
        accountId: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        result = await self._fetch_assets_result(source=source)
        assets = filter_assets(result.assets, source, accountId, category)
        payload: dict[str, Any] = {
            "assets": [asset.to_dict() for asset in assets],
            "count": len(assets),
        }
        return _with_fetch_status(payload, result)

    async def get_net_worth(self) -> dict[str, Any]:
        result = await self._fetch_assets_result()
        return _with_fetch_status(build_net_worth(result.assets), result)

    async def get_asset_dashboard_data(self) -> dict[str, Any]:
        result = await self._fetch_assets_result()
        return _with_fetch_status(build_dashboard_data(result.assets), result)

    async def get_allocation(
        self,
        groupBy: str,
        source: str | None = None,
        accountId: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """读取实时资产并按统一维度计算配置占比。

        输入：分组维度 ``groupBy``，以及可选来源、账户和类别过滤条件。
        输出：确定性的总价值、分组金额/占比和同步错误元数据；失败来源可包含 STALE 缓存。
        """
        result = await self._fetch_assets_result(source=source)
        assets = filter_assets(result.assets, source, accountId, category)
        return _with_fetch_status(build_allocation(assets, groupBy), result)

    async def get_risk(self) -> dict[str, Any]:
        """计算当前组合与合约仓位的核心风险指标。

        输入：Service 配置的全部资产 Provider、支持仓位的交易所 Provider 及可选缓存。
        输出：确定性集中度、托管比例、稳定币和合约敞口指标，以及资产/仓位读取产生的
        ``partial`` 与脱敏错误；该方法不调用 AI，也不执行任何交易。
        """
        asset_result = await self._fetch_assets_result()
        position_result = await self._fetch_positions_result()
        payload = build_risk(asset_result.assets, position_result.positions)
        errors = [*asset_result.provider_errors, *position_result.provider_errors]
        payload["ok"] = not errors
        payload["partial"] = asset_result.partial or position_result.partial
        payload["providerErrors"] = [error.to_dict() for error in errors]
        return payload

    async def get_portfolio_overview(self) -> dict[str, Any]:
        """一次读取首页所需的实时资产与合约数据。

        输入：Service 当前配置的全部只读 Provider 和可选 SQLite 缓存。
        输出：Dashboard、风险、标准化资产明细及统一同步状态；每类实时数据仅读取一次，
        避免 Web 首页为指标、风险和下钻重复请求交易所或链上接口。
        """
        asset_result, position_result = await asyncio.gather(
            self._fetch_assets_result(),
            self._fetch_positions_result(),
        )
        dashboard = _with_fetch_status(build_dashboard_data(asset_result.assets), asset_result)
        risk = build_risk(asset_result.assets, position_result.positions)
        errors = [*asset_result.provider_errors, *position_result.provider_errors]
        risk["ok"] = not errors
        risk["partial"] = asset_result.partial or position_result.partial
        risk["providerErrors"] = [error.to_dict() for error in errors]
        return {
            "dashboard": dashboard,
            "risk": risk,
            "assets": [asset.to_dict() for asset in asset_result.assets],
            "ok": not errors,
            "partial": asset_result.partial or position_result.partial,
            "providerErrors": [error.to_dict() for error in errors],
        }

    async def run_scenario(self, shocks: dict[str, Any]) -> dict[str, Any]:
        """对当前资产和线性合约运行价格冲击测试。

        输入：资产代码到百分比冲击的映射，例如 ``{"BTC": -20}``。
        输出：估算净值、损益、回撤和按资产影响，以及同步状态；只计算、不修改仓位。
        """
        asset_result = await self._fetch_assets_result()
        position_result = await self._fetch_positions_result()
        payload = calculate_scenario(
            asset_result.assets,
            position_result.positions,
            shocks,
        )
        errors = [*asset_result.provider_errors, *position_result.provider_errors]
        payload["ok"] = not errors
        payload["partial"] = asset_result.partial or position_result.partial
        payload["providerErrors"] = [error.to_dict() for error in errors]
        return payload

    async def get_history(
        self,
        days: int = 30,
        asOf: str | None = None,
    ) -> dict[str, Any]:
        """读取每日不可变快照形成净值历史。

        输入：1～3650 天窗口及可选 ISO 截止日期，缺省为 UTC 当天。
        输出：日期升序的净值点、来源数和资产行数；缺失日期不补零，无 SQLite 仓库时
        抛出 ``RuntimeError``，防止返回伪造历史。
        """
        if self.store is None:
            raise RuntimeError("get_history requires a SQLite store")
        points = self.store.load_net_worth_history(days=days, as_of=asOf)
        return {
            "days": days,
            "asOf": asOf,
            "points": points,
            "count": len(points),
            "ok": True,
            "partial": False,
            "providerErrors": [],
        }

    async def get_futures_positions(self, source: str | None = None) -> dict[str, Any]:
        """读取并标准化当前交易所合约仓位。

        输入：可选 ``binance`` 或 ``okx`` 来源过滤条件；其他 Provider 会自动跳过。
        输出：包含仓位、数量、部分失败标记和脱敏 Provider 错误的字典；失败来源若有
        SQLite 成功缓存，则以 ``STALE`` 状态返回旧仓位而不是伪造零仓位。
        """
        result = await self._fetch_positions_result(source=source)
        payload: dict[str, Any] = {
            "positions": [position.to_dict() for position in result.positions],
            "count": len(result.positions),
        }
        return _with_fetch_status(payload, result)

    async def health_check_sources(self) -> dict[str, Any]:
        config = self._config()
        results = await asyncio.gather(
            *[
                self._run_provider_action(provider_source, provider, config, "health_check")
                for provider_source, provider in build_provider_entries(config)
            ],
        )
        rows: list[AccountStatus] = []
        provider_errors: list[_ProviderError] = []
        for result in results:
            if result.error is not None:
                provider_errors.append(result.error)
            else:
                rows.extend(result.items)
        provider_error_dicts = [error.to_dict() for error in provider_errors]
        return {
            "ok": all(row.ok for row in rows) and not provider_errors,
            "partial": bool(provider_errors),
            "accounts": [row.to_dict() for row in rows],
            "providerErrors": provider_error_dicts,
        }

    async def _fetch_all_assets(self, source: str | None = None) -> list[Asset]:
        result = await self._fetch_assets_result(source=source)
        if result.provider_errors:
            _raise_provider_errors(result.provider_errors)
        return result.assets

    async def _fetch_assets_result(self, source: str | None = None) -> _FetchAssetsResult:
        config = self._config()
        results = await asyncio.gather(
            *[
                self._run_provider_action(provider_source, provider, config, "fetch_assets")
                for provider_source, provider in build_provider_entries(config, source=source)
            ],
        )
        assets: list[Asset] = []
        provider_errors: list[_ProviderError] = []
        for result in results:
            if result.error is not None:
                provider_errors.append(result.error)
                if result.error.code == ProviderErrorCode.PROVIDER_PARTIAL:
                    cached_items = (
                        self.store.load_current_assets(source=result.source)
                        if self.store is not None
                        else []
                    )
                    current_items = result.items
                    if result.source == "onchain":
                        current_items = _reuse_last_known_onchain_prices(
                            current_items,
                            cached_items,
                        )
                    merged_items = _merge_partial_assets(
                        current_items,
                        cached_items,
                        result.failed_scopes,
                    )
                    assets.extend(merged_items)
                    if self.store is not None:
                        self.store.replace_current_assets(result.source, merged_items)
                elif self.store is not None:
                    assets.extend(
                        self.store.load_current_assets(
                            source=result.source,
                            sync_status="STALE",
                        )
                    )
            else:
                current_items = result.items
                if result.source == "onchain":
                    cached_items = (
                        self.store.load_current_assets(source="onchain")
                        if self.store is not None
                        else []
                    )
                    current_items = _reuse_last_known_onchain_prices(
                        current_items,
                        cached_items,
                    )
                assets.extend(current_items)
                if self.store is not None:
                    self.store.replace_current_assets(result.source, current_items)
                    if all(item.syncStatus == "FRESH" for item in current_items):
                        self.store.save_daily_snapshot(result.source, current_items)
        return _FetchAssetsResult(
            assets=_with_configured_tags(assets, config),
            provider_errors=provider_errors,
        )

    async def _fetch_positions_result(
        self,
        source: str | None = None,
    ) -> _FetchPositionsResult:
        """执行支持仓位读取的 Provider 并处理持久化。

        输入：可选来源过滤条件，以及 Service 中的配置、超时和 SQLite 仓库。
        输出：成功或 STALE 仓位与结构化错误；成功结果原子更新当前仓位并首次写入
        当日不可变快照，失败且无缓存时仅返回错误。
        """
        config = self._config()
        entries = [
            (provider_source, provider)
            for provider_source, provider in build_provider_entries(config, source=source)
            if callable(getattr(provider, "fetch_positions", None))
        ]
        results = await asyncio.gather(
            *[
                self._run_provider_action(
                    provider_source,
                    provider,
                    config,
                    "fetch_positions",
                )
                for provider_source, provider in entries
            ],
        )
        positions: list[Position] = []
        provider_errors: list[_ProviderError] = []
        for result in results:
            if result.error is not None:
                provider_errors.append(result.error)
                if self.store is not None:
                    positions.extend(
                        self.store.load_current_positions(
                            source=result.source,
                            sync_status="STALE",
                        )
                    )
            else:
                positions.extend(result.items)
                if self.store is not None:
                    self.store.replace_current_positions(result.source, result.items)
                    self.store.save_daily_position_snapshot(result.source, result.items)
        return _FetchPositionsResult(
            positions=positions,
            provider_errors=provider_errors,
        )

    async def _run_provider_action(
        self,
        source: str,
        provider: AssetProvider,
        config: AppConfig,
        action: str,
    ) -> _ProviderActionResult:
        timeout_seconds = self._provider_timeout_for_source(source)
        if source in PROCESS_ISOLATED_SOURCES:
            return await asyncio.to_thread(
                _run_provider_action_in_process,
                source,
                config,
                action,
                timeout_seconds,
            )
        return await _run_provider_action_in_thread(
            source,
            provider,
            action,
            timeout_seconds,
        )

    def _provider_timeout_for_source(self, source: str) -> float:
        """返回指定资产来源单次操作的超时秒数。

        输入：Provider 来源名称，例如 ``onchain``、``binance`` 或 ``okx``。
        输出：显式传入的自定义超时；使用系统默认值时，链上来源返回 90 秒以容纳
        EVM 节流和 429 退避，其他来源仍返回默认的 20 秒。
        """
        if (
            source == "onchain"
            and self.provider_timeout_seconds == DEFAULT_PROVIDER_TIMEOUT_SECONDS
        ):
            return DEFAULT_ONCHAIN_PROVIDER_TIMEOUT_SECONDS
        return self.provider_timeout_seconds

    def _config(self) -> AppConfig:
        return self.config if self.config is not None else load_config()


async def _run_provider_action_in_thread(
    source: str,
    provider: AssetProvider,
    action: str,
    timeout_seconds: float,
) -> _ProviderActionResult:
    try:
        items = await asyncio.wait_for(
            asyncio.to_thread(_run_provider_action_sync, provider, action),
            timeout=timeout_seconds,
        )
        return _ProviderActionResult(source=source, items=items)
    except asyncio.TimeoutError:
        return _ProviderActionResult(
            source=source,
            items=[],
            error=_timeout_error(source, action, timeout_seconds),
        )
    except PartialAssetFetchError as exc:
        return _ProviderActionResult(
            source=source,
            items=exc.assets,
            error=_partial_provider_error(source, exc),
            failed_scopes=exc.failed_scopes,
        )
    except Exception as exc:  # noqa: BLE001
        return _ProviderActionResult(source=source, items=[], error=_provider_error(source, exc))


def _run_provider_action_sync(provider: AssetProvider, action: str) -> list[Any]:
    return asyncio.run(getattr(provider, action)())


def _run_provider_action_in_process(
    source: str,
    config: AppConfig,
    action: str,
    timeout_seconds: float,
) -> _ProviderActionResult:
    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_provider_action_child,
        args=(source, config, action, result_queue),
    )
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
        return _ProviderActionResult(
            source=source,
            items=[],
            error=_timeout_error(source, action, timeout_seconds),
        )

    try:
        payload = result_queue.get(timeout=1)
    except queue.Empty:
        error = _ProviderError(
            source=source,
            code=ProviderErrorCode.PROVIDER_EXITED,
            error="ProviderExited",
            message="provider exited without returning a result",
            retryable=True,
        )
        return _ProviderActionResult(source=source, items=[], error=error)

    if not payload["ok"]:
        error = _ProviderError(
            source=source,
            code=ProviderErrorCode.PROVIDER_EXCEPTION,
            error=payload["error"],
            message=payload["message"],
            retryable=False,
        )
        return _ProviderActionResult(source=source, items=[], error=error)

    item_cls = Asset if action == "fetch_assets" else AccountStatus
    return _ProviderActionResult(
        source=source,
        items=[item_cls(**item) for item in payload["items"]],
    )


def _provider_action_child(
    source: str,
    config: AppConfig,
    action: str,
    result_queue: Any,
) -> None:
    _redirect_stdio_to_devnull()
    try:
        provider = PROVIDER_FACTORIES[source](config)
        items = asyncio.run(getattr(provider, action)())
        result_queue.put({"ok": True, "items": [item.to_dict() for item in items]})
    except Exception as exc:  # noqa: BLE001
        result_queue.put(
            {
                "ok": False,
                "error": exc.__class__.__name__,
                "message": exc.__class__.__name__,
            }
        )


def _redirect_stdio_to_devnull() -> None:
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    finally:
        os.close(devnull)


def _timeout_error(source: str, action: str, timeout_seconds: float) -> _ProviderError:
    return _ProviderError(
        source=source,
        code=ProviderErrorCode.TIMEOUT,
        error="Timeout",
        message=f"{action} timed out after {timeout_seconds:g}s",
        retryable=True,
    )


def _provider_error(source: str, exc: Exception) -> _ProviderError:
    return _ProviderError(
        source=source,
        code=ProviderErrorCode.PROVIDER_EXCEPTION,
        error=exc.__class__.__name__,
        message=exc.__class__.__name__,
        retryable=False,
    )


def _partial_provider_error(
    source: str,
    exc: PartialAssetFetchError,
) -> _ProviderError:
    """把部分读取异常转换为可公开的结构化错误。

    输入：Provider 来源和只包含脱敏摘要的 ``PartialAssetFetchError``。
    输出：错误码为 ``provider_partial``、允许重试的 Provider 错误；成功资产与失败
    地址范围由调用方分别处理，不在公开错误中暴露完整账户地址。
    """
    return _ProviderError(
        source=source,
        code=ProviderErrorCode.PROVIDER_PARTIAL,
        error=exc.__class__.__name__,
        message=str(exc),
        retryable=True,
    )


def _raise_provider_errors(errors: list[_ProviderError]) -> None:
    names = ", ".join(f"{error.source}:{error.error}" for error in errors)
    raise RuntimeError(f"Failed to fetch one or more providers: {names}")


def _with_fetch_status(
    payload: dict[str, Any],
    result: _FetchAssetsResult | _FetchPositionsResult,
) -> dict[str, Any]:
    payload["ok"] = not result.provider_errors
    payload["partial"] = result.partial
    payload["providerErrors"] = [error.to_dict() for error in result.provider_errors]
    return payload


def _reuse_last_known_onchain_prices(
    current_assets: list[Asset],
    cached_assets: list[Asset],
) -> list[Asset]:
    """为缺失实时价格的链上余额恢复上次有效单价。

    输入：本轮 Provider 返回的链上资产，以及 SQLite 保存的上一轮链上资产。匹配键由
    来源、账户、链、钱包、资产代码和原始数据源组成，避免跨钱包或跨链误用价格。
    输出：实时价格存在时原样返回；匹配到正数历史单价时按本轮数量重新计算美元价值，
    标记 ``STALE`` 和缓存价格来源；没有历史价格时保留余额并标记 ``ERROR``，防止零价
    被误认为正常估值。两个输入列表和其中的不可变资产对象均不会被修改。
    """
    cached_by_key = {
        _onchain_price_key(asset): asset
        for asset in cached_assets
        if asset.unitPriceUsd > 0
    }
    restored: list[Asset] = []
    for asset in current_assets:
        if asset.unitPriceUsd > 0:
            restored.append(asset)
            continue
        previous = cached_by_key.get(_onchain_price_key(asset))
        if previous is None:
            restored.append(
                replace(
                    asset,
                    priceSource="unavailable",
                    syncStatus="ERROR",
                )
            )
            continue
        original_price_source = (previous.priceSource or "onchain").removeprefix("cached:")
        restored.append(
            replace(
                asset,
                unitPriceUsd=previous.unitPriceUsd,
                valueUsd=round(asset.quantity * previous.unitPriceUsd, 8),
                priceSource=f"cached:{original_price_source}",
                syncStatus="STALE",
            )
        )
    return restored


def _merge_partial_assets(
    current_assets: list[Asset],
    cached_assets: list[Asset],
    failed_scopes: frozenset[AssetScope],
) -> list[Asset]:
    """合并部分成功资产和仅属于失败地址的历史缓存。

    输入：本轮成功读取的资产、上一轮同来源缓存，以及失败的
    ``(accountId, wallet)`` 集合。输出：成功地址保持本轮同步状态，失败地址缓存复制为
    ``STALE``；成功但余额已清零的地址不会错误恢复旧资产，输入列表不会被修改。
    """
    stale_assets = [
        replace(asset, syncStatus="STALE")
        for asset in cached_assets
        if _asset_scope(asset) in failed_scopes
    ]
    return [*current_assets, *stale_assets]


def _asset_scope(asset: Asset) -> AssetScope:
    """返回资产所属的地址缓存范围。

    输入：一个标准化资产。输出：由账户 ID 和钱包标识组成的二元组；无钱包的资产以
    空字符串占位，使比较保持确定性且不会与正常链上地址意外匹配。
    """
    return asset.accountId, asset.wallet or ""


def _onchain_price_key(
    asset: Asset,
) -> tuple[str, str, str | None, str | None, str, str | None]:
    """生成链上历史价格的保守匹配键。

    输入：一个标准化 ``Asset``。
    输出：账户、链、钱包、资产代码和原始数据源组成的元组；只有这些定位字段完全一致
    时才允许复用旧价格。
    """
    return (
        asset.source,
        asset.accountId,
        asset.chain,
        asset.wallet,
        asset.symbol.upper(),
        asset.rawSource,
    )


def _with_configured_tags(assets: list[Asset], config: AppConfig) -> list[Asset]:
    """把配置的资产、账户和钱包标签合并到标准化资产。

    输入：Provider 或 STALE 缓存返回的资产，以及当前 ``AppConfig`` 标签映射。
    输出：标签按 Provider、资产、账户、钱包顺序去重后的新资产列表；输入对象保持不变。
    钱包目标使用稳定的 ``accountId/wallet`` 形式。
    """
    tagged: list[Asset] = []
    for asset in assets:
        wallet_key = f"{asset.accountId}/{asset.wallet}" if asset.wallet else None
        configured = [
            *config.assetTags.get(asset.symbol.upper(), ()),
            *config.accountTags.get(asset.accountId, ()),
            *(config.walletTags.get(wallet_key, ()) if wallet_key else ()),
        ]
        tagged.append(replace(asset, tags=(*asset.tags, *configured)))
    return tagged
