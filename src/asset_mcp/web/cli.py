from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Sequence


def main(
    argv: Sequence[str] | None = None,
    runner: Callable[[], Any] | None = None,
) -> Any:
    """通过 Streamlit CLI 启动 Asset MCP Web Server。

    输入：可选 Streamlit 额外参数；console script 缺省读取当前进程参数。测试可注入无参
    ``runner`` 以验证命令而不绑定端口。
    输出：Streamlit CLI 的退出结果；固定启用 headless 并关闭遥测，调用结束后恢复原
    ``sys.argv``，从而不污染嵌入式调用环境。
    """
    extra_arguments = list(sys.argv[1:] if argv is None else argv)
    if runner is None:
        from streamlit.web.cli import main as streamlit_main

        runner = streamlit_main
    app_path = Path(__file__).with_name("app.py").resolve()
    previous_arguments = sys.argv
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
        *extra_arguments,
    ]
    try:
        return runner()
    finally:
        sys.argv = previous_arguments
