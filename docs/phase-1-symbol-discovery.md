# Phase 1 — Symbol Discovery

This document covers T06–T14: researching exchange APIs, implementing symbol normalisation, and building the subscription list pipeline.

---

## Overview

Symbol Discovery is the first component to run at system startup. It:

1. Calls REST APIs on four exchange/market combinations
2. Normalises all symbols to a unified `BASE-QUOTE` format
3. Computes the intersection of symbols available on both spot and futures
4. Persists the result to `cache/subscription_lists.yaml`
5. Refreshes periodically in the background

The output — `subscription_lists` — is consumed by Phase 4 (Collectors) to determine which WebSocket streams to subscribe to.

---

## REST API Endpoints (T06)

### Binance

| Market | Endpoint | Activity field | Notes |
|--------|----------|---------------|-------|
| Spot | `GET https://api.binance.com/api/v3/exchangeInfo` | `symbol.status == "TRADING"` | All symbols in one response |
| USDT-M Futures | `GET https://fapi.binance.com/fapi/v1/exchangeInfo` | `symbol.status == "TRADING"` AND `symbol.contractType == "PERPETUAL"` | Excludes delivery and non-perpetual contracts |

Both return a JSON object with a top-level `"symbols"` array. No authentication required. Rate weight: 10 per call (limit: 1200/min).

### Bybit

| Market | Endpoint | Activity field | Notes |
|--------|----------|---------------|-------|
| Spot | `GET https://api.bybit.com/v5/market/instruments-info?category=spot&limit=1000` | `instrument.status == "Trading"` | v5 unified API |
| Linear Futures | `GET https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000` | `instrument.status == "Trading"` AND `instrument.contractType == "LinearPerpetual"` | Excludes inverse and delivery |

Response structure: `{ "retCode": 0, "result": { "list": [...] } }`. `retCode != 0` signals a business-logic error.

---

## Symbol Formats (T07)

All four sources use the same raw format: base and quote concatenated without a separator.

| Source | Raw example | Unified |
|--------|-------------|---------|
| Binance Spot | `BTCUSDT` | `BTC-USDT` |
| Binance USDT-M | `BTCUSDT` | `BTC-USDT` |
| Bybit Spot | `BTCUSDT` | `BTC-USDT` |
| Bybit Linear | `BTCUSDT` | `BTC-USDT` |

**Edge cases:**

| Raw | Unified | Note |
|-----|---------|------|
| `1000SHIBUSDT` | `1000SHIB-USDT` | Rebased tokens |
| `BTCUSDT` | `BTC-USDT` | Standard |
| `ETHBTC` | `None` | Filtered out (not USDT) |

**Normalisation rules** (`symbol_normalizer.py`):

- `raw_to_unified(raw)`: strip the `USDT` suffix → `{base}-USDT`. Returns `None` if the symbol doesn't end with the quote currency.
- `unified_to_raw(unified)`: remove the `-` separator → `{base}{quote}`.

Both Binance USDT-M futures and Bybit Linear perpetuals use the same format as their spot counterparts — no `_PERP` or `_SWAP` suffix.

---

## Intersection Logic (T12)

A unified symbol is included in the subscription lists if it is tradeable on **both spot AND futures of at least one exchange**:

```
valid = (binance_spot ∩ binance_futures) ∪ (bybit_spot ∩ bybit_futures)
```

**Why this rule?** The spread reader needs a matching spot/futures pair for every symbol. If a symbol only exists on one side, there is no spread to calculate.

**Example:**

| Symbol | Binance Spot | Binance Fut | Bybit Spot | Bybit Fut | Included? |
|--------|:---:|:---:|:---:|:---:|:---:|
| BTC-USDT | ✓ | ✓ | ✓ | ✓ | ✓ (both exchanges) |
| XYZ-USDT | ✓ | ✗ | ✓ | ✓ | ✓ (Bybit has both) |
| ABC-USDT | ✓ | ✗ | ✓ | ✗ | ✗ (no exchange has both) |

