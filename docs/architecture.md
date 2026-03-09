# Crypto Spread Monitor — Architecture Overview

Version: 1.1 | Source: `base1.pdf`

---

## Purpose

The Crypto Spread Monitor (CSM) calculates real-time bid-ask spreads between **spot** and **futures** markets across **Binance** and **Bybit**. The primary metric is the percentage spread between the best futures ask and best spot bid for each trading pair:

```
spread = (best_ask_futures − best_bid_spot) / best_bid_spot × 100%
```

Negative spreads (backwardation) are valid and are not clipped.

---

## Data Flow

```
┌──────────────────────────────────────────────────────────┐
│  Symbol Discovery                                        │
│  Binance REST + Bybit REST  →  unified subscription list │
└────────────────────────┬─────────────────────────────────┘
                         │  cache/subscription_lists.yaml
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Collectors  (4 WebSocket connections)                   │
│  Binance Spot │ Binance Futures │ Bybit Spot │ Bybit Fut  │
└────────────────────────┬─────────────────────────────────┘
                         │  raw JSON  (asyncio.Queue)
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Normalizer                                              │
│  parse → validate → Quote dataclass                      │
└────────────────────────┬─────────────────────────────────┘
                         │  Quote
                         ▼
┌──────────────────────────────────────────────────────────┐
│  POSIX Shared Memory  /csm_quotes_v1                     │
│  Seqlock slot table  (MAX_SLOTS slots × SLOT_SIZE bytes) │
└────────────────────────┬─────────────────────────────────┘
                         │  mmap read
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Spread Reader                                           │
│  Read all slots → match spot/futures pairs →             │
│  calculate spread → write atomic snapshot file           │
└──────────────────────────────────────────────────────────┘
```

---

## Components

### Symbol Discovery (`symbol_discovery/`)

Fetches active trading pairs from exchange REST APIs, normalises symbols to a unified `BASE-QUOTE` format (e.g. `BTC-USDT`), computes the intersection of pairs available on both spot and futures, and saves the result to `cache/subscription_lists.yaml`.

A pair is included only if it exists in **spot AND futures** of at least one exchange.

Refreshes on a configurable interval (default: every hour).

### Collectors (`collectors/`)

Four asyncio tasks maintain persistent WebSocket connections:

| Collector | Feed | Stream |
|-----------|------|--------|
| BinanceSpotCollector | Binance Spot | `bookTicker` |
| BinanceFuturesCollector | Binance USDT-M Futures | `bookTicker` |
| BybitSpotCollector | Bybit Spot | `orderbook.1` |
| BybitFuturesCollector | Bybit Linear Futures | `orderbook.1` |

Each collector implements exponential-backoff reconnection and forwards raw JSON messages to the normalizer via `asyncio.Queue`.

### Normalizer (`normalizer/`)

Parses raw exchange messages into the canonical `Quote` dataclass:

```python
@dataclass
class Quote:
    exchange: str          # "binance" | "bybit"
    market_type: str       # "spot" | "futures"
    unified_symbol: str    # "BTC-USDT"
    bid: float
    ask: float
    ts_exchange_ns: int    # exchange-side timestamp (nanoseconds)
    ts_recv_ns: int        # local receive timestamp (nanoseconds)
```

Validation rules: `bid > 0`, `ask > 0`, `bid <= ask`, all fields non-null.

### Shared Memory (`shm/`)

A POSIX shared memory segment (`/csm_quotes_v1`) stores the latest `Quote` for every active symbol. The layout uses a **seqlock** protocol for wait-free concurrent reads.

**Slot layout (76 bytes):**

| Field | Size | Description |
|-------|------|-------------|
| `seq_begin` | 8 B | Odd during write, even when stable |
| `bid` | 8 B | Best bid (IEEE 754 double) |
| `ask` | 8 B | Best ask |
| `ts_ns` | 8 B | Exchange timestamp (ns) |
| `symbol` | 32 B | Unified symbol, null-padded UTF-8 |
| `exchange` | 8 B | Exchange id, null-padded |
| `market` | 4 B | `spot` or `fut`, null-padded |
| `seq_end` | 8 B | Must equal `seq_begin` for valid read |

**Seqlock write protocol:**
1. `seq_begin += 1` (now odd — signals write in progress)
2. Memory barrier
3. Write all fields
4. Memory barrier
5. `seq_end = seq_begin` (now even — write complete)

**Seqlock read protocol:**
1. Spin until `seq_begin` is even
2. Read all fields
3. Check `seq_end == seq_begin` (retry if not equal)

### Spread Reader (`spread_reader/`)

Reads all active SHM slots every `snapshot_interval_s` seconds, pairs spot and futures quotes by unified symbol, validates staleness, and writes atomic snapshot files.

**Snapshot file format (`spread_snapshot_<ISO8601>.txt`):**

```
# Generated: 2026-03-09T12:00:00Z  Slots: 1250
SYMBOL       | SPREAD%  | ASK_FUT  | BID_SPOT | FUT_EXCH | SPOT_EXCH | STALE
BTC-USDT     |   0.021  | 84123.10 | 84105.40 | binance  | binance   | false
ETH-USDT     |  -0.003  |  3201.55 |  3201.65 | bybit    | binance   | false
XYZ-USDT     |   N/A    |   N/A    |   N/A    | N/A      | N/A       | true
```

Atomic write: temp file → `os.rename()` (POSIX-atomic).

### Infra (`infra/`)

- **`logging_config.py`** — structured JSON logging via `structlog`, per-component levels, daily file rotation
- **`metrics.py`** (Phase 7) — Prometheus counters, histograms, gauges
- **`health_check.py`** (Phase 7) — SHM freshness + collector liveness

---

## Exchange Coverage

| Exchange | Spot | Futures |
|----------|------|---------|
| Binance | ✓ | USDT-M Perpetual |
| Bybit | ✓ | Linear Perpetual |

Only `quote_currency: USDT` pairs are monitored (configurable).

---

## Phase Roadmap

| Phase | Module | Status |
|-------|--------|--------|
| 0 | Infrastructure | ✅ Done |
| 1 | Symbol Discovery | ⬜ Pending |
| 2 | Normalizer Schema | ⬜ Pending |
| 3 | SHM Table | ⬜ Pending |
| 4 | Collectors | ⬜ Pending |
| 5 | Normalizer→SHM Pipeline | ⬜ Pending |
| 6 | Spread Reader | ⬜ Pending |
| 7 | Observability | ⬜ Pending |
| 8 | Integration Tests | ⬜ Pending |
