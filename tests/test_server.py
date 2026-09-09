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
