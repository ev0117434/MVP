"""
Bybit Spot WebSocket collector.

Connects to Bybit V5 public spot endpoint and subscribes to orderbook.1
for all symbols in the subscription list.

Wire format:
    Snapshot: {"topic":"orderbook.1.BTCUSDT","type":"snapshot","ts":...,
               "data":{"s":"BTCUSDT","b":[["price","qty"]],"a":[...]}}
    Delta:    same structure with "type":"delta" and possibly empty b/a

Keepalive: Bybit requires a client-side ping every 20 s.
    Send: {"op":"ping"}
    Expect: {"op":"pong"} (filtered out by is_ack)
    If 3 consecutive pings receive no pong, server closes the connection
    and our reconnect loop handles the rest.
"""

import asyncio
import json

from collectors.base_collector import BaseCollector

_PING_INTERVAL_S = 20


class BybitSpotCollector(BaseCollector):
    exchange = "bybit"
    market_type = "spot"
    ws_url = "wss://stream.bybit.com/v5/public/spot"

    async def subscribe(self, ws) -> None:
        args = [
            f"orderbook.1.{sym.replace('-', '')}"
            for sym in self.symbols
        ]
        payload = json.dumps({"op": "subscribe", "args": args})
        await ws.send(payload)

    async def keepalive(self, ws) -> None:
        """Send a ping every 20 s to keep the connection alive."""
        while True:
            await asyncio.sleep(_PING_INTERVAL_S)
            await ws.send(json.dumps({"op": "ping"}))

    def is_ack(self, msg: dict) -> bool:
        return msg.get("op") in ("subscribe", "pong")
