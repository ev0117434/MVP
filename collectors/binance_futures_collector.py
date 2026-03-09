"""
Binance USDT-M Futures WebSocket collector.

Connects to Binance Futures combined stream endpoint and subscribes to bookTicker.

Wire format (combined stream envelope):
    {"stream": "btcusdt@bookTicker",
     "data": {"e":"bookTicker","E":1700000000000,"s":"BTCUSDT","b":"...","a":"..."}}

Keepalive: handled automatically by the `websockets` library (ping/pong).
"""

import asyncio

from collectors.base_collector import BaseCollector


class BinanceFuturesCollector(BaseCollector):
    exchange = "binance"
    market_type = "futures"

    @property
    def ws_url(self) -> str:
        streams = "/".join(
            f"{sym.lower().replace('-', '')}@bookTicker"
            for sym in self.symbols
        )
        return f"wss://fstream.binance.com/stream?streams={streams}"

    async def subscribe(self, ws) -> None:
        pass

    async def keepalive(self, ws) -> None:
        await asyncio.sleep(float("inf"))

    def is_ack(self, msg: dict) -> bool:
        return False
