from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from asset_mcp.config import ConfigError, parse_config
from asset_mcp.config.redaction import SECRET_KEYS

SECTION_KEYS = {
    "accounts": ("exchanges", "brokers"),
    "wallets": ("onchain",),
    "manual": ("manual",),
    "loans": ("loans",),
    "tags": ("tags",),
}


def load_editable_config(path: str | Path) -> dict[str, Any]:
    """读取可在浏览器中安全显示的原始配置。

    输入：本地 YAML 配置路径；该函数不会解析 ``credentialRef`` 或访问凭据保险库。
    输出：不含明文秘密的配置字典；文件缺失、YAML 根节点非法或存在旧式明文凭据时
    抛出 ``ConfigError``，并要求先执行凭据迁移。
    """
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML: {exc.__class__.__name__}") from exc
    if not isinstance(document, dict):
        raise ConfigError("Config root must be a mapping.")
    if _contains_inline_secret(document):
        raise ConfigError(
            "Inline credentials cannot be opened in Web; run asset-mcp migrate-credentials first."
        )
    return document


def build_editable_sections(document: dict[str, Any]) -> dict[str, str]:
    """把完整配置拆成五个受控 YAML 编辑区。

    输入：已确认不含秘密的原始配置字典。
    输出：账户、钱包、手工资产、借贷资产、标签五段 YAML；基础币种与汇率留在原文档
    中不变。缺失的借贷配置显示为空列表，便于直接添加条目。
    """
    sections: dict[str, str] = {}
    for section, keys in SECTION_KEYS.items():
        value = {
            key: deepcopy(document.get(key, [] if key == "loans" else {}))
            for key in keys
        }
        sections[section] = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    return sections


def save_editable_sections(
    path: str | Path,
    original: dict[str, Any],
    sections: dict[str, str],
) -> Path:
    """校验并原子保存 Web 编辑的配置分区。

    输入：目标路径、加载时的完整原文档和五个受控 YAML 分区文本。
    输出：成功写入的 ``Path``；语法错误、越界字段、明文秘密或领域配置校验失败时抛出
    ``ConfigError``，并保证原文件不被替换。新文件权限固定为 0600。
    """
    document = deepcopy(original)
    for section, allowed_keys in SECTION_KEYS.items():
        parsed = _parse_section(section, sections.get(section, ""), allowed_keys)
        for key in allowed_keys:
            document[key] = parsed.get(key, [] if key == "loans" else {})
    if _contains_inline_secret(document):
        raise ConfigError("Inline credentials are forbidden; use credentialRef.")
    parse_config(document)

    config_path = Path(path)
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        dir=config_path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(0o600)
        temp_path.replace(config_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return config_path


def _parse_section(section: str, text: str, allowed_keys: tuple[str, ...]) -> dict[str, Any]:
    """解析单个受控 YAML 分区。

    输入：分区名、用户文本和允许的顶层键。
    输出：映射形式的分区数据；YAML 错误、非映射根节点或跨分区键抛出 ``ConfigError``。
    """
    try:
        value = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {section}: {exc.__class__.__name__}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{section} section must be a mapping.")
    unexpected = sorted(set(value) - set(allowed_keys))
    if unexpected:
        raise ConfigError(f"Unexpected keys in {section}: {', '.join(unexpected)}")
    return value


def _contains_inline_secret(value: Any) -> bool:
    """递归检查配置是否包含非空明文秘密；输入任意 YAML 值，输出布尔结果。"""
    if isinstance(value, dict):
        if any(
            str(key).lower() in SECRET_KEYS and item not in (None, "")
            for key, item in value.items()
        ):
            return True
        return any(_contains_inline_secret(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_inline_secret(item) for item in value)
    return False
