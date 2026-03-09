"""
Binance Spot WebSocket collector.

Connects to Binance combined stream endpoint and subscribes to bookTicker
for all symbols in the subscription list.

Wire format (combined stream envelope):
    {"stream": "btcusdt@bookTicker", "data": {"s":"BTCUSDT","b":"...","a":"..."}}

Keepalive: the `websockets` library responds to server ping frames automatically,
so no explicit keepalive is needed here.
"""

import asyncio

from collectors.base_collector import BaseCollector


class BinanceSpotCollector(BaseCollector):
    exchange = "binance"
    market_type = "spot"

    @property
    def ws_url(self) -> str:
        streams = "/".join(
            f"{sym.lower().replace('-', '')}@bookTicker"
            for sym in self.symbols
        )
        return f"wss://stream.binance.com:9443/stream?streams={streams}"

    async def subscribe(self, ws) -> None:
        # Subscriptions are encoded in the URL; nothing to send.
        pass

    async def keepalive(self, ws) -> None:
        # websockets handles RFC 6455 ping/pong automatically.
        # This coroutine must exist but has nothing to do.
        await asyncio.sleep(float("inf"))

    def is_ack(self, msg: dict) -> bool:
        # URL-based combined streams have no subscription ACK messages.
        return False
