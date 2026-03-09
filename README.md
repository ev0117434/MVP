# Crypto Spread Monitor (CSM)

Real-time spread monitor between **spot** and **futures** markets on **Binance** and **Bybit**.

The primary metric is the percentage spread between the best futures ask and the best spot bid for each USDT-quoted trading pair:

```
spread = (best_ask_futures − best_bid_spot) / best_bid_spot × 100%
```

Negative values (backwardation) are valid and are not clipped.

---

## Current Status

| Phase | Module | Status |
|-------|--------|--------|
| 0 | Infrastructure — directories, config, logging, Makefile | ✅ Done |
| 1 | Symbol Discovery — REST fetchers, normaliser, subscription lists | ✅ Done |
| 2 | Normalizer Schema — Quote dataclass, WS parsers, validation | ✅ Done |
| 3 | SHM Table — seqlock writer/reader, init/cleanup, POSIX mmap | ✅ Done |
| 3 | SHM Table | ⬜ Pending |
| 4 | Collectors | ⬜ Pending |
| 5 | Normalizer → SHM Pipeline | ⬜ Pending |
| 6 | Spread Reader | ⬜ Pending |
| 7 | Observability | ⬜ Pending |
| 8 | Integration Tests | ⬜ Pending |

---

## Architecture

```
Symbol Discovery  ──REST──►  cache/subscription_lists.yaml
                                           │
                          ┌────────────────┼────────────────┐
                          ▼                ▼                 ▼
                   Binance Spot    Binance Futures    Bybit Spot   Bybit Futures
                   Collector       Collector          Collector    Collector
                          │                │                 │          │
                          └────────────────┴────────────────┴──────────┘
                                           │  raw JSON  →  asyncio.Queue
                                           ▼
                                       Normalizer
                                     (Quote dataclass)
                                           │
                                           ▼
                             POSIX SHM  /csm_quotes_v1
                           (seqlock slot table, 2048 slots)
                                           │
                                           ▼
                                     Spread Reader
                              match spot/futures → spread%
                              → snapshots/spread_snapshot_*.txt
```

---

## Requirements

- Python **3.11+**
- Linux (POSIX shared memory via `/dev/shm`)

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd MVP

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# — or install as a package (editable mode) —
pip install -e ".[dev]"
```

---

## Project Layout

```
MVP/
├── config/
│   └── config.yaml              # All runtime parameters
├── symbol_discovery/
│   ├── symbol_normalizer.py     # raw ↔ unified symbol conversion
│   ├── base_exchange_info.py    # abstract REST fetcher interface
│   ├── binance_exchange_info.py # BinanceSpotInfo, BinanceFuturesInfo
│   ├── bybit_exchange_info.py   # BybitSpotInfo, BybitFuturesInfo
│   ├── intersection.py          # compute_subscription_lists()
│   └── discovery_runner.py      # DiscoveryRunner orchestrator
├── collectors/                  # (Phase 4) WebSocket collectors
├── normalizer/                  # (Phase 2–5) Quote parsing
├── shm/                         # (Phase 3) POSIX SHM seqlock table
├── spread_reader/               # (Phase 6) Spread calculation + snapshots
├── infra/
│   └── logging_config.py        # Structured JSON logging (structlog)
├── cache/                       # Auto-generated: subscription_lists.yaml
├── logs/                        # Auto-generated: csm.log (daily rotation)
├── snapshots/                   # Auto-generated: spread_snapshot_*.txt
├── docs/
│   ├── architecture.md
│   ├── configuration.md
│   ├── phase-0-infrastructure.md
│   └── phase-1-symbol-discovery.md
├── Makefile
├── requirements.txt
└── pyproject.toml
```

---

## Configuration

All parameters are in `config/config.yaml`. The file is loaded once at startup:

```python
import yaml
config = yaml.safe_load(open("config/config.yaml"))
```

### Key parameters

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `shm` | `name` | `/csm_quotes_v1` | POSIX SHM segment name |
| `shm` | `max_slots` | `2048` | Max concurrent quote slots |
| `shm` | `slot_size` | `76` | Slot size in bytes (seqlock layout) |
| `timing` | `staleness_threshold_ms` | `2000` | Quotes older than this are marked stale |
| `timing` | `snapshot_interval_s` | `5` | How often to write spread snapshots |
| `timing` | `discovery_refresh_interval_s` | `3600` | Symbol list refresh interval (0 = once only) |
| `paths` | `cache_dir` | `cache` | Directory for `subscription_lists.yaml` |
| `paths` | `snapshot_dir` | `snapshots` | Directory for spread snapshot files |
| `paths` | `log_dir` | `logs` | Directory for rotating log files |
| *(root)* | `quote_currency` | `USDT` | Only pairs quoted in this currency are monitored |

Per-component log levels can be overridden under `logging.levels`. See [docs/configuration.md](docs/configuration.md) for the full reference.

---

## Makefile Commands

```
make help        — Show all available commands

