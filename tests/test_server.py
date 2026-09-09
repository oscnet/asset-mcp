from __future__ import annotations

import pytest

import asset_mcp.server as server_module


@pytest.mark.asyncio
async def test_get_futures_positions_mcp_tool_delegates_source_filter(monkeypatch):
    calls = []

    async def fake_call_service(method_name: str, **kwargs):
        """记录 MCP 工具委派参数并返回确定性结果。

        输入：Service 方法名称和工具传入的关键字参数。
        输出：最小仓位响应，同时把输入保存到 ``calls`` 供契约断言。
        """
        calls.append((method_name, kwargs))
        return {"positions": [], "count": 0, "ok": True, "partial": False}

    monkeypatch.setattr(server_module, "_call_service", fake_call_service)

    result = await server_module.get_futures_positions(source="okx")

    assert result["count"] == 0
    assert calls == [("get_futures_positions", {"source": "okx"})]


@pytest.mark.asyncio
async def test_get_allocation_mcp_tool_delegates_filters(monkeypatch):
    calls = []

    async def fake_call_service(method_name: str, **kwargs):
        """记录 allocation MCP 委派。

        输入：Service 方法名及筛选参数。
        输出：最小确定性 allocation 响应，并保存调用供断言。
        """
        calls.append((method_name, kwargs))
        return {"groupBy": "source", "groups": [], "totalValueUsd": 0}

    monkeypatch.setattr(server_module, "_call_service", fake_call_service)

    await server_module.get_allocation(
        groupBy="source",
        source="manual",
        category="cash",
    )

    assert calls == [
        (
            "get_allocation",
            {
                "groupBy": "source",
                "source": "manual",
                "accountId": None,
                "category": "cash",
            },
        )
    ]
