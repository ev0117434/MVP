"""
WebSocket message parsers for all four exchange/market combinations (T16, T17).

## Wire formats (T16)

### Binance Spot — bookTicker stream

Stream URL (combined):
    wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker

Individual stream message:
    {
        "u": 400900217,      // order book updateId
        "s": "BNBUSDT",      // symbol
        "b": "25.35190000",  // best bid price (string)
        "B": "31.21000000",  // best bid qty
        "a": "25.36520000",  // best ask price (string)
        "A": "40.66000000"   // best ask qty
    }

Combined stream envelope:
    {"stream": "bnbusdt@bookTicker", "data": { <above> }}

NOTE: Binance Spot bookTicker does NOT include an exchange timestamp.
ts_exchange_ns is set to 0; staleness is tracked via ts_recv_ns.

### Binance USDT-M Futures — bookTicker stream

Stream URL (combined):
    wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker

Message:
    {
        "e": "bookTicker",   // event type
        "u": 400900217,      // order book updateId
        "E": 1568014460893,  // event time (ms) ← exchange timestamp
        "T": 1568014460891,  // transaction time (ms)
        "s": "BTCUSDT",
        "b": "11793.90",     // best bid price
        "B": "236",          // best bid qty
        "a": "11794.10",     // best ask price
        "A": "258"
    }

Also wrapped in combined stream envelope (same as spot).
ts_exchange_ns = E * 1_000_000 (ms → ns).

### Bybit Spot — orderbook.1 stream

Topic: orderbook.1.<SYMBOL>
Message (snapshot):
    {
        "topic": "orderbook.1.BTCUSDT",
        "type": "snapshot",
        "ts": 1672304484978,   // exchange timestamp (ms)
        "data": {
            "s": "BTCUSDT",
            "b": [["16493.50", "0.006"]],  // bids: [[price, qty], ...]
            "a": [["16611.00", "0.029"]],  // asks: [[price, qty], ...]
            "u": 18521288,
            "seq": 7961638724
        }
    }

Message (delta — incremental update):
    same structure, type == "delta"
    b/a may be empty [] if that side did not change

Both "snapshot" and "delta" are parsed.  If b or a is empty, the message
is skipped (returns None) — the previous best price remains valid.

ts_exchange_ns = ts * 1_000_000.

### Bybit Linear Futures — orderbook.1 stream

Identical structure to Bybit Spot.  Collector connects to the linear
category endpoint; the parser is the same.

---

## Parser contract

Each parser function has the signature:

    parse_<exchange>_<market>(msg: dict, ts_recv_ns: int) -> Quote | None

Returns:
    Quote   — valid, validated quote
    None    — message should be silently skipped (heartbeat, ping,
              delta with empty side, or unrecognised event type)

Raises:
    QuoteValidationError  — message looks like a quote but fails invariants
                            (bid > ask, negative prices, etc.)
                            Callers should log and discard these.

Parsers handle both the combined stream envelope
    {"stream": "...", "data": {...}}
and the bare message format received on individual streams.
"""

from __future__ import annotations

import time

from symbol_discovery.symbol_normalizer import raw_to_unified
from normalizer.schema import Quote, QuoteValidationError, validate_quote

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_ns() -> int:
    return time.time_ns()


def _unwrap(msg: dict) -> dict:
    """Unwrap Binance combined-stream envelope if present."""
    return msg.get("data", msg)


