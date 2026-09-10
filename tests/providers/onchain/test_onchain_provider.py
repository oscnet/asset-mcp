from decimal import Decimal
from urllib.parse import urlparse

import httpx
import pytest

from asset_mcp.config import parse_config
from asset_mcp.providers.base import PartialAssetFetchError
from asset_mcp.providers.onchain import OnchainProvider


@pytest.mark.asyncio
async def test_onchain_provider_discovers_assets_from_addresses():
    config = parse_config(
        {
            "rates": {
                "BTC": 50000,
                "ETH": 2000,
                "SOL": 100,
                "TRX": 0.1,
                "USDT": 1,
                "USDD": 1,
                "SUSDD": 1.02,
            },
            "onchain": {
                "accounts": [
                    {
                        "id": "onchain-main",
                        "label": "On-chain Wallet",
                        "addresses": [
                            {"chain": "bitcoin", "address": "bc1qexample"},
                            {
                                "chain": "ethereum",
                                "address": "0x0000000000000000000000000000000000000001",
                            },
                            {
                                "chain": "501",
                                "address": "So11111111111111111111111111111111111111112",
                            },
                            {"chain": "tron", "address": "TXYZexample"},
                        ],
                    }
                ],
            },
        }
    )
    client = _FakeOnchainClient()

    assets = await OnchainProvider(config, client=client).fetch_assets()

    by_symbol_wallet = {(asset.symbol, asset.wallet): asset for asset in assets}
    assert by_symbol_wallet[("BTC", "bitcoin:bc1qexample")].quantity == 1
    assert by_symbol_wallet[("BTC", "bitcoin:bc1qexample")].valueUsd == 50000
    assert by_symbol_wallet[("ETH", "ethereum:0x0000...0001")].quantity == 2
    assert by_symbol_wallet[("ETH", "ethereum:0x0000...0001")].valueUsd == 4000
    assert by_symbol_wallet[("USDT", "ethereum:0x0000...0001")].quantity == Decimal("123.45")
    assert by_symbol_wallet[("USDD", "ethereum:0x0000...0001")].quantity == 10
    assert by_symbol_wallet[("SUSDD", "ethereum:0x0000...0001")].valueUsd == Decimal("5.1")
    assert by_symbol_wallet[("SOL", "solana:So1111...1112")].valueUsd == 300
    assert by_symbol_wallet[("JUP123...7890", "solana:So1111...1112")].valueUsd == 25
    assert by_symbol_wallet[("TRX", "tron:TXYZexample")].valueUsd == Decimal("0.5")
    assert by_symbol_wallet[("USDT", "tron:TXYZexample")].valueUsd == 20


