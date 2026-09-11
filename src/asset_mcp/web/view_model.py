from __future__ import annotations

from typing import Any


def build_home_view_model(
    dashboard: dict[str, Any],
    risk: dict[str, Any],
    history: dict[str, Any],
    assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造 Streamlit 首页使用的稳定展示模型。

    输入：现有 Dashboard、风险和历史服务响应，以及可选标准化资产明细；
    允许部分字段缺失，资产明细用于构造五级下钻的初始状态。
    输出：八项已格式化指标、资产/位置分布、净值序列、Top Assets、下钻和警告；
    该函数只负责展示转换，不重新计算领域层风险指标。
    """
    total = float(dashboard.get("totalValueUsd") or 0)
    points = list(history.get("points") or [])
    change_value, change_percent = _history_change(points)
    stablecoin = risk.get("stablecoin") or {}
    custody = risk.get("custody") or {}
    futures = risk.get("futures") or {}
    warnings = list(risk.get("warnings") or [])
    is_stale = bool(dashboard.get("partial") or risk.get("partial")) or any(
        warning.get("code") == "stale_data" for warning in warnings
    )
    metrics = [
        _metric("net_worth", "资产净值", _currency(total), "全部资产折算价值"),
        _metric(
            "change_24h",
            "近一期变动",
            _signed_currency(change_value) if change_value is not None else "—",
            _signed_percent(change_percent) if change_percent is not None else "暂无上一期快照",
        ),
        _metric("stablecoin", "稳定币占比", _percent(stablecoin.get("percent")), "流动性资产"),
        _metric("cex", "中心化平台", _percent(custody.get("cexPercent")), "托管于交易所"),
        _metric(
            "self_custody",
            "链上自托管",
            _percent(custody.get("selfCustodyPercent")),
            "个人钱包资产",
        ),
        _metric(
            "futures",
            "合约敞口",
            _percent(futures.get("grossExposurePercent")),
            "名义本金占比",
        ),
        _metric(
            "unrealized_pnl",
            "未实现盈亏",
            _signed_currency(float(futures.get("unrealizedPnlUsd") or 0)),
            "当前合约持仓",
        ),
        _metric(
            "sync_status",
            "数据状态",
            "需要关注" if is_stale else "数据正常",
            "请查看下方风险提示" if is_stale else "所有来源同步正常",
        ),
    ]
    return {
        "metrics": metrics,
        "assetAllocation": list(dashboard.get("pieByCategory") or []),
        "locationAllocation": list(dashboard.get("pieBySource") or []),
        "walletAllocation": list(dashboard.get("pieByWallet") or []),
        "topAssets": list(dashboard.get("topAssets") or []),
        "history": points,
        "warnings": warnings,
        "isStale": is_stale,
        "drilldown": build_drilldown_view(assets or [], {}),
    }


DRILLDOWN_DIMENSIONS = ("symbol", "source", "accountId", "accountType", "location")
DRILLDOWN_COLUMNS = {
    "symbol": "币种",
    "source": "平台",
    "accountId": "账户",
    "accountType": "类型",
    "location": "位置",
}

_DIMENSION_LABELS = {
    "source": {
        "binance": "Binance",
        "okx": "OKX",
        "onchain": "链上钱包",
        "manual": "手工资产",
        "loan": "借贷负债",
        "moomoo": "富途 moomoo",
        "longbridge": "长桥",
        "ibkr": "盈透证券",
    },
    "accountType": {
        "spot": "现货",
        "funding": "资金账户",
        "futures": "合约",
        "wallet": "钱包",
        "manual": "手工录入",
        "brokerage": "证券账户",
        "liability": "负债",
    },
    "location": {
        "binance": "Binance",
        "okx": "OKX",
        "self_custody": "链上自托管",
        "manual": "手工录入",
        "loan": "借贷中",
    },
}


def build_drilldown_view(
    assets: list[dict[str, Any]],
    selections: dict[str, str],
) -> dict[str, Any]:
    """构造从币种到资产位置的级联下钻数据。

    输入：标准化资产字典列表，以及 ``symbol/source/accountId/accountType/location``
    中任意已选条件；空维度统一显示为 ``unassigned``。
    输出：每一级基于前序选择生成的可选值、最终过滤后的中文展示行和美元合计；
    不修改输入资产，也不重新估值。
    """
    filtered = list(assets)
    options: dict[str, list[str]] = {}
    for dimension in DRILLDOWN_DIMENSIONS:
        options[dimension] = sorted({_dimension_value(asset, dimension) for asset in filtered})
        selected = selections.get(dimension)
        if selected:
            display_selection = _localized_dimension_value(dimension, selected)
            filtered = [
                asset
                for asset in filtered
                if _dimension_value(asset, dimension) == display_selection
            ]

    rows = [
        {
            "币种": _dimension_value(asset, "symbol"),
            "平台": _dimension_value(asset, "source"),
            "账户": _dimension_value(asset, "accountId"),
            "类型": _dimension_value(asset, "accountType"),
            "位置": _dimension_value(asset, "location"),
            "借贷人": str(asset.get("borrower") or "—"),
            "数量": asset.get("quantity", 0),
            "价值 (USD)": asset.get("valueUsd", 0),
            "状态": _sync_status_label(asset.get("syncStatus")),
        }
        for asset in sorted(
            filtered,
            key=lambda item: float(item.get("valueUsd") or 0),
            reverse=True,
        )
    ]
    return {
        "assets": list(assets),
        "filteredAssets": filtered,
        "options": options,
        "rows": rows,
        "totalValueUsd": sum(float(asset.get("valueUsd") or 0) for asset in filtered),
    }


def _dimension_value(asset: dict[str, Any], dimension: str) -> str:
    """输入资产字典和下钻维度；输出去空白后的稳定显示值或中文“未分类”。"""
    value = asset.get(dimension)
    normalized = str(value).strip() if value is not None else ""
    return _localized_dimension_value(dimension, normalized) if normalized else "未分类"


def _localized_dimension_value(dimension: str, value: str) -> str:
    """本地化下钻筛选值并保留用户自定义内容。

    输入：下钻维度名称和已去空白的原始值，可传入已经本地化的选项值。
    输出：已知平台、账户类型和位置代码的中文或规范品牌名；未知值及已翻译值原样返回。
    """
    labels = _DIMENSION_LABELS.get(dimension, {})
    return labels.get(value, value)


def _sync_status_label(value: Any) -> str:
    """把内部同步状态转换为中文展示文本。

    输入：资产的 ``syncStatus`` 值，允许为空或未知字符串。
    输出：``FRESH/STALE/ERROR`` 分别映射为“实时/缓存/异常”；未知值返回“未知”。
    """
    return {
        "FRESH": "实时",
        "STALE": "缓存",
        "ERROR": "异常",
    }.get(str(value or "").upper(), "未知")


def _history_change(points: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """计算最近两个实际快照之间的净值变化。

    输入：日期升序或可排序的历史点。
    输出：美元变化和相对前值百分比；不足两点或前值为零时返回相应 ``None``。
    """
    if len(points) < 2:
        return None, None
    ordered = sorted(points, key=lambda point: str(point.get("date") or ""))
    previous = float(ordered[-2].get("totalValueUsd") or 0)
    current = float(ordered[-1].get("totalValueUsd") or 0)
    change = current - previous
    return change, (change * 100 / previous if previous else None)


def _metric(identifier: str, label: str, value: str, detail: str) -> dict[str, str]:
    """构造单个首页指标。

    输入：稳定 ID、标签、格式化值和补充说明。
    输出：字段固定的字典，供 Streamlit 卡片统一渲染。
    """
    return {"id": identifier, "label": label, "value": value, "detail": detail}


def _currency(value: float) -> str:
    """输入美元数值；输出带千分位和两位小数的美元字符串。"""
    return f"${value:,.2f}"


def _signed_currency(value: float) -> str:
    """输入有符号美元数值；输出符号位于美元符号之前的展示字符串。"""
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def _percent(value: Any) -> str:
    """输入可缺失百分比数值；输出两位小数百分比字符串。"""
    return f"{float(value or 0):.2f}%"


def _signed_percent(value: float) -> str:
    """输入有符号百分比；输出显式正负号及两位小数。"""
    return f"{value:+.2f}%"
