from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

import httpx

from asset_mcp.config import AppConfig, OkxAccountConfig
from asset_mcp.domain.models import AccountStatus, Asset, Position, decimal_amount, utc_now_iso
from asset_mcp.providers.base import AssetProvider

OKX_STABLE_USD_SYMBOLS = {"USD", "USDT", "USDC", "DAI"}


class OkxProvider(AssetProvider):
    def __init__(self, config: AppConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client

    async def fetch_assets(self) -> list[Asset]:
        assets: list[Asset] = []
        async with self._client() as client:
            for account in self.config.okxAccounts:
                if not account.enabled:
                    continue
                account_balances = await self._signed_get(client, account, "/api/v5/account/balance")
                funding_balances = await self._signed_get(client, account, "/api/v5/asset/balances")
                prices = await self._price_map(client, account)
                assets.extend(
                    self._assets_from_balances(account, account_balances, funding_balances, prices)
                )
        return assets

    async def fetch_positions(self) -> list[Position]:
        """读取所有启用 OKX 账户的当前合约仓位。

        输入：Provider 初始化时注入的 OKX 账户配置和可选 HTTP client；
        账户 API 凭证仅需读取权限。
        输出：统一 ``Position`` 列表，包含 SWAP 与 FUTURES 非零仓位；
        数量和名义价值为绝对值，方向单独存储。
        """
        positions: list[Position] = []
        async with self._client() as client:
            for account in self.config.okxAccounts:
                if not account.enabled:
                    continue
                response = await self._signed_get(
                    client,
                    account,
                    "/api/v5/account/positions",
                )
                positions.extend(self._positions_from_response(account, response))
        return positions

    def _positions_from_response(
        self,
        account: OkxAccountConfig,
        response: dict,
    ) -> list[Position]:
        """把 OKX account/positions 响应标准化。

        输入：单个 OKX 账户配置和 API JSON 响应，兼容 long/short 双向持仓及
        net 净持仓模式。
        输出：非零的 SWAP/FUTURES 统一仓位；缺失保证金时用
        ``abs(notionalUsd) / leverage`` 估算，空清算价输出 ``None``。
        """
        positions: list[Position] = []
        now = utc_now_iso()
        rows = response.get("data", []) if isinstance(response, dict) else []
        for row in rows if isinstance(rows, list) else []:
            amount = decimal_amount(row.get("pos") or 0)
            if amount == 0:
                continue
            instrument = str(row.get("instId") or "").upper()
            instrument_type = str(row.get("instType") or "").upper()
            mark_price = decimal_amount(row.get("markPx") or 0)
            raw_notional = row.get("notionalUsd")
            notional = abs(
                decimal_amount(raw_notional)
                if raw_notional not in (None, "")
                else amount * mark_price
            )
            leverage = decimal_amount(row.get("lever") or 0)
            raw_margin = row.get("margin") or row.get("imr")
            margin = abs(decimal_amount(raw_margin)) if raw_margin not in (None, "") else None
            if margin is None and leverage > 0:
                margin = notional / leverage
            raw_liquidation_price = row.get("liqPx")
            liquidation_price = (
                decimal_amount(raw_liquidation_price)
                if raw_liquidation_price not in (None, "")
                else None
            )
            position_side = str(row.get("posSide") or "net").lower()
            is_long = position_side == "long" or (
                position_side == "net" and amount > 0
            )
            positions.append(
                Position(
                    source="okx",
                    accountId=account.id,
                    accountLabel=account.label,
                    symbol=instrument.split("-", 1)[0],
                    instrument=instrument,
                    side="long" if is_long else "short",
                    quantity=abs(amount),
                    entryPriceUsd=row.get("avgPx") or 0,
                    markPriceUsd=mark_price,
                    notionalUsd=notional,
                    unrealizedPnlUsd=row.get("upl") or 0,
                    leverage=leverage,
                    liquidationPriceUsd=liquidation_price,
                    marginUsd=margin,
                    accountType=(
                        "okx_perpetual" if instrument_type == "SWAP" else "okx_futures"
                    ),
                    updatedAt=now,
                    rawSource="account_positions",
                )
            )
        return positions

    async def health_check(self) -> list[AccountStatus]:
        statuses: list[AccountStatus] = []
        async with self._client() as client:
            for account in self.config.okxAccounts:
                if not account.enabled:
                    statuses.append(AccountStatus("okx", account.id, account.label, False, True, "disabled"))
                    continue
                if not account.apiKey or not account.apiSecret or not account.passphrase:
                    statuses.append(
                        AccountStatus("okx", account.id, account.label, True, False, "missing credentials")
                    )
                    continue
                try:
                    await self._signed_get(client, account, "/api/v5/account/balance")
                    statuses.append(AccountStatus("okx", account.id, account.label, True, True, "ok"))
                except Exception as exc:  # noqa: BLE001
                    statuses.append(
                        AccountStatus("okx", account.id, account.label, True, False, exc.__class__.__name__)
                    )
        return statuses

    def _assets_from_balances(
        self,
        account: OkxAccountConfig,
        account_balances: dict,
        funding_balances: dict,
        prices: dict[str, float],
    ) -> list[Asset]:
        now = utc_now_iso()
        assets: list[Asset] = []
        for item in account_balances.get("data", []):
            for detail in item.get("details", []):
                currency = str(detail.get("ccy", "")).upper()
                quantity = float(detail.get("cashBal") or detail.get("eq") or 0)
                asset = self._asset_from_quantity(
                    account,
                    currency,
                    quantity,
                    prices,
                    now,
                    "account_balance",
                    "trading",
                )
                if asset is not None:
                    assets.append(asset)
        for item in funding_balances.get("data", []):
            currency = str(item.get("ccy", "")).upper()
            quantity = float(item.get("bal") or item.get("availBal") or 0)
            asset = self._asset_from_quantity(
                account,
                currency,
                quantity,
                prices,
                now,
                "funding_balance",
                "funding",
            )
            if asset is not None:
                assets.append(asset)

        return assets

    def _asset_from_quantity(
        self,
        account: OkxAccountConfig,
        currency: str,
        quantity: float,
        prices: dict[str, float],
        updated_at: str,
        raw_source: str,
        wallet: str,
    ) -> Asset | None:
        if quantity <= 0:
            return None
        unit_price_usd = 1.0 if currency in OKX_STABLE_USD_SYMBOLS else prices.get(currency, 0.0)
        value_usd = round(quantity * unit_price_usd, 8)
        if value_usd <= 0:
            return None
        return Asset(
            source="okx",
            accountId=account.id,
            accountLabel=account.label,
            category="crypto",
            symbol=currency,
            quantity=quantity,
            currency=currency,
            unitPriceUsd=round(unit_price_usd, 8),
            valueUsd=value_usd,
            updatedAt=updated_at,
            rawSource=raw_source,
            wallet=wallet,
        )

    async def _price_map(self, client: httpx.AsyncClient, account: OkxAccountConfig) -> dict[str, float]:
        response = await client.get(f"{account.domain}/api/v5/market/tickers", params={"instType": "SPOT"})
        response.raise_for_status()
        prices: dict[str, float] = {symbol: 1.0 for symbol in OKX_STABLE_USD_SYMBOLS}
        for row in response.json().get("data", []):
            inst_id = str(row.get("instId", "")).upper()
            last = float(row.get("last") or 0)
            if inst_id.endswith("-USDT"):
                prices[inst_id.removesuffix("-USDT")] = last
            elif inst_id.endswith("-USDC"):
                prices.setdefault(inst_id.removesuffix("-USDC"), last)
        return prices

    async def _signed_get(
        self,
        client: httpx.AsyncClient,
        account: OkxAccountConfig,
        path: str,
    ) -> dict:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        message = f"{timestamp}GET{path}"
        signature = base64.b64encode(
            hmac.new(account.apiSecret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()
        response = await client.get(
            f"{account.domain}{path}",
            headers={
                "OK-ACCESS-KEY": account.apiKey,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": account.passphrase,
            },
        )
        response.raise_for_status()
        return response.json()

    def _client(self):
        if self.client is not None:
            return _NullAsyncContext(self.client)
        return httpx.AsyncClient(timeout=20)


class _NullAsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False