@pytest.mark.asyncio
async def test_onchain_provider_fetches_configured_erc20_tokens_with_indexer():
    config = parse_config(
        {
            "rates": {"CUSTOM": 2},
            "onchain": {
                "indexer": {"provider": "covalent", "apiKey": "test-key"},
                "accounts": [
                    {
                        "id": "onchain-main",
                        "label": "On-chain Wallet",
                        "addresses": [
                            {
                                "chain": "ethereum",
                                "address": "0x0000000000000000000000000000000000000001",
                                "tokens": [
                                    {
                                        "symbol": "USDT",
                                        "name": "Tether USD",
                                        "contractAddress": (
                                            "0xdAC17F958D2ee523a2206206994597C13D831ec7"
                                        ),
                                        "decimals": 6,
                                    },
                                    {
                                        "symbol": "CUSTOM",
                                        "name": "Custom Token",
                                        "contractAddress": (
                                            "0x00000000000000000000000000000000000000c0"
                                        ),
                                        "decimals": 18,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        }
    )

    assets = await OnchainProvider(config, client=_FakeOnchainClient()).fetch_assets()

    by_symbol = {asset.symbol: asset for asset in assets}
    assert by_symbol["ETH"].rawSource == "covalent_balances_v2"
    assert by_symbol["USDT"].rawSource == "covalent_balances_v2"
    assert by_symbol["CUSTOM"].quantity == 7
    assert by_symbol["CUSTOM"].valueUsd == 14
    assert by_symbol["CUSTOM"].rawSource == "configured_erc20_balance"
    assert [asset.symbol for asset in assets].count("USDT") == 1


@pytest.mark.asyncio
async def test_onchain_health_check_reports_unsupported_chain():
    config = parse_config(
        {
            "onchain": {
                "accounts": [
                    {
                        "id": "onchain-main",
                        "label": "On-chain Wallet",
                        "addresses": [{"chain": "unknown-chain", "address": "0x1"}],
                    }
                ]
            },
        }
    )

    statuses = await OnchainProvider(config, client=_FakeOnchainClient()).health_check()

    assert statuses[0].ok is False
    assert statuses[0].message == "1 of 1 addresses failed: ValueError"


@pytest.mark.asyncio
async def test_solana_keeps_standard_tokens_when_token_2022_is_unsupported():
    """输入 Token-2022 返回 INVALID_PARAMS、标准 Token 正常；输出仍保留 SOL 和 SPL。"""
    config = parse_config(
        {
            "rates": {"SOL": 100},
            "onchain": {
                "accounts": [
                    {
                        "id": "solana-main",
                        "label": "Solana Wallet",
                        "addresses": [
                            {
                                "chain": "solana",
                                "address": "So11111111111111111111111111111111111111112",
                            }
                        ],
                    }
                ]
            },
        }
    )

    assets = await OnchainProvider(config, client=_FakeOnchainClient()).fetch_assets()

    assert {asset.symbol for asset in assets} == {"SOL", "JUP123...7890"}


@pytest.mark.asyncio
async def test_onchain_fetch_isolates_one_failed_address_and_reports_partial_assets():
    """输入同账户一个 EVM 地址失败、一个成功；输出成功资产和不含原地址的部分异常。"""
    config = parse_config(
        {
            "rates": {"ETH": 2000},
            "onchain": {
                "accounts": [
                    {
                        "id": "wallet-main",
                        "label": "Wallet",
                        "addresses": [
                            {
                                "chain": "ethereum",
                                "address": "0x0000000000000000000000000000000000000001",
                            },
                            {
                                "chain": "ethereum",
                                "address": "0x0000000000000000000000000000000000000002",
                            },
                        ],
                    }
                ]
            },
        }
    )
    provider = OnchainProvider(
        config,
        client=_PartiallyFailingEvmClient(),
        evm_min_interval_seconds=0,
    )

    with pytest.raises(PartialAssetFetchError) as captured:
        await provider.fetch_assets()

    assert [(asset.symbol, asset.wallet) for asset in captured.value.assets] == [
        ("ETH", "ethereum:0x0000...0002")
    ]
    assert captured.value.failed_scopes == {
        ("wallet-main", "ethereum:0x0000...0001")
    }
    assert "0x0000000000000000000000000000000000000001" not in str(captured.value)

    statuses = await provider.health_check()
    assert statuses[0].ok is False
    assert statuses[0].message.startswith("1 of 2 addresses failed")
    assert "0x0000000000000000000000000000000000000001" not in statuses[0].message


class _FakeResponse:
    def __init__(self, status_code, data, headers=None):
        self.status_code = status_code
        self._data = data
        self.headers = dict(headers or {})

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code < 400:
            return
        request = httpx.Request("GET", "https://example.test")
        response = httpx.Response(self.status_code, json=self._data, request=request)
        raise httpx.HTTPStatusError("error", request=request, response=response)


class _FakeOnchainClient:
    async def get(self, url, **kwargs):
        parsed = urlparse(url)
        params = kwargs.get("params", {})
        if parsed.netloc == "blockstream.info" and parsed.path == "/api/address/bc1qexample":
            return _FakeResponse(
                200,
                {
                    "chain_stats": {"funded_txo_sum": 100000000, "spent_txo_sum": 0},
                    "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 0},
                },
            )
        if parsed.netloc == "api.jup.ag" and parsed.path == "/price/v3":
            return _FakeResponse(200, {"JUP1234567890": {"usdPrice": 0.5}})
        if parsed.netloc == "api.covalenthq.com" and parsed.path.endswith("/balances_v2/"):
            return _FakeResponse(
                200,
                {
                    "data": {
                        "items": [
                            {
                                "contract_ticker_symbol": "ETH",
                                "contract_display_name": "Ethereum",
                                "contract_address": (
                                    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
                                ),
                                "contract_decimals": 18,
                                "balance": str(2 * 10**18),
                                "quote_rate": 2000,
                                "quote": 4000,
                            },
                            {
                                "contract_ticker_symbol": "USDT",
                                "contract_display_name": "Tether USD",
                                "contract_address": (
                                    "0xdAC17F958D2ee523a2206206994597C13D831ec7"
                                ),
                                "contract_decimals": 6,
                                "balance": "123450000",
                                "quote_rate": 1,
                                "quote": 123.45,
                            },
                        ]
                    }
                },
            )
        if (
            parsed.netloc == "apilist.tronscanapi.com"
            and parsed.path == "/api/account/tokens"
            and params.get("address") == "TXYZexample"
        ):
            return _FakeResponse(
                200,
                {
                    "data": [
                        {"tokenAbbr": "TRX", "tokenName": "TRON", "quantity": 5, "priceInUsd": 0.1},
                        {
                            "tokenAbbr": "USDT",
                            "tokenName": "Tether USD",
                            "quantity": 20,
                            "priceInUsd": 1,
                        },
                    ]
                },
            )
        return _FakeResponse(404, {})

    async def post(self, url, **kwargs):
        """输入模拟 RPC URL 与 JSON 请求；输出对应链和方法的固定测试响应。"""
        parsed = urlparse(url)
        payload = kwargs.get("json", {})
        if parsed.netloc == "api.mainnet-beta.solana.com":
            if payload.get("method") == "getBalance":
                return _FakeResponse(200, {"result": {"value": 3 * 10**9}})
            if payload.get("method") == "getTokenAccountsByOwner":
                program_id = payload["params"][1]["programId"]
                if program_id.startswith("Tokenz"):
                    return _FakeResponse(
                        200,
                        {"error": {"code": -32602, "message": "INVALID_PARAMS"}},
                    )
                return _FakeResponse(
                    200,
                    {
                        "result": {
                            "value": [
                                {
                                    "account": {
                                        "data": {
                                            "parsed": {
                                                "info": {
                                                    "mint": "JUP1234567890",
                                                    "tokenAmount": {
                                                        "uiAmountString": "50",
                                                        "decimals": 6,
                                                    },
                                                }
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    },
                )
        if parsed.netloc == "ethereum-rpc.publicnode.com":
            if payload.get("method") == "eth_getBalance":
                return _FakeResponse(200, {"result": hex(2 * 10**18)})
            if payload.get("method") == "eth_call":
                contract = payload["params"][0]["to"].lower()
                if contract == "0xdac17f958d2ee523a2206206994597c13d831ec7":
                    return _FakeResponse(200, {"result": hex(123450000)})
                if contract == "0x4f8e5de400de08b164e7421b3ee387f461becd1a":
                    return _FakeResponse(200, {"result": hex(10 * 10**18)})
                if contract == "0xc5d6a7b61d18afa11435a889557b068bb9f29930":
                    return _FakeResponse(200, {"result": hex(5 * 10**18)})
                if contract == "0x00000000000000000000000000000000000000c0":
                    return _FakeResponse(200, {"result": hex(7 * 10**18)})
                return _FakeResponse(200, {"result": "0x0"})
        return _FakeResponse(404, {})


@pytest.mark.asyncio
async def test_evm_rpc_throttles_consecutive_requests_to_same_host():
    """输入同一 EVM RPC 的连续请求；输出第二次请求前等待剩余最小间隔。"""
    clock = _FakeClock()
    client = _SequencedRpcClient([_FakeResponse(200, {"result": "0x0"})] * 2, clock)
    provider = OnchainProvider(
        parse_config({}),
        client=client,
        evm_min_interval_seconds=0.1,
        sleep=clock.sleep,
        clock=clock,
    )

    await provider._json_rpc(client, "https://rpc.example.test", "eth_getBalance", [])
    await provider._json_rpc(client, "https://rpc.example.test", "eth_getBalance", [])

    assert client.request_times == [0.0, 0.1]
    assert clock.sleeps == [pytest.approx(0.1)]


@pytest.mark.asyncio
async def test_evm_rpc_retries_429_and_honors_retry_after():
    """输入一次带 Retry-After 的 429 后成功响应；输出等待指定秒数并返回成功数据。"""
    clock = _FakeClock()
    client = _SequencedRpcClient(
        [
            _FakeResponse(429, {}, {"Retry-After": "0.25"}),
            _FakeResponse(200, {"result": "0x2a"}),
        ],
        clock,
    )
    provider = OnchainProvider(
        parse_config({}),
        client=client,
        evm_min_interval_seconds=0.1,
        sleep=clock.sleep,
        clock=clock,
    )

    result = await provider._json_rpc(
        client,
        "https://rpc.example.test",
        "eth_getBalance",
        [],
    )

    assert result == {"result": "0x2a"}
    assert len(client.request_times) == 2
    assert clock.sleeps == [pytest.approx(0.25)]


@pytest.mark.asyncio
async def test_evm_rpc_uses_bounded_exponential_backoff_then_raises():
    """输入持续 429；输出 0.5/1/2 秒退避三次，第四次失败后停止重试。"""
    clock = _FakeClock()
    client = _SequencedRpcClient([_FakeResponse(429, {})] * 4, clock)
    provider = OnchainProvider(
        parse_config({}),
        client=client,
        evm_min_interval_seconds=0,
        evm_max_retries=3,
        sleep=clock.sleep,
        clock=clock,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider._json_rpc(
            client,
            "https://rpc.example.test",
            "eth_getBalance",
            [],
        )

    assert len(client.request_times) == 4
    assert clock.sleeps == [0.5, 1.0, 2.0]


@pytest.mark.asyncio
async def test_evm_rpc_does_not_retry_non_rate_limit_http_errors():
    """输入 HTTP 403；输出立即抛出且不睡眠、不重试。"""
    clock = _FakeClock()
    client = _SequencedRpcClient([_FakeResponse(403, {})], clock)
    provider = OnchainProvider(
        parse_config({}),
        client=client,
        sleep=clock.sleep,
        clock=clock,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await provider._json_rpc(
            client,
            "https://rpc.example.test",
            "eth_getBalance",
            [],
        )

    assert len(client.request_times) == 1
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_price_lookup_retries_429_and_reuses_successful_cache():
    """输入价格接口先 429 后成功及两次相同查询；输出仅重试一次并复用成功价格。"""
    clock = _FakeClock()
    client = _SequencedPriceClient(
        [
            _FakeResponse(429, {}, {"Retry-After": "0.25"}),
            _FakeResponse(200, {"ethereum": {"usd": 2500}}),
        ],
        clock,
    )
    provider = OnchainProvider(
        parse_config({}),
        client=client,
        sleep=clock.sleep,
        clock=clock,
    )

    first = await provider._price_map_for_coin_ids(client, {"ethereum": "ETH"})
    second = await provider._price_map_for_coin_ids(client, {"ethereum": "ETH"})

    assert first["ETH"] == 2500
    assert second["ETH"] == 2500
    assert client.request_count == 2
    assert clock.sleeps == [pytest.approx(0.25)]


@pytest.mark.asyncio
async def test_evm_balance_survives_exhausted_price_rate_limit():
    """输入余额 RPC 成功但 CoinGecko 持续 429；输出保留余额并用零美元价格降级。"""
    config = parse_config(
        {
            "onchain": {
                "accounts": [
                    {
                        "id": "onchain-main",
                        "label": "On-chain Wallet",
                        "addresses": [
                            {
                                "chain": "ethereum",
                                "address": "0x0000000000000000000000000000000000000001",
                            }
                        ],
                    }
                ]
            }
        }
    )
    clock = _FakeClock()
    client = _RateLimitedPriceEvmClient(clock)
    provider = OnchainProvider(
        config,
        client=client,
        evm_min_interval_seconds=0,
        sleep=clock.sleep,
        clock=clock,
    )

    assets = await provider.fetch_assets()

    assert [(asset.symbol, asset.quantity, asset.valueUsd) for asset in assets] == [
        ("ETH", 2, 0)
    ]
    assert client.price_request_count == 4


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        """输入无；输出当前模拟单调时钟秒数。"""
        return self.now

    async def sleep(self, seconds):
        """输入等待秒数；输出无，并推进模拟时钟供节流测试断言。"""
        self.sleeps.append(seconds)
        self.now += seconds


class _SequencedRpcClient:
    def __init__(self, responses, clock):
        self.responses = list(responses)
        self.clock = clock
        self.request_times = []

    async def post(self, _url, **_kwargs):
        """输入 RPC URL 与请求参数；输出预置响应并记录请求发生时刻。"""
        self.request_times.append(self.clock())
        return self.responses.pop(0)


class _SequencedPriceClient:
    def __init__(self, responses, clock):
        """输入预置价格响应和模拟时钟；输出可记录 GET 次数的测试客户端。"""
        self.responses = list(responses)
        self.clock = clock
        self.request_count = 0

    async def get(self, _url, **_kwargs):
        """输入价格 URL 与查询参数；输出下一项预置响应并累计请求次数。"""
        self.request_count += 1
        return self.responses.pop(0)


class _RateLimitedPriceEvmClient:
    def __init__(self, clock):
        """输入模拟时钟；输出余额成功、价格持续限流的测试客户端。"""
        self.clock = clock
        self.price_request_count = 0

    async def post(self, _url, **kwargs):
        """输入 EVM JSON-RPC 请求；输出 2 ETH 原生余额或零 ERC-20 余额。"""
        method = kwargs.get("json", {}).get("method")
        result = hex(2 * 10**18) if method == "eth_getBalance" else "0x0"
        return _FakeResponse(200, {"result": result})

    async def get(self, _url, **_kwargs):
        """输入价格 URL 与参数；输出持续 HTTP 429 以验证余额降级行为。"""
        self.price_request_count += 1
        return _FakeResponse(429, {})


class _PartiallyFailingEvmClient:
    async def post(self, _url, **kwargs):
        """输入两个地址的 EVM RPC；输出首地址错误、次地址 2 ETH 或零 Token 余额。"""
        payload = kwargs.get("json", {})
        method = payload.get("method")
        params = payload.get("params", [])
        if method == "eth_getBalance" and params[0].endswith("0001"):
            return _FakeResponse(200, {"error": {"code": -32000, "message": "unavailable"}})
        if method == "eth_getBalance":
            return _FakeResponse(200, {"result": hex(2 * 10**18)})
        return _FakeResponse(200, {"result": "0x0"})