make init-shm    — Create and initialise the POSIX SHM segment
make run         — Start all components in the background (writes .csm.pid)
make stop        — Send SIGTERM to the running process
make clean-shm   — Remove the POSIX SHM segment
make snapshot    — Write a single spread snapshot and exit
```

### Typical startup sequence (once all phases are implemented)

```bash
make init-shm   # allocate shared memory
make run        # start symbol discovery + collectors + spread reader
# ... wait for data ...
make snapshot   # inspect current spreads
make stop       # graceful shutdown
make clean-shm  # release shared memory
```

---

## Phase 0 — Infrastructure

Sets up the skeleton used by every other component.

**What was done:**
- `config/config.yaml` — single source of truth for all parameters
- `infra/logging_config.py` — structured logging (JSON in production, coloured console in TTY)
- `Makefile` — `init-shm`, `run`, `stop`, `clean-shm`, `snapshot`
- Directory tree with `.gitkeep` files (`cache/`, `logs/`, `snapshots/`)

**Using the logger in your code:**

```python
import yaml
from infra.logging_config import setup_logging

config = yaml.safe_load(open("config/config.yaml"))
log = setup_logging("symbol_discovery", config["logging"], config["paths"]["log_dir"])

log.info("started", exchange="binance", market="spot")
log.warning("fetch failed", error=str(exc))
```

Log output format:
- **TTY / terminal** → coloured human-readable (structlog `ConsoleRenderer`)
- **File / pipe** → one JSON object per line (structlog `JSONRenderer`)

Logs rotate daily, keeping the last 7 days (`logs/csm.log`, `logs/csm.log.2026-03-08`, …).

---

## Phase 1 — Symbol Discovery

Determines which symbols to subscribe to before any WebSocket is opened.

### How it works

1. Calls four REST endpoints concurrently (`asyncio.gather`):

   | Source | URL | Active filter |
   |--------|-----|---------------|
   | Binance Spot | `GET /api/v3/exchangeInfo` | `status == "TRADING"` |
   | Binance USDT-M Futures | `GET /fapi/v1/exchangeInfo` | `status == "TRADING"` + `contractType == "PERPETUAL"` |
   | Bybit Spot | `GET /v5/market/instruments-info?category=spot` | `status == "Trading"` |
   | Bybit Linear Futures | `GET /v5/market/instruments-info?category=linear` | `status == "Trading"` + `contractType == "LinearPerpetual"` |

2. Normalises all raw symbols (`BTCUSDT`) to unified format (`BTC-USDT`), filtering by `quote_currency`.

3. Computes the inclusion set — a symbol is kept only if it is available on **both spot AND futures** of at least one exchange:

   ```
   valid = (binance_spot ∩ binance_futures) ∪ (bybit_spot ∩ bybit_futures)
   ```

4. Saves the result to `cache/subscription_lists.yaml` (atomic write).

5. Refreshes on the interval set by `timing.discovery_refresh_interval_s`.

### Failure behaviour

| Scenario | Behaviour |
|----------|-----------|
| All sources reachable | Fresh data used |
| Some sources fail, cache exists | Cached lists used for failed sources, `WARNING` logged |
| Any source fails, no cache | `RuntimeError` raised — startup aborts |

### Symbol format

All exchanges use the same raw format — base and quote concatenated with no separator:

```
BTCUSDT  →  BTC-USDT
1000SHIBUSDT  →  1000SHIB-USDT
ETHBTC   →  filtered out (not USDT)
```

### Using Symbol Discovery standalone

**One-shot fetch** (e.g. at startup or for inspection):

```python
import asyncio, yaml
from symbol_discovery.discovery_runner import run_discovery

config = yaml.safe_load(open("config/config.yaml"))
lists = asyncio.run(run_discovery(config))

