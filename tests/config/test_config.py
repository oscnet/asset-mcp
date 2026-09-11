import pytest

from asset_mcp.config import (
    ConfigError,
    default_config_path,
    load_config,
    parse_config,
    redact_secrets,
)
from asset_mcp.security.credentials import CredentialVault


def test_parse_config_supports_multiple_accounts():
    config = parse_config(
        {
            "rates": {"USD": 1, "CNY": 0.14},
            "exchanges": {
                "binance": {
                    "accounts": [
                        {"id": "binance-main", "label": "Main", "apiKey": "k1", "apiSecret": "s1"},
                        {"id": "binance-sub", "label": "Sub", "apiKey": "k2", "apiSecret": "s2"},
                    ]
                },
                "okx": {
                    "accounts": [
                        {
                            "id": "okx-main",
                            "label": "OKX",
                            "apiKey": "k",
                            "apiSecret": "s",
                            "passphrase": "p",
                        }
                    ]
                },
            },
            "manual": {
                "accounts": [
                    {
                        "id": "bank-cmb",
                        "label": "CMB",
                        "category": "cash",
                        "assets": [{"symbol": "CNY", "quantity": 100, "currency": "CNY"}],
                    }
                ]
            },
            "brokers": {
                "longbridge": {
                    "accounts": [
                        {
                            "id": "longbridge-main",
                            "label": "Longbridge",
                            "appKey": "key",
                            "appSecret": "secret",
                            "accessToken": "token",
                        }
                    ]
                },
                "ibkr": {
                    "accounts": [
                        {
                            "id": "ibkr-main",
                            "label": "IBKR",
                            "token": "token",
                            "queryId": "12345",
                            "baseUrl": "https://example.test/flex/",
                            "accountId": "U1234567",
                            "statementRetries": 2,
                            "statementRetryDelaySeconds": 0,
                        }
                    ]
                }
            },
            "onchain": {
                "accounts": [
                    {
                        "id": "onchain-main",
                        "label": "On-chain Wallet",
                        "addresses": [
                            {
                                "chain": "ethereum",
                                "address": "0x0000000000000000000000000000000000000001",
                                "tokens": [
                                    {
                                        "symbol": "CUSTOM",
                                        "contractAddress": (
                                            "0x00000000000000000000000000000000000000c0"
                                        ),
                                        "decimals": 18,
                                        "name": "Custom Token",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        }
    )

    assert [account.id for account in config.binanceAccounts] == ["binance-main", "binance-sub"]
    assert config.okxAccounts[0].id == "okx-main"
    assert config.longbridgeAccounts[0].appKey == "key"
    assert config.ibkrAccounts[0].token == "token"
    assert config.ibkrAccounts[0].queryId == "12345"
    assert config.ibkrAccounts[0].baseUrl == "https://example.test/flex"
    assert config.ibkrAccounts[0].accountId == "U1234567"
    assert config.ibkrAccounts[0].statementRetries == 2
    assert config.onchainAccounts[0].addresses[0].chain == "ethereum"
    assert config.onchainAccounts[0].addresses[0].tokens[0].symbol == "CUSTOM"
    assert config.manualAccounts[0].assets[0].quantity == 100


def test_parse_config_normalizes_asset_account_and_wallet_tags():
    config = parse_config(
        {
            "tags": {
                "assets": {"BTC": [" Core ", "Core", "Long Term"]},
                "accounts": {"binance-main": ["Personal"]},
                "wallets": {"onchain-main/Ledger": ["Cold Storage"]},
            }
        }
    )

    assert config.assetTags == {"BTC": ("Core", "Long Term")}
    assert config.accountTags == {"binance-main": ("Personal",)}
    assert config.walletTags == {"onchain-main/Ledger": ("Cold Storage",)}


def test_parse_config_supports_manual_loan_assets():
    """输入借贷人、币种和数量；输出规范化且可停用的借贷配置。"""
    config = parse_config(
        {
            "loans": [
                {"borrower": "张三", "symbol": "btc", "quantity": "0.125"},
                {"borrower": "李四", "symbol": "USDT", "quantity": 100, "enabled": False},
            ]
        }
    )

    assert config.loanAssets[0].borrower == "张三"
    assert config.loanAssets[0].symbol == "BTC"
    assert config.loanAssets[0].quantity == pytest.approx(0.125)
    assert config.loanAssets[1].enabled is False


@pytest.mark.parametrize("quantity", [0, -1, "bad"])
def test_parse_config_rejects_invalid_loan_quantity(quantity):
    """输入零数、负数或非法借贷数量；输出可操作的配置校验错误。"""
    with pytest.raises(ConfigError, match=r"loans\[\].quantity"):
        parse_config({"loans": [{"borrower": "张三", "symbol": "BTC", "quantity": quantity}]})


@pytest.mark.parametrize("loans", [{"borrower": "张三"}, ["not-a-mapping"]])
def test_parse_config_rejects_invalid_loan_collection(loans):
    """输入非列表或包含非映射项的借贷配置；输出稳定的配置错误而非内部异常。"""
    with pytest.raises(ConfigError, match="loans"):
        parse_config({"loans": loans})


def test_duplicate_account_ids_are_rejected():
    with pytest.raises(ConfigError, match="Duplicate account id"):
        parse_config(
            {
                "exchanges": {
                    "binance": {
                        "accounts": [{"id": "same", "apiKey": "k", "apiSecret": "s"}],
                    },
                    "okx": {
                        "accounts": [
                            {"id": "same", "apiKey": "k", "apiSecret": "s", "passphrase": "p"}
                        ],
                    },
                }
            }
        )


def test_redact_secrets_hides_nested_secret_values():
    redacted = redact_secrets(
        {
            "apiKey": "key",
            "nested": {"apiSecret": "secret", "passphrase": "phrase"},
            "accessToken": "token",
            "label": "visible",
        }
    )

    assert redacted["apiKey"] == "***REDACTED***"
    assert redacted["nested"]["apiSecret"] == "***REDACTED***"
    assert redacted["nested"]["passphrase"] == "***REDACTED***"
    assert redacted["accessToken"] == "***REDACTED***"
    assert redacted["label"] == "visible"


def test_default_config_path_prefers_env(monkeypatch, tmp_path):
    env_config = tmp_path / "env.yaml"
    local_config = tmp_path / "config.local.yaml"
    env_config.write_text("baseCurrency: HKD\n", encoding="utf-8")
    local_config.write_text("baseCurrency: CNY\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSET_MCP_CONFIG", str(env_config))

    assert default_config_path() == env_config
    assert load_config().baseCurrency == "HKD"


def test_default_config_path_prefers_local_config(monkeypatch, tmp_path):
    user_config = tmp_path / "home" / ".config" / "asset-mcp" / "config.local.yaml"
    local_config = tmp_path / "config.local.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("baseCurrency: SGD\n", encoding="utf-8")
    local_config.write_text("baseCurrency: CNY\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ASSET_MCP_CONFIG", raising=False)

    assert default_config_path() == local_config
    assert load_config().baseCurrency == "CNY"


def test_default_config_path_uses_user_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    user_config = home / ".config" / "asset-mcp" / "config.local.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("baseCurrency: SGD\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ASSET_MCP_CONFIG", raising=False)

    assert default_config_path() == user_config
    assert load_config().baseCurrency == "SGD"


def test_load_config_resolves_credential_reference_at_runtime(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
exchanges:
  binance:
    accounts:
      - id: binance-main
        credentialRef: binance/main
""",
        encoding="utf-8",
    )
    vault = CredentialVault(
        _ConfigMemoryBackend(
            {"binance/main": {"apiKey": "runtime-key", "apiSecret": "runtime-secret"}}
        )
    )

    config = load_config(config_path, credential_vault=vault)

    assert config.binanceAccounts[0].credentialRef == "binance/main"
    assert config.binanceAccounts[0].apiKey == "runtime-key"
    assert config.binanceAccounts[0].apiSecret == "runtime-secret"
    assert "runtime-key" not in config_path.read_text(encoding="utf-8")


class _ConfigMemoryBackend:
    def __init__(self, values):
        self.values = values

    def get(self, reference: str):
        """读取配置测试凭据。

        输入：稳定凭据引用。
        输出：对应 secret bundle 的副本；不存在时返回 ``None``。
        """
        value = self.values.get(reference)
        return dict(value) if value is not None else None

    def set(self, reference: str, secrets: dict[str, str]):
        """保存配置测试凭据。

        输入：引用和 secret bundle。
        输出：无返回值；更新内存映射。
        """
        self.values[reference] = dict(secrets)
