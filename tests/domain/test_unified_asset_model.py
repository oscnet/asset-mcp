import json

import pytest

from asset_mcp.domain.aggregation import build_net_worth
from asset_mcp.domain.models import Asset


def test_exchange_asset_derives_location_account_type_and_price_source():
    asset = _asset(source="binance", wallet="funding")

    assert asset.location == "binance"
    assert asset.accountType == "funding"
    assert asset.chain is None
    assert asset.priceSource == "binance"
    assert asset.syncStatus == "FRESH"


def test_onchain_asset_derives_chain_without_exposing_address_as_account_type():
    asset = _asset(source="onchain", wallet="ethereum:0x1234...abcd")

    assert asset.accountType == "wallet"
    assert asset.chain == "ethereum"
    assert asset.location == "onchain"


def test_explicit_dimensions_are_preserved_and_tags_are_normalized():
    asset = _asset(
        source="manual",
        wallet=None,
        accountType="bank_deposit",
        location="CMB",
        priceSource="manual_rate",
        syncStatus="STALE",
        strategy="Long Term",
        riskLevel="Low",
        tags=(" Family ", "Reserve", "Family", ""),
    )

    assert asset.accountType == "bank_deposit"
    assert asset.location == "CMB"
    assert asset.priceSource == "manual_rate"
    assert asset.syncStatus == "STALE"
    assert asset.strategy == "Long Term"
    assert asset.riskLevel == "Low"
    assert asset.tags == ("Family", "Reserve")


def test_asset_dict_serializes_new_dimensions_and_tags():
    payload = json.loads(json.dumps(_asset(source="onchain", wallet="solana:So11").to_dict()))

    assert payload["accountType"] == "wallet"
    assert payload["chain"] == "solana"
    assert payload["syncStatus"] == "FRESH"
    assert payload["tags"] == []


def test_invalid_sync_status_is_rejected():
    with pytest.raises(ValueError, match="syncStatus"):
        _asset(source="manual", wallet=None, syncStatus="UNKNOWN")


def test_net_worth_groups_new_dimensions_and_ignores_missing_chain():
    result = build_net_worth(
        [
            _asset(source="binance", wallet="spot", value="100"),
            _asset(source="onchain", wallet="ethereum:0x1234", value="200"),
            _asset(source="manual", wallet=None, value="50"),
        ]
    )

    assert {row["key"] for row in result["byAccountType"]} == {"spot", "wallet", "manual"}
    assert result["byChain"] == [{"key": "ethereum", "valueUsd": 200.0, "assetCount": 1}]
    assert {row["key"] for row in result["byLocation"]} == {"binance", "onchain", "manual"}


def _asset(
    *,
    source,
    wallet,
    value="1",
    accountType=None,
    location=None,
    priceSource=None,
    syncStatus="FRESH",
    strategy=None,
    riskLevel=None,
    tags=(),
):
    return Asset(
        source=source,
        accountId=f"{source}-main",
        accountLabel=f"{source} Main",
        category="crypto" if source != "manual" else "cash",
        symbol="USD",
        quantity="1",
        currency="USD",
        unitPriceUsd=value,
        valueUsd=value,
        updatedAt="2026-09-09T00:00:00Z",
        wallet=wallet,
        accountType=accountType,
        location=location,
        priceSource=priceSource,
        syncStatus=syncStatus,
        strategy=strategy,
        riskLevel=riskLevel,
        tags=tags,
    )