print(f"Binance spot:    {len(lists['binance_spot'])} symbols")
print(f"Binance futures: {len(lists['binance_futures'])} symbols")
print(f"Bybit spot:      {len(lists['bybit_spot'])} symbols")
print(f"Bybit futures:   {len(lists['bybit_futures'])} symbols")
print(lists["binance_spot"][:5])
# ['1000BONKUSDT', '1000LUNCUSDT', '1000PEPEUSDT', '1000SHIBUSDT', '10000NFTUSDT']
```

**Background task** (long-running process):

```python
import asyncio, yaml
from symbol_discovery.discovery_runner import DiscoveryRunner

config = yaml.safe_load(open("config/config.yaml"))
runner = DiscoveryRunner(config)

async def main():
    task = asyncio.create_task(runner.run_forever())
    # runner.subscription_lists is updated every discovery_refresh_interval_s
    await task

asyncio.run(main())
```

**Normaliser utilities** (used internally, also useful for testing):

```python
from symbol_discovery.symbol_normalizer import raw_to_unified, unified_to_raw

raw_to_unified("BTCUSDT")         # "BTC-USDT"
raw_to_unified("ETHBTC")          # None  (filtered — not USDT)
unified_to_raw("BTC-USDT")        # "BTCUSDT"
```

**Compute subscription lists from custom raw data:**

```python
from symbol_discovery.intersection import compute_subscription_lists

lists = compute_subscription_lists(
    binance_spot_raw=["BTCUSDT", "ETHUSDT"],
    binance_futures_raw=["BTCUSDT"],
    bybit_spot_raw=["BTCUSDT", "SOLUSDT"],
    bybit_futures_raw=["BTCUSDT", "SOLUSDT"],
)
# lists["binance_spot"] == ["BTCUSDT", "ETHUSDT"]  — ETHUSDT kept: Binance has both
# lists["bybit_spot"]   == ["BTCUSDT", "SOLUSDT"]  — SOLUSDT kept: Bybit has both
```

### Cache file

`cache/subscription_lists.yaml` is written after every successful fetch:

```yaml
binance_futures:
- BTCUSDT
- ETHUSDT
- SOLUSDT
# ...
binance_spot:
- BTCUSDT
- ETHUSDT
# ...
bybit_futures:
- BTCUSDT
- SOLUSDT
# ...
bybit_spot:
- BTCUSDT
- SOLUSDT
# ...
```

---

## Phase 2 — Normalizer Schema

Defines the `Quote` dataclass — the canonical data contract between all components — and implements four WebSocket message parsers (one per exchange/market).

**Files:**
- `normalizer/schema.py` — `Quote`, `validate_quote()`, `QuoteValidationError`
- `normalizer/normalizer.py` — parsers + `PARSERS` dispatch table

### `Quote` dataclass

```python
from normalizer.schema import Quote

q = Quote(
    exchange="binance",
    market_type="futures",
    unified_symbol="BTC-USDT",
    bid=84000.0,
    ask=84001.5,
    ts_exchange_ns=1700000000000 * 1_000_000,
    ts_recv_ns=1700000000001 * 1_000_000,
)
print(q.effective_ts_ns)  # ts_exchange_ns (non-zero)
print(q.age_ms)           # ms since effective timestamp
print(q.is_valid())       # True
```

### Parsing a WebSocket message

```python
import time
from normalizer.normalizer import parse_binance_futures, parse_bybit_spot
from normalizer.schema import QuoteValidationError

# Binance USDT-M Futures bookTicker
msg = {"e": "bookTicker", "E": 1700000000000, "s": "BTCUSDT",
       "b": "84000.00", "a": "84001.50"}

try:
    quote = parse_binance_futures(msg, ts_recv_ns=time.time_ns())
    if quote:                   # None = heartbeat / ping / empty delta
        print(quote)            # → Quote(binance/futures BTC-USDT bid=84000.0 ask=84001.5)
except QuoteValidationError as e:
    print(f"Rejected: {e}")     # crossed book, zero price, etc.
```

```python
# Bybit Spot orderbook.1
msg = {
    "topic": "orderbook.1.BTCUSDT", "type": "snapshot", "ts": 1700000000000,
    "data": {"s": "BTCUSDT", "b": [["83999.50", "0.01"]], "a": [["84000.00", "0.02"]]}
}
quote = parse_bybit_spot(msg, ts_recv_ns=time.time_ns())
```

### Dispatch table (for Phase 5 pipeline)

```python
from normalizer.normalizer import PARSERS
import time

