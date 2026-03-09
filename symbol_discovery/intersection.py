"""
Subscription list computation via set intersection (T12).

## Inclusion rule

A unified symbol is included in the final subscription lists if and only if
it is tradeable on BOTH spot AND futures of at least one exchange:

    valid = (binance_spot ∩ binance_futures) ∪ (bybit_spot ∩ bybit_futures)

This guarantees that the spread reader always has a matching spot/futures
pair for every monitored symbol on at least one exchange.

## Output format

Returns a dict with four keys (raw exchange-native symbols):

    {
        "binance_spot":    ["BTCUSDT", "ETHUSDT", ...],
        "binance_futures": ["BTCUSDT", "ETHUSDT", ...],
        "bybit_spot":      ["BTCUSDT", "ETHUSDT", ...],
        "bybit_futures":   ["BTCUSDT", "ETHUSDT", ...],
    }

Each list is sorted alphabetically for deterministic output.
"""

from __future__ import annotations

from symbol_discovery.symbol_normalizer import raw_to_unified, unified_to_raw

SubscriptionLists = dict[str, list[str]]


def compute_subscription_lists(
    binance_spot_raw: list[str],
    binance_futures_raw: list[str],
    bybit_spot_raw: list[str],
    bybit_futures_raw: list[str],
    quote_currency: str = "USDT",
) -> SubscriptionLists:
    """
    Compute the four subscription lists from raw exchange symbol lists.

    Parameters
    ----------
    binance_spot_raw:
        Raw symbols from BinanceSpotInfo.fetch_symbols().
    binance_futures_raw:
        Raw symbols from BinanceFuturesInfo.fetch_symbols().
    bybit_spot_raw:
        Raw symbols from BybitSpotInfo.fetch_symbols().
    bybit_futures_raw:
        Raw symbols from BybitFuturesInfo.fetch_symbols().
    quote_currency:
        Only include pairs quoted in this currency (default "USDT").

    Returns
    -------
    SubscriptionLists
        Dict with keys "binance_spot", "binance_futures", "bybit_spot",
        "bybit_futures", each containing a sorted list of raw symbols.
    """
    # Normalise each raw list to unified symbols, filtering by quote currency
    def to_unified_set(raw_list: list[str]) -> set[str]:
        result = set()
        for raw in raw_list:
            unified = raw_to_unified(raw, quote_currency)
            if unified is not None:
                result.add(unified)
        return result

    bs_u = to_unified_set(binance_spot_raw)
    bf_u = to_unified_set(binance_futures_raw)
    ys_u = to_unified_set(bybit_spot_raw)
    yf_u = to_unified_set(bybit_futures_raw)

    # Symbols valid on at least one full exchange (spot + futures pair)
    valid_unified: set[str] = (bs_u & bf_u) | (ys_u & yf_u)

    # Build raw subscription lists — keep only unified symbols in valid set
    def to_raw_list(raw_list: list[str], valid: set[str]) -> list[str]:
        result = []
        for raw in raw_list:
            unified = raw_to_unified(raw, quote_currency)
            if unified in valid:
                result.append(raw)
        return sorted(result)

    return {
        "binance_spot": to_raw_list(binance_spot_raw, valid_unified),
        "binance_futures": to_raw_list(binance_futures_raw, valid_unified),
        "bybit_spot": to_raw_list(bybit_spot_raw, valid_unified),
        "bybit_futures": to_raw_list(bybit_futures_raw, valid_unified),
    }


def subscription_lists_stats(lists: SubscriptionLists) -> dict[str, int]:
    """Return symbol counts per stream — useful for logging."""
    return {key: len(val) for key, val in lists.items()}
