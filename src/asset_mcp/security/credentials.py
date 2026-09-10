from __future__ import annotations

import base64
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from asset_mcp.config.errors import ConfigError
from asset_mcp.config.redaction import SECRET_KEYS

REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
KDF_ITERATIONS = 600_000


class CredentialBackend(Protocol):
    def get(self, reference: str) -> dict[str, str] | None:
        """输入稳定引用；输出 secret bundle 副本或 ``None``。"""

    def set(self, reference: str, secrets: dict[str, str]) -> None:
        """输入稳定引用和 secret bundle；输出无，安全保存凭据。"""


class CredentialVault:
    """对配置层隐藏具体凭据后端的统一保险库。

    输入：实现 ``CredentialBackend`` 的 Keychain 或加密文件后端。
    输出：提供经过引用和 secret bundle 校验的 ``put/get`` 接口。
    """

    def __init__(self, backend: CredentialBackend):
        self.backend = backend

    def put(self, reference: str, secrets: dict[str, str]) -> None:
        """校验并保存一组账户凭据。

        输入：最长 128 字符的稳定引用，以及非空字符串键值组成的 secret bundle。
        输出：无返回值；非法引用或空凭据抛出 ``ValueError``，合法值交给后端加密保存。
        """
        _validate_reference(reference)
        normalized = _normalize_secrets(secrets)
        self.backend.set(reference, normalized)

    def get(self, reference: str) -> dict[str, str] | None:
        """按引用读取账户凭据。

        输入：与保存时相同的稳定引用。
        输出：secret bundle 副本；引用不存在时返回 ``None``，非法引用抛出 ``ValueError``。
        """
        _validate_reference(reference)
        secrets = self.backend.get(reference)
        return dict(secrets) if secrets is not None else None


