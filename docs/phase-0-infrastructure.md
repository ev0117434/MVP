# Phase 0 — Infrastructure

This document describes what was set up in Phase 0 and the reasoning behind each decision.

---

## What Was Created

### Directory Structure (T01)

```
MVP/
├── symbol_discovery/   # Symbol discovery and normalisation
│   └── __init__.py
├── collectors/         # WebSocket collectors (one per exchange/market)
│   └── __init__.py
├── normalizer/         # Message parsing and Quote schema
│   └── __init__.py
├── shm/                # POSIX shared memory management
│   └── __init__.py
├── spread_reader/      # Spread calculation and snapshot generation
│   └── __init__.py
├── infra/              # Cross-cutting: logging, metrics, health checks
│   └── __init__.py
├── config/             # YAML configuration (not a Python package)
│   └── config.yaml
├── cache/              # Subscription list cache (gitkeep, runtime data)
│   └── .gitkeep
├── snapshots/          # Spread snapshot output files (runtime data)
│   └── .gitkeep
├── logs/               # Application logs (runtime data)
│   └── .gitkeep
└── docs/               # Project documentation
```

**Why `__init__.py` only for code packages?**
`config/`, `cache/`, `snapshots/`, and `logs/` are data directories — they are not imported as Python modules. Keeping them as plain directories avoids confusion and import errors.

**Why `.gitkeep`?**
Git does not track empty directories. `.gitkeep` files ensure the directory structure is preserved in the repository so the system can write files there on first run without needing `mkdir`.

---

### Dependencies (T02)

**`requirements.txt`** contains pinned minimum versions:

| Package | Role | Why this one |
|---------|------|--------------|
| `aiohttp` | Async HTTP client for REST API calls (symbol discovery) | Mature, asyncio-native |
| `websockets` | WebSocket client for exchange streams | Pure Python, asyncio-native, well-maintained |
| `pyyaml` | YAML config parsing | Standard library complement for YAML |
| `structlog` | Structured logging | JSON output by default, excellent asyncio support, per-key context binding |
| `posix_ipc` | POSIX shared memory (`shm_open`, `ftruncate`) | Thinner than writing ctypes calls manually; exposes the POSIX API directly |
| `prometheus_client` | Metrics exposition | Industry standard for Python Prometheus integration |

**Why `posix_ipc` instead of built-in `mmap`?**
Python's `mmap` module can map files but does not expose `shm_open` / `shm_unlink`. `posix_ipc.SharedMemory` wraps these syscalls directly, which is required for a named POSIX SHM segment (`/csm_quotes_v1`). The actual byte-level read/write still uses `mmap` on the resulting file descriptor.

**`pyproject.toml`** declares the same deps under `[project.dependencies]` so the project can be installed with `pip install -e .` for development. The `dev` extras add pytest for future test phases.

---

### Configuration (T03)

`config/config.yaml` is the single source of truth for all runtime parameters.

**Key decisions:**

- **`slot_size: 76`** — This value is derived directly from the seqlock layout defined in the architecture: `8 (seq_begin) + 8 (bid) + 8 (ask) + 8 (ts_ns) + 32 (symbol) + 8 (exchange) + 4 (market) + 8 (seq_end) = 76 bytes`. It is stored in config so it can be validated against the compiled layout constant at startup.

- **`max_slots: 2048`** — Conservative upper bound. At 76 bytes/slot the SHM segment is ~152 KB — trivially small. Binance alone has ~500 active USDT pairs; 2048 gives headroom for both exchanges × both markets.

- **`staleness_threshold_ms: 2000`** — Two seconds is long enough to survive momentary network blips but short enough that stale data is surfaced quickly in snapshots.

- **`discovery_refresh_interval_s: 3600`** — Exchange symbol lists change infrequently (new listings, delistings). Hourly refresh is sufficient and avoids hammering REST APIs.

- **`quote_currency: USDT`** — Only USDT-quoted pairs are monitored. This keeps the subscription list manageable and aligns with the most liquid market segment.

---

### Logging Configuration (T04)

`infra/logging_config.py` provides the `setup_logging(component, config)` function used by every module.

**Design choices:**

- **`structlog` over stdlib `logging`** — structlog makes it trivial to emit structured JSON events (`log.info("msg", key=value, ...)`). JSON logs are machine-parseable and integrate with log aggregators (ELK, Loki) without custom parsers.

- **Console vs file renderer** — On a TTY (development), `ConsoleRenderer` produces coloured, human-readable output. In production (piped / redirected), `JSONRenderer` emits newline-delimited JSON. Detection is automatic via `sys.stderr.isatty()`.

- **`TimedRotatingFileHandler` (daily, 7-day retention)** — Keeps the `logs/` directory bounded. The file handler captures DEBUG and above so nothing is lost even if the console level is INFO.

- **Per-component levels** — Each subsystem can have its own verbosity. For example, `collectors: DEBUG` enables detailed WebSocket frame logging without flooding the console with SHM write traces.

**Usage example:**

```python
import yaml
from infra.logging_config import setup_logging

with open("config/config.yaml") as f:
    cfg = yaml.safe_load(f)

log = setup_logging("collectors", cfg["logging"], log_dir=cfg["paths"]["log_dir"])
log.info("collector started", exchange="binance", market="spot", symbols=42)
```

---

### Makefile (T05)

The `Makefile` provides a standard operator interface regardless of how the system is deployed.

| Target | Command | Description |
|--------|---------|-------------|
| `init-shm` | `make init-shm` | Create and initialise the POSIX SHM segment. Run once before `make run`. |
| `run` | `make run` | Start the full system in the background. PID is saved to `.csm.pid`. |
| `stop` | `make stop` | Send SIGTERM to the running process (graceful shutdown). |
| `clean-shm` | `make clean-shm` | Remove the SHM segment (`shm_unlink`). Safe to run while stopped. |
| `snapshot` | `make snapshot` | Write a single spread snapshot and exit. Useful for debugging. |
| `help` | `make help` | Print available targets. |

**Typical workflow:**

```bash
pip install -r requirements.txt
make init-shm
make run
# ... observe snapshots/ ...
make stop
make clean-shm
```

---

## What Comes Next

Phase 0 is complete. Phases 1, 2, and 3 can now proceed **in parallel**:

- **Phase 1** — Symbol Discovery: fetch pairs from Binance/Bybit REST APIs
- **Phase 2** — Normalizer schema: define the `Quote` dataclass and parsers
- **Phase 3** — SHM table: implement seqlock layout, writer, and reader

See [architecture.md](architecture.md) for the full dependency graph.
