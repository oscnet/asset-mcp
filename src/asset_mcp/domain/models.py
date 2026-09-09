from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal

AssetCategory = Literal["crypto", "stock", "cash", "manual"]
AssetSource = Literal["binance", "okx", "moomoo", "longbridge", "ibkr", "manual", "onchain"]
DecimalLike = Decimal | str | int | float
SyncStatus = Literal["FRESH", "STALE", "ERROR"]
RiskLevel = Literal["Low", "Medium", "High"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Asset:
    source: AssetSource
    accountId: str
    accountLabel: str
    category: AssetCategory
    symbol: str
    quantity: Decimal
    currency: str
    unitPriceUsd: Decimal
    valueUsd: Decimal
    updatedAt: str
    name: str | None = None
    rawSource: str | None = None
    wallet: str | None = None
    accountType: str | None = None
    chain: str | None = None
    location: str | None = None
    priceSource: str | None = None
    syncStatus: SyncStatus = "FRESH"
    strategy: str | None = None
    riskLevel: RiskLevel | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """把 Provider 传入的数字统一为有限 Decimal。

        输入：构造函数中的 ``quantity``、``unitPriceUsd``、``valueUsd``，允许 Decimal、
        字符串、整数或旧 Provider 产生的浮点数。
        输出：三个字段在不可变对象内部统一为 Decimal；非法值、NaN 和 Infinity 抛出
        ``ValueError``，防止污染组合汇总。
        """
        object.__setattr__(self, "quantity", decimal_amount(self.quantity))
        object.__setattr__(self, "unitPriceUsd", decimal_amount(self.unitPriceUsd))
        object.__setattr__(self, "valueUsd", decimal_amount(self.valueUsd))
        if self.syncStatus not in ("FRESH", "STALE", "ERROR"):
            raise ValueError("syncStatus must be FRESH, STALE, or ERROR")
        object.__setattr__(self, "location", self.location or self.source)
        object.__setattr__(self, "accountType", self.accountType or _default_account_type(self))
        object.__setattr__(self, "chain", self.chain or _default_chain(self))
        object.__setattr__(self, "priceSource", self.priceSource or self.source)
        object.__setattr__(self, "tags", normalize_tags(self.tags))

    def to_dict(self) -> dict[str, Any]:
        """输出保持现有 MCP 契约的 JSON 兼容字典。

        输入：当前精确 Asset 对象。
        输出：字段名称不变，三个 Decimal 金额在传输边界转成 JSON number；内部计算不会
        使用该降精度结果。
        """
        payload = asdict(self)
        for field_name in ("quantity", "unitPriceUsd", "valueUsd"):
            payload[field_name] = json_number(payload[field_name])
        return payload


@dataclass(frozen=True)
class AccountStatus:
    source: AssetSource
    accountId: str
    accountLabel: str
    enabled: bool
    ok: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decimal_amount(value: DecimalLike) -> Decimal:
    """把外部数字转换为有限 Decimal。

    输入：Decimal、十进制字符串、整数或浮点数。浮点数使用 15 位有效数字格式化，以清除
    ``0.1 + 0.2`` 这类二进制尾差，并兼容现有 Provider。
    输出：有限的 Decimal；无法解析或非有限输入抛出 ``ValueError``。
    """
    try:
        text = format(value, ".15g") if isinstance(value, float) else str(value)
        result = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("value must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError("value must be a finite decimal")
    return result


def normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """规范化资产标签并保持稳定顺序。

    输入：任意字符串可迭代对象；每个标签允许包含首尾空白和重复值。
    输出：去除空白、空标签和重复项后的不可变元组，首次出现顺序保持不变。
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = str(tag).strip()
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return tuple(normalized)


def json_number(value: DecimalLike) -> float:
    """在 JSON/MCP 边界把精确金额转换为兼容的 number。

    输入：任意 ``DecimalLike`` 金额。
    输出：有限 Python ``float``；该值仅用于传输和展示，不得重新参与领域计算。
    """
    return float(decimal_amount(value))


def sum_value_usd(assets: list[Asset]) -> Decimal:
    """精确汇总资产美元价值。

    输入：已完成 Decimal 规范化的 Asset 列表。
    输出：Decimal 总值，保留最多八位美元小数，与既有输出精度一致。
    """
    return round(sum((asset.valueUsd for asset in assets), start=Decimal("0")), 8)


def _default_account_type(asset: Asset) -> str:
    if asset.source == "onchain":
        return "wallet"
    if asset.source in ("binance", "okx"):
        return asset.wallet or "exchange"
    if asset.source in ("moomoo", "longbridge", "ibkr"):
        return "brokerage"
    return "manual"


def _default_chain(asset: Asset) -> str | None:
    if asset.source != "onchain" or not asset.wallet or ":" not in asset.wallet:
        return None
    return asset.wallet.split(":", 1)[0] or None
