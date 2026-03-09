# Phase 2 — Normalizer Schema

This document covers T15–T17: the `Quote` dataclass, WebSocket wire formats for all four feeds, and the parser implementations.

---

## Overview

The normalizer is the **data contract layer** between the raw exchange feeds and everything downstream (SHM writer, spread reader). Its job is to:

1. Accept a raw WebSocket message dict from the collector queue
2. Parse the exchange-specific JSON into a canonical `Quote`
3. Validate all fields — reject crossed books, zero prices, and missing fields
4. Return a validated `Quote` or `None` (for heartbeats, pings, etc.)

---

## `Quote` Dataclass (T15)

**File:** `normalizer/schema.py`

```python
@dataclass(slots=True, frozen=True)
class Quote:
    exchange:        str   # "binance" | "bybit"
    market_type:     str   # "spot"    | "futures"
    unified_symbol:  str   # "BTC-USDT"
    bid:             float # best bid price, > 0
    ask:             float # best ask price, > 0, >= bid
    ts_exchange_ns:  int   # exchange timestamp (ns); 0 if unavailable
    ts_recv_ns:      int   # local receive timestamp (ns)
```

**Frozen + slots** — immutable and memory-efficient; safe to pass between coroutines without copying.

### Derived helpers

| Property / Method | Description |
|---|---|
| `effective_ts_ns` | `ts_exchange_ns` if non-zero, else `ts_recv_ns` |
| `age_ms` | Milliseconds since `effective_ts_ns` (uses `time.time_ns()`) |
| `is_valid()` | Boolean: all invariants hold |

### Validation

`validate_quote(quote: Quote) -> Quote` raises `QuoteValidationError` if:
- `bid <= 0` or `ask <= 0`
- `bid > ask` (crossed book)
- `exchange`, `market_type`, or `unified_symbol` is empty
- `ts_recv_ns <= 0`

All parsers call `validate_quote()` before returning — callers receive either a valid `Quote` or an exception, never a silently invalid object.

---

## WebSocket Wire Formats (T16)

### Binance Spot — `bookTicker` stream

**Endpoint:**
```
wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker
```

**Message (bare / individual stream):**
```json
{
    "u": 400900217,
    "s": "BTCUSDT",
    "b": "84000.10",
    "B": "0.50",
    "a": "84001.00",
    "A": "1.00"
}
```

**Fields used:**

| Field | Meaning | Mapped to |
|-------|---------|-----------|
| `s` | Symbol (raw) | `unified_symbol` via normaliser |
| `b` | Best bid price (string) | `bid` |
| `a` | Best ask price (string) | `ask` |

**Exchange timestamp:** ❌ not present in Spot bookTicker.
`ts_exchange_ns = 0` — staleness uses `ts_recv_ns`.

---

### Binance USDT-M Futures — `bookTicker` stream

**Endpoint:**
```
wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker
```

**Message:**
```json
{
    "e": "bookTicker",
    "u": 400900217,
    "E": 1700000000000,
    "T": 1700000000000,
    "s": "BTCUSDT",
    "b": "84000.00",
    "B": "10",
    "a": "84001.50",
    "A": "5"
}
```

**Fields used:**

| Field | Meaning | Mapped to |
|-------|---------|-----------|
| `e` | Event type | Checked: must be `"bookTicker"` |
| `s` | Symbol | `unified_symbol` |
| `b` | Best bid (string) | `bid` |
| `a` | Best ask (string) | `ask` |
| `E` | Event time (ms) | `ts_exchange_ns = E × 1 000 000` |

**Exchange timestamp:** ✅ field `E` (milliseconds → nanoseconds).

---

### Bybit Spot — `orderbook.1` stream

**Topic:** `orderbook.1.<SYMBOL>`

**Message (snapshot — first message after subscribe):**
```json
{
    "topic": "orderbook.1.BTCUSDT",
    "type": "snapshot",
    "ts": 1700000000000,
    "data": {
        "s": "BTCUSDT",
        "b": [["83999.50", "0.01"]],
        "a": [["84000.00", "0.02"]],
        "u": 18521288,
        "seq": 7961638724
    }
}
```

**Message (delta — subsequent updates):**
```json
{
    "topic": "orderbook.1.BTCUSDT",
    "type": "delta",
    "ts": 1700000000050,
    "data": {
        "s": "BTCUSDT",
        "b": [["83999.75", "0.03"]],
        "a": [],
        "u": 18521289,
        "seq": 7961638725
    }
}
```

**Fields used:**

