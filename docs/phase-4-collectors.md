# Phase 4 — Collectors

## Overview

Phase 4 implements four persistent WebSocket collectors that receive real-time
best bid/ask quotes from Binance and Bybit, then forward raw JSON messages to
the normalizer pipeline via a shared `asyncio.Queue`.

```
Symbol Discovery (Phase 1)
    └─ cache/subscription_lists.yaml
            │
            ▼
    CollectorRunner (Phase 4)
    ├─ BinanceSpotCollector
    ├─ BinanceFuturesCollector
    ├─ BybitSpotCollector
    └─ BybitFuturesCollector
            │
            ▼ asyncio.Queue
    Normalizer pipeline (Phase 5)
            │
            ▼
    SHM Table (Phase 3)
```

---

## WebSocket Feeds

### Binance bookTicker

| Market | Endpoint |
|--------|----------|
| Spot | `wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker/...` |
| USDT-M Futures | `wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/...` |

**Subscription model:** symbols are encoded directly in the URL (combined stream).
No subscription message is sent after connection.

**Wire format:**
```json
{
  "stream": "btcusdt@bookTicker",
  "data": {
    "u": 123456,
    "s": "BTCUSDT",
    "b": "84000.00",
    "B": "0.500",
    "a": "84001.50",
    "A": "1.000"
  }
}
```
Futures messages additionally include `"e":"bookTicker"` and `"E":<timestamp_ms>` inside `data`.

**Keepalive:** The server sends WebSocket ping frames periodically. The
`websockets` library responds with pong frames automatically — no application-level
ping is needed.

**Limits:** Up to 1024 streams per connection.

---

### Bybit orderbook.1

| Market | Endpoint |
|--------|----------|
| Spot | `wss://stream.bybit.com/v5/public/spot` |
| Linear Futures | `wss://stream.bybit.com/v5/public/linear` |

**Subscription message** (sent once after connection):
```json
{"op": "subscribe", "args": ["orderbook.1.BTCUSDT", "orderbook.1.ETHUSDT"]}
```

**ACK** (filtered out, not forwarded to queue):
```json
{"success": true, "op": "subscribe"}
```

**Wire format — snapshot** (first message per symbol):
```json
{
  "topic": "orderbook.1.BTCUSDT",
  "type": "snapshot",
  "ts": 1700000000000,
  "data": {
    "s": "BTCUSDT",
    "b": [["83999.50", "0.01"]],
    "a": [["84000.00", "0.02"]]
  }
}
```

**Wire format — delta** (subsequent updates; `b` or `a` may be empty):
```json
{"topic": "orderbook.1.BTCUSDT", "type": "delta", "ts": ..., "data": {"s":"BTCUSDT","b":[],"a":[["84001.0","0.01"]]}}
```

Deltas with an empty side are returned as `None` by the Phase 2 parser and
silently discarded.

**Keepalive:** Client must send `{"op":"ping"}` every **20 seconds**.
The server responds with `{"op":"pong"}`. If the server receives no ping for
too long it closes the connection; the reconnect loop re-establishes it.

---

## Transport: asyncio.Queue

**Choice:** `asyncio.Queue(maxsize=0)` (unbounded, single process).

**Queue element:**
```python
(exchange: str, market_type: str, raw_msg: dict, ts_recv_ns: int)
```

**Rationale:**

| Option | Pros | Cons |
|--------|------|------|
| `asyncio.Queue` | Zero serialisation, no IPC, native async | Single-process only |
| POSIX pipe | Cross-process | JSON serialisation overhead |
| Redis | Durable, multi-consumer | External dependency |

All four collectors and the normalizer run in the same asyncio event loop,
so `asyncio.Queue` is the lowest-overhead choice with no added dependencies.

---

## Reconnection Strategy

`ExponentialBackoff(base=1.0, cap=60.0)` in `BaseCollector`:

