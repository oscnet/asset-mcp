from __future__ import annotations

import sys

import pytest

import asset_mcp.web.app as app_module
from asset_mcp.web.app import load_home_model


@pytest.mark.asyncio
async def test_load_home_model_uses_existing_read_only_services():
    service = _FakeService()

    model = await load_home_model(service)

    assert len(model["metrics"]) == 8
    assert service.calls == ["dashboard", "risk", "history"]


def test_main_renders_complete_dashboard_with_streamlit_contract(monkeypatch):
    streamlit = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", streamlit)

    async def fake_load_home_model():
        """输入无；输出覆盖所有首页渲染分支的完整 ViewModel。"""
        return {
            "metrics": [
                {"id": f"m{index}", "label": f"Metric {index}", "value": "$1", "detail": "ok"}
                for index in range(8)
            ],
            "history": [{"date": "2026-09-09", "totalValueUsd": 1}],
            "assetAllocation": [{"label": "crypto", "valueUsd": 1}],
            "locationAllocation": [{"label": "binance", "valueUsd": 1}],
            "topAssets": [{"symbol": "BTC", "valueUsd": 1}],
            "warnings": [{"code": "test", "message": "review"}],
        }

    monkeypatch.setattr(app_module, "load_home_model", fake_load_home_model)

    app_module.main()

    assert streamlit.page_config["page_title"].startswith("Asset MCP")
    assert streamlit.line_chart_calls == 1
    assert streamlit.bar_chart_calls == 1
    assert streamlit.dataframe_calls == 2
    assert streamlit.warnings == ["test: review"]


class _FakeService:
    def __init__(self):
        self.calls = []

    async def get_asset_dashboard_data(self):
        """输入无；输出最小 Dashboard 服务响应并记录调用。"""
        self.calls.append("dashboard")
        return {"totalValueUsd": 100, "partial": False}

    async def get_risk(self):
        """输入无；输出最小风险服务响应并记录调用。"""
        self.calls.append("risk")
        return {"stablecoin": {}, "custody": {}, "futures": {}, "warnings": []}

    async def get_history(self, days: int):
        """输入历史窗口；输出最小历史服务响应并记录调用。"""
        assert days == 30
        self.calls.append("history")
        return {"points": []}


class _FakeStreamlit:
    def __init__(self):
        self.page_config = {}
        self.line_chart_calls = 0
        self.bar_chart_calls = 0
        self.dataframe_calls = 0
        self.warnings = []

    def set_page_config(self, **kwargs):
        """输入页面配置；输出无并保存配置。"""
        self.page_config = kwargs

    def markdown(self, *_args, **_kwargs):
        """输入 Markdown 与选项；输出无。"""

    def columns(self, spec, **_kwargs):
        """输入列数量或权重；输出可用作上下文的模拟列。"""
        count = spec if isinstance(spec, int) else len(spec)
        return [_FakeColumn(self) for _ in range(count)]

    def line_chart(self, *_args, **_kwargs):
        """输入历史数据；输出无并累计折线图调用。"""
        self.line_chart_calls += 1

    def bar_chart(self, *_args, **_kwargs):
        """输入分配数据；输出无并累计柱状图调用。"""
        self.bar_chart_calls += 1

    def dataframe(self, *_args, **_kwargs):
        """输入表格数据；输出无并累计表格调用。"""
        self.dataframe_calls += 1

    def warning(self, message):
        """输入警告文本；输出无并保存文本。"""
        self.warnings.append(message)

    def info(self, *_args, **_kwargs):
        """输入提示文本；输出无。"""

    def error(self, *_args, **_kwargs):
        """输入错误文本；输出无。"""


class _FakeColumn:
    def __init__(self, root):
        self.root = root

    def __enter__(self):
        """输入无；输出当前模拟列上下文。"""
        return self

    def __exit__(self, *_args):
        """输入上下文异常信息；输出 ``False`` 以保留异常。"""
        return False

    def markdown(self, *args, **kwargs):
        """输入 Markdown；输出无并委派根对象。"""
        self.root.markdown(*args, **kwargs)
