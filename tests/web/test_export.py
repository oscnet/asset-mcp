from __future__ import annotations

import csv
import io
import json

from asset_mcp.web.export import build_portfolio_exports


def test_portfolio_exports_create_machine_readable_csv_and_json():
    """输入标准化资产；输出字段稳定的 CSV/JSON，且排除未批准字段和潜在秘密。"""
    asset = {
        "symbol": "BTC",
        "name": "=HYPERLINK(\"https://example.invalid\")",
        "source": "binance",
        "accountId": "binance-main",
        "accountLabel": "Main",
        "category": "crypto",
        "accountType": "spot",
        "location": "binance",
        "wallet": "spot",
        "chain": None,
        "quantity": 0.5,
        "currency": "BTC",
        "unitPriceUsd": 60000,
        "valueUsd": 30000,
        "priceSource": "binance",
        "syncStatus": "FRESH",
        "updatedAt": "2026-09-09T00:00:00Z",
        "tags": ["Core", "Personal"],
        "borrower": "张三",
        "apiKey": "must-not-leak",
        "rawSource": "private-debug-field",
    }

    exports = build_portfolio_exports([asset], as_of="2026-09-09")
    csv_rows = list(
        csv.DictReader(io.StringIO(exports["csv"]["data"].decode("utf-8-sig")))
    )
    json_payload = json.loads(exports["json"]["data"])

    assert exports["csv"]["filename"] == "asset-mcp-portfolio-2026-09-09.csv"
    assert csv_rows[0]["symbol"] == "BTC"
    assert csv_rows[0]["name"].startswith("'=")
    assert csv_rows[0]["tags"] == "Core|Personal"
    assert "apiKey" not in csv_rows[0]
    assert json_payload["schemaVersion"] == 1
    assert json_payload["count"] == 1
    assert json_payload["totalValueUsd"] == 30000
    assert json_payload["assets"][0]["tags"] == ["Core", "Personal"]
    assert json_payload["assets"][0]["borrower"] == "张三"
    assert json_payload["assets"][0]["name"].startswith("=HYPERLINK")
    assert "apiKey" not in json_payload["assets"][0]
    assert "rawSource" not in json_payload["assets"][0]


def test_empty_portfolio_exports_keep_headers_and_zero_summary():
    """输入空资产列表；输出仍可打开的表头 CSV 和零条目 JSON 摘要。"""
    exports = build_portfolio_exports([], as_of="2026-09-09")

    csv_text = exports["csv"]["data"].decode("utf-8-sig")
    json_payload = json.loads(exports["json"]["data"])

    assert csv_text.startswith("symbol,name,source,accountId")
    assert json_payload["count"] == 0
    assert json_payload["totalValueUsd"] == 0
    assert json_payload["assets"] == []