| Attempt | Delay |
|---------|-------|
| 1 | 1 s |
| 2 | 2 s |
| 3 | 4 s |
| 4 | 8 s |
| 5 | 16 s |
| 6 | 32 s |
| 7+ | 60 s (capped) |

On successful connection the counter resets to 0. Every reconnect attempt is
logged at `WARNING` level with the error and the delay.

---

## Module Reference

### `collectors/base_collector.py`

| Name | Type | Description |
|------|------|-------------|
| `ExponentialBackoff` | class | Backoff with `next()` / `reset()` |
| `BaseCollector` | ABC | Abstract collector; subclass must implement `ws_url`, `subscribe`, `keepalive`, `is_ack` |
| `BaseCollector.run()` | coroutine | Infinite reconnect loop |
| `BaseCollector._receive_loop()` | coroutine | Read loop with concurrent keepalive task |

### `collectors/binance_spot_collector.py`

`BinanceSpotCollector(symbols, queue, cfg)` — Binance Spot bookTicker.

### `collectors/binance_futures_collector.py`

`BinanceFuturesCollector(symbols, queue, cfg)` — Binance USDT-M Futures bookTicker.

### `collectors/bybit_spot_collector.py`

`BybitSpotCollector(symbols, queue, cfg)` — Bybit Spot orderbook.1 with 20 s ping.

### `collectors/bybit_futures_collector.py`

`BybitFuturesCollector(symbols, queue, cfg)` — Bybit Linear Futures orderbook.1.

### `collectors/collector_runner.py`

| Name | Description |
|------|-------------|
| `CollectorRunner(subscription_lists, queue, cfg)` | Holds 4 collectors |
| `CollectorRunner.from_cache(queue, cfg)` | Load subscription lists from `cache/subscription_lists.yaml` |
| `CollectorRunner.run()` | `asyncio.gather` all 4 collectors |

---

## Smoke Test (real network)

```bash
# 1. Ensure subscription lists exist (run Phase 1 first if not):
python3 -c "
import asyncio, yaml
from symbol_discovery.discovery_runner import DiscoveryRunner
cfg = yaml.safe_load(open('config/config.yaml'))
asyncio.run(DiscoveryRunner(cfg).run_once())
"

# 2. Run collector smoke test — prints first 20 messages and exits:
python3 -m collectors.collector_runner --config config/config.yaml
```

Expected output (abbreviated):
```
[01] binance/spot: {'stream': 'btcusdt@bookTicker', 'data': {'s': 'BTCUSDT', ...}}
[02] bybit/futures: {'topic': 'orderbook.1.BTCUSDT', 'type': 'snapshot', ...}
...
Received 20 messages — smoke test passed.
```

---

## Unit Tests (no network required)

```python
# ExponentialBackoff
from collectors.base_collector import ExponentialBackoff
b = ExponentialBackoff(base=1.0, cap=60.0)
assert b.next() == 1.0
assert b.next() == 2.0
assert b.next() == 4.0
b.reset()
assert b.next() == 1.0

# Binance Spot URL
import asyncio
from collectors.binance_spot_collector import BinanceSpotCollector
c = BinanceSpotCollector(["BTC-USDT", "ETH-USDT"], asyncio.Queue(), {})
assert "btcusdt@bookTicker" in c.ws_url
assert "stream.binance.com" in c.ws_url

# Bybit ACK filter
from collectors.bybit_spot_collector import BybitSpotCollector
c = BybitSpotCollector(["BTC-USDT"], asyncio.Queue(), {})
assert c.is_ack({"op": "subscribe"})
assert c.is_ack({"op": "pong"})
assert not c.is_ack({"topic": "orderbook.1.BTCUSDT"})

# CollectorRunner instantiation
from collectors.collector_runner import CollectorRunner
q = asyncio.Queue()
lists = {k: ["BTC-USDT"] for k in ["binance_spot","binance_futures","bybit_spot","bybit_futures"]}
runner = CollectorRunner(lists, q, {})
assert len(runner._collectors) == 4
print("All unit tests passed")
```