| Field | Meaning | Mapped to |
|-------|---------|-----------|
| `topic` | Must start with `"orderbook"` | Guard check |
| `type` | `"snapshot"` or `"delta"` | Guard check |
| `ts` | Exchange timestamp (ms) | `ts_exchange_ns = ts × 1 000 000` |
| `data.s` | Symbol | `unified_symbol` |
| `data.b[0][0]` | Best bid price (string) | `bid` |
| `data.a[0][0]` | Best ask price (string) | `ask` |

**Exchange timestamp:** ✅ field `ts` (milliseconds → nanoseconds).

**Delta handling:** if `b` or `a` is an empty list, the parser returns `None` — that side did not change. The previous value in SHM remains valid.

---

### Bybit Linear Futures — `orderbook.1` stream

**Identical structure to Bybit Spot.** The collector connects to a different WebSocket endpoint (linear category), but the message format is the same. Parser is shared (`_parse_bybit_orderbook` with `market_type="futures"`).

---

## Combined Stream Envelopes

Both Binance parsers transparently handle the combined stream wrapper:

```json
{
    "stream": "btcusdt@bookTicker",
    "data": { ... }
}
```

If `"data"` key is present, parsers use `msg["data"]`; otherwise they use the top-level dict.

---

## Parsers (T17)

**File:** `normalizer/normalizer.py`

| Function | Exchange | Market |
|----------|----------|--------|
| `parse_binance_spot(msg, ts_recv_ns, quote_currency)` | Binance | Spot |
| `parse_binance_futures(msg, ts_recv_ns, quote_currency)` | Binance | USDT-M Futures |
| `parse_bybit_spot(msg, ts_recv_ns, quote_currency)` | Bybit | Spot |
| `parse_bybit_futures(msg, ts_recv_ns, quote_currency)` | Bybit | Linear Futures |

All return `Quote | None` and raise `QuoteValidationError` for malformed quotes.

### Dispatch table

`PARSERS` maps `(exchange, market_type) → parser_function` for use by the pipeline (Phase 5):

```python
from normalizer.normalizer import PARSERS

parser = PARSERS[("binance", "spot")]
quote  = parser(raw_msg, ts_recv_ns=time.time_ns())
```

### Return value semantics

| Return | Meaning | Caller action |
|--------|---------|---------------|
| `Quote` | Valid quote | Write to SHM |
| `None` | Not a quote message (ping, pong, subscription ACK, empty delta) | Discard silently |
| raises `QuoteValidationError` | Quote-shaped but invalid (crossed book, etc.) | Log warning, discard |

---

## Usage Examples

**Parse a single message:**

```python
import time
from normalizer.normalizer import parse_binance_futures
from normalizer.schema import QuoteValidationError

msg = {
    "e": "bookTicker",
    "E": 1700000000000,
    "s": "BTCUSDT",
    "b": "84000.00",
    "a": "84001.50",
}

try:
    quote = parse_binance_futures(msg, ts_recv_ns=time.time_ns())
    if quote:
        print(quote)              # Quote(binance/futures BTC-USDT bid=84000.0 ask=84001.5)
        print(quote.age_ms)       # ms since exchange timestamp
except QuoteValidationError as e:
    print(f"Rejected: {e}")
```

**Use the dispatch table (Phase 5 pipeline):**

```python
from normalizer.normalizer import PARSERS
import time

def handle_message(exchange: str, market_type: str, raw_msg: dict):
    parser = PARSERS.get((exchange, market_type))
    if parser is None:
        return
    quote = parser(raw_msg, ts_recv_ns=time.time_ns())
    if quote:
        shm_writer.write(quote)  # Phase 3/5
```

---

## Design Decisions

### Why `frozen=True` on Quote?

Quotes flow from collectors → normalizer → SHM writer as Python objects in an asyncio pipeline. Making them immutable prevents accidental mutation in multi-step processing and allows safe sharing across coroutines.

### Why `ts_exchange_ns = 0` for Binance Spot?

Binance Spot `bookTicker` simply does not include a timestamp. The alternatives — fetching a separate ticker stream or using a workaround — add complexity for no practical benefit. The `effective_ts_ns` property abstracts this away: downstream code never needs to branch on whether exchange timestamp is available.

### Why skip delta messages with empty bid/ask (Bybit)?

With `orderbook.1` depth 1, a delta that contains only one side means the other side's best price did not change. The SHM slot already holds the last known value. Writing a partial update (one side from the delta, one side from memory) would require the normalizer to maintain state, which is outside its scope. Skipping is safe and correct.

### Why validate inside the parser rather than at the pipeline boundary?

Pushing validation into the parser means every `Quote` object that exists in the system is guaranteed valid. Callers do not need defensive checks. Malformed data is caught at the earliest possible point, closest to the source.
