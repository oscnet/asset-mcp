from __future__ import annotations

from asset_mcp.web.view_model import build_drilldown_view, build_home_view_model


def test_home_view_model_builds_eight_metrics_and_chart_data():
    dashboard = {
        "totalValueUsd": 125000,
        "pieByCategory": [{"label": "crypto", "valueUsd": 100000, "assetCount": 3}],
        "pieBySource": [{"label": "binance", "valueUsd": 70000, "assetCount": 2}],
        "pieByWallet": [{"label": "spot", "valueUsd": 70000, "assetCount": 2}],
        "topAssets": [{"symbol": "BTC", "valueUsd": 70000}],
        "partial": False,
    }
    risk = {
        "stablecoin": {"percent": 25},
        "custody": {"cexPercent": 72, "selfCustodyPercent": 28},
        "futures": {"grossExposurePercent": 12.4, "unrealizedPnlUsd": -350},
        "warnings": [],
        "partial": False,
    }
    history = {
        "points": [
            {"date": "2026-09-08", "totalValueUsd": 100000},
            {"date": "2026-09-09", "totalValueUsd": 125000},
        ]
    }

    model = build_home_view_model(dashboard, risk, history)

    metrics = {item["id"]: item for item in model["metrics"]}
    assert len(metrics) == 8
    assert metrics["net_worth"]["value"] == "$125,000.00"
    assert metrics["change_24h"]["value"] == "+$25,000.00"
    assert metrics["change_24h"]["detail"] == "+25.00%"
    assert metrics["stablecoin"]["value"] == "25.00%"
    assert metrics["unrealized_pnl"]["value"] == "-$350.00"
    assert metrics["net_worth"]["label"] == "资产净值"
    assert metrics["net_worth"]["detail"] == "全部资产折算价值"
    assert metrics["sync_status"]["label"] == "数据状态"
    assert metrics["sync_status"]["value"] == "数据正常"
    assert metrics["sync_status"]["detail"] == "所有来源同步正常"
    assert model["assetAllocation"] == dashboard["pieByCategory"]
    assert model["locationAllocation"] == dashboard["pieBySource"]
    assert model["history"] == history["points"]


def test_home_view_model_marks_partial_or_stale_data():
    model = build_home_view_model(
        {"totalValueUsd": 0, "partial": True},
        {
            "stablecoin": {},
            "custody": {},
            "futures": {},
            "warnings": [{"code": "stale_data", "message": "stale"}],
        },
        {"points": []},
    )

    metrics = {item["id"]: item for item in model["metrics"]}
    assert metrics["sync_status"]["value"] == "需要关注"
    assert metrics["sync_status"]["detail"] == "请查看下方风险提示"
    assert metrics["change_24h"]["value"] == "—"
    assert metrics["change_24h"]["detail"] == "暂无上一期快照"
    assert model["warnings"][0]["code"] == "stale_data"


def test_drilldown_view_builds_cascading_options_and_filtered_rows():
    assets = [
        _asset("BTC", "binance", "main", "spot", "binance", 60000),
        _asset("BTC", "onchain", "ledger", "wallet", "self_custody", 30000),
        _asset("ETH", "okx", "trading", "spot", "okx", 10000),
    ]

    view = build_drilldown_view(assets, {"symbol": "BTC", "source": "onchain"})

    assert view["options"]["symbol"] == ["BTC", "ETH"]
    assert view["options"]["source"] == ["Binance", "链上钱包"]
    assert view["options"]["accountId"] == ["ledger"]
    assert view["totalValueUsd"] == 30000
    assert len(view["filteredAssets"]) == 1
    assert view["filteredAssets"][0]["accountId"] == "ledger"
    assert view["rows"] == [
        {
            "币种": "BTC",
            "平台": "链上钱包",
            "账户": "ledger",
            "类型": "钱包",
            "位置": "链上自托管",
            "数量": 1,
            "价值 (USD)": 30000,
            "状态": "实时",
        }
    ]


def test_drilldown_view_uses_unassigned_for_missing_dimensions():
    asset = _asset("USD", "manual", "cash", None, None, 50)

    view = build_drilldown_view([asset], {})

    assert view["options"]["accountType"] == ["未分类"]
    assert view["options"]["location"] == ["未分类"]


def test_drilldown_view_localizes_all_sync_statuses():
    """输入三种内部同步状态；输出面向用户的中文状态文本。"""
    assets = [
        {**_asset("BTC", "binance", "a", "spot", "binance", 3), "syncStatus": "FRESH"},
        {**_asset("ETH", "onchain", "b", "wallet", "self_custody", 2), "syncStatus": "STALE"},
        {**_asset("SOL", "onchain", "c", "wallet", "self_custody", 1), "syncStatus": "ERROR"},
    ]

    view = build_drilldown_view(assets, {})

    assert [row["状态"] for row in view["rows"]] == ["实时", "缓存", "异常"]


def _asset(symbol, source, account_id, account_type, location, value_usd):
    """输入下钻维度与美元价值；输出标准化资产字典测试夹具。"""
    return {
        "symbol": symbol,
        "source": source,
        "accountId": account_id,
        "accountType": account_type,
        "location": location,
        "quantity": 1,
        "valueUsd": value_usd,
        "syncStatus": "FRESH",
    }
