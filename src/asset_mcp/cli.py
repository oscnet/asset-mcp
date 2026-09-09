from __future__ import annotations

import argparse
import sys
from importlib import resources
from pathlib import Path
from typing import Sequence

from asset_mcp import __version__
from asset_mcp.config import default_user_config_path
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
    restore_parser.add_argument("--path", type=Path, required=True, help="existing backup file")
    restore_parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm replacement of the current database",
    )
    restore_parser.set_defaults(handler=_handle_restore)

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

    输入：argparse Namespace，包含备份 ``path`` 和布尔 ``yes`` 确认标记。
    输出：缺少确认时不修改数据、打印错误并返回 2；验证和恢复成功时返回 0。
    """
    if not args.yes:
        print("Restore replaces the current database and requires --yes.", file=sys.stderr)
        return 2
    store = PortfolioStore(default_database_path())
    store.restore_from(args.path)
    print(f"Restored database: {store.path}")
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
