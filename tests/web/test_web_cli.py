from __future__ import annotations

import sys

from asset_mcp.web import cli


def test_web_cli_launches_streamlit_with_app_and_safe_defaults():
    """输入额外 Streamlit 参数；输出正式 server 启动参数，并返回 runner 退出码。"""
    captured = {}

    def fake_runner():
        """输入来自 ``sys.argv``；输出 0，并保存传给 Streamlit 的完整参数。"""
        captured["argv"] = list(sys.argv)
        return 0

    result = cli.main(["--server.port=9000"], runner=fake_runner)

    assert result == 0
    assert captured["argv"][0:2] == ["streamlit", "run"]
    assert captured["argv"][2].endswith("asset_mcp/web/app.py")
    assert "--server.headless=true" in captured["argv"]
    assert "--browser.gatherUsageStats=false" in captured["argv"]
    assert captured["argv"][-1] == "--server.port=9000"
