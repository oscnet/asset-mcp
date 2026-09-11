from __future__ import annotations

from pathlib import Path

import yaml
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
    assert app.caption[-2].value == "1 项资产 · 合计 $276,000.00"
    assert app.dataframe[-1].value.iloc[0]["账户"] == "property-home"
    assert [button.label for button in app.get("download_button")] == [
        "导出当前结果 · CSV",
        "导出当前结果 · JSON",
    ]


def test_streamlit_configuration_page_saves_manual_assets_without_secrets(monkeypatch, tmp_path):
    """输入配置页中的手工资产 YAML；输出经校验保存的新资产，且页面无运行异常。"""
    repository = Path(__file__).resolve().parents[2]
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "baseCurrency: USD\nrates: {USD: 1}\nmanual: {accounts: []}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ASSET_MCP_CONFIG", str(config_path))
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(tmp_path / "portfolio.db"))
    app = AppTest.from_file(
        str(repository / "src/asset_mcp/web/app.py"),
        default_timeout=20,
    ).run()

    app.sidebar.radio[0].set_value("配置中心").run()

    assert not app.exception
    assert [area.label for area in app.text_area] == [
        "账户 YAML",
        "钱包 YAML",
        "手工资产 YAML",
        "标签 YAML",
    ]
    assert all(not area.disabled for area in app.text_area)
    assert "manual:" in app.text_area[2].value
    app.text_area[2].set_value(
        """
manual:
  accounts:
    - id: cash
      label: Cash
      category: cash
      assets:
        - symbol: USD
          quantity: 88
          currency: USD
"""
    )
    app.button[0].click().run()

    assert not app.exception
    assert app.success[-1].value == "配置已安全保存，刷新资产总览后生效。"
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["manual"]["accounts"][0]["assets"][0]["quantity"] == 88
