from __future__ import annotations

from typing import Any


def build_home_view_model(
    dashboard: dict[str, Any],
    risk: dict[str, Any],
    history: dict[str, Any],
) -> dict[str, Any]:
    """构造 Streamlit 首页使用的稳定展示模型。

    输入：现有 Dashboard、风险和历史服务响应；允许部分字段缺失。
    输出：八项已格式化指标、资产/位置分布、净值序列、Top Assets 和警告；
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
        _metric("net_worth", "Total Net Worth", _currency(total), "Portfolio value"),
        _metric(
            "change_24h",
            "24H Change",
            _signed_currency(change_value) if change_value is not None else "—",
            _signed_percent(change_percent) if change_percent is not None else "No prior snapshot",
        ),
        _metric("stablecoin", "Stablecoin Ratio", _percent(stablecoin.get("percent")), "Liquidity"),
        _metric("cex", "CEX Exposure", _percent(custody.get("cexPercent")), "Custodial"),
        _metric(
            "self_custody",
            "Self Custody",
            _percent(custody.get("selfCustodyPercent")),
            "On-chain",
        ),
        _metric(
            "futures",
            "Futures Exposure",
            _percent(futures.get("grossExposurePercent")),
            "Gross notional",
        ),
        _metric(
            "unrealized_pnl",
            "Unrealized P&L",
            _signed_currency(float(futures.get("unrealizedPnlUsd") or 0)),
            "Open positions",
        ),
        _metric(
            "sync_status",
            "Data Status",
            "STALE" if is_stale else "FRESH",
            "Review warnings" if is_stale else "Sources healthy",
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
    }


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
