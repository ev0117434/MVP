"""
Abstract base class for all WebSocket collectors.

Each concrete collector connects to one exchange/market WebSocket endpoint,
subscribes to bookTicker or orderbook.1 feeds for the given symbols, and
forwards raw JSON messages to a shared asyncio.Queue.

Queue element format:
    (exchange: str, market_type: str, raw_msg: dict, ts_recv_ns: int)
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod

import websockets

from infra.logging_config import setup_logging


class ExponentialBackoff:
    """
    Exponential backoff with a cap.

    next() returns base * 2^attempt, clamped to [base, cap].
    reset() restarts the sequence from attempt 0.
    """

    def __init__(self, base: float = 1.0, cap: float = 60.0) -> None:
        self._base = base
        self._cap = cap
        self._attempt = 0

    def next(self) -> float:
        delay = min(self._base * (2 ** self._attempt), self._cap)
        self._attempt += 1
        return delay

    def reset(self) -> None:
        self._attempt = 0


class BaseCollector(ABC):
    """
    Abstract WebSocket collector.

    Subclasses must implement:
      - ws_url (property)
      - subscribe(ws)
      - keepalive(ws)   – coroutine; run concurrently with receive loop
      - is_ack(msg)     – True if message should be silently dropped
    """

    exchange: str
    market_type: str

    def __init__(
        self,
        symbols: list[str],
        queue: asyncio.Queue,
        cfg: dict,
    ) -> None:
        self.symbols = symbols
        self.queue = queue
        self._cfg = cfg
        self._backoff = ExponentialBackoff(base=1.0, cap=60.0)

        logging_cfg = cfg.get("logging", {})
        log_dir = cfg.get("paths", {}).get("log_dir", "logs")
        self.log = setup_logging("collectors", logging_cfg, log_dir).bind(
            exchange=self.exchange,
            market=self.market_type,
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def ws_url(self) -> str:
        """Full WebSocket URL including query string if needed."""

    @abstractmethod
    async def subscribe(self, ws) -> None:
        """Send subscription request(s) after connection is established."""

    @abstractmethod
    async def keepalive(self, ws) -> None:
        """Keepalive coroutine; runs concurrently with receive loop.
        Raise any exception to trigger reconnect.
        """

    @abstractmethod
    def is_ack(self, msg: dict) -> bool:
        """Return True for subscription ACK / ping-pong messages to skip."""

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Connect → subscribe → receive loop.
        On any exception: log, backoff, reconnect. Never exits.
        """
        while True:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,   # we manage keepalive ourselves for Bybit;
                    ping_timeout=None,    # websockets handles Binance ping/pong automatically
                    open_timeout=10,
                ) as ws:
                    self._backoff.reset()
                    self.log.info("ws_connected", url=self.ws_url[:80])
                    await self.subscribe(ws)
                    await self._receive_loop(ws)
            except Exception as exc:
                delay = self._backoff.next()
                self.log.warning(
                    "ws_disconnected",
                    error=repr(exc),
                    retry_in_s=round(delay, 1),
                )
                await asyncio.sleep(delay)

    async def _receive_loop(self, ws) -> None:
        """
        Read messages from *ws* until it closes or raises.
        Runs keepalive() as a concurrent task; cancels it on exit.
        """
        keepalive_task = asyncio.create_task(self.keepalive(ws))
        try:
            async for raw in ws:
                ts_recv_ns = time.time_ns()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self.log.debug("json_decode_error", raw=raw[:200])
                    continue
                if self.is_ack(msg):
                    continue
                await self.queue.put(
                    (self.exchange, self.market_type, msg, ts_recv_ns)
                )
        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
