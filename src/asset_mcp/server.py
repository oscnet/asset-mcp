from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from asset_mcp.config import ConfigError
from asset_mcp.service import AssetService

mcp = FastMCP(
    "asset-mcp",
    instructions=(
        "Read-only personal asset aggregation server. It summarizes configured "
        "Binance, OKX, moomoo OpenD, Longbridge, IBKR, on-chain wallets, "
        "and manual accounts in USD."
    ),
)


@mcp.tool()
async def get_net_worth() -> dict[str, Any]:
    """Return total net worth and summaries by category, source, account, and currency."""
    return await _call_service("get_net_worth")


@mcp.tool()
async def get_assets(
    source: str | None = None,
    accountId: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Return normalized assets, optionally filtered by source, accountId, or category."""
    service = AssetService()
    try:
        return await service.get_assets_payload(
            source=source,
            accountId=accountId,
            category=category,
        )
    except ConfigError as exc:
        return {"ok": False, "error": exc.__class__.__name__, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": exc.__class__.__name__, "message": exc.__class__.__name__}


@mcp.tool()
async def get_asset_dashboard_data() -> dict[str, Any]:
    """Return chart-ready data for asset dashboards."""
    return await _call_service("get_asset_dashboard_data")


@mcp.tool()
async def get_futures_positions(source: str | None = None) -> dict[str, Any]:
    """读取统一的 Binance/OKX 合约仓位。

    输入：可选 ``source``，目前支持 ``binance`` 或 ``okx``；缺省时读取全部。
    输出：只读仓位列表、数量、同步状态、部分失败标记及脱敏错误，不包含 API 凭据。
    """
    return await _call_service("get_futures_positions", source=source)


@mcp.tool()
async def get_allocation(
    groupBy: str,
    source: str | None = None,
    accountId: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """按统一资产维度返回确定性配置占比。

    输入：``groupBy`` 支持 asset/source/accountType/chain/location/tag，并可按来源、
    账户或类别过滤。
    输出：总美元价值、分组金额/百分比、数据同步状态和脱敏错误；工具严格只读。
    """
    return await _call_service(
        "get_allocation",
        groupBy=groupBy,
        source=source,
        accountId=accountId,
        category=category,
    )


@mcp.tool()
async def get_risk() -> dict[str, Any]:
    """返回当前资产与合约仓位的确定性风险指标。

    输入：无，读取全部已启用来源。
    输出：集中度、稳定币、CEX/自托管、合约敞口、规则警告及同步状态；严格只读。
    """
    return await _call_service("get_risk")


@mcp.tool()
async def run_scenario(shocks: dict[str, float]) -> dict[str, Any]:
    """对当前 Portfolio 运行确定性价格冲击测试。

    输入：资产代码到百分比冲击的映射，例如 ``{"BTC": -20, "ETH": -30}``。
    输出：现货和线性合约的估算损益、净值、回撤及模型假设；不会交易或修改数据。
    """
    return await _call_service("run_scenario", shocks=shocks)


@mcp.tool()
async def get_history(days: int = 30, asOf: str | None = None) -> dict[str, Any]:
    """读取 Portfolio 每日净值历史。

    输入：1～3650 天窗口，以及可选 ``YYYY-MM-DD`` 截止日期。
    输出：实际存在快照的每日总值、来源数和资产行数；缺失日期不补零，严格只读。
    """
    return await _call_service("get_history", days=days, asOf=asOf)


@mcp.tool()
async def health_check_sources() -> dict[str, Any]:
    """Check configured sources and accounts without returning secrets."""
    return await _call_service("health_check_sources")


async def _call_service(method_name: str, **kwargs) -> dict[str, Any] | list[dict[str, Any]]:
    service = AssetService()
    try:
        method = getattr(service, method_name)
        return await method(**kwargs)
    except ConfigError as exc:
        return {"ok": False, "error": exc.__class__.__name__, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": exc.__class__.__name__, "message": exc.__class__.__name__}


def main() -> None:
    mcp.run()
