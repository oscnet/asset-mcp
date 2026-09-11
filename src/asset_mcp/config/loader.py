from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from asset_mcp.config.errors import ConfigError
from asset_mcp.config.models import (
    AppConfig,
    BinanceAccountConfig,
    IbkrAccountConfig,
    LongbridgeAccountConfig,
    LoanAssetConfig,
    ManualAccountConfig,
    ManualAssetConfig,
    MoomooAccountConfig,
    OkxAccountConfig,
    OnchainAccountConfig,
    OnchainAddressConfig,
    OnchainIndexerConfig,
    OnchainTokenConfig,
)
from asset_mcp.config.validation import validate_unique_account_ids
from asset_mcp.domain.models import decimal_amount, normalize_tags


def default_user_config_path() -> Path:
    return Path.home() / ".config" / "asset-mcp" / "config.local.yaml"


def default_config_path() -> Path:
    env_path = os.environ.get("ASSET_MCP_CONFIG")
    if env_path:
        return Path(env_path)

    local_config_path = Path("config.local.yaml")
    if local_config_path.exists():
        return local_config_path.resolve()

    return default_user_config_path()


def load_config(path: str | Path | None = None, credential_vault: Any | None = None) -> AppConfig:
    """加载 YAML 配置并在内存中解析凭据引用。

    输入：可选配置路径与可选 ``CredentialVault``；存在 ``credentialRef`` 时，
    未显式传入保险库会自动选择 OS Keychain 或 Fernet 文件后端。
    输出：完成类型校验的 ``AppConfig``；原 YAML 不被修改，缺失引用抛出 ``ConfigError``。
    """
    config_path = Path(path) if path is not None else default_config_path()
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}. Run `asset-mcp init` to create one."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if _contains_credential_ref(raw):
        from asset_mcp.security import default_credential_vault, resolve_credential_refs

        vault = credential_vault or default_credential_vault()
        raw = resolve_credential_refs(raw, vault)

    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    rates = {
        str(currency).upper(): _as_float(rate, f"rates.{currency}")
        for currency, rate in (raw.get("rates") or {}).items()
    }
    rates.setdefault("USD", 1.0)
    rates.setdefault("USDT", 1.0)

    exchanges = raw.get("exchanges") or {}
    brokers = raw.get("brokers") or {}
    onchain = raw.get("onchain") or {}
    manual = raw.get("manual") or {}
    loans = raw.get("loans") or []
    tags = raw.get("tags") or {}
    if not isinstance(loans, list):
        raise ConfigError("loans must be a list.")
    if any(not isinstance(item, dict) for item in loans):
        raise ConfigError("Each loans[] item must be a mapping.")

    binance_accounts = [
        BinanceAccountConfig(
            id=_required_str(item, "id", "exchanges.binance.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            environment=str(item.get("environment", "production")),
            apiKey=str(item.get("apiKey", "")),
            apiSecret=str(item.get("apiSecret", "")),
            credentialRef=_optional_str(item.get("credentialRef")),
        )
        for item in _account_items(exchanges, "binance")
    ]

    okx_accounts = [
        OkxAccountConfig(
            id=_required_str(item, "id", "exchanges.okx.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            environment=str(item.get("environment", "production")),
            domain=str(item.get("domain", "https://www.okx.com")).rstrip("/"),
            apiKey=str(item.get("apiKey", "")),
            apiSecret=str(item.get("apiSecret", "")),
            passphrase=str(item.get("passphrase", "")),
            credentialRef=_optional_str(item.get("credentialRef")),
        )
        for item in _account_items(exchanges, "okx")
    ]

    moomoo_accounts = [
        MoomooAccountConfig(
            id=_required_str(item, "id", "brokers.moomoo.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            host=str(item.get("host", "127.0.0.1")),
            port=int(item.get("port", 11111)),
            trdMarket=str(item.get("trdMarket", "US")),
            securityFirm=str(item.get("securityFirm", "FUTUSECURITIES")),
            accountId=_optional_int(item.get("accountId")),
        )
        for item in _account_items(brokers, "moomoo")
    ]

    longbridge_accounts = [
        LongbridgeAccountConfig(
            id=_required_str(item, "id", "brokers.longbridge.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            appKey=str(item.get("appKey", "")),
            appSecret=str(item.get("appSecret", "")),
            accessToken=str(item.get("accessToken", "")),
            credentialRef=_optional_str(item.get("credentialRef")),
        )
        for item in _account_items(brokers, "longbridge")
    ]

    ibkr_accounts = [
        IbkrAccountConfig(
            id=_required_str(item, "id", "brokers.ibkr.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            token=str(item.get("token", "")),
            queryId=str(item.get("queryId", "")),
            credentialRef=_optional_str(item.get("credentialRef")),
            baseUrl=str(
                item.get(
                    "baseUrl",
                    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService",
                )
            ).rstrip("/"),
            accountId=_optional_str(item.get("accountId")),
            version=int(item.get("version", 3)),
            statementRetries=int(item.get("statementRetries", 3)),
            statementRetryDelaySeconds=_as_float(
                item.get("statementRetryDelaySeconds", 5),
                "brokers.ibkr.accounts[].statementRetryDelaySeconds",
            ),
        )
        for item in _account_items(brokers, "ibkr")
    ]

    indexer = onchain.get("indexer") or {}
    onchain_indexer = OnchainIndexerConfig(
        provider=str(indexer.get("provider", "covalent")),
        apiKey=str(indexer.get("apiKey", "")),
        credentialRef=_optional_str(indexer.get("credentialRef")),
        baseUrl=str(indexer.get("baseUrl", "https://api.covalenthq.com/v1")).rstrip("/"),
    )

    onchain_accounts = [
        OnchainAccountConfig(
            id=_required_str(item, "id", "onchain.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            addresses=[
                OnchainAddressConfig(
                    chain=_required_str(
                        address,
                        "chain",
                        f"onchain.accounts[{item.get('id')}].addresses[]",
                    ),
                    address=_required_str(
                        address, "address", f"onchain.accounts[{item.get('id')}].addresses[]"
                    ),
                    label=_optional_str(address.get("label")),
                    rpcUrl=_optional_str(address.get("rpcUrl")),
                    explorerApiUrl=_optional_str(address.get("explorerApiUrl")),
                    tokens=[
                        OnchainTokenConfig(
                            symbol=_required_str(
                                token,
                                "symbol",
                                f"onchain.accounts[{item.get('id')}].addresses[].tokens[]",
                            ),
                            contractAddress=_required_str(
                                token,
                                "contractAddress",
                                f"onchain.accounts[{item.get('id')}].addresses[].tokens[]",
                            ),
                            decimals=int(token.get("decimals", 18)),
                            name=_optional_str(token.get("name")),
                            coinGeckoId=_optional_str(token.get("coinGeckoId")),
                        )
                        for token in address.get("tokens", [])
                    ],
                )
                for address in item.get("addresses", [])
            ],
        )
        for item in onchain.get("accounts", [])
    ]

    manual_accounts = [
        ManualAccountConfig(
            id=_required_str(item, "id", "manual.accounts[]"),
            label=str(item.get("label") or item.get("id")),
            enabled=bool(item.get("enabled", True)),
            category=str(item.get("category", "manual")),
            assets=[
                ManualAssetConfig(
                    symbol=_required_str(asset, "symbol", f"manual.accounts[{item.get('id')}].assets[]"),
                    name=asset.get("name"),
                    quantity=_as_float(asset.get("quantity", 0), "manual.assets[].quantity"),
                    currency=str(asset.get("currency") or asset.get("symbol")).upper(),
                )
                for asset in item.get("assets", [])
            ],
        )
        for item in manual.get("accounts", [])
    ]

    loan_assets = [
        LoanAssetConfig(
            borrower=_required_str(item, "borrower", "loans[]").strip(),
            symbol=_required_str(item, "symbol", "loans[]").strip().upper(),
            quantity=_as_positive_decimal(item.get("quantity"), "loans[].quantity"),
            enabled=bool(item.get("enabled", True)),
        )
        for item in loans
    ]

    config = AppConfig(
        baseCurrency=str(raw.get("baseCurrency", "USD")).upper(),
        rates=rates,
        binanceAccounts=binance_accounts,
        okxAccounts=okx_accounts,
        moomooAccounts=moomoo_accounts,
        longbridgeAccounts=longbridge_accounts,
        ibkrAccounts=ibkr_accounts,
        onchainIndexer=onchain_indexer,
        onchainAccounts=onchain_accounts,
        manualAccounts=manual_accounts,
        loanAssets=loan_assets,
        assetTags=_tag_map(tags.get("assets"), uppercase_keys=True),
        accountTags=_tag_map(tags.get("accounts")),
        walletTags=_tag_map(tags.get("wallets")),
    )
    validate_unique_account_ids(config)
    return config


def _account_items(section: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return list(((section.get(key) or {}).get("accounts") or []))


def _required_str(item: dict[str, Any], key: str, location: str) -> str:
    value = item.get(key)
    if value is None or str(value).strip() == "":
        raise ConfigError(f"Missing required field '{key}' in {location}.")
    return str(value)


def _as_float(value: Any, location: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Expected number at {location}.") from exc


def _as_positive_decimal(value: Any, location: str) -> Decimal:
    """解析配置中的正数精确数量。

    输入：YAML 数值或十进制字符串，以及用于错误提示的字段路径。
    输出：大于零的有限 ``Decimal``；零、负数和非法值统一抛出包含字段路径的
    ``ConfigError``，避免借贷方向由正负号产生歧义。
    """
    try:
        result = decimal_amount(value)
    except ValueError as exc:
        raise ConfigError(f"Expected positive number at {location}.") from exc
    if result <= 0:
        raise ConfigError(f"Expected positive number at {location}.")
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def _contains_credential_ref(value: Any) -> bool:
    """递归检查配置是否需要凭据保险库。

    输入：任意 YAML 解析结果。
    输出：发现至少一个非空 ``credentialRef`` 返回 ``True``，否则返回 ``False``。
    """
    if isinstance(value, dict):
        if value.get("credentialRef"):
            return True
        return any(_contains_credential_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_credential_ref(item) for item in value)
    return False


def _tag_map(value: Any, uppercase_keys: bool = False) -> dict[str, tuple[str, ...]]:
    """解析配置中的标签映射。

    输入：目标到标签列表的 YAML 映射，以及是否把目标统一为大写。
    输出：移除空目标、空标签和重复标签后的不可变元组映射；非映射输入抛出
    ``ConfigError``，防止静默忽略错误配置。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("Tag groups must be mappings.")
    result: dict[str, tuple[str, ...]] = {}
    for raw_key, raw_tags in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if not isinstance(raw_tags, (list, tuple)):
            raise ConfigError(f"Tags for '{key}' must be a list.")
        normalized = normalize_tags(raw_tags)
        if normalized:
            result[key.upper() if uppercase_keys else key] = normalized
    return result
