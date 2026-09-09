from __future__ import annotations

import json

import pytest

from asset_mcp.config import ConfigError
from asset_mcp.security.credentials import (
    CredentialVault,
    FernetFileBackend,
    KeyringBackend,
    migrate_inline_credentials,
    resolve_credential_refs,
)


def test_credential_vault_round_trips_secret_bundle():
    backend = _MemoryBackend()
    vault = CredentialVault(backend)

    vault.put("binance/main", {"apiKey": "key", "apiSecret": "secret"})

    assert vault.get("binance/main") == {"apiKey": "key", "apiSecret": "secret"}
    assert backend.values["binance/main"]["apiSecret"] == "secret"


def test_fernet_file_backend_encrypts_at_rest_and_rejects_wrong_password(tmp_path):
    path = tmp_path / "credentials.enc"
    backend = FernetFileBackend(path, "correct horse battery staple")

    backend.set("okx/main", {"apiKey": "visible-key", "apiSecret": "hidden-secret"})

    raw = path.read_text(encoding="utf-8")
    assert "visible-key" not in raw
    assert "hidden-secret" not in raw
    assert json.loads(raw)["version"] == 1
    assert backend.get("okx/main") == {
        "apiKey": "visible-key",
        "apiSecret": "hidden-secret",
    }
    with pytest.raises(ConfigError, match="master password"):
        FernetFileBackend(path, "wrong password").get("okx/main")


def test_resolve_credential_refs_injects_secrets_without_mutating_yaml_data():
    raw = {
        "exchanges": {
            "binance": {
                "accounts": [
                    {
                        "id": "binance-main",
                        "credentialRef": "binance/main",
                        "enabled": True,
                    }
                ]
            }
        }
    }
    vault = CredentialVault(
        _MemoryBackend(
            {"binance/main": {"apiKey": "key", "apiSecret": "secret"}}
        )
    )

    resolved = resolve_credential_refs(raw, vault)

    account = resolved["exchanges"]["binance"]["accounts"][0]
    assert account["apiKey"] == "key"
    assert account["apiSecret"] == "secret"
    assert raw["exchanges"]["binance"]["accounts"][0].get("apiKey") is None


def test_resolve_credential_refs_rejects_missing_reference():
    raw = {
        "exchanges": {
            "okx": {
                "accounts": [{"id": "okx-main", "credentialRef": "okx/missing"}]
            }
        }
    }

    with pytest.raises(ConfigError, match="okx/missing"):
        resolve_credential_refs(raw, CredentialVault(_MemoryBackend()))


def test_keyring_backend_serializes_one_bundle_per_reference():
    keyring = _FakeKeyring()
    backend = KeyringBackend(keyring_module=keyring)

    backend.set("binance/main", {"apiKey": "key", "apiSecret": "secret"})

    assert backend.get("binance/main") == {"apiKey": "key", "apiSecret": "secret"}
    assert keyring.values[("asset-mcp", "binance/main")].startswith("{")
    assert backend.get("missing/ref") is None


def test_migrate_inline_credentials_writes_vault_and_returns_reference_config():
    raw = {
        "exchanges": {
            "binance": {
                "accounts": [
                    {
                        "id": "binance-main",
                        "apiKey": "key",
                        "apiSecret": "secret",
                        "enabled": True,
                    }
                ]
            }
        }
    }
    backend = _MemoryBackend()

    migrated, count = migrate_inline_credentials(raw, CredentialVault(backend))

    account = migrated["exchanges"]["binance"]["accounts"][0]
    assert count == 1
    assert account["credentialRef"] == "binance/binance-main"
    assert "apiKey" not in account
    assert "apiSecret" not in account
    assert backend.values["binance/binance-main"] == {
        "apiKey": "key",
        "apiSecret": "secret",
    }
    assert raw["exchanges"]["binance"]["accounts"][0]["apiKey"] == "key"


class _MemoryBackend:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, reference: str):
        """读取内存凭据。

        输入：稳定凭据引用。
        输出：引用对应的凭据副本；不存在时返回 ``None``。
        """
        value = self.values.get(reference)
        return dict(value) if value is not None else None

    def set(self, reference: str, secrets: dict[str, str]):
        """保存内存凭据。

        输入：稳定引用和仅含字符串的 secret bundle。
        输出：无返回值；保存副本供后续测试读取。
        """
        self.values[reference] = dict(secrets)


class _FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service: str, username: str):
        """读取模拟系统 Keychain。

        输入：服务名和作为 username 的凭据引用。
        输出：已保存 JSON 字符串；不存在时返回 ``None``。
        """
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str):
        """写入模拟系统 Keychain。

        输入：服务名、凭据引用和序列化 secret bundle。
        输出：无返回值；保存字符串供读取断言。
        """
        self.values[(service, username)] = password
