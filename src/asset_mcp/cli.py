from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from importlib import resources
from pathlib import Path
from typing import Sequence

import yaml

from asset_mcp import __version__
from asset_mcp.config import default_config_path, default_user_config_path
from asset_mcp.security import default_credential_vault, migrate_inline_credentials
from asset_mcp.server import main as server_main
from asset_mcp.storage import PortfolioStore, default_database_path

TEMPLATE_PACKAGE = "asset_mcp.templates"
TEMPLATE_NAME = "config.local.yaml"


def main(argv: Sequence[str] | None = None) -> int | None:
    args = list(argv) if argv is not None else None
    if args == []:
        server_main()
        return None

    parser = _build_parser()
    namespace = parser.parse_args(args)
    return namespace.handler(namespace)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-mcp",
        description="Read-only MCP server for personal asset aggregation.",
    )
    parser.add_argument("--version", action="version", version=f"asset-mcp {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init",
        help="create a local config template",
        description="Create a local asset-mcp config template.",
    )
    init_parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="config path to create; defaults to ~/.config/asset-mcp/config.local.yaml",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing config file",
    )
    init_parser.set_defaults(handler=_handle_init)

    serve_parser = subparsers.add_parser("serve", help="start the MCP stdio server")
    serve_parser.set_defaults(handler=_handle_serve)

    backup_parser = subparsers.add_parser(
        "backup",
        help="create a consistent SQLite backup",
    )
    backup_parser.add_argument("--path", type=Path, required=True, help="new backup file path")
    backup_parser.set_defaults(handler=_handle_backup)

    restore_parser = subparsers.add_parser(
        "restore",
        help="restore the SQLite database from a validated backup",
    )
    restore_source = restore_parser.add_mutually_exclusive_group(required=True)
    restore_source.add_argument("--path", type=Path, help="existing backup file")
    restore_source.add_argument(
        "--stdin",
        action="store_true",
        help="read SQLite backup bytes from stdin",
    )
    restore_parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm replacement of the current database",
    )
    restore_parser.set_defaults(handler=_handle_restore)

    migrate_parser = subparsers.add_parser(
        "migrate-credentials",
        help="copy inline secrets to the credential vault",
    )
    migrate_parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="legacy config path; defaults to the active config",
    )
    migrate_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new credential-reference config path",
    )
    migrate_parser.set_defaults(handler=_handle_migrate_credentials)

    credential_parser = subparsers.add_parser(
        "set-credential",
        help="store one JSON credential bundle from stdin",
    )
    credential_parser.add_argument(
        "--reference",
        required=True,
        help="stable credentialRef used by config, for example binance/main",
    )
    credential_parser.set_defaults(handler=_handle_set_credential)

    parser.set_defaults(handler=_handle_serve)
    return parser


def _handle_init(args: argparse.Namespace) -> int:
    config_path = init_config(path=args.path, force=args.force)
    if config_path.created:
        print(f"Created config: {config_path.path}")
    else:
        print(f"Config already exists: {config_path.path}")
    return 0


def _handle_serve(_args: argparse.Namespace) -> None:
    server_main()
    return None


def _handle_backup(args: argparse.Namespace) -> int:
    """执行本地数据库在线备份命令。

    输入：argparse Namespace，其中 ``path`` 是尚不存在的目标 SQLite 文件。
    输出：成功时打印备份路径并返回 0；目标冲突或数据库错误由存储层明确抛出。
    """
    store = PortfolioStore(default_database_path())
    backup_path = store.backup_to(args.path)
    print(f"Created backup: {backup_path}")
    return 0


def _handle_restore(args: argparse.Namespace) -> int:
    """执行经过显式确认的本地数据库恢复命令。

    输入：argparse Namespace，包含备份 ``path`` 或二进制 ``stdin`` 来源，以及布尔
    ``yes`` 确认标记；stdin 内容只暂存到系统临时目录且最终自动删除。
    输出：缺少确认时不读取输入、不修改数据并返回 2；验证和原子恢复成功时返回 0。
    """
    if not args.yes:
        print("Restore replaces the current database and requires --yes.", file=sys.stderr)
        return 2
    store = PortfolioStore(default_database_path())
    temporary_path: Path | None = None
    source_path = args.path
    if args.stdin:
        with tempfile.NamedTemporaryFile(
            prefix="asset-mcp-restore-",
            suffix=".db",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            shutil.copyfileobj(getattr(sys.stdin, "buffer", sys.stdin), temporary_file)
        source_path = temporary_path
    try:
        store.restore_from(source_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    print(f"Restored database: {store.path}")
    return 0


def _handle_migrate_credentials(args: argparse.Namespace) -> int:
    """把旧 YAML 明文凭据迁移到安全保险库。

    输入：可选旧配置 ``path`` 和必须不存在的 ``output`` 路径；保险库自动优先
    OS Keychain，无可用 Keychain 时使用主密码 Fernet 文件。
    输出：原文件保持不变，新引用配置写入 0600 文件并返回 0；输出已存在时拒绝覆盖。
    """
    source_path = args.path or default_config_path()
    output_path = args.output.expanduser()
    if output_path.exists():
        raise FileExistsError(output_path)
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    migrated, count = migrate_inline_credentials(raw, default_credential_vault())
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _chmod_if_supported(output_path, 0o600)
    print(f"Migrated {count} account(s): {output_path}")
    return 0


def _handle_set_credential(args: argparse.Namespace) -> int:
    """从标准输入安全保存一组凭据。

    输入：Namespace 中的稳定 ``reference``，以及 stdin 中仅含字符串键值的 JSON 对象。
    输出：保存成功时仅打印引用与字段名并返回 0；秘密值永不回显，非法引用或内容由
    ``CredentialVault`` 拒绝。该接口便于 Docker 通过重定向输入而不把秘密写进参数。
    """
    secrets = json.load(sys.stdin)
    if not isinstance(secrets, dict):
        raise ValueError("credential input must be a JSON object")
    default_credential_vault().put(args.reference, secrets)
    fields = ", ".join(sorted(secrets))
    print(f"Stored credential: {args.reference} ({fields})")
    return 0


def init_config(path: str | Path | None = None, *, force: bool = False) -> "InitConfigResult":
    target_path = Path(path).expanduser() if path is not None else default_user_config_path()
    target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _chmod_if_supported(target_path.parent, 0o700)

    if target_path.exists() and not force:
        return InitConfigResult(path=target_path, created=False)

    template = _read_template()
    target_path.write_text(template, encoding="utf-8")
    _chmod_if_supported(target_path, 0o600)
    return InitConfigResult(path=target_path, created=True)


def _read_template() -> str:
    template = resources.files(TEMPLATE_PACKAGE).joinpath(TEMPLATE_NAME).read_text(encoding="utf-8")
    if not template.endswith("\n"):
        template += "\n"
    return template


def _chmod_if_supported(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


class InitConfigResult:
    def __init__(self, *, path: Path, created: bool) -> None:
        self.path = path
        self.created = created
