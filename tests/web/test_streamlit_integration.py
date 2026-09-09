from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_home_supports_symbol_drilldown(monkeypatch, tmp_path):
    """输入示例手工资产配置；输出可运行首页及 HOME 单资产下钻结果。"""
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("ASSET_MCP_CONFIG", str(repository / "config.example.yaml"))
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(tmp_path / "portfolio.db"))

    app = AppTest.from_file(
        str(repository / "src/asset_mcp/web/app.py"),
        default_timeout=20,
    ).run()

    assert not app.exception
    assert [control.label for control in app.selectbox] == [
        "币种",
        "平台",
        "账户",
        "类型",
        "位置",
    ]

    app.selectbox[0].select("HOME").run()

    assert not app.exception
    assert app.caption[-1].value == "1 assets · $276,000.00"
    assert app.dataframe[-1].value.iloc[0]["账户"] == "property-home"
