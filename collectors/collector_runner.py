"""
CollectorRunner — launches all four WebSocket collectors concurrently.

Usage (from main.py or standalone):

    import asyncio, yaml
    from collectors.collector_runner import CollectorRunner

    cfg = yaml.safe_load(open("config/config.yaml"))
    queue = asyncio.Queue()
    runner = CollectorRunner.from_cache(queue, cfg)
    asyncio.run(runner.run())

Standalone (for manual testing):

    python -m collectors.collector_runner --config config/config.yaml
"""

import asyncio
import os

import yaml

from collectors.binance_futures_collector import BinanceFuturesCollector
from collectors.binance_spot_collector import BinanceSpotCollector
from collectors.bybit_futures_collector import BybitFuturesCollector
from collectors.bybit_spot_collector import BybitSpotCollector
from infra.logging_config import setup_logging


class CollectorRunner:
    """
    Holds four collectors and runs them concurrently via asyncio.gather.

    Parameters
    ----------
    subscription_lists:
        Dict with keys ``binance_spot``, ``binance_futures``,
        ``bybit_spot``, ``bybit_futures`` → list[str] of unified symbols.
    queue:
        Shared asyncio.Queue for raw messages.
        Element: (exchange, market_type, raw_msg, ts_recv_ns)
    cfg:
        Full config dict (from config.yaml).
    """

    def __init__(
        self,
        subscription_lists: dict[str, list[str]],
        queue: asyncio.Queue,
        cfg: dict,
    ) -> None:
        self._collectors = [
            BinanceSpotCollector(subscription_lists["binance_spot"], queue, cfg),
            BinanceFuturesCollector(subscription_lists["binance_futures"], queue, cfg),
            BybitSpotCollector(subscription_lists["bybit_spot"], queue, cfg),
            BybitFuturesCollector(subscription_lists["bybit_futures"], queue, cfg),
        ]
        logging_cfg = cfg.get("logging", {})
        log_dir = cfg.get("paths", {}).get("log_dir", "logs")
        self.log = setup_logging("collectors", logging_cfg, log_dir).bind(
            component="collector_runner"
        )

    @classmethod
    def from_cache(
        cls,
        queue: asyncio.Queue,
        cfg: dict,
    ) -> "CollectorRunner":
        """
        Load subscription lists from ``cache/subscription_lists.yaml``
        (written by Phase 1 DiscoveryRunner) and build a CollectorRunner.
        """
        cache_dir = cfg.get("paths", {}).get("cache_dir", "cache")
        cache_path = os.path.join(cache_dir, "subscription_lists.yaml")
        with open(cache_path, encoding="utf-8") as f:
            subscription_lists = yaml.safe_load(f)
        return cls(subscription_lists, queue, cfg)

    async def run(self) -> None:
        """Start all collectors. Runs indefinitely; each handles its own reconnect."""
        self.log.info(
            "starting_collectors",
            count=len(self._collectors),
            feeds=[f"{c.exchange}/{c.market_type}" for c in self._collectors],
        )
        await asyncio.gather(*[c.run() for c in self._collectors])


# ---------------------------------------------------------------------------
# Standalone entry point — for manual testing / smoke test
# ---------------------------------------------------------------------------

async def _main(config_path: str) -> None:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    queue: asyncio.Queue = asyncio.Queue()
    runner = CollectorRunner.from_cache(queue, cfg)

    # Consumer: print first 20 messages then exit
    async def consumer():
        for i in range(20):
            exchange, market, msg, ts_ns = await queue.get()
            print(f"[{i+1:02d}] {exchange}/{market}: {str(msg)[:120]}")
        print("Received 20 messages — smoke test passed.")

    await asyncio.gather(runner.run(), consumer())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run collectors smoke test")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()
    asyncio.run(_main(args.config))