The output subscription lists contain raw (exchange-native) symbols for each stream, preserving only valid unified symbols.

---

## Component Map

```
symbol_discovery/
├── __init__.py
├── symbol_normalizer.py      # T08 — raw ↔ unified conversion
├── base_exchange_info.py     # T09 — abstract base class
├── binance_exchange_info.py  # T10 — BinanceSpotInfo, BinanceFuturesInfo
├── bybit_exchange_info.py    # T11 — BybitSpotInfo, BybitFuturesInfo
├── intersection.py           # T12 — compute_subscription_lists()
└── discovery_runner.py       # T13 — DiscoveryRunner (orchestrator)
```

### Data flow

```
BinanceSpotInfo.fetch_symbols()   ─┐
BinanceFuturesInfo.fetch_symbols() ─┤
BybitSpotInfo.fetch_symbols()      ─┼─→  compute_subscription_lists()
BybitFuturesInfo.fetch_symbols()   ─┘         │
    (concurrent via asyncio.gather)            ▼
                                    cache/subscription_lists.yaml
                                               │
                                               ▼
                                    Phase 4: Collectors
```

---

## Open Decisions (T14)

### Volume filter
**Decision: No volume filter (for now).**

Rationale: volume data requires a separate REST call (e.g. `GET /api/v3/ticker/24hr`), which would double the startup time and double the API weight usage. The active symbol lists from `exchangeInfo` already exclude delisted and suspended pairs. A `min_volume_usdt_24h` threshold can be added to `config.yaml` in a future iteration if the symbol list grows too large.

### REST unavailability at startup
**Decision: Fallback to cache if available; fail-fast if no cache.**

- If all sources are reachable → use fresh data.
- If some sources fail but cache exists → use cached lists for failed sources, log WARNING.
- If any source fails and there is no cache at all → raise `RuntimeError` and abort startup. Operating with an incomplete symbol list is worse than not starting.

### Update frequency
**Decision: Configurable via `config.timing.discovery_refresh_interval_s` (default 3600 s).**

Exchange listings change rarely (a few new tokens per day at most). Hourly refreshes keep subscription lists current without hammering REST APIs. Set to `0` to disable periodic refresh.

### Symbol disappears from exchange
**Decision: Mark stale via `staleness_threshold_ms` — do not delete SHM slots.**

When a symbol is delisted, collectors stop receiving updates for it. The SHM slot goes stale and the spread reader writes `N/A` for it. Explicit SHM slot deletion would require a coordinated writer lock across the normalizer and spread reader — unnecessary complexity for a rare event. Slots are reused when a new symbol claims the same slot ID.

### Quote currency filtering
**Decision: Configurable via `config.quote_currency` (default "USDT").**

All `raw_to_unified()` calls pass `quote_currency` from config. Changing to `USDC` or another currency requires only a config change.

---

## Cache Format

`cache/subscription_lists.yaml`:

```yaml
binance_futures:
- BTCUSDT
- ETHUSDT
- ...
binance_spot:
- BTCUSDT
- ETHUSDT
- ...
bybit_futures:
- BTCUSDT
- ETHUSDT
- ...
bybit_spot:
- BTCUSDT
- ETHUSDT
- ...
```

Written atomically: temp file → `os.rename()`.

---

## Usage

One-shot (e.g. at startup):

```python
import asyncio, yaml
from symbol_discovery.discovery_runner import run_discovery

config = yaml.safe_load(open("config/config.yaml"))
lists = asyncio.run(run_discovery(config))
print(lists["binance_spot"][:5])
```

Long-running (background task):

```python
from symbol_discovery.discovery_runner import DiscoveryRunner

runner = DiscoveryRunner(config)
asyncio.create_task(runner.run_forever())
# Access current lists at any time via runner.subscription_lists
```
