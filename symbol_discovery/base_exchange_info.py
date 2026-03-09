"""
Abstract base class for exchange info fetchers (T09).

Each concrete subclass fetches the list of active raw symbols from one
exchange + market combination via a single REST call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import aiohttp


class BaseExchangeInfo(ABC):
    """
    Abstract interface for fetching active symbols from one exchange/market.

    Concrete subclasses
    -------------------
    BinanceSpotInfo, BinanceFuturesInfo  →  binance_exchange_info.py
    BybitSpotInfo, BybitFuturesInfo      →  bybit_exchange_info.py
    """

    # Subclasses should declare these for logging / identification
    exchange: str = ""
    market: str = ""

    @abstractmethod
    async def fetch_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        """
        Fetch and return a list of active raw (exchange-native) symbol strings.

        Parameters
        ----------
        session:
            Shared aiohttp.ClientSession — callers manage its lifecycle.

        Returns
        -------
        list[str]
            Raw symbols, e.g. ["BTCUSDT", "ETHUSDT", ...].
            Empty list on transient error (caller decides how to handle).

        Raises
        ------
        aiohttp.ClientError
            On network or HTTP errors — propagated to the caller.
        """
        ...
