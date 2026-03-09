"""
POSIX SHM layout constants (T18).

This module is the single source of truth for all offsets, sizes, and
struct formats used by the SHM writer and reader.  Any component that
touches the shared-memory segment must import from here — never hard-code
offsets elsewhere.

Memory map
----------

    ┌─────────────────────────────────┐ offset 0
    │  Header  (32 bytes)             │
    ├─────────────────────────────────┤ offset HEADER_SIZE = 32
    │  Slot 0  (84 bytes)             │
    ├─────────────────────────────────┤ offset 32 + 84
    │  Slot 1  (84 bytes)             │
    │  …                              │
    ├─────────────────────────────────┤ offset 32 + MAX_SLOTS × 84
    │  (end of segment)               │
    └─────────────────────────────────┘

Header layout (32 bytes, little-endian)
----------------------------------------

    Offset  Size  Field
    ------  ----  -----
         0     8  MAGIC      — b"CSMQ_V01"; identifies the SHM format
         8     4  VERSION    — uint32; format version (currently 1)
        12     4  MAX_SLOTS  — uint32; total slot count in this segment
        16     4  SLOT_SIZE  — uint32; bytes per slot (84)
        20    12  _reserved  — zero-filled

Slot layout (84 bytes, little-endian)
---------------------------------------

    Offset  Size  Field     struct  Description
    ------  ----  -----     ------  -----------
         0     8  seq_begin   Q    Seqlock write-start counter
         8     8  bid         d    Best bid price (IEEE 754 double)
        16     8  ask         d    Best ask price
        24     8  ts_ns       Q    Timestamp (ns); see Quote.effective_ts_ns
        32    32  symbol     32s   Unified symbol, e.g. b"BTC-USDT\x00…"
        64     8  exchange    8s   Exchange, e.g. b"binance\x00"
        72     4  market      I    Market code: 0=spot, 1=futures
        76     8  seq_end     Q    Seqlock write-end counter

    Total: 84 bytes.

    Note: seq_end sits at offset 76, which is not 8-byte aligned.
    Python's struct module handles unaligned access transparently.
    A future C reader should use __attribute__((packed)) or memcpy.

Seqlock invariant
-----------------

    Stable (safe to read):   seq_begin == seq_end
    Write in progress:       seq_begin != seq_end  (seq_begin was incremented first)
    Never written:           seq_begin == seq_end == 0

Total SHM size (default config): 32 + 2048 × 84 = 172 064 bytes ≈ 168 KB
"""

from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# Magic / version
# ---------------------------------------------------------------------------

MAGIC_BYTES: bytes = b"CSMQ_V01"   # 8-byte ASCII identifier
MAGIC: int = int.from_bytes(MAGIC_BYTES, "little")  # 0x43534D515F563031
VERSION: int = 1

# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------

HEADER_SIZE: int = 32
SLOT_SIZE: int = 84     # must match config.yaml shm.slot_size
MAX_SLOTS: int = 2048   # default; runtime value comes from config

# ---------------------------------------------------------------------------
# Header struct
# ---------------------------------------------------------------------------

STRUCT_HEADER: struct.Struct = struct.Struct("<8sIII12x")
# magic(8) + version(4) + max_slots(4) + slot_size(4) + reserved(12) = 32 bytes

assert STRUCT_HEADER.size == HEADER_SIZE, (
    f"STRUCT_HEADER size mismatch: {STRUCT_HEADER.size} != {HEADER_SIZE}"
)

# ---------------------------------------------------------------------------
# Slot field offsets (relative to the start of a slot)
# ---------------------------------------------------------------------------

OFF_SEQ_BEGIN: int = 0
OFF_BID:       int = 8
OFF_ASK:       int = 16
OFF_TS_NS:     int = 24
OFF_SYMBOL:    int = 32
OFF_EXCHANGE:  int = 64
OFF_MARKET:    int = 72
OFF_SEQ_END:   int = 76

# ---------------------------------------------------------------------------
# Slot data struct (covers bid .. market, i.e. everything between the seqlocks)
# ---------------------------------------------------------------------------

STRUCT_SLOT_DATA: struct.Struct = struct.Struct("<ddQ32s8sI")
# bid(8) + ask(8) + ts_ns(8) + symbol(32) + exchange(8) + market(4) = 68 bytes
# Packed at offset OFF_BID within the slot.

assert STRUCT_SLOT_DATA.size == OFF_SEQ_END - OFF_BID, (
    f"STRUCT_SLOT_DATA size mismatch: {STRUCT_SLOT_DATA.size} "
    f"!= {OFF_SEQ_END - OFF_BID}"
)

# Single-counter structs for the seqlock fields
STRUCT_SEQ: struct.Struct = struct.Struct("<Q")

# Sanity check: seq_end + 8 == SLOT_SIZE
assert OFF_SEQ_END + STRUCT_SEQ.size == SLOT_SIZE, (
    f"Slot size mismatch: OFF_SEQ_END({OFF_SEQ_END}) + 8 != SLOT_SIZE({SLOT_SIZE})"
)

# ---------------------------------------------------------------------------
# Market type encoding
# ---------------------------------------------------------------------------

MARKET_SPOT:    int = 0
MARKET_FUTURES: int = 1

MARKET_CODE: dict[str, int] = {
    "spot":    MARKET_SPOT,
    "futures": MARKET_FUTURES,
}

MARKET_NAME: dict[int, str] = {v: k for k, v in MARKET_CODE.items()}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slot_offset(slot_id: int) -> int:
    """Return the byte offset of slot *slot_id* within the SHM segment."""
    return HEADER_SIZE + slot_id * SLOT_SIZE
