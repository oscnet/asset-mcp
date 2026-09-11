from __future__ import annotations

import stat

import pytest
import yaml

from asset_mcp.config import ConfigError
from asset_mcp.web.config_editor import (
    build_editable_sections,
    load_editable_config,
    save_editable_sections,
)


def test_load_and_build_sections_never_resolve_or_display_credentials(tmp_path):
    """输入仅含凭据引用的配置；输出分区 YAML，且不访问保险库或显示密钥。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        """
exchanges:
  binance:
    accounts:
      - id: main
        credentialRef: binance/main
manual:
  accounts: []
""",
        encoding="utf-8",
    )

    sections = build_editable_sections(load_editable_config(path))

    assert "credentialRef: binance/main" in sections["accounts"]
    assert "apiSecret" not in "".join(sections.values())


def test_load_editable_config_rejects_legacy_inline_secrets(tmp_path):
    """输入含明文 API 密钥的旧配置；输出拒绝错误，防止秘密进入浏览器编辑器。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        "exchanges: {binance: {accounts: [{id: main, apiKey: visible}]}}",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="migrate-credentials"):
        load_editable_config(path)


def test_save_sections_validates_and_atomically_writes_supported_groups(tmp_path):
    """输入五个受控配置分区；输出权限为 0600、可解析且包含借贷的新配置文件。"""
    path = tmp_path / "config.yaml"
    path.write_text("baseCurrency: USD\nrates: {USD: 1}\n", encoding="utf-8")
    document = load_editable_config(path)
    sections = {
        "accounts": """
exchanges:
  binance:
    accounts:
      - id: binance-main
        label: Main
        enabled: false
        credentialRef: binance/main
brokers: {}
""",
        "wallets": """
onchain:
  accounts:
    - id: ledger
      label: Ledger
      addresses:
        - chain: ethereum
          address: '0x0000000000000000000000000000000000000001'
""",
        "manual": """
manual:
  accounts:
    - id: cash
      label: Cash
      category: cash
      assets:
        - symbol: USD
          quantity: 100
          currency: USD
""",
        "loans": """
loans:
  - borrower: 张三
    symbol: BTC
    quantity: 0.25
""",
        "tags": """
tags:
  assets:
    BTC: [Core]
  accounts:
    binance-main: [Personal]
  wallets:
    ledger/Ledger: [Cold Storage]
""",
    }

    saved = save_editable_sections(path, document, sections)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert saved == path
    assert raw["tags"]["assets"]["BTC"] == ["Core"]
    assert raw["manual"]["accounts"][0]["id"] == "cash"
    assert raw["loans"][0] == {"borrower": "张三", "symbol": "BTC", "quantity": 0.25}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_sections_does_not_replace_valid_file_when_edit_is_invalid(tmp_path):
    """输入重复账户 ID 的无效编辑；输出校验错误，原配置字节保持不变。"""
    path = tmp_path / "config.yaml"
    original = "baseCurrency: USD\nrates: {USD: 1}\n"
    path.write_text(original, encoding="utf-8")
    document = load_editable_config(path)
    sections = build_editable_sections(document)
    sections["accounts"] = """
exchanges:
  binance: {accounts: [{id: duplicate}]}
  okx: {accounts: [{id: duplicate}]}
brokers: {}
"""

    with pytest.raises(ConfigError, match="Duplicate account id"):
        save_editable_sections(path, document, sections)

    assert path.read_text(encoding="utf-8") == original
