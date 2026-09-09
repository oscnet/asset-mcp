from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from asset_mcp.domain.models import Asset, json_number, sum_value_usd

ALLOCATION_DIMENSIONS = {
    "asset": "symbol",
    "source": "source",
    "accountType": "accountType",
    "chain": "chain",
    "location": "location",
}


def filter_assets(
    assets: Iterable[Asset],
    source: str | None = None,
    accountId: str | None = None,
    category: str | None = None,
) -> list[Asset]:
    return [
        asset
        for asset in assets
        if (source is None or asset.source == source)
        and (accountId is None or asset.accountId == accountId)
        and (category is None or asset.category == category)
    ]


def build_net_worth(assets: list[Asset]) -> dict[str, Any]:
    return {
        "baseCurrency": "USD",
        "totalValueUsd": json_number(sum_value_usd(assets)),
        "byCategory": _group(assets, lambda asset: asset.category),
        "bySource": _group(assets, lambda asset: asset.source),
        "byLocation": _group(assets, lambda asset: asset.location),
        "byAccountType": _group(assets, lambda asset: asset.accountType),
        "byChain": _group(assets, lambda asset: asset.chain),
        "byWallet": _group(assets, lambda asset: asset.wallet or asset.source),
        "byAccount": _group(
            assets,
            lambda asset: asset.accountId,
            lambda asset: {
                "accountId": asset.accountId,
                "accountLabel": asset.accountLabel,
                "source": asset.source,
            },
        ),
        "byCurrency": _group(assets, lambda asset: asset.currency),
        "assetCount": len(assets),
    }


def build_dashboard_data(assets: list[Asset]) -> dict[str, Any]:
    by_category = build_net_worth(assets)["byCategory"]
    by_source = build_net_worth(assets)["bySource"]
    by_wallet = build_net_worth(assets)["byWallet"]
    by_account = build_net_worth(assets)["byAccount"]
    top_assets = sorted(
        [
            {
                "label": asset.name or asset.symbol,
                "symbol": asset.symbol,
                "source": asset.source,
                "accountId": asset.accountId,
                "valueUsd": json_number(round(asset.valueUsd, 8)),
            }
            for asset in assets
        ],
        key=lambda item: item["valueUsd"],
        reverse=True,
    )
    return {
        "baseCurrency": "USD",
        "totalValueUsd": json_number(sum_value_usd(assets)),
        "pieByCategory": _chart_rows(by_category, "category"),
        "pieBySource": _chart_rows(by_source, "source"),
        "pieByWallet": _chart_rows(by_wallet, "wallet"),
        "barByAccount": _chart_rows(by_account, "accountId"),
        "topAssets": top_assets[:20],
    }


def build_allocation(assets: list[Asset], group_by: str) -> dict[str, Any]:
    """按一个统一维度计算资产配置。

    输入：标准化 ``Asset`` 列表，以及 ``asset/source/accountType/chain/location/tag``
    之一；空维度值归入 ``unassigned``，tag 会把多标签资产展开到多个重叠分组。
    输出：总美元价值、按价值降序的分组金额/占比/行数，以及 tag 是否重叠的标记；
    不支持的维度抛出 ``ValueError``。
    """
    if group_by != "tag" and group_by not in ALLOCATION_DIMENSIONS:
        raise ValueError(f"Unsupported allocation dimension: {group_by}")
    total = sum_value_usd(assets)
    buckets: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if group_by == "tag":
            keys = asset.tags or ("unassigned",)
        else:
            raw_key = getattr(asset, ALLOCATION_DIMENSIONS[group_by])
            keys = (str(raw_key).strip() if raw_key else "unassigned",)
        for key in keys:
            bucket = buckets.setdefault(
                key,
                {"key": key, "valueUsd": Decimal("0"), "assetCount": 0},
            )
            bucket["valueUsd"] += asset.valueUsd
            bucket["assetCount"] += 1

    groups = []
    for bucket in buckets.values():
        value = round(bucket["valueUsd"], 8)
        percentage = round(value * 100 / total, 8) if total else Decimal("0")
        groups.append(
            {
                "key": bucket["key"],
                "valueUsd": json_number(value),
                "percentage": json_number(percentage),
                "assetCount": bucket["assetCount"],
            }
        )
    groups.sort(key=lambda row: (-row["valueUsd"], row["key"]))
    return {
        "groupBy": group_by,
        "totalValueUsd": json_number(total),
        "overlapping": group_by == "tag",
        "groups": groups,
    }


def _group(
    assets: list[Asset],
    key_fn,
    meta_fn=None,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for asset in assets:
        raw_key = key_fn(asset)
        if raw_key is None or str(raw_key).strip() == "":
            continue
        key = str(raw_key)
        if key not in buckets:
            buckets[key] = {"key": key, "valueUsd": Decimal("0"), "assetCount": 0}
            if meta_fn is not None:
                buckets[key].update(meta_fn(asset))
        buckets[key]["valueUsd"] += asset.valueUsd
        buckets[key]["assetCount"] += 1

    rows = []
    for key, row in buckets.items():
        row["valueUsd"] = json_number(round(row["valueUsd"], 8))
        rows.append(row)
    return sorted(rows, key=lambda row: row["valueUsd"], reverse=True)


def _chart_rows(rows: list[dict[str, Any]], label_key: str) -> list[dict[str, Any]]:
    return [
        {
            "label": row.get(label_key) or row["key"],
            "valueUsd": row["valueUsd"],
            "assetCount": row["assetCount"],
        }
        for row in rows
    ]
