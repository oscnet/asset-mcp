from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from asset_mcp.domain.models import Asset, SyncStatus

SCHEMA_VERSION = 1
DECIMAL_FIELDS = ("quantity", "unitPriceUsd", "valueUsd")


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
    for field_name in DECIMAL_FIELDS:
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
