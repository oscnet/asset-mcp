from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

EXPORT_FIELDS = (
    "symbol",
    "name",
    "source",
    "accountId",
    "accountLabel",
    "category",
    "accountType",
    "location",
    "wallet",
    "chain",
    "quantity",
    "currency",
    "unitPriceUsd",
    "valueUsd",
    "priceSource",
    "syncStatus",
    "updatedAt",
    "tags",
    "borrower",
)


def build_portfolio_exports(
    assets: list[dict[str, Any]],
    as_of: str | None = None,
) -> dict[str, dict[str, Any]]:
    """生成当前资产结果的安全 CSV 和 JSON 下载内容。

    输入：标准化资产字典列表，以及可选 ISO 日期；日期缺省为当前 UTC 日期。
    输出：``csv`` 与 ``json`` 两个下载描述，分别包含 UTF-8 数据、文件名和 MIME；
    只输出 ``EXPORT_FIELDS`` 白名单字段，凭据、Provider 原始响应和调试字段不会进入文件。
    非法日期抛出 ``ValueError``。
    """
    export_date = date.fromisoformat(as_of) if as_of else datetime.now(timezone.utc).date()
    normalized_assets = [_normalized_asset(asset) for asset in assets]

    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for asset in normalized_assets:
        csv_asset = {**asset, "tags": "|".join(asset["tags"])}
        writer.writerow({field: _csv_value(value) for field, value in csv_asset.items()})

    payload = {
        "schemaVersion": 1,
        "asOf": export_date.isoformat(),
        "baseCurrency": "USD",
        "count": len(normalized_assets),
        "totalValueUsd": sum(
            float(asset.get("valueUsd") or 0) for asset in normalized_assets
        ),
        "assets": normalized_assets,
    }
    stem = f"asset-mcp-portfolio-{export_date.isoformat()}"
    return {
        "csv": {
            "data": csv_buffer.getvalue().encode("utf-8-sig"),
            "filename": f"{stem}.csv",
            "mime": "text/csv",
        },
        "json": {
            "data": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "filename": f"{stem}.json",
            "mime": "application/json",
        },
    }


def _normalized_asset(asset: dict[str, Any]) -> dict[str, Any]:
    """输入任意资产字典；输出仅含导出白名单且可 JSON 序列化的新字典。"""
    normalized = {field: _json_value(asset.get(field)) for field in EXPORT_FIELDS}
    raw_tags = asset.get("tags") or []
    normalized["tags"] = [str(tag) for tag in raw_tags]
    return normalized


def _json_value(value: Any) -> Any:
    """输入资产字段值；输出 JSON 兼容值，Decimal 在导出边界转换为 number。"""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _csv_value(value: Any) -> Any:
    """输入待写入 CSV 的字段；输出阻止表格软件公式执行的安全单元格值。"""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value