parser = PARSERS[("bybit", "futures")]
quote  = parser(raw_msg, ts_recv_ns=time.time_ns())
```

### Parser return semantics

| Return | Meaning |
|--------|---------|
| `Quote` | Valid quote — write to SHM |
| `None` | Not a quote (ping, pong, ACK, empty delta) — discard silently |
| raises `QuoteValidationError` | Quote-shaped but invalid — log + discard |

### Exchange timestamp availability

| Source | `ts_exchange_ns` | Notes |
|--------|:---:|---|
| Binance Spot bookTicker | ❌ `0` | Stream carries no timestamp |
| Binance Futures bookTicker | ✅ | Field `E` (ms → ns) |
| Bybit Spot orderbook.1 | ✅ | Field `ts` (ms → ns) |
| Bybit Futures orderbook.1 | ✅ | Field `ts` (ms → ns) |

When `ts_exchange_ns == 0`, use `quote.effective_ts_ns` which falls back to `ts_recv_ns`.

---

## Phase 3 — SHM Table

The POSIX shared-memory segment is the core inter-process transport between the normalizer (writer) and the spread reader.  It holds up to 2 048 quote slots and uses a **seqlock** for lock-free reads.

**Files:**
- `shm/shm_layout.py` — constants, offsets, struct formats (single source of truth)
- `shm/shm_init.py` — open or create the SHM segment, MAGIC check
- `shm/shm_writer.py` — seqlock writer + slot allocation
- `shm/shm_reader.py` — seqlock reader + `SlotData` dataclass
- `shm/shm_cleaner.py` — cleanup on shutdown, signal handlers

### Quick start

```bash
# Initialise the SHM segment (creates /dev/shm/csm_quotes_v1)
make init-shm

# Remove it
make clean-shm
```

### Round-trip example

```python
import time
from shm.shm_init    import open_or_create_shm
from shm.shm_writer  import ShmWriter
from shm.shm_reader  import ShmReader
from shm.shm_cleaner import cleanup_shm, register_cleanup
from normalizer.schema import Quote

shm, buf = open_or_create_shm("/csm_quotes_v1", max_slots=2048)
register_cleanup("/csm_quotes_v1", buf, shm)  # SIGINT/SIGTERM/atexit

writer = ShmWriter(buf, max_slots=2048)
reader = ShmReader(buf, max_slots=2048)

q = Quote("binance", "futures", "BTC-USDT",
          bid=84000.0, ask=84001.5,
          ts_exchange_ns=time.time_ns(), ts_recv_ns=time.time_ns())

writer.write(q)                    # True
slot = reader.read_slot(0)
print(slot.unified_symbol)         # BTC-USDT
print(slot.is_stale(2000))         # False (< 2 s old)
print(writer.slots_used)           # 1
```

### Slot layout (84 bytes)

```
Offset  Size  Field
     0     8  seq_begin   seqlock write-start counter
     8     8  bid         IEEE 754 double
    16     8  ask         IEEE 754 double
    24     8  ts_ns       nanoseconds (Quote.effective_ts_ns)
    32    32  symbol      null-padded ASCII  "BTC-USDT"
    64     8  exchange    null-padded ASCII  "binance"
    72     4  market      integer  0=spot 1=futures
    76     8  seq_end     seqlock write-end counter
```

Stable read: `seq_begin == seq_end != 0`.  Write in progress: `seq_begin != seq_end`.

---

## Detailed Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | Full architecture, data flow, component descriptions, SHM slot layout |
| [docs/configuration.md](docs/configuration.md) | All `config.yaml` parameters with types, defaults, and descriptions |
| [docs/phase-0-infrastructure.md](docs/phase-0-infrastructure.md) | Phase 0 decisions and rationale |
| [docs/phase-1-symbol-discovery.md](docs/phase-1-symbol-discovery.md) | REST API details, symbol formats, intersection logic, open decisions |
| [docs/phase-2-normalizer.md](docs/phase-2-normalizer.md) | WS wire formats, parser design, Quote invariants, design decisions |
| [docs/phase-3-shm.md](docs/phase-3-shm.md) | SHM layout, seqlock protocol, module API, design decisions |

---

## Development

```bash
# Run tests (once test suite is in place)
pytest

# Run tests with asyncio support
pytest --asyncio-mode=auto
```

Dependencies are declared in both `requirements.txt` (plain pip) and `pyproject.toml` (PEP 517). Use whichever suits your workflow.
