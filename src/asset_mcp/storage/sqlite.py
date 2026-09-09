from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from asset_mcp.domain.models import Asset, Position, SyncStatus

SCHEMA_VERSION = 2
ASSET_DECIMAL_FIELDS = ("quantity", "unitPriceUsd", "valueUsd")
POSITION_DECIMAL_FIELDS = (
    "quantity",
    "entryPriceUsd",
    "markPriceUsd",
    "notionalUsd",
    "unrealizedPnlUsd",
    "leverage",
    "liquidationPriceUsd",
    "marginUsd",
)


def default_database_path() -> Path:
    """解析本地资产数据库默认路径。

    输入：可选环境变量 ``ASSET_MCP_DATABASE``；若未设置则使用当前用户数据目录。
    输出：展开用户目录后的 ``Path``，默认是
    ``~/.local/share/asset-mcp/portfolio.db``，但本函数不创建文件。
    """
    configured_path = os.environ.get("ASSET_MCP_DATABASE")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".local" / "share" / "asset-mcp" / "portfolio.db"


class PortfolioStore:
    """本地 SQLite 资产状态与每日快照仓库。

    输入：数据库文件路径；初始化时自动创建父目录并迁移到当前 schema。
    输出：提供按来源原子替换当前资产、读取缓存和写入不可变每日快照的同步接口。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._initialize()

    def replace_current_assets(self, source: str, assets: list[Asset]) -> None:
        """原子替换一个来源的最新成功资产。

        输入：稳定来源名称及该来源本次成功读取的完整 ``Asset`` 列表；允许空列表。
        输出：无返回值；只删除并重写指定来源，其他来源缓存保持不变，失败时事务回滚。
        """
        if any(asset.source != source for asset in assets):
            raise ValueError("all assets must match the requested source")
        with self._connect() as connection:
            connection.execute("DELETE FROM current_assets WHERE source = ?", (source,))
            connection.executemany(
                "INSERT INTO current_assets (source, payload) VALUES (?, ?)",
                [(source, _serialize_asset(asset)) for asset in assets],
            )

    def load_current_assets(
        self,
        source: str | None = None,
        sync_status: SyncStatus | None = None,
    ) -> list[Asset]:
        """读取最新成功资产缓存。

        输入：可选来源过滤条件，以及可选的返回状态覆盖值；传入 ``STALE`` 可用于
        上游失败时明确标记旧数据，且不会修改数据库中的原始成功状态。
        输出：按来源和写入顺序稳定排列的 ``Asset`` 列表，金额恢复为精确 Decimal。
        """
        query = "SELECT payload FROM current_assets"
        params: tuple[str, ...] = ()
        if source is not None:
            query += " WHERE source = ?"
            params = (source,)
        query += " ORDER BY source, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        assets = [_deserialize_asset(row[0]) for row in rows]
        if sync_status is not None:
            return [replace(asset, syncStatus=sync_status) for asset in assets]
        return assets

    def replace_current_positions(self, source: str, positions: list[Position]) -> None:
        """原子替换一个交易所来源的最新合约仓位。

        输入：``binance`` 或 ``okx`` 来源，以及该来源本次成功读取的完整仓位列表；
        空列表表示确认当前无仓位。
        输出：无返回值；只替换指定来源，Decimal 精度在 JSON 存储中保持不变。
        """
        if any(position.source != source for position in positions):
            raise ValueError("all positions must match the requested source")
        with self._connect() as connection:
            connection.execute("DELETE FROM current_positions WHERE source = ?", (source,))
            connection.executemany(
                "INSERT INTO current_positions (source, payload) VALUES (?, ?)",
                [(source, _serialize_position(position)) for position in positions],
            )

    def load_current_positions(
        self,
        source: str | None = None,
        sync_status: SyncStatus | None = None,
    ) -> list[Position]:
        """读取最新成功合约仓位缓存。

        输入：可选交易所来源，以及可选的返回状态覆盖值；传入 ``STALE`` 时仅修改
        返回对象状态，不覆盖数据库中的最后成功记录。
        输出：按来源和写入顺序稳定排列、金额恢复为 Decimal 的 ``Position`` 列表。
        """
        query = "SELECT payload FROM current_positions"
        params: tuple[str, ...] = ()
        if source is not None:
            query += " WHERE source = ?"
            params = (source,)
        query += " ORDER BY source, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        positions = [_deserialize_position(row[0]) for row in rows]
        if sync_status is not None:
            return [replace(position, syncStatus=sync_status) for position in positions]
        return positions

    def save_daily_snapshot(
        self,
        source: str,
        assets: list[Asset],
        snapshot_date: str | date | None = None,
    ) -> bool:
        """首次成功时保存一个来源的每日不可变快照。

        输入：来源、本次成功资产列表，以及可选 ISO 日期；日期缺省时使用 UTC 当天。
        输出：首次写入返回 ``True``；同日同来源已存在时不修改原快照并返回 ``False``。
        """
        if any(asset.source != source for asset in assets):
            raise ValueError("all assets must match the requested source")
        day = _snapshot_day(snapshot_date)
        captured_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO snapshot_sources (snapshot_date, source, captured_at)
                VALUES (?, ?, ?)
                """,
                (day, source, captured_at),
            )
            if cursor.rowcount == 0:
                return False
            connection.executemany(
                """
                INSERT INTO asset_snapshots (snapshot_date, source, payload)
                VALUES (?, ?, ?)
                """,
                [(day, source, _serialize_asset(asset)) for asset in assets],
            )
        return True

    def load_snapshot(
        self,
        snapshot_date: str | date,
        source: str | None = None,
    ) -> list[Asset]:
        """读取指定日期的不可变资产快照。

        输入：ISO 日期或 ``date`` 对象，以及可选来源过滤条件。
        输出：按来源和快照行顺序排列的 ``Asset`` 列表；不存在时返回空列表。
        """
        day = _snapshot_day(snapshot_date)
        query = "SELECT payload FROM asset_snapshots WHERE snapshot_date = ?"
        params: tuple[str, ...] = (day,)
        if source is not None:
            query += " AND source = ?"
            params = (day, source)
        query += " ORDER BY source, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_deserialize_asset(row[0]) for row in rows]

    def save_daily_position_snapshot(
        self,
        source: str,
        positions: list[Position],
        snapshot_date: str | date | None = None,
    ) -> bool:
        """首次成功时保存一个交易所来源的每日仓位快照。

        输入：交易所来源、完整仓位列表及可选 ISO 日期；资产快照是否存在不影响本操作。
        输出：当日该来源首次写入返回 ``True``；重复调用保持原仓位并返回 ``False``。
        """
        if any(position.source != source for position in positions):
            raise ValueError("all positions must match the requested source")
        day = _snapshot_day(snapshot_date)
        captured_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO position_snapshot_sources
                    (snapshot_date, source, captured_at)
                VALUES (?, ?, ?)
                """,
                (day, source, captured_at),
            )
            if cursor.rowcount == 0:
                return False
            connection.executemany(
                """
                INSERT INTO position_snapshots (snapshot_date, source, payload)
                VALUES (?, ?, ?)
                """,
                [(day, source, _serialize_position(position)) for position in positions],
            )
        return True

    def load_position_snapshot(
        self,
        snapshot_date: str | date,
        source: str | None = None,
    ) -> list[Position]:
        """读取指定日期的不可变合约仓位快照。

        输入：ISO 日期或 ``date`` 对象，以及可选交易所来源。
        输出：按来源和快照行顺序排列的 ``Position`` 列表；不存在时返回空列表。
        """
        day = _snapshot_day(snapshot_date)
        query = "SELECT payload FROM position_snapshots WHERE snapshot_date = ?"
        params: tuple[str, ...] = (day,)
        if source is not None:
            query += " AND source = ?"
            params = (day, source)
        query += " ORDER BY source, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_deserialize_position(row[0]) for row in rows]

    def backup_to(self, destination: str | Path) -> Path:
        """在线备份当前 Portfolio 数据库。

        输入：尚不存在的目标数据库路径；父目录可以不存在，但目标不能与当前库相同。
        输出：成功备份后的展开路径；目标已存在时抛出 ``FileExistsError``，避免静默覆盖。
        """
        target_path = Path(destination).expanduser()
        if target_path.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from database path")
        if target_path.exists():
            raise FileExistsError(target_path)
        target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with self._connect() as source_connection:
                with closing(sqlite3.connect(target_path)) as target_connection:
                    source_connection.backup(target_connection)
        except Exception:
            if target_path.exists():
                target_path.unlink()
            raise
        try:
            target_path.chmod(0o600)
        except OSError:
            pass
        return target_path

    def restore_from(self, backup_path: str | Path) -> None:
        """用已验证通过的 SQLite 备份恢复当前数据库。

        输入：存在、完整、schema 版本为 1 到当前版本的 Asset MCP SQLite 文件。
        输出：无返回值；验证失败时当前库不变并抛出 ``ValueError``，成功后自动迁移旧版本。
        """
        source_path = Path(backup_path).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if source_path.resolve() == self.path.resolve():
            raise ValueError("restore source must differ from database path")
        try:
            with closing(
                sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            ) as source_connection:
                integrity = source_connection.execute("PRAGMA quick_check").fetchone()[0]
                version = source_connection.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in source_connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                if integrity != "ok" or version not in range(1, SCHEMA_VERSION + 1):
                    raise ValueError("backup is not a valid SQLite portfolio database")
                if "current_assets" not in tables or "snapshot_sources" not in tables:
                    raise ValueError("backup is not a valid SQLite portfolio database")
                with closing(sqlite3.connect(self.path)) as target_connection:
                    source_connection.backup(target_connection)
        except sqlite3.DatabaseError as exc:
            raise ValueError("backup is not a valid SQLite portfolio database") from exc
        self._initialize()

    def _initialize(self) -> None:
        """创建或验证当前 SQLite schema。

        输入：构造器保存的数据库路径。
        输出：新库创建为 schema v1；版本过高或不受支持时抛出 ``RuntimeError``。
        """
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"database schema {version} is newer than {SCHEMA_VERSION}")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE current_assets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX current_assets_source_idx ON current_assets(source);

                    CREATE TABLE snapshot_sources (
                        snapshot_date TEXT NOT NULL,
                        source TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        PRIMARY KEY (snapshot_date, source)
                    );
                    CREATE TABLE asset_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_date TEXT NOT NULL,
                        source TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        FOREIGN KEY (snapshot_date, source)
                            REFERENCES snapshot_sources(snapshot_date, source)
                    );
                    CREATE INDEX asset_snapshots_day_idx
                        ON asset_snapshots(snapshot_date, source);

                    CREATE TRIGGER snapshot_sources_no_update
                    BEFORE UPDATE ON snapshot_sources
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
                    CREATE TRIGGER snapshot_sources_no_delete
                    BEFORE DELETE ON snapshot_sources
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
                    CREATE TRIGGER asset_snapshots_no_update
                    BEFORE UPDATE ON asset_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
                    CREATE TRIGGER asset_snapshots_no_delete
                    BEFORE DELETE ON asset_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;

                    PRAGMA user_version = 1;
                    """
                )
                version = 1
            if version == 1:
                connection.executescript(
                    """
                    CREATE TABLE current_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE INDEX current_positions_source_idx ON current_positions(source);

                    CREATE TABLE position_snapshot_sources (
                        snapshot_date TEXT NOT NULL,
                        source TEXT NOT NULL,
                        captured_at TEXT NOT NULL,
                        PRIMARY KEY (snapshot_date, source)
                    );
                    CREATE TABLE position_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_date TEXT NOT NULL,
                        source TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        FOREIGN KEY (snapshot_date, source)
                            REFERENCES position_snapshot_sources(snapshot_date, source)
                    );
                    CREATE INDEX position_snapshots_day_idx
                        ON position_snapshots(snapshot_date, source);

                    CREATE TRIGGER position_snapshot_sources_no_update
                    BEFORE UPDATE ON position_snapshot_sources
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
                    CREATE TRIGGER position_snapshot_sources_no_delete
                    BEFORE DELETE ON position_snapshot_sources
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
                    CREATE TRIGGER position_snapshots_no_update
                    BEFORE UPDATE ON position_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
                    CREATE TRIGGER position_snapshots_no_delete
                    BEFORE DELETE ON position_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;

                    PRAGMA user_version = 2;
                    """
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """打开启用外键约束的短生命周期连接。

        输入：仓库数据库路径。
        输出：事务成功时提交、失败时回滚，并在退出上下文后关闭的 SQLite 连接。
        """
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()


def _serialize_asset(asset: Asset) -> str:
    """无损序列化领域资产。

    输入：金额已规范为 Decimal 的 ``Asset``。
    输出：紧凑 JSON 字符串；金额保存为十进制文本，标签保存为 JSON 数组。
    """
    payload = asdict(asset)
    for field_name in ASSET_DECIMAL_FIELDS:
        payload[field_name] = str(payload[field_name])
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _deserialize_asset(payload_json: str) -> Asset:
    """从数据库恢复领域资产。

    输入：由 ``_serialize_asset`` 生成的 JSON 字符串。
    输出：金额重新规范为 Decimal、标签重新规范为不可变元组的 ``Asset``。
    """
    payload = json.loads(payload_json)
    payload["tags"] = tuple(payload.get("tags") or ())
    return Asset(**payload)


def _serialize_position(position: Position) -> str:
    """无损序列化合约仓位。

    输入：所有数字已规范为 Decimal 的 ``Position``。
    输出：紧凑 JSON 字符串；Decimal 保存为十进制文本，可选空值保持 ``null``。
    """
    payload = asdict(position)
    for field_name in POSITION_DECIMAL_FIELDS:
        value = payload[field_name]
        payload[field_name] = str(value) if value is not None else None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _deserialize_position(payload_json: str) -> Position:
    """从数据库恢复合约仓位。

    输入：由 ``_serialize_position`` 生成的 JSON 字符串。
    输出：金额和杠杆重新规范为 Decimal 的不可变 ``Position``。
    """
    return Position(**json.loads(payload_json))


def _snapshot_day(value: str | date | None) -> str:
    """规范化快照日期。

    输入：``None``、ISO 日期字符串或 ``date`` 对象。
    输出：严格的 ``YYYY-MM-DD`` 字符串；非法日期由 ``date.fromisoformat`` 抛错。
    """
    if value is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()
