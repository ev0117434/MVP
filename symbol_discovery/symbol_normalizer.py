"""
Symbol normalisation between exchange-native formats and unified BASE-QUOTE format.

## Exchange symbol formats (T07)

All four sources use the same raw format: base and quote currency concatenated
without a separator, e.g. "BTCUSDT". There are no exchange-specific suffixes
for perpetual contracts on either Binance or Bybit (Binance used to append
"_PERP" for coin-margined futures, but USDT-M perpetuals are plain "BTCUSDT").

Unified format: "BASE-QUOTE"  →  e.g. "BTC-USDT", "ETH-USDT", "1000SHIB-USDT"

Conversion strategy
-------------------
raw_to_unified:
    Strip the known quote currency suffix from the raw symbol.
    Base = raw[:-len(quote)], unified = f"{base}-{quote}".
    Returns None if the raw symbol does not end with the expected quote currency.

unified_to_raw:
    Split on "-", concatenate parts.  "BTC-USDT" → "BTCUSDT".
"""

from __future__ import annotations


def raw_to_unified(
    raw: str,
    quote_currency: str = "USDT",
) -> str | None:
    """
    Convert an exchange-native symbol to unified BASE-QUOTE format.

    Parameters
    ----------
    raw:
        Exchange-native symbol, e.g. "BTCUSDT", "ETHUSDT", "1000SHIBUSDT".
    quote_currency:
        Expected quote currency suffix (default "USDT").

    Returns
    -------
    str
        Unified symbol, e.g. "BTC-USDT".
    None
        If *raw* does not end with *quote_currency* (filtered out by caller).

    Examples
    --------
    >>> raw_to_unified("BTCUSDT")
    'BTC-USDT'
    >>> raw_to_unified("1000SHIBUSDT")
    '1000SHIB-USDT'
    >>> raw_to_unified("BTCBTC") is None
    True
    """
    if not raw.endswith(quote_currency):
        return None
    base = raw[: -len(quote_currency)]
    if not base:
        return None
    return f"{base}-{quote_currency}"


def unified_to_raw(
    unified: str,
    exchange: str = "",   # noqa: ARG001  kept for future per-exchange mapping
    market: str = "",     # noqa: ARG001
) -> str:
    """
    Convert a unified BASE-QUOTE symbol back to exchange-native raw format.

    Both Binance and Bybit (spot and futures) use the same concatenated format,
    so *exchange* and *market* parameters are accepted for API consistency but
    are currently unused.

    Parameters
    ----------
    unified:
        Unified symbol, e.g. "BTC-USDT".
    exchange:
        "binance" | "bybit" (reserved for future per-exchange divergence).
    market:
        "spot" | "futures" (reserved).

    Returns
    -------
    str
        Exchange-native symbol, e.g. "BTCUSDT".

    Examples
    --------
    >>> unified_to_raw("BTC-USDT")
    'BTCUSDT'
    >>> unified_to_raw("1000SHIB-USDT")
    '1000SHIBUSDT'
    """
    return unified.replace("-", "")
