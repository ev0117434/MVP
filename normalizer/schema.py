"""
Canonical Quote dataclass — the inter-component data contract (T15).

Every component downstream of the collectors (normalizer, SHM writer,
spread reader) operates on ``Quote`` objects.  Nothing else crosses
component boundaries.

Field semantics
---------------
exchange
    Source exchange identifier.  Lowercase string: "binance" | "bybit".

market_type
    Market side.  Lowercase string: "spot" | "futures".

unified_symbol
    Trading pair in BASE-QUOTE format, e.g. "BTC-USDT".
    Produced by ``symbol_discovery.symbol_normalizer.raw_to_unified()``.

bid
    Best bid price (highest price a buyer is willing to pay).
    IEEE 754 double-precision float, > 0.

ask
    Best ask price (lowest price a seller is willing to accept).
    IEEE 754 double-precision float, > 0, >= bid.

ts_exchange_ns
    Timestamp reported by the exchange, converted to nanoseconds since
    the Unix epoch.  Set to 0 when the exchange does not provide a
    timestamp in the relevant stream (Binance Spot bookTicker).

ts_recv_ns
    Local wall-clock time at which the raw WebSocket message was
    received, in nanoseconds since the Unix epoch.  Always populated.

Effective timestamp
-------------------
``Quote.effective_ts_ns`` returns ``ts_exchange_ns`` when it is non-zero,
otherwise ``ts_recv_ns``.  Components that need a single representative
timestamp (e.g. SHM writer, spread reader staleness check) should use
this property rather than choosing a field manually.

Immutability
------------
The dataclass is frozen so that Quote objects can be safely shared across
coroutines without defensive copying.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Quote:
    """Canonical best-bid/ask record produced by the normalizer."""

    exchange: str        # "binance" | "bybit"
    market_type: str     # "spot" | "futures"
    unified_symbol: str  # "BTC-USDT"
    bid: float
    ask: float
    ts_exchange_ns: int  # nanoseconds; 0 if not provided by exchange
    ts_recv_ns: int      # nanoseconds; local receive time

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def effective_ts_ns(self) -> int:
        """
        Best available timestamp in nanoseconds.

        Returns ``ts_exchange_ns`` when the exchange provides one,
        otherwise falls back to ``ts_recv_ns``.
        """
        return self.ts_exchange_ns if self.ts_exchange_ns > 0 else self.ts_recv_ns

    @property
    def age_ms(self) -> float:
        """Milliseconds elapsed since ``effective_ts_ns``."""
        return (time.time_ns() - self.effective_ts_ns) / 1_000_000

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """
        Return True if all fields satisfy the invariants required for
        a usable spread calculation.

        Rules (from architecture section 4.2):
        - bid > 0
        - ask > 0
        - bid <= ask  (crossed book is rejected)
        - exchange, market_type, unified_symbol are non-empty strings
        - ts_recv_ns > 0
        """
        return (
            self.bid > 0
            and self.ask > 0
            and self.bid <= self.ask
            and bool(self.exchange)
            and bool(self.market_type)
            and bool(self.unified_symbol)
            and self.ts_recv_ns > 0
        )

    def __str__(self) -> str:
        return (
            f"Quote({self.exchange}/{self.market_type} {self.unified_symbol} "
            f"bid={self.bid} ask={self.ask})"
        )


class QuoteValidationError(ValueError):
    """
    Raised by ``validate_quote()`` when a ``Quote`` fails invariant checks.

    Attributes
    ----------
    quote:
        The invalid ``Quote`` object.
    reason:
        Human-readable description of the violated invariant.
    """

    def __init__(self, quote: Quote, reason: str) -> None:
        self.quote = quote
        self.reason = reason
        super().__init__(f"Invalid quote {quote}: {reason}")


def validate_quote(quote: Quote) -> Quote:
    """
    Validate a ``Quote`` and return it unchanged, or raise
    ``QuoteValidationError``.

    Intended to be called by parsers after construction so that all
    downstream components can assume they receive only valid quotes.

    Parameters
    ----------
    quote:
        The ``Quote`` to validate.

    Returns
    -------
    Quote
        The same object, unchanged.

    Raises
    ------
    QuoteValidationError
        If any invariant is violated.
    """
    if not quote.exchange:
        raise QuoteValidationError(quote, "exchange is empty")
    if not quote.market_type:
        raise QuoteValidationError(quote, "market_type is empty")
    if not quote.unified_symbol:
        raise QuoteValidationError(quote, "unified_symbol is empty")
    if quote.bid <= 0:
        raise QuoteValidationError(quote, f"bid={quote.bid} is not > 0")
    if quote.ask <= 0:
        raise QuoteValidationError(quote, f"ask={quote.ask} is not > 0")
    if quote.bid > quote.ask:
        raise QuoteValidationError(
            quote, f"crossed book: bid={quote.bid} > ask={quote.ask}"
        )
    if quote.ts_recv_ns <= 0:
        raise QuoteValidationError(quote, "ts_recv_ns is not positive")
    return quote
