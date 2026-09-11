from __future__ import annotations

import json
import sys

import pytest

import asset_mcp.web.app as app_module
from asset_mcp.web.app import load_home_model


@pytest.mark.asyncio
async def test_load_home_model_uses_existing_read_only_services():
    service = _FakeService()

    model = await load_home_model(service)

    assert len(model["metrics"]) == 8
    assert service.calls == ["overview", "history"]
    assert model["drilldown"]["rows"][0]["币种"] == "USD"


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
            "warnings": [{"code": "stale_data", "message": "ignored raw message"}],
            "drilldown": {
                "assets": [],
                "options": {},
                "rows": [],
                "totalValueUsd": 0,
            },
        }

    monkeypatch.setattr(app_module, "load_home_model", fake_load_home_model)

    app_module.main()

    assert streamlit.page_config["page_title"] == "个人资产中枢 · Asset MCP"
    assert streamlit.sidebar.selection == "资产总览"
    assert streamlit.line_chart_calls == 1
    assert streamlit.bar_chart_calls == 1
    assert streamlit.dataframe_calls == 2
    assert streamlit.dataframe_widths == ["stretch", "stretch"]
    rendered = "\n".join(streamlit.markdowns)
    assert "个人资产<em>中枢</em>" in rendered
    assert "净值趋势" in rendered
    assert "资产配置" in rendered
    assert "存放位置" in rendered
    assert "核心持仓" in rendered
    assert "风险提示" in rendered
    assert "Net worth trajectory" not in rendered
    assert streamlit.warnings == ["数据时效：部分资产使用历史缓存，请检查对应数据来源。"]


def test_render_exports_registers_csv_and_json_downloads():
    streamlit = _FakeStreamlit()
    assets = [
        {
            "symbol": "BTC",
            "source": "binance",
            "accountId": "main",
            "quantity": 1,
            "valueUsd": 60000,
            "apiSecret": "must-not-leak",
        }
    ]

    app_module._render_exports(streamlit, assets)

    assert [item["file_name"].rsplit(".", 1)[-1] for item in streamlit.downloads] == [
        "csv",
        "json",
    ]
    json_download = streamlit.downloads[1]
    assert json.loads(json_download["data"])["assets"][0]["symbol"] == "BTC"
    assert b"must-not-leak" not in json_download["data"]


class _FakeService:
    def __init__(self):
        self.calls = []

    async def get_portfolio_overview(self):
        """输入无；输出首页聚合数据并记录单次读取调用。"""
        self.calls.append("overview")
        return {
            "dashboard": {"totalValueUsd": 100, "partial": False},
            "risk": {"stablecoin": {}, "custody": {}, "futures": {}, "warnings": []},
            "assets": [
                {
                    "symbol": "USD",
                    "source": "manual",
                    "accountId": "manual-main",
                    "accountType": "manual",
                    "location": "manual",
                    "quantity": 100,
                    "valueUsd": 100,
                    "syncStatus": "FRESH",
                }
            ],
        }

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
        self.dataframe_widths = []
        self.warnings = []
        self.downloads = []
        self.markdowns = []
        self.sidebar = _FakeSidebar()

    def set_page_config(self, **kwargs):
        """输入页面配置；输出无并保存配置。"""
        self.page_config = kwargs

    def markdown(self, body, *_args, **_kwargs):
        """输入 Markdown 与选项；输出无并保存渲染内容。"""
        self.markdowns.append(body)

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

    def dataframe(self, *_args, **kwargs):
        """输入表格数据与显示选项；输出无并记录调用次数和宽度。"""
        self.dataframe_calls += 1
        self.dataframe_widths.append(kwargs.get("width"))

    def warning(self, message):
        """输入警告文本；输出无并保存文本。"""
        self.warnings.append(message)

    def info(self, *_args, **_kwargs):
        """输入提示文本；输出无。"""

    def error(self, *_args, **_kwargs):
        """输入错误文本；输出无。"""

    def download_button(self, _label, **kwargs):
        """输入下载按钮参数；输出 ``False`` 并记录可下载文件。"""
        self.downloads.append(kwargs)
        return False

    def caption(self, *_args, **_kwargs):
        """输入辅助说明；输出无。"""


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


class _FakeSidebar:
    def __init__(self):
        self.selection = None

    def radio(self, _label, options, **_kwargs):
        """输入导航标签和选项；输出首项并记录当前选择。"""
        self.selection = options[0]
        return self.selection
