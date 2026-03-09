"""
Binance symbol info fetchers (T10).

## REST API endpoints (T06)

Binance Spot
    GET https://api.binance.com/api/v3/exchangeInfo
    Active filter: symbol["status"] == "TRADING"
    Symbol format: "BTCUSDT"  (base + quote, no separator)

Binance USDT-M Futures (fapi)
    GET https://fapi.binance.com/fapi/v1/exchangeInfo
    Active filter: symbol["status"] == "TRADING"
                   symbol["contractType"] == "PERPETUAL"
    Symbol format: "BTCUSDT"  (same as spot — no "_PERP" suffix for USDT-M)

Both endpoints return an "exchangeInfo" JSON object with a top-level
"symbols" array.  No authentication is required.  The response includes
all symbols (active and inactive), so client-side filtering is necessary.

Rate limits (as of 2024): 1200 weight/min; exchangeInfo costs 10 weight.
"""

from __future__ import annotations

import aiohttp

from symbol_discovery.base_exchange_info import BaseExchangeInfo


class BinanceSpotInfo(BaseExchangeInfo):
    """Fetches active Binance Spot trading pairs."""

    exchange = "binance"
    market = "spot"

    _URL = "https://api.binance.com/api/v3/exchangeInfo"

    async def fetch_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        async with session.get(self._URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        return [
            s["symbol"]
            for s in data["symbols"]
            if s["status"] == "TRADING"
        ]


class BinanceFuturesInfo(BaseExchangeInfo):
    """Fetches active Binance USDT-M perpetual futures pairs."""

    exchange = "binance"
    market = "futures"

    _URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

    async def fetch_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        async with session.get(self._URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        return [
            s["symbol"]
            for s in data["symbols"]
            if s["status"] == "TRADING" and s["contractType"] == "PERPETUAL"
        ]
