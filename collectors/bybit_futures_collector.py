"""
Bybit Linear (USDT-M) Futures WebSocket collector.

Identical keepalive and subscribe protocol to Bybit Spot;
only the endpoint URL differs.

Wire format:
    Same structure as Bybit Spot (orderbook.1 topic, snapshot + delta).
"""

import asyncio
import json

from collectors.base_collector import BaseCollector

_PING_INTERVAL_S = 20


class BybitFuturesCollector(BaseCollector):
    exchange = "bybit"
    market_type = "futures"
    ws_url = "wss://stream.bybit.com/v5/public/linear"

    async def subscribe(self, ws) -> None:
        args = [
            f"orderbook.1.{sym.replace('-', '')}"
            for sym in self.symbols
        ]
        await ws.send(json.dumps({"op": "subscribe", "args": args}))

    async def keepalive(self, ws) -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL_S)
            await ws.send(json.dumps({"op": "ping"}))

    def is_ack(self, msg: dict) -> bool:
        return msg.get("op") in ("subscribe", "pong")