class KeyringBackend:
    """使用操作系统 Keychain/Secret Service 的凭据后端。

    输入：可选兼容 ``keyring`` API 的模块，生产环境默认导入系统 keyring。
    输出：以 ``asset-mcp`` 为 service、凭据引用为 username 保存 JSON secret bundle。
    """

    def __init__(self, keyring_module: Any | None = None):
        if keyring_module is None:
            import keyring as keyring_module  # type: ignore[no-redef]

        self.keyring = keyring_module

    def is_available(self) -> bool:
        """检查系统是否存在可写 Keychain 后端。

        输入：构造器中的 keyring 模块。
        输出：后端 priority 大于零时返回 ``True``；探测异常时返回 ``False``。
        """
        try:
            return float(self.keyring.get_keyring().priority) > 0
        except Exception:  # noqa: BLE001
            return False

    def get(self, reference: str) -> dict[str, str] | None:
        """从系统 Keychain 读取凭据。

        输入：稳定凭据引用。
        输出：反序列化后的 secret bundle；不存在时返回 ``None``，内容损坏时抛出
        ``ConfigError``。
        """
        raw = self.keyring.get_password("asset-mcp", reference)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Invalid credential bundle in keyring: {reference}") from exc
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise ConfigError(f"Invalid credential bundle in keyring: {reference}")
        return value

    def set(self, reference: str, secrets: dict[str, str]) -> None:
        """把凭据写入系统 Keychain。

        输入：稳定凭据引用和已校验 secret bundle。
        输出：无返回值；bundle 被序列化为紧凑 JSON 并交由 OS Keychain 加密保存。
        """
        payload = json.dumps(secrets, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self.keyring.set_password("asset-mcp", reference, payload)


class FernetFileBackend:
    """使用主密码派生密钥的本地 Fernet 文件后端。

    输入：加密文件路径和不会写入磁盘的主密码。
    输出：整个引用映射经过认证加密后保存；错误密码或篡改文件会被拒绝。
    """

    def __init__(self, path: str | Path, master_password: str):
        if not master_password:
            raise ConfigError("ASSET_MCP_MASTER_PASSWORD is required for file vault")
        self.path = Path(path).expanduser()
        self.master_password = master_password

    def get(self, reference: str) -> dict[str, str] | None:
        """从加密文件读取一个凭据引用。

        输入：稳定凭据引用。
        输出：解密后的 secret bundle 副本；引用不存在时返回 ``None``，密码错误或
        文件被篡改时抛出 ``ConfigError``。
        """
        values, _salt = self._read_values()
        value = values.get(reference)
        return dict(value) if value is not None else None

    def set(self, reference: str, secrets: dict[str, str]) -> None:
        """加密保存一个凭据引用。

        输入：稳定引用和已校验的 secret bundle。
        输出：无返回值；保留其他引用，使用认证加密原子替换权限为 0600 的保险库文件。
        """
        values, salt = self._read_values()
        salt = salt or os.urandom(16)
        values[reference] = dict(secrets)
        plaintext = json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        document = {
            "version": 1,
            "salt": base64.urlsafe_b64encode(salt).decode(),
            "ciphertext": _fernet(self.master_password, salt).encrypt(plaintext).decode(),
        }
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        temp_path.chmod(0o600)
        temp_path.replace(self.path)

    def _read_values(self) -> tuple[dict[str, dict[str, str]], bytes | None]:
        """读取并解密完整文件保险库。

        输入：构造器中的路径与主密码。
        输出：引用到 secret bundle 的映射及 KDF salt；文件不存在时返回空映射和 ``None``。
        """
        if not self.path.exists():
            return {}, None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("version") != 1:
                raise ConfigError("unsupported credential vault version")
            salt = base64.urlsafe_b64decode(document["salt"])
            plaintext = _fernet(self.master_password, salt).decrypt(
                document["ciphertext"].encode()
            )
            values = json.loads(plaintext)
        except (InvalidKey, InvalidToken, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConfigError("credential vault master password is invalid") from exc
        if not isinstance(values, dict):
            raise ConfigError("credential vault content is invalid")
        return values, salt


def resolve_credential_refs(raw: dict[str, Any], vault: CredentialVault) -> dict[str, Any]:
    """把 YAML 中的凭据引用解析为仅存在于内存的运行时配置。

    输入：解析后的 YAML 字典和已配置后端的 ``CredentialVault``。
    输出：深拷贝后的配置字典；每个 ``credentialRef`` 对应 secret bundle 被注入副本，
    原始字典不变；引用缺失或同时含明文 secret 时抛出 ``ConfigError``。
    """
    resolved = deepcopy(raw)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("credentialRef")
            if reference:
                inline_secrets = [
                    key
                    for key, item in value.items()
                    if str(key).lower() in SECRET_KEYS and item not in (None, "")
                ]
                if inline_secrets:
                    raise ConfigError(
                        f"credentialRef cannot be combined with inline secrets: {reference}"
                    )
                secrets = vault.get(str(reference))
                if secrets is None:
                    raise ConfigError(f"Credential reference not found: {reference}")
                value.update(secrets)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(resolved)
    return resolved


def migrate_inline_credentials(
    raw: dict[str, Any],
    vault: CredentialVault,
) -> tuple[dict[str, Any], int]:
    """把旧配置中的账户明文凭据迁移到保险库。

    输入：旧 YAML 字典和目标 ``CredentialVault``；账户字典需要稳定 ``id``。
    输出：不含已迁移 secret、改用 ``provider/account-id`` 引用的深拷贝及迁移账户数；
    原输入不变，没有明文凭据的节点保持原样。
    """
    migrated = deepcopy(raw)
    migrated_count = 0

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        nonlocal migrated_count
        if isinstance(value, dict):
            secrets = {
                str(key): str(item)
                for key, item in value.items()
                if str(key).lower() in SECRET_KEYS and item not in (None, "")
            }
            account_id = value.get("id")
            is_indexer = len(path) >= 2 and path[-1] == "indexer"
            node_id = account_id or ("indexer" if is_indexer else None)
            if secrets and node_id:
                provider = path[-2] if len(path) >= 2 else "account"
                reference = str(value.get("credentialRef") or f"{provider}/{node_id}")
                vault.put(reference, secrets)
                for key in secrets:
                    value.pop(key, None)
                value["credentialRef"] = reference
                migrated_count += 1
            for key, item in list(value.items()):
                visit(item, path + (str(key),))
        elif isinstance(value, list):
            for item in value:
                visit(item, path)

    visit(migrated)
    return migrated, migrated_count


def default_credential_vault() -> CredentialVault:
    """选择当前环境可用的安全凭据后端。

    输入：系统 keyring 能力，以及可选 ``ASSET_MCP_MASTER_PASSWORD``、
    ``ASSET_MCP_MASTER_PASSWORD_FILE`` 和 ``ASSET_MCP_VAULT_FILE`` 环境变量；
    密码文件仅移除末尾换行，不会误删密码中的空格。
    输出：优先使用 OS Keychain 的 ``CredentialVault``；无 Keychain 时使用 Fernet
    文件后端；两者均不可用时抛出 ``ConfigError`` 并拒绝加载引用。
    """
    try:
        keyring_backend = KeyringBackend()
        if keyring_backend.is_available():
            return CredentialVault(keyring_backend)
    except (ImportError, ModuleNotFoundError):
        pass
    master_password = os.environ.get("ASSET_MCP_MASTER_PASSWORD")
    password_file = os.environ.get("ASSET_MCP_MASTER_PASSWORD_FILE")
    if not master_password and password_file:
        try:
            master_password = Path(password_file).expanduser().read_text(
                encoding="utf-8"
            ).rstrip("\r\n")
        except OSError as exc:
            raise ConfigError("credential vault master password file is unavailable") from exc
    if not master_password:
        raise ConfigError(
            "No OS keyring available; set ASSET_MCP_MASTER_PASSWORD_FILE or "
            "ASSET_MCP_MASTER_PASSWORD for encrypted file vault"
        )
    vault_path = Path(
        os.environ.get(
            "ASSET_MCP_VAULT_FILE",
            Path.home() / ".local" / "share" / "asset-mcp" / "credentials.enc",
        )
    )
    return CredentialVault(FernetFileBackend(vault_path, master_password))


def _fernet(master_password: str, salt: bytes) -> Fernet:
    """从主密码派生 Fernet 实例。

    输入：非空主密码和 16 字节随机 salt。
    输出：使用 PBKDF2-SHA256 及 600000 次迭代派生的 Fernet 认证加密器。
    """
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    ).derive(master_password.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _validate_reference(reference: str) -> None:
    """验证凭据引用格式。

    输入：候选引用字符串。
    输出：合法时无返回值；空值、超长或含空白及特殊字符时抛出 ``ValueError``。
    """
    if not isinstance(reference, str) or not REFERENCE_PATTERN.fullmatch(reference):
        raise ValueError("invalid credential reference")


def _normalize_secrets(secrets: dict[str, str]) -> dict[str, str]:
    """验证并复制 secret bundle。

    输入：凭据字段到值的映射。
    输出：所有键值均为非空字符串的新字典；空映射或非法键值抛出 ``ValueError``。
    """
    if not secrets:
        raise ValueError("credential bundle must not be empty")
    if any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        for key, value in secrets.items()
    ):
        raise ValueError("credential keys and values must be non-empty strings")
    return dict(secrets)
