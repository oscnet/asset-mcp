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
