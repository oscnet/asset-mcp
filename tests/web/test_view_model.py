from __future__ import annotations

from asset_mcp.web.view_model import build_home_view_model


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
    assert metrics["sync_status"]["value"] == "FRESH"
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
    assert metrics["sync_status"]["value"] == "STALE"
    assert metrics["change_24h"]["value"] == "—"
    assert model["warnings"][0]["code"] == "stale_data"