def _to_float(value: str | float | int | None) -> float | None:
    """Parse a price string to float; return None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _build_quote(
    exchange: str,
    market_type: str,
    raw_symbol: str,
    bid: float,
    ask: float,
    ts_exchange_ns: int,
    ts_recv_ns: int,
    quote_currency: str = "USDT",
) -> Quote | None:
    """Construct and validate a Quote; return None if symbol is filtered out."""
    unified = raw_to_unified(raw_symbol, quote_currency)
    if unified is None:
        return None
    quote = Quote(
        exchange=exchange,
        market_type=market_type,
        unified_symbol=unified,
        bid=bid,
        ask=ask,
        ts_exchange_ns=ts_exchange_ns,
        ts_recv_ns=ts_recv_ns,
    )
    return validate_quote(quote)  # raises QuoteValidationError on bad data


# ---------------------------------------------------------------------------
# Binance Spot — bookTicker
# ---------------------------------------------------------------------------

def parse_binance_spot(
    msg: dict,
    ts_recv_ns: int | None = None,
    quote_currency: str = "USDT",
) -> Quote | None:
    """
    Parse a Binance Spot bookTicker WebSocket message.

    Handles both individual-stream and combined-stream (envelope) formats.
    Returns None for non-quote messages (e.g. subscription confirmations).

    ts_exchange_ns is always 0 — Binance Spot bookTicker carries no timestamp.
    """
    if ts_recv_ns is None:
        ts_recv_ns = _now_ns()

    data = _unwrap(msg)

    raw_symbol = data.get("s")
    bid = _to_float(data.get("b"))
    ask = _to_float(data.get("a"))

    if raw_symbol is None or bid is None or ask is None:
        return None  # not a bookTicker message (e.g. subscription result)

    return _build_quote(
        exchange="binance",
        market_type="spot",
        raw_symbol=raw_symbol,
        bid=bid,
        ask=ask,
        ts_exchange_ns=0,   # not available in Spot bookTicker
        ts_recv_ns=ts_recv_ns,
        quote_currency=quote_currency,
    )


# ---------------------------------------------------------------------------
# Binance USDT-M Futures — bookTicker
# ---------------------------------------------------------------------------

def parse_binance_futures(
    msg: dict,
    ts_recv_ns: int | None = None,
    quote_currency: str = "USDT",
) -> Quote | None:
    """
    Parse a Binance USDT-M Futures bookTicker WebSocket message.

    Uses event time field ``E`` (milliseconds) as the exchange timestamp.
    Skips messages whose ``e`` field is not ``"bookTicker"``.
    """
    if ts_recv_ns is None:
        ts_recv_ns = _now_ns()

    data = _unwrap(msg)

    # Futures bookTicker has an "e" (event type) field
    event_type = data.get("e")
    if event_type is not None and event_type != "bookTicker":
        return None  # e.g. markPriceUpdate or other event types on the same connection

    raw_symbol = data.get("s")
    bid = _to_float(data.get("b"))
    ask = _to_float(data.get("a"))

    if raw_symbol is None or bid is None or ask is None:
        return None

    # "E" is event time in ms; "T" is transaction time in ms
    ts_exchange_ms = data.get("E") or data.get("T") or 0
    ts_exchange_ns = int(ts_exchange_ms) * 1_000_000

    return _build_quote(
        exchange="binance",
        market_type="futures",
        raw_symbol=raw_symbol,
        bid=bid,
        ask=ask,
        ts_exchange_ns=ts_exchange_ns,
        ts_recv_ns=ts_recv_ns,
        quote_currency=quote_currency,
    )


# ---------------------------------------------------------------------------
# Bybit Spot — orderbook.1
# ---------------------------------------------------------------------------

def parse_bybit_spot(
    msg: dict,
    ts_recv_ns: int | None = None,
    quote_currency: str = "USDT",
) -> Quote | None:
    """
    Parse a Bybit Spot orderbook.1 WebSocket message.

    Handles both ``type: "snapshot"`` (initial) and ``type: "delta"``
    (incremental update) messages.  Returns None when either the bid or
    ask side is empty — the previous best price remains valid in that case.

    ``ts`` (milliseconds) is used as the exchange timestamp.
    """
    if ts_recv_ns is None:
        ts_recv_ns = _now_ns()

    return _parse_bybit_orderbook(
        msg=msg,
        market_type="spot",
        ts_recv_ns=ts_recv_ns,
        quote_currency=quote_currency,
    )


# ---------------------------------------------------------------------------
# Bybit Linear Futures — orderbook.1
# ---------------------------------------------------------------------------

def parse_bybit_futures(
    msg: dict,
    ts_recv_ns: int | None = None,
    quote_currency: str = "USDT",
) -> Quote | None:
    """
    Parse a Bybit Linear Futures orderbook.1 WebSocket message.

    Structurally identical to the Spot parser; only ``market_type`` differs.
    """
    if ts_recv_ns is None:
        ts_recv_ns = _now_ns()

    return _parse_bybit_orderbook(
        msg=msg,
        market_type="futures",
        ts_recv_ns=ts_recv_ns,
        quote_currency=quote_currency,
    )


def _parse_bybit_orderbook(
    msg: dict,
    market_type: str,
    ts_recv_ns: int,
    quote_currency: str,
) -> Quote | None:
    """Shared implementation for Bybit Spot and Futures orderbook.1 parsers."""
    # Skip non-orderbook messages (e.g. pong responses, subscription confirmations)
    topic: str = msg.get("topic", "")
    if not topic.startswith("orderbook"):
        return None

    msg_type = msg.get("type", "")
    if msg_type not in ("snapshot", "delta"):
        return None

    data = msg.get("data", {})
    raw_symbol: str = data.get("s", "")
    bids: list = data.get("b", [])
    asks: list = data.get("a", [])

    # For delta messages, either side may be empty if unchanged — skip those.
    if not bids or not asks:
        return None

    bid = _to_float(bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0])
    ask = _to_float(asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0])

    if bid is None or ask is None or not raw_symbol:
        return None

    ts_exchange_ms = msg.get("ts", 0)
    ts_exchange_ns = int(ts_exchange_ms) * 1_000_000

    return _build_quote(
        exchange="bybit",
        market_type=market_type,
        raw_symbol=raw_symbol,
        bid=bid,
        ask=ask,
        ts_exchange_ns=ts_exchange_ns,
        ts_recv_ns=ts_recv_ns,
        quote_currency=quote_currency,
    )


# ---------------------------------------------------------------------------
# Dispatch table (for use by the collector runner in Phase 4/5)
# ---------------------------------------------------------------------------

PARSERS: dict[tuple[str, str], callable] = {
    ("binance", "spot"):    parse_binance_spot,
    ("binance", "futures"): parse_binance_futures,
    ("bybit",   "spot"):    parse_bybit_spot,
    ("bybit",   "futures"): parse_bybit_futures,
}
"""
Lookup table mapping (exchange, market_type) → parser function.

Usage in the normalizer pipeline (Phase 5)::

    parser = PARSERS[(exchange, market_type)]
    quote  = parser(raw_msg, ts_recv_ns=time.time_ns())
"""
