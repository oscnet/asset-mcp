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
from asset_mcp.web.export import build_portfolio_exports
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
        page_title="个人资产中枢 · Asset MCP",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_STYLES, unsafe_allow_html=True)
    page = st.sidebar.radio(
        "工作区",
        ["资产总览", "配置中心"],
        help="配置页只处理非敏感 YAML；API 密钥仍保存在凭据保险库。",
    )
    st.markdown(
        """
        <header class="masthead">
          <div><span class="eyebrow">个人资产数据工作台</span>
          <h1>个人资产<em>中枢</em></h1>
          <p>聚合账户、链上钱包与持仓风险，一处掌握资产全貌。</p></div>
          <div class="readonly"><i></i> 只读访问 · 本地优先</div>
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
        st.error(f"资产数据加载失败（{exc.__class__.__name__}）")
        st.info("请检查本地配置、credentialRef 和数据来源健康状态，然后刷新页面。")
        return
    _render_metrics(st, model["metrics"])
    left, right = st.columns([1.45, 1], gap="large")
    with left:
        _render_section_title(st, "01", "净值趋势", "最近 30 天资产变化")
        if model["history"]:
            history_rows = [
                {"日期": row.get("date"), "资产净值（USD）": row.get("totalValueUsd")}
                for row in model["history"]
            ]
            st.line_chart(history_rows, x="日期", y="资产净值（USD）", height=330)
        else:
            st.info("生成至少两份每日快照后，这里将显示净值趋势。")
    with right:
        _render_section_title(st, "02", "资产配置", "按资产类别观察分布")
        if model["assetAllocation"]:
            st.bar_chart(
                _localized_allocation_rows(model["assetAllocation"]),
                x="资产类别",
                y="资产价值（USD）",
                height=330,
            )
        else:
            st.info("暂时没有可估值资产。")
    lower_left, lower_right = st.columns(2, gap="large")
    with lower_left:
        _render_section_title(st, "03", "存放位置", "资产托管与平台分布")
        st.dataframe(
            _localized_location_rows(model["locationAllocation"]),
            width="stretch",
            hide_index=True,
            column_config={
                "label": "平台 / 位置",
                "valueUsd": "资产价值（USD）",
                "assetCount": "资产项数",
            },
        )
    with lower_right:
        _render_section_title(st, "04", "核心持仓", "按美元价值从高到低")
        st.dataframe(
            model["topAssets"],
            width="stretch",
            hide_index=True,
            column_config={
                "symbol": "资产",
                "valueUsd": "资产价值（USD）",
                "percentage": "组合占比",
            },
        )
    _render_drilldown(st, model["drilldown"])
    if model["warnings"]:
        _render_section_title(st, "06", "风险提示", "需要留意的组合信号")
        for warning in model["warnings"]:
            st.warning(_localized_warning(warning))


def _render_configuration(st: Any) -> None:
    """渲染不接触凭据保险库的 YAML 配置中心。

    输入：Streamlit 模块，以及 ``ASSET_MCP_CONFIG`` 解析出的当前本地配置路径。
    输出：账户、钱包、手工资产和标签四个受控编辑区；提交时原子保存，错误时保留原文件。
    """
    path = default_config_path()
    _render_section_title(st, "设置", "配置中心", "管理账户、钱包与资产标签")
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
    _render_section_title(st, "05", "资产明细", "从币种逐级筛选到存放位置")
    assets = initial_view["assets"]
    if not assets:
        st.info("暂无资产明细。")
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
    st.caption(f"{len(view['rows'])} 项资产 · 合计 ${view['totalValueUsd']:,.2f}")
    st.dataframe(view["rows"], width="stretch", hide_index=True)
    _render_exports(st, view["filteredAssets"])


def _render_exports(st: Any, assets: list[dict[str, Any]]) -> None:
    """渲染当前筛选资产的 CSV 与 JSON 下载按钮。

    输入：Streamlit 模块及下钻后保留的标准化资产字典。
    输出：无返回值；生成两个只含白名单字段的内存下载，不在服务器写临时导出文件。
    """
    exports = build_portfolio_exports(assets)
    st.caption("导出当前筛选结果 · 仅包含安全字段")
    csv_column, json_column = st.columns(2, gap="small")
    with csv_column:
        st.download_button(
            "导出当前结果 · CSV",
            data=exports["csv"]["data"],
            file_name=exports["csv"]["filename"],
            mime=exports["csv"]["mime"],
            width="stretch",
        )
    with json_column:
        st.download_button(
            "导出当前结果 · JSON",
            data=exports["json"]["data"],
            file_name=exports["json"]["filename"],
            mime=exports["json"]["mime"],
            width="stretch",
        )


def _render_metrics(st: Any, metrics: list[dict[str, str]]) -> None:
    """渲染两行四列首页指标卡。

    输入：Streamlit 模块和八项格式化指标。
    输出：无返回值；生成带稳定 DOM class 的响应式指标网格。
    """
    for offset in (0, 4):
        columns = st.columns(4, gap="medium")
        for index, (column, metric) in enumerate(
            zip(columns, metrics[offset : offset + 4]),
            start=offset,
        ):
            with column:
                state_class = (
                    " is-alert"
                    if metric["id"] == "sync_status" and metric["value"] != "数据正常"
                    else ""
                )
                column.markdown(
                    f"""
                    <section class="metric-card metric-{escape(metric['id'])}{state_class}"
                      style="--delay:{index * 45}ms">
                      <span>{escape(metric['label'])}</span>
                      <strong>{escape(metric['value'])}</strong>
                      <small>{escape(metric['detail'])}</small>
                    </section>
                    """,
                    unsafe_allow_html=True,
                )


def _render_section_title(st: Any, index: str, title: str, subtitle: str) -> None:
    """渲染统一的中文内容区标题。

    输入：Streamlit 模块、章节序号、主标题和辅助说明；所有文本都会进行 HTML 转义。
    输出：无返回值；向页面写入带稳定 class 的标题结构，供主题样式统一控制。
    """
    st.markdown(
        f"""
        <div class="section-title">
          <span>{escape(index)}</span>
          <div><h2>{escape(title)}</h2><p>{escape(subtitle)}</p></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _localized_allocation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """本地化资产配置图的类别标签。

    输入：包含 ``label``、``valueUsd`` 等字段的配置图行。
    输出：使用“资产类别”“资产价值（USD）”中文字段的新列表，常见类别翻译为中文，
    未知类别保留原值；原输入不会被修改。
    """
    labels = {
        "crypto": "加密资产",
        "cash": "现金",
        "stock": "股票",
        "fund": "基金",
        "property": "房产",
        "commodity": "大宗商品",
    }
    return [
        {
            "资产类别": labels.get(str(row.get("label")), row.get("label")),
            "资产价值（USD）": row.get("valueUsd", 0),
        }
        for row in rows
    ]


def _localized_location_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规范化存放位置表中的平台名称。

    输入：Dashboard 按来源聚合的表格行。输出：字段结构不变的新列表，内置来源显示为
    中文名称或规范品牌名，未知来源保留原值；原输入不会被修改。
    """
    labels = {
        "binance": "Binance",
        "okx": "OKX",
        "onchain": "链上钱包",
        "manual": "手工资产",
        "moomoo": "富途 moomoo",
        "longbridge": "长桥",
        "ibkr": "盈透证券",
    }
    return [{**row, "label": labels.get(str(row.get("label")), row.get("label"))} for row in rows]


def _localized_warning(warning: dict[str, Any]) -> str:
    """把领域层风险代码转换为简洁中文提醒。

    输入：包含稳定 ``code`` 和可选原始 ``message`` 的风险字典。
    输出：已知风险返回完整中文标题与处理提示；未知代码使用“风险提醒”和原始说明，
    不改变 MCP 领域层的稳定英文契约。
    """
    known = {
        "asset_concentration": "资产集中度：单一资产占比超过 50%，请关注价格波动风险。",
        "cex_concentration": "平台集中度：中心化交易所托管占比超过 50%，请关注平台风险。",
        "high_leverage": "杠杆风险：存在至少 5 倍杠杆持仓，请关注强平距离。",
        "stale_data": "数据时效：部分资产使用历史缓存，请检查对应数据来源。",
    }
    code = str(warning.get("code") or "")
    if code in known:
        return known[code]
    return f"风险提醒：{warning.get('message') or '请检查当前组合。'}"


_STYLES = """
<style>
:root {
  --ink:#0b100e; --ink-soft:#111915; --panel:#141c18; --panel-high:#1a241e;
  --line:#2c3a32; --gold:#d9b36c; --gold-soft:#8e7445; --jade:#76d6ad;
  --paper:#f0eee4; --muted:#929c94; --danger:#e69a71;
}
.stApp {
  background:
    radial-gradient(circle at 88% 0%, rgba(61,91,72,.42) 0, transparent 30rem),
    linear-gradient(135deg, rgba(217,179,108,.035) 25%, transparent 25%) 0 0/28px 28px,
    var(--ink);
  color:var(--paper);
  font-family:'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
}
.block-container { max-width:1480px; padding:2.4rem 3.2rem 5rem; }
[data-testid='stSidebar'] { background:linear-gradient(180deg,#111814,#0b100e); border-right:1px solid var(--line); }
[data-testid='stSidebar'] [role='radiogroup'] { gap:.35rem; }
[data-testid='stSidebar'] label { border-radius:3px; padding:.38rem .55rem; transition:background .2s ease; }
[data-testid='stSidebar'] label:hover { background:rgba(217,179,108,.08); }
.masthead { display:flex; justify-content:space-between; align-items:flex-end; border-bottom:1px solid var(--line); padding:1rem 0 1.7rem; margin-bottom:1.8rem; animation:rise .55s ease both; }
.eyebrow,.readonly { color:var(--jade); font:500 .72rem Menlo,'SFMono-Regular',monospace; letter-spacing:.16em; }
.masthead h1 { margin:.48rem 0 .6rem; color:var(--paper); font:600 clamp(2.5rem,4.8vw,5.2rem)/.95 'Songti SC','STSong',serif; letter-spacing:-.08em; }
.masthead h1 em { color:var(--gold); font-style:normal; font-weight:400; margin-left:.16em; }
.masthead p { margin:0; color:var(--muted); font-size:.92rem; letter-spacing:.03em; }
.readonly { border:1px solid #3d5e4d; background:#132019; padding:.62rem .82rem; white-space:nowrap; }
.readonly i { display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--jade); box-shadow:0 0 12px var(--jade); margin-right:.4rem; }
.metric-card { min-height:132px; position:relative; overflow:hidden; background:linear-gradient(145deg,rgba(27,38,32,.96),rgba(15,21,18,.96)); border:1px solid var(--line); padding:1.08rem 1.2rem; margin-bottom:1rem; box-shadow:0 18px 48px rgba(0,0,0,.22); animation:rise .45s calc(var(--delay)) ease both; transition:transform .2s ease,border-color .2s ease; }
.metric-card::before { content:''; position:absolute; left:0; top:0; width:2px; height:100%; background:var(--gold); }
.metric-card:hover { transform:translateY(-3px); border-color:#56675c; }
.metric-card span,.metric-card small { display:block; color:var(--muted); font-size:.72rem; letter-spacing:.08em; }
.metric-card strong { display:block; color:var(--paper); font:600 clamp(1.4rem,2vw,2.1rem) 'Songti SC','STSong',serif; margin:.62rem 0 .38rem; letter-spacing:-.035em; }
.metric-sync_status::before { background:var(--jade); }
.metric-sync_status strong { color:var(--jade); font-family:'PingFang SC','Hiragino Sans GB',sans-serif; font-size:1.35rem; }
.metric-sync_status.is-alert::before { background:var(--danger); }
.metric-sync_status.is-alert strong { color:var(--danger); }
.section-title { display:flex; align-items:center; gap:.85rem; margin:1.35rem 0 .72rem; }
.section-title>span { color:var(--gold); font:500 .7rem Menlo,'SFMono-Regular',monospace; border:1px solid var(--gold-soft); min-width:2.15rem; height:2.15rem; display:grid; place-items:center; }
.section-title h2 { color:var(--paper); font:500 1.02rem 'Songti SC','STSong',serif; margin:0; letter-spacing:.08em; }
.section-title p { color:var(--muted); font-size:.7rem; margin:.16rem 0 0; }
[data-testid='stDataFrame'],[data-testid='stVegaLiteChart'] { border:1px solid var(--line); background:rgba(17,25,21,.78); padding:.5rem; box-shadow:0 14px 38px rgba(0,0,0,.16); }
[data-testid='stAlert'] { border-radius:2px; border-color:var(--line); }
[data-testid='stDownloadButton'] button,[data-testid='stFormSubmitButton'] button { border:1px solid var(--gold-soft); color:var(--gold); background:#151b17; letter-spacing:.03em; transition:all .2s ease; }
[data-testid='stDownloadButton'] button:hover,[data-testid='stFormSubmitButton'] button:hover { border-color:var(--gold); color:var(--paper); transform:translateY(-1px); }
[data-baseweb='tab-list'] { border-bottom:1px solid var(--line); }
[data-baseweb='tab'] { color:var(--muted); }
[data-baseweb='tab'][aria-selected='true'] { color:var(--gold); }
[data-testid='stTextArea'] [data-baseweb='base-input'] { background:var(--ink-soft) !important; border-color:var(--line) !important; }
[data-testid='stTextArea'] textarea {
  background:var(--ink-soft) !important;
  color:var(--paper) !important;
  -webkit-text-fill-color:var(--paper);
  caret-color:var(--gold);
  border:1px solid var(--line) !important;
  border-radius:2px !important;
  font:400 .82rem/1.55 Menlo,'SFMono-Regular',monospace;
  opacity:1 !important;
}
[data-testid='stTextArea'] textarea:focus { border-color:var(--gold-soft) !important; box-shadow:0 0 0 1px var(--gold-soft); }
[data-testid='stTextArea'] textarea::selection { background:#6e5a35; color:#fff; }
[data-testid='stTextArea'] label,[data-testid='stTextArea'] label p { color:var(--paper) !important; }
input,[data-baseweb='select']>div { border-radius:2px !important; }
@keyframes rise { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
@media(max-width:700px){
  .block-container{padding:1.25rem 1rem 3rem}.masthead{align-items:flex-start;gap:1rem}.readonly{display:none}
  .masthead h1{font-size:2.75rem}.masthead p{max-width:23rem}.section-title{margin-top:1rem}
}
</style>
"""


if __name__ == "__main__":
    main()
