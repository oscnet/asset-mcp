from __future__ import annotations

import asyncio
from html import escape
from typing import Any

from asset_mcp.config import ConfigError, default_config_path
from asset_mcp.service import AssetService
from asset_mcp.web.config_editor import (
    build_editable_sections,
    load_editable_config,
    save_editable_sections,
)
from asset_mcp.web.view_model import (
    DRILLDOWN_COLUMNS,
    build_drilldown_view,
    build_home_view_model,
)


async def load_home_model(service: AssetService | None = None) -> dict[str, Any]:
    """并行加载首页所需的只读数据。

    输入：可选 ``AssetService``，测试可注入替身，生产环境缺省创建本地持久化服务。
    输出：由 Dashboard、风险和 30 天历史响应组合成的首页 ViewModel。
    """
    active_service = service or AssetService()
    overview, history = await asyncio.gather(
        active_service.get_portfolio_overview(),
        active_service.get_history(days=30),
    )
    return build_home_view_model(
        overview["dashboard"],
        overview["risk"],
        history,
        assets=overview["assets"],
    )


def main() -> None:
    """启动并渲染 Asset MCP Streamlit Web 应用。

    输入：当前本地配置、Keychain/Fernet 凭据和 SQLite 快照，无命令行参数。
    输出：浏览器中的只读 Portfolio Dashboard 与安全配置编辑页；加载失败时显示可操作
    错误而不泄露凭据。
    """
    import streamlit as st

    st.set_page_config(
        page_title="Asset MCP · Portfolio Observatory",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_STYLES, unsafe_allow_html=True)
    page = st.sidebar.radio(
        "Workspace",
        ["资产总览", "配置中心"],
        help="配置页只处理非敏感 YAML；API 密钥仍保存在凭据保险库。",
    )
    st.markdown(
        """
        <header class="masthead">
          <div><span class="eyebrow">PERSONAL ASSET DATA PLATFORM</span>
          <h1>Portfolio <em>Observatory</em></h1></div>
          <div class="readonly">READ ONLY · LOCAL FIRST</div>
        </header>
        """,
        unsafe_allow_html=True,
    )
    if page == "配置中心":
        _render_configuration(st)
        return
    try:
        model = asyncio.run(load_home_model())
    except Exception as exc:  # noqa: BLE001
        st.error(f"Dashboard data unavailable: {exc.__class__.__name__}")
        st.info("Check the local config, credentialRef, and source health, then refresh.")
        return
    _render_metrics(st, model["metrics"])
    left, right = st.columns([1.45, 1], gap="large")
    with left:
        st.markdown("### Net worth trajectory")
        if model["history"]:
            st.line_chart(model["history"], x="date", y="totalValueUsd", height=330)
        else:
            st.info("History appears after the first two daily snapshots.")
    with right:
        st.markdown("### Asset allocation")
        if model["assetAllocation"]:
            st.bar_chart(
                model["assetAllocation"],
                x="label",
                y="valueUsd",
                height=330,
            )
        else:
            st.info("No valued assets yet.")
    lower_left, lower_right = st.columns(2, gap="large")
    with lower_left:
        st.markdown("### Location exposure")
        st.dataframe(model["locationAllocation"], width="stretch", hide_index=True)
    with lower_right:
        st.markdown("### Largest positions")
        st.dataframe(model["topAssets"], width="stretch", hide_index=True)
    _render_drilldown(st, model["drilldown"])
    if model["warnings"]:
        st.markdown("### Risk signals")
        for warning in model["warnings"]:
            st.warning(f"{warning.get('code', 'risk')}: {warning.get('message', '')}")


def _render_configuration(st: Any) -> None:
    """渲染不接触凭据保险库的 YAML 配置中心。

    输入：Streamlit 模块，以及 ``ASSET_MCP_CONFIG`` 解析出的当前本地配置路径。
    输出：账户、钱包、手工资产和标签四个受控编辑区；提交时原子保存，错误时保留原文件。
    """
    path = default_config_path()
    st.markdown("### Configuration studio")
    st.caption(str(path))
    st.info(
        "这里只保存非敏感配置。API Key、Secret、Passphrase 和 Token 必须通过 "
        "credentialRef 引用 Keychain/Fernet 保险库。"
    )
    try:
        document = load_editable_config(path)
        sections = build_editable_sections(document)
    except ConfigError as exc:
        st.error(str(exc))
        return

    with st.form("configuration_editor"):
        account_tab, wallet_tab, manual_tab, tag_tab = st.tabs(
            ["账户", "钱包", "手工资产", "标签"]
        )
        with account_tab:
            accounts = st.text_area(
                "账户 YAML",
                sections["accounts"],
                height=360,
                help="支持 exchanges 与 brokers；凭据仅填写 credentialRef。",
            )
        with wallet_tab:
            wallets = st.text_area(
                "钱包 YAML",
                sections["wallets"],
                height=360,
                help="只填写公开地址，禁止私钥和助记词。",
            )
        with manual_tab:
            manual = st.text_area("手工资产 YAML", sections["manual"], height=360)
        with tag_tab:
            tags = st.text_area(
                "标签 YAML",
                sections["tags"],
                height=360,
                help="支持 assets、accounts、wallets；钱包目标格式为 accountId/wallet。",
            )
        submitted = st.form_submit_button("验证并保存", type="primary")

    if not submitted:
        return
    try:
        save_editable_sections(
            path,
            document,
            {
                "accounts": accounts,
                "wallets": wallets,
                "manual": manual,
                "tags": tags,
            },
        )
    except ConfigError as exc:
        st.error(f"配置未保存：{exc}")
        return
    st.success("配置已安全保存，刷新资产总览后生效。")


