"""
SHM seqlock writer (T20).

``ShmWriter`` receives validated ``Quote`` objects and writes them into
the shared-memory segment using the seqlock protocol.

Seqlock write protocol
----------------------

Each slot has two 8-byte counters: ``seq_begin`` (first field) and
``seq_end`` (last field).

Invariants:
    - ``seq_begin == seq_end``  → slot is stable; readers may proceed.
    - ``seq_begin != seq_end``  → write in progress; readers must spin.
    - ``seq_begin == seq_end == 0`` → slot has never been written.

Write sequence for slot at byte offset *off*:

    1. Read current seq_begin (N).
    2. Write seq_begin = N + 1   (now odd if N was even; seq_begin != seq_end → lock).
    3. Write all data fields (bid, ask, ts_ns, symbol, exchange, market).
    4. Write seq_end = N + 1     (seq_begin == seq_end → unlock, readers may proceed).

Memory ordering
---------------

Python does not expose explicit memory-barrier instructions.  On x86/x86-64
(TSO memory model) store–store ordering is guaranteed by hardware, so
``struct.pack_into`` calls are sufficient.  On ARM-based hosts a future
C extension shim with explicit barriers would be needed.  This is
documented and acceptable for the MVP.

Slot allocation
---------------

``ShmWriter`` maintains an in-process mapping
``(exchange, market_type, unified_symbol) → slot_id``.  When a symbol is
seen for the first time a new slot is allocated.  Slots are never freed
(symbols are expected to be long-lived).

When ``MAX_SLOTS`` is exhausted the quote is **dropped** and a WARNING is
logged.  The system continues without crashing (per architecture spec).
"""

from __future__ import annotations

import logging
import mmap
import struct

from normalizer.schema import Quote
from shm.shm_layout import (
    HEADER_SIZE,
    MARKET_CODE,
    OFF_BID,
    OFF_SEQ_BEGIN,
    OFF_SEQ_END,
    SLOT_SIZE,
    STRUCT_SEQ,
    STRUCT_SLOT_DATA,
    slot_offset,
)

logger = logging.getLogger(__name__)

# Precompile single-field pack calls for the seqlock counters
_PACK_SEQ = struct.Struct("<Q").pack_into


class ShmWriter:
    """
    Writes ``Quote`` objects into the POSIX SHM segment using seqlock.

    Parameters
    ----------
    buf:
        Writable ``mmap.mmap`` view of the full SHM segment.
    max_slots:
        Slot capacity of the segment (from config ``shm.max_slots``).
    """

    def __init__(self, buf: mmap.mmap, max_slots: int) -> None:
        self._buf = buf
        self._max_slots = max_slots
        # (exchange, market_type, unified_symbol) → slot_id
        self._slot_map: dict[tuple[str, str, str], int] = {}
        self._next_slot: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, quote: Quote) -> bool:
        """
        Write *quote* into the SHM segment.

        Returns
        -------
        bool
            ``True`` on success, ``False`` if the slot table is full.
        """
        slot_id = self._get_or_allocate_slot(quote)
        if slot_id is None:
            return False

        off = slot_offset(slot_id)
        self._seqlock_write(off, quote)
        return True

    @property
    def slots_used(self) -> int:
        """Number of slots currently allocated."""
        return self._next_slot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_allocate_slot(self, quote: Quote) -> int | None:
        key = (quote.exchange, quote.market_type, quote.unified_symbol)
        slot_id = self._slot_map.get(key)
        if slot_id is not None:
            return slot_id

        if self._next_slot >= self._max_slots:
            logger.warning(
                "MAX_SLOTS exhausted — quote dropped",
                extra={
                    "exchange": quote.exchange,
                    "market_type": quote.market_type,
                    "symbol": quote.unified_symbol,
                    "max_slots": self._max_slots,
                },
            )
            return None

        slot_id = self._next_slot
        self._slot_map[key] = slot_id
        self._next_slot += 1
        logger.debug(
            "Allocated SHM slot",
            extra={"slot_id": slot_id, "key": key},
        )
        return slot_id

    def _seqlock_write(self, off: int, quote: Quote) -> None:
        """Execute the seqlock write sequence at byte offset *off*."""
        buf = self._buf

        # 1. Read current seq_begin
        seq: int = STRUCT_SEQ.unpack_from(buf, off + OFF_SEQ_BEGIN)[0]
        new_seq: int = seq + 1  # will be odd if seq was even (initial state = 0)

        # 2. Lock: increment seq_begin (seq_begin != seq_end → readers spin)
        _PACK_SEQ(buf, off + OFF_SEQ_BEGIN, new_seq)

        # 3. Write data fields
        symbol_b  = quote.unified_symbol.encode("ascii")[:32].ljust(32, b"\x00")
        exchange_b = quote.exchange.encode("ascii")[:8].ljust(8, b"\x00")
        market_code = MARKET_CODE.get(quote.market_type, 0)

        STRUCT_SLOT_DATA.pack_into(
            buf,
            off + OFF_BID,
            quote.bid,
            quote.ask,
            quote.effective_ts_ns,
            symbol_b,
            exchange_b,
            market_code,
        )

        # 4. Unlock: set seq_end = seq_begin (readers see stable slot again)
        _PACK_SEQ(buf, off + OFF_SEQ_END, new_seq)
