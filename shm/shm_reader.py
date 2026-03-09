"""
SHM seqlock reader (T21).

``ShmReader`` reads ``SlotData`` records from the shared-memory segment
without acquiring any lock, using the seqlock read protocol.

Seqlock read protocol
---------------------

    MAX_SPIN iterations:
        1. Read seq_begin.
        2. Read seq_end.
        3. If seq_begin != seq_end  → spin (write in progress).
        4. If seq_begin == seq_end == 0 → return None (slot never written).
        5. Read all data fields.
        6. Read seq_end again.
        7. If seq_end changed since step 2  → spin (write happened during read).
        8. Return SlotData.

    If spin limit is exceeded → return None and log a debug message.

``ShmReader`` is stateless with respect to slot allocation: it scans all
slots from 0 to ``max_slots`` and returns non-empty ones.

Typical usage by the spread reader (Phase 6)
---------------------------------------------

    reader = ShmReader(buf, max_slots=cfg["shm"]["max_slots"])
    slots  = reader.read_all()      # list[SlotData]
    for s in slots:
        age_ms = (time.time_ns() - s.ts_ns) / 1_000_000
        if age_ms < staleness_threshold_ms:
            ...
"""

from __future__ import annotations

import logging
import mmap
import time
from dataclasses import dataclass

from shm.shm_layout import (
    HEADER_SIZE,
    MARKET_NAME,
    OFF_BID,
    OFF_SEQ_BEGIN,
    OFF_SEQ_END,
    SLOT_SIZE,
    STRUCT_SEQ,
    STRUCT_SLOT_DATA,
    slot_offset,
)

logger = logging.getLogger(__name__)

_MAX_SPIN: int = 1_000  # spin limit per slot read attempt


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SlotData:
    """
    A snapshot of one SHM slot as read by ``ShmReader``.

    All fields are guaranteed consistent (no torn read) by the seqlock
    protocol.

    Attributes
    ----------
    slot_id:      Index of the slot in the SHM table.
    unified_symbol: e.g. ``"BTC-USDT"``
    exchange:     ``"binance"`` or ``"bybit"``
    market_type:  ``"spot"`` or ``"futures"``
    bid:          Best bid price.
    ask:          Best ask price.
    ts_ns:        Timestamp stored by the writer (``Quote.effective_ts_ns``).
    """

    slot_id: int
    unified_symbol: str
    exchange: str
    market_type: str
    bid: float
    ask: float
    ts_ns: int

    @property
    def age_ms(self) -> float:
        """Milliseconds since ``ts_ns``."""
        return (time.time_ns() - self.ts_ns) / 1_000_000

    def is_stale(self, threshold_ms: float) -> bool:
        """Return True if the slot's data is older than *threshold_ms*."""
        return self.age_ms > threshold_ms


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class ShmReader:
    """
    Lock-free reader for the POSIX SHM quote table.

    Parameters
    ----------
    buf:
        ``mmap.mmap`` view of the full SHM segment (read-only is sufficient,
        but the mmap is typically opened read-write by the process that also
        runs the writer).
    max_slots:
        Slot capacity of the segment (from config ``shm.max_slots``).
    """

    def __init__(self, buf: mmap.mmap, max_slots: int) -> None:
        self._buf = buf
        self._max_slots = max_slots

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_slot(self, slot_id: int) -> SlotData | None:
        """
        Read one slot with the seqlock protocol.

        Returns
        -------
        SlotData
            Consistent snapshot of the slot, or ``None`` if the slot was
            never written or the spin limit was exceeded.
        """
        buf = self._buf
        off = slot_offset(slot_id)

        for _ in range(_MAX_SPIN):
            seq_b: int = STRUCT_SEQ.unpack_from(buf, off + OFF_SEQ_BEGIN)[0]
            seq_e: int = STRUCT_SEQ.unpack_from(buf, off + OFF_SEQ_END)[0]

            if seq_b != seq_e:
                continue  # write in progress — spin

            if seq_b == 0:
                return None  # slot was never written

            # Read data fields
            bid, ask, ts_ns, symbol_b, exchange_b, market_code = (
                STRUCT_SLOT_DATA.unpack_from(buf, off + OFF_BID)
            )

            # Verify seq_end hasn't changed (no write happened during our read)
            seq_e2: int = STRUCT_SEQ.unpack_from(buf, off + OFF_SEQ_END)[0]
            if seq_e2 != seq_b:
                continue  # write happened during read — retry

            # Decode byte strings
            unified_symbol = symbol_b.rstrip(b"\x00").decode("ascii", errors="replace")
            exchange       = exchange_b.rstrip(b"\x00").decode("ascii", errors="replace")
            market_type    = MARKET_NAME.get(market_code, "unknown")

            return SlotData(
                slot_id=slot_id,
                unified_symbol=unified_symbol,
                exchange=exchange,
                market_type=market_type,
                bid=bid,
                ask=ask,
                ts_ns=ts_ns,
            )

        # Spin limit exceeded — extremely busy writer or corrupted slot
        logger.debug("Spin limit exceeded reading slot", extra={"slot_id": slot_id})
        return None

    def read_all(self) -> list[SlotData]:
        """
        Read all slots that have ever been written.

        Returns
        -------
        list[SlotData]
            One entry per written slot in slot-id order.  Slots that were
            never written (seq == 0) are omitted.
        """
        results: list[SlotData] = []
        for slot_id in range(self._max_slots):
            data = self.read_slot(slot_id)
            if data is not None:
                results.append(data)
        return results
