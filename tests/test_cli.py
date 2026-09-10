from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from asset_mcp import __version__
from asset_mcp import cli
from asset_mcp.domain.models import Asset
from asset_mcp.storage import PortfolioStore
from asset_mcp.security import CredentialVault


def test_init_creates_default_config_under_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = cli.main(["init"])

    config_path = tmp_path / ".config" / "asset-mcp" / "config.local.yaml"
    assert exit_code == 0
    assert config_path.exists()
    assert "credentialRef: null" in config_path.read_text(encoding="utf-8")
    assert f"Created config: {config_path}" in capsys.readouterr().out


def test_init_does_not_overwrite_existing_config(tmp_path, capsys):
    config_path = tmp_path / "config.local.yaml"
    config_path.write_text("custom: true\n", encoding="utf-8")

    exit_code = cli.main(["init", "--path", str(config_path)])

    assert exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "custom: true\n"
    assert f"Config already exists: {config_path}" in capsys.readouterr().out


def test_init_force_overwrites_existing_config(tmp_path):
    config_path = tmp_path / "config.local.yaml"
    config_path.write_text("custom: true\n", encoding="utf-8")

    exit_code = cli.main(["init", "--path", str(config_path), "--force"])

    assert exit_code == 0
    content = config_path.read_text(encoding="utf-8")
    assert "custom: true" not in content
    assert "baseCurrency: USD" in content


def test_init_path_writes_custom_location(tmp_path):
    config_path = tmp_path / "nested" / "asset.yaml"

    exit_code = cli.main(["init", "--path", str(config_path)])

    assert exit_code == 0
    assert config_path.exists()
    assert "manual:" in config_path.read_text(encoding="utf-8")


def test_help_includes_init(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    assert "init" in capsys.readouterr().out


def test_version_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])

    assert exc_info.value.code == 0
    assert f"asset-mcp {__version__}" in capsys.readouterr().out


def test_no_args_delegates_to_server(monkeypatch):
    calls = []

    def fake_server_main():
        calls.append("served")

    monkeypatch.setattr(cli, "server_main", fake_server_main)

    result = cli.main([])

    assert result is None
    assert calls == ["served"]


def test_backup_and_confirmed_restore_commands(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "portfolio.db"
    backup_path = tmp_path / "backups" / "portfolio.db"
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(database_path))
    store = PortfolioStore(database_path)
    store.replace_current_assets("manual", [_manual_asset("BTC", 6000)])

    assert cli.main(["backup", "--path", str(backup_path)]) == 0
    assert backup_path.exists()
    assert f"Created backup: {backup_path}" in capsys.readouterr().out

    store.replace_current_assets("manual", [_manual_asset("USD", 100)])
    assert cli.main(["restore", "--path", str(backup_path)]) == 2
    assert "requires --yes" in capsys.readouterr().err
    assert store.load_current_assets()[0].symbol == "USD"

    assert cli.main(["restore", "--path", str(backup_path), "--yes"]) == 0
    assert store.load_current_assets()[0].symbol == "BTC"
    assert f"Restored database: {database_path}" in capsys.readouterr().out


def test_confirmed_restore_accepts_sqlite_backup_from_stdin(tmp_path, monkeypatch, capsys):
    """输入 stdin 中的 SQLite 备份字节；输出恢复后的原资产，且无需容器内上传文件。"""
    database_path = tmp_path / "portfolio.db"
    backup_path = tmp_path / "portfolio.backup.db"
    monkeypatch.setenv("ASSET_MCP_DATABASE", str(database_path))
    store = PortfolioStore(database_path)
    store.replace_current_assets("manual", [_manual_asset("BTC", 6000)])
    store.backup_to(backup_path)
    store.replace_current_assets("manual", [_manual_asset("USD", 100)])
    stdin = _BinaryStdin(backup_path.read_bytes())
    monkeypatch.setattr(cli.sys, "stdin", stdin)

    result = cli.main(["restore", "--stdin", "--yes"])

    assert result == 0
    assert store.load_current_assets()[0].symbol == "BTC"
    assert "Restored database" in capsys.readouterr().out


def test_migrate_credentials_writes_new_reference_config(tmp_path, monkeypatch, capsys):
    source_path = tmp_path / "legacy.yaml"
    output_path = tmp_path / "config.refs.yaml"
    source_path.write_text(
        """
exchanges:
  binance:
    accounts:
      - id: binance-main
        apiKey: key
        apiSecret: secret
""",
        encoding="utf-8",
    )
    backend = _CliMemoryBackend()
    monkeypatch.setattr(
        cli,
        "default_credential_vault",
        lambda: CredentialVault(backend),
    )

    result = cli.main(
        [
            "migrate-credentials",
            "--path",
            str(source_path),
            "--output",
            str(output_path),
        ]
    )

    migrated = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    account = migrated["exchanges"]["binance"]["accounts"][0]
    assert result == 0
    assert account["credentialRef"] == "binance/binance-main"
    assert "apiKey" not in account
    assert source_path.read_text(encoding="utf-8").find("apiSecret: secret") > 0
    assert backend.values["binance/binance-main"]["apiSecret"] == "secret"
    assert "Migrated 1 account" in capsys.readouterr().out


def test_set_credential_reads_json_from_stdin_without_echoing_values(monkeypatch, capsys):
    """输入 stdin 中的凭据 JSON；输出保险库引用与字段名，但绝不回显秘密值。"""
    backend = _CliMemoryBackend()
    monkeypatch.setattr(cli, "default_credential_vault", lambda: CredentialVault(backend))
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        io.StringIO('{"apiKey":"visible-key","apiSecret":"visible-secret"}'),
    )

    result = cli.main(["set-credential", "--reference", "binance/main"])

    output = capsys.readouterr().out
    assert result == 0
    assert backend.values["binance/main"]["apiSecret"] == "visible-secret"
    assert "apiKey, apiSecret" in output
    assert "visible-key" not in output
    assert "visible-secret" not in output


def _manual_asset(symbol: str, value_usd: int) -> Asset:
    """构造 CLI 备份测试使用的手工资产。

    输入：资产代码及整数美元价值。
    输出：可写入 SQLite 的单个 ``Asset``，数量与单价相乘等于输入价值。
    """
    return Asset(
        source="manual",
        accountId="manual-main",
        accountLabel="Manual",
        category="manual",
        symbol=symbol,
        quantity=value_usd,
        currency="USD",
        unitPriceUsd=1,
        valueUsd=value_usd,
        updatedAt="2026-09-09T00:00:00Z",
    )


class _CliMemoryBackend:
    def __init__(self):
        self.values = {}

    def get(self, reference: str):
        """读取 CLI 测试凭据。

        输入：稳定引用。
        输出：对应 bundle 副本；不存在时返回 ``None``。
        """
        value = self.values.get(reference)
        return dict(value) if value is not None else None

    def set(self, reference: str, secrets: dict[str, str]):
        """保存 CLI 测试凭据。

        输入：稳定引用及 secret bundle。
        输出：无返回值；写入内存字典供断言。
        """
        self.values[reference] = dict(secrets)


class _BinaryStdin:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)
