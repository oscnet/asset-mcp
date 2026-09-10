from __future__ import annotations

import pytest

from asset_mcp.acceptance import evaluate_acceptance


def test_acceptance_passes_healthy_sources_chains_and_amount_coverage():
    """输入健康账户、三条链资产和官方总额；输出全部验收项通过的脱敏报告。"""
    report = evaluate_acceptance(
        health={
            "ok": True,
            "accounts": [
                {"source": "binance", "enabled": True, "ok": True},
                {"source": "okx", "enabled": True, "ok": True},
                {"source": "onchain", "enabled": True, "ok": True},
            ],
            "providerErrors": [],
        },
        overview={
            "ok": True,
            "assets": [
                _asset("binance", "BTC", 4000),
                _asset("okx", "USDT", 3000),
                _asset("onchain", "BTC", 1000, chain="bitcoin"),
                _asset("onchain", "ETH", 1000, chain="ethereum"),
                _asset("onchain", "SOL", 1000, chain="solana"),
            ],
            "providerErrors": [],
        },
        expected_total_usd=10_200,
        required_sources=("binance", "okx"),
        required_chains=("bitcoin", "ethereum", "solana"),
    )

    assert report["passed"] is True
    assert report["observedTotalUsd"] == 10_000
    assert report["amountCoveragePercent"] == pytest.approx(98.03921569)
    assert all(check["passed"] for check in report["checks"])
    assert "accounts" not in report


def test_acceptance_fails_missing_source_chain_stale_data_and_low_coverage():
    """输入不健康来源、缺链、旧数据和较大金额差异；输出逐项失败而非误判通过。"""
    report = evaluate_acceptance(
        health={
            "ok": False,
            "accounts": [
                {"source": "binance", "enabled": True, "ok": True},
                {"source": "okx", "enabled": True, "ok": False},
            ],
            "providerErrors": [{"source": "okx", "code": "provider_exception"}],
        },
        overview={
            "ok": False,
            "assets": [
                _asset("binance", "BTC", 5000, sync_status="STALE"),
                _asset("onchain", "BTC", 1000, chain="bitcoin"),
            ],
            "providerErrors": [{"source": "okx", "code": "provider_exception"}],
        },
        expected_total_usd=10_000,
        required_sources=("binance", "okx"),
        required_chains=("bitcoin", "ethereum", "solana"),
    )

    checks = {check["id"]: check for check in report["checks"]}
    assert report["passed"] is False
    assert report["amountCoveragePercent"] == 60
    assert report["staleAssetCount"] == 1
    assert checks["source:okx"]["passed"] is False
    assert checks["chain:ethereum"]["passed"] is False
    assert checks["chain:solana"]["passed"] is False
    assert checks["fresh_data"]["passed"] is False
    assert checks["amount_coverage"]["passed"] is False


@pytest.mark.parametrize("expected", [0, -1])
def test_acceptance_rejects_non_positive_expected_total(expected):
    """输入零或负官方总额；输出 ValueError，避免产生无意义覆盖率。"""
    with pytest.raises(ValueError, match="expected_total_usd"):
        evaluate_acceptance({}, {}, expected)


def _asset(
    source: str,
    symbol: str,
    value_usd: float,
    *,
    chain: str | None = None,
    sync_status: str = "FRESH",
) -> dict[str, object]:
    """输入来源、币种、美元价值、链和同步状态；输出验收测试使用的标准资产字典。"""
    return {
        "source": source,
        "symbol": symbol,
        "valueUsd": value_usd,
        "chain": chain,
        "syncStatus": sync_status,
    }
