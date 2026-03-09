"""
Bybit symbol info fetchers (T11).

## REST API endpoints (T06)

Bybit Spot
    GET https://api.bybit.com/v5/market/instruments-info?category=spot
    Active filter: instrument["status"] == "Trading"
    Symbol format: "BTCUSDT"  (base + quote, no separator)

Bybit Linear (USDT-margined perpetual futures)
    GET https://api.bybit.com/v5/market/instruments-info?category=linear
    Active filter: instrument["status"] == "Trading"
                   instrument["contractType"] == "LinearPerpetual"
    Symbol format: "BTCUSDT"  (no suffix for USDT linear)

Both use the v5 unified API.  Response structure:
    { "retCode": 0, "result": { "list": [ ... ], "nextPageCursor": "..." } }

Bybit paginates results (default limit=500).  The instruments-info endpoint
returns all instruments in a single page for standard filtering; the cursor
is used only when the full list exceeds 1000 entries, which is rare for
active USDT pairs.  We fetch a single page with limit=1000 which covers all
active USDT pairs as of 2024.

Rate limits: no per-call weight system; up to 120 requests/min per IP.
"""

from __future__ import annotations

import aiohttp

from symbol_discovery.base_exchange_info import BaseExchangeInfo

_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
_PAGE_LIMIT = 1000


class BybitSpotInfo(BaseExchangeInfo):
    """Fetches active Bybit Spot trading pairs."""

    exchange = "bybit"
    market = "spot"

    async def fetch_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        params = {"category": "spot", "limit": _PAGE_LIMIT}
        async with session.get(
            _INSTRUMENTS_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        _check_ret_code(data)
        return [
            item["symbol"]
            for item in data["result"]["list"]
            if item["status"] == "Trading"
        ]


class BybitFuturesInfo(BaseExchangeInfo):
    """Fetches active Bybit Linear (USDT-M) perpetual futures pairs."""

    exchange = "bybit"
    market = "futures"

    async def fetch_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        params = {"category": "linear", "limit": _PAGE_LIMIT}
        async with session.get(
            _INSTRUMENTS_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        _check_ret_code(data)
        return [
            item["symbol"]
            for item in data["result"]["list"]
            if item["status"] == "Trading" and item["contractType"] == "LinearPerpetual"
        ]


def _check_ret_code(data: dict) -> None:
    """Raise RuntimeError if Bybit response signals a business-logic error."""
    ret_code = data.get("retCode", -1)
    if ret_code != 0:
        msg = data.get("retMsg", "unknown error")
        raise RuntimeError(f"Bybit API error {ret_code}: {msg}")
