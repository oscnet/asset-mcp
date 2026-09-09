from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from asset_mcp.config.errors import ConfigError
from asset_mcp.config.models import (
    AppConfig,
    BinanceAccountConfig,
    IbkrAccountConfig,
    LongbridgeAccountConfig,
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
