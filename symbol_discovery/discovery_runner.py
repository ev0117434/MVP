"""
Symbol discovery orchestrator (T13).

Coordinates the four exchange-info fetchers, computes subscription lists,
persists them to cache, and schedules periodic refreshes.

## Open decisions resolved (T14)

REST unavailability at startup
    Fallback to cache if ``cache/subscription_lists.yaml`` exists.
    Fail-fast (raise) if REST is unreachable AND no cache is available.
    Partial failure (only some sources down): use cached lists for the
    failed sources and proceed with whatever was fetched successfully,
    logging a WARNING.

Volume filter
    Not implemented.  All active USDT pairs pass through.
    Future: add ``min_volume_usdt_24h`` to config and fetch ticker data.

Update frequency
    Driven by ``config.timing.discovery_refresh_interval_s``.
    Set to 0 to disable periodic refresh (fetch once at startup only).

Symbol disappears from exchange
    The subscription list is rebuilt on each refresh.  Collectors that
    were subscribed to a now-absent symbol will stop receiving updates;
    the SHM slot will naturally go stale and be marked N/A by the spread
    reader via ``staleness_threshold_ms``.  No explicit SHM deletion is
    performed — slot reuse is handled in Phase 3.

Quote currency
    Configurable via ``config.quote_currency`` (default "USDT").
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp
import yaml

from symbol_discovery.binance_exchange_info import BinanceFuturesInfo, BinanceSpotInfo
from symbol_discovery.bybit_exchange_info import BybitFuturesInfo, BybitSpotInfo
from symbol_discovery.intersection import SubscriptionLists, compute_subscription_lists, subscription_lists_stats

log = logging.getLogger("symbol_discovery")


class DiscoveryRunner:
    """
    Fetches, computes, and persists subscription lists.

    Parameters
    ----------
    config:
        Full parsed config.yaml dict.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._quote_currency: str = config.get("quote_currency", "USDT")
        self._cache_path: str = os.path.join(
            config["paths"]["cache_dir"], "subscription_lists.yaml"
        )
        self._refresh_interval: int = config["timing"]["discovery_refresh_interval_s"]

        self._sources = [
            BinanceSpotInfo(),
            BinanceFuturesInfo(),
            BybitSpotInfo(),
            BybitFuturesInfo(),
        ]

        self._subscription_lists: SubscriptionLists | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_once(self) -> SubscriptionLists:
        """
        Fetch symbols from all sources, compute intersection, save cache.

        Falls back to cached lists if REST calls fail and cache exists.

        Returns
        -------
        SubscriptionLists
            The freshly computed (or cached) subscription lists.
        """
        raw = await self._fetch_all()
        lists = compute_subscription_lists(
            binance_spot_raw=raw["binance_spot"],
            binance_futures_raw=raw["binance_futures"],
            bybit_spot_raw=raw["bybit_spot"],
            bybit_futures_raw=raw["bybit_futures"],
            quote_currency=self._quote_currency,
        )
        self._subscription_lists = lists
        self._save_cache(lists)
        stats = subscription_lists_stats(lists)
        log.info("subscription lists updated: %s", stats)
        return lists

    async def run_forever(self) -> None:
        """
        Run discovery once, then refresh on the configured interval.

        Intended to be run as an asyncio task alongside the rest of the system.
        Exits cleanly on asyncio.CancelledError.
        """
        await self.run_once()

        if self._refresh_interval <= 0:
            log.info("periodic discovery refresh disabled (interval=0)")
            return

        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                await self.run_once()
            except asyncio.CancelledError:
                log.info("discovery runner cancelled")
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("discovery refresh failed, keeping previous lists: %s", exc)

    @property
    def subscription_lists(self) -> SubscriptionLists | None:
        """Last computed subscription lists, or None if not yet fetched."""
        return self._subscription_lists

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_all(self) -> dict[str, list[str]]:
        """
        Concurrently fetch raw symbols from all four sources.

        If a source fails and a cache exists, its cached value is used.
        If a source fails and there is no cache, raises RuntimeError.
        """
        cache = self._load_cache()

        async with aiohttp.ClientSession() as session:
            tasks = {
                src.exchange + "_" + src.market: self._fetch_one(src, session, cache)
                for src in self._sources
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        raw: dict[str, list[str]] = {}
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, BaseException):
                if cache and key in cache:
                    log.warning(
                        "fetch failed for %s, using cached list (%d symbols): %s",
                        key, len(cache[key]), result,
                    )
                    raw[key] = cache[key]
                else:
                    raise RuntimeError(
                        f"fetch failed for {key} and no cache available: {result}"
                    ) from result
            else:
                raw[key] = result  # type: ignore[assignment]

        return raw

    async def _fetch_one(
        self,
        source: Any,
        session: aiohttp.ClientSession,
        cache: dict | None,
    ) -> list[str]:
        key = source.exchange + "_" + source.market
        try:
            symbols = await source.fetch_symbols(session)
            log.debug("fetched %d raw symbols from %s", len(symbols), key)
            return symbols
        except Exception as exc:
            log.warning("error fetching %s: %s", key, exc)
            raise

    def _save_cache(self, lists: SubscriptionLists) -> None:
        os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        tmp = self._cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(lists, f, default_flow_style=False, allow_unicode=True)
        os.rename(tmp, self._cache_path)
        log.debug("cache saved to %s", self._cache_path)

    def _load_cache(self) -> dict | None:
        if not os.path.exists(self._cache_path):
            return None
        try:
            with open(self._cache_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            log.debug("cache loaded from %s", self._cache_path)
            return data or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load cache: %s", exc)
            return None


async def run_discovery(config: dict[str, Any]) -> SubscriptionLists:
    """
    Convenience wrapper: create runner, fetch once, return lists.

    Use this for one-shot symbol discovery at system startup.
    For the long-running periodic task, use ``DiscoveryRunner.run_forever()``.
    """
    runner = DiscoveryRunner(config)
    return await runner.run_once()
