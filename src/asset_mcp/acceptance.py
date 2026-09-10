from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

DEFAULT_REQUIRED_SOURCES = ("binance", "okx")
DEFAULT_REQUIRED_CHAINS = ("bitcoin", "ethereum", "solana")


def evaluate_acceptance(
    health: dict[str, Any],
    overview: dict[str, Any],
    expected_total_usd: Decimal | str | int | float,
    required_sources: Iterable[str] = DEFAULT_REQUIRED_SOURCES,
    required_chains: Iterable[str] = DEFAULT_REQUIRED_CHAINS,
    minimum_coverage_percent: Decimal | str | int | float = 90,
) -> dict[str, Any]:
    """生成真实账户的确定性验收报告。

    输入：数据源健康响应、Portfolio overview 响应、官方页面合计美元价值、必须健康的
    来源、必须出现资产的链，以及最低金额覆盖率。覆盖率使用较小总额除以较大总额，
    同时识别漏算与明显重复计算。
    输出：不含账户、地址和凭据的 JSON 兼容报告；包括总额、覆盖率、已观察维度和逐项
    通过状态。参数无效时抛出 ``ValueError``，本函数不访问网络也不修改资产。
    """
    expected = _positive_decimal(expected_total_usd, "expected_total_usd")
    threshold = Decimal(str(minimum_coverage_percent))
    if not threshold.is_finite() or threshold < 0 or threshold > 100:
        raise ValueError("minimum_coverage_percent must be between 0 and 100")

    assets = list(overview.get("assets") or [])
    observed = sum(
        (Decimal(str(asset.get("valueUsd") or 0)) for asset in assets),
        Decimal("0"),
    )
    coverage = min(observed, expected) * Decimal("100") / max(observed, expected)
    coverage_number = round(float(coverage), 8)

    accounts = list(health.get("accounts") or [])
    healthy_sources = sorted(
        {
            str(account.get("source"))
            for account in accounts
            if account.get("enabled") and account.get("ok") and account.get("source")
        }
    )
    observed_sources = sorted(
        {str(asset.get("source")) for asset in assets if asset.get("source")}
    )
    observed_chains = sorted(
        {str(asset.get("chain")) for asset in assets if asset.get("chain")}
    )
    stale_count = sum(1 for asset in assets if asset.get("syncStatus") != "FRESH")

    checks = [
        _check("provider_health", bool(health.get("ok")), "all enabled providers healthy"),
        _check("portfolio_fetch", bool(overview.get("ok")), "portfolio fetch completed"),
        _check("fresh_data", stale_count == 0, f"stale asset rows: {stale_count}"),
    ]
    checks.extend(
        _check(
            f"source:{source}",
            source in healthy_sources,
            f"healthy source required: {source}",
        )
        for source in dict.fromkeys(required_sources)
    )
    checks.extend(
        _check(
            f"chain:{chain}",
            chain in observed_chains,
            f"asset-bearing chain required: {chain}",
        )
        for chain in dict.fromkeys(required_chains)
    )
    checks.append(
        _check(
            "amount_coverage",
            coverage >= threshold,
            f"minimum coverage: {float(threshold):g}%",
        )
    )

    return {
        "schemaVersion": 1,
        "passed": all(check["passed"] for check in checks),
        "observedTotalUsd": float(observed),
        "expectedTotalUsd": float(expected),
        "amountCoveragePercent": coverage_number,
        "minimumCoveragePercent": float(threshold),
        "healthySources": healthy_sources,
        "observedSources": observed_sources,
        "observedChains": observed_chains,
        "staleAssetCount": stale_count,
        "providerErrorCount": len(health.get("providerErrors") or []),
        "portfolioErrorCount": len(overview.get("providerErrors") or []),
        "checks": checks,
    }


def _positive_decimal(value: Decimal | str | int | float, field_name: str) -> Decimal:
    """校验正有限十进制数。

    输入：Decimal 兼容值和用于错误消息的字段名。
    输出：严格大于零的有限 ``Decimal``；零、负数、NaN 或无法解析时抛出 ``ValueError``。
    """
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{field_name} must be a positive number") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field_name} must be a positive number")
    return result


def _check(identifier: str, passed: bool, detail: str) -> dict[str, Any]:
    """构造稳定验收项。

    输入：机器可读标识、布尔结果和不含秘密的说明。
    输出：字段固定为 ``id/passed/detail`` 的 JSON 兼容字典。
    """
    return {"id": identifier, "passed": passed, "detail": detail}