def _render_drilldown(st: Any, initial_view: dict[str, Any]) -> None:
    """渲染五级资产下钻筛选器和明细表。

    输入：Streamlit 模块与含原始资产的初始下钻 ViewModel。
    输出：无返回值；按币种、平台、账户、类型、位置级联筛选并显示价值合计。
    """
    st.markdown("### Portfolio drilldown")
    assets = initial_view["assets"]
    if not assets:
        st.info("No asset details available.")
        return
    selections: dict[str, str] = {}
    columns = st.columns(5, gap="small")
    for column, (dimension, label) in zip(columns, DRILLDOWN_COLUMNS.items()):
        view = build_drilldown_view(assets, selections)
        with column:
            selected = st.selectbox(
                label,
                ["全部", *view["options"][dimension]],
                key=f"drilldown_{dimension}",
            )
        if selected != "全部":
            selections[dimension] = selected
    view = build_drilldown_view(assets, selections)
    st.caption(f"{len(view['rows'])} assets · ${view['totalValueUsd']:,.2f}")
    st.dataframe(view["rows"], width="stretch", hide_index=True)


def _render_metrics(st: Any, metrics: list[dict[str, str]]) -> None:
    """渲染两行四列首页指标卡。

    输入：Streamlit 模块和八项格式化指标。
    输出：无返回值；生成带稳定 DOM class 的响应式指标网格。
    """
    for offset in (0, 4):
        columns = st.columns(4, gap="medium")
        for column, metric in zip(columns, metrics[offset : offset + 4]):
            with column:
                column.markdown(
                    f"""
                    <section class="metric-card metric-{escape(metric['id'])}">
                      <span>{escape(metric['label'])}</span>
                      <strong>{escape(metric['value'])}</strong>
                      <small>{escape(metric['detail'])}</small>
                    </section>
                    """,
                    unsafe_allow_html=True,
                )


_STYLES = """
<style>
:root { --ink:#111412; --panel:#191d1a; --line:#31372f; --amber:#f5b942; --mint:#7de2bd; --paper:#e9eadf; }
.stApp { background: radial-gradient(circle at 82% -8%, #293328 0, #111412 38%); color:var(--paper); font-family:'Avenir Next Condensed','Trebuchet MS',sans-serif; }
.block-container { max-width:1440px; padding:2.2rem 3rem 4rem; }
.masthead { display:flex; justify-content:space-between; align-items:flex-end; border-bottom:1px solid var(--line); padding-bottom:1.4rem; margin-bottom:1.6rem; }
.eyebrow,.readonly { color:var(--mint); font:500 .72rem Menlo,monospace; letter-spacing:.16em; }
.masthead h1 { margin:.35rem 0 0; font-size:clamp(2.1rem,4vw,4.4rem); letter-spacing:-.065em; line-height:.95; }
.masthead h1 em { color:var(--amber); font-style:normal; font-weight:400; }
.readonly { border:1px solid #49705f; padding:.55rem .75rem; }
.metric-card { min-height:126px; background:linear-gradient(145deg,#1d221e,#151815); border:1px solid var(--line); border-top:2px solid var(--amber); padding:1rem 1.1rem; margin-bottom:1rem; box-shadow:0 18px 50px #0005; }
.metric-card span,.metric-card small { display:block; color:#9ba397; font:500 .7rem Menlo,monospace; text-transform:uppercase; letter-spacing:.08em; }
.metric-card strong { display:block; color:var(--paper); font:600 clamp(1.35rem,2vw,2rem) 'Avenir Next Condensed',sans-serif; margin:.55rem 0 .35rem; }
.metric-sync_status { border-top-color:var(--mint); }
h3 { font-family:Menlo,monospace !important; font-size:.86rem !important; text-transform:uppercase; letter-spacing:.1em; color:var(--amber) !important; }
[data-testid='stDataFrame'],[data-testid='stVegaLiteChart'] { border:1px solid var(--line); background:#161a17; padding:.4rem; }
@media(max-width:700px){ .block-container{padding:1.2rem}.masthead{align-items:flex-start;gap:1rem}.readonly{display:none} }
</style>
"""


if __name__ == "__main__":
    main()
