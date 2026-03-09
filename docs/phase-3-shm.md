# Phase 3 — SHM Table

This document covers T18–T22: the POSIX shared-memory segment layout, initialisation, seqlock writer and reader, and graceful cleanup.

---

## Overview

The SHM table is the zero-copy, lock-free transport layer between the normalizer (writer) and the spread reader (reader). It is a fixed-size region of POSIX shared memory divided into a header and an array of quote slots.

```
┌─────────────────────────────────┐ offset 0
│  Header  (32 bytes)             │
├─────────────────────────────────┤ offset 32
│  Slot 0  (84 bytes)             │
├─────────────────────────────────┤ offset 116
│  Slot 1  (84 bytes)             │
│  …                              │
├─────────────────────────────────┤ offset 32 + 2048 × 84 = 172 064
│  (end of segment)               │
└─────────────────────────────────┘
Total: 172 064 bytes ≈ 168 KB (default config)
```

---

## Header Layout (T18)

32 bytes, little-endian.

| Offset | Size | Field | Value |
|--------|------|-------|-------|
| 0 | 8 B | MAGIC | `b"CSMQ_V01"` |
| 8 | 4 B | VERSION | `1` |
| 12 | 4 B | MAX_SLOTS | from config (default 2048) |
| 16 | 4 B | SLOT_SIZE | `84` |
| 20 | 12 B | _reserved | zeros |

The **MAGIC** field lets `shm_init` detect stale segments from a different format version and recreate them automatically.

---

## Slot Layout (T18)

84 bytes per slot, little-endian.

| Offset | Size | Field | struct | Description |
|--------|------|-------|--------|-------------|
| 0 | 8 B | seq_begin | `Q` | Seqlock write-start counter |
| 8 | 8 B | bid | `d` | Best bid price (IEEE 754 double) |
| 16 | 8 B | ask | `d` | Best ask price |
| 24 | 8 B | ts_ns | `Q` | `Quote.effective_ts_ns` (nanoseconds) |
| 32 | 32 B | symbol | `32s` | Unified symbol, null-padded ASCII (`"BTC-USDT"`) |
| 64 | 8 B | exchange | `8s` | Exchange, null-padded ASCII (`"binance"`) |
| 72 | 4 B | market | `I` | Integer code: `0` = spot, `1` = futures |
| 76 | 8 B | seq_end | `Q` | Seqlock write-end counter |

**Note on alignment:** `seq_end` at offset 76 is not 8-byte aligned. Python's `struct` module handles this transparently. A future C reader should use `memcpy` or `__attribute__((packed))`.

**Slot size arithmetic:** 8 + 8 + 8 + 8 + 32 + 8 + 4 + 8 = **84 bytes**
(config.yaml `shm.slot_size` must be 84; the original value of 76 was a bug — it forgot to count `seq_end`).

---

## Seqlock Protocol

### Invariants

| State | `seq_begin` vs `seq_end` |
|-------|--------------------------|
| Never written | `seq_begin == seq_end == 0` |
| Write in progress | `seq_begin != seq_end` |
| Stable (readable) | `seq_begin == seq_end != 0` |

### Writer sequence (T20)

```
seq  = read seq_begin at OFF_SEQ_BEGIN       # e.g. N
       write seq_begin = N + 1               # LOCK  (seq_begin ≠ seq_end)
       write bid, ask, ts_ns, symbol, exchange, market
       write seq_end = N + 1                 # UNLOCK (seq_begin == seq_end)
```

### Reader sequence (T21)

```
for _ in range(MAX_SPIN=1000):
    seq_b = read seq_begin
    seq_e = read seq_end
    if seq_b != seq_e:  continue             # write in progress
    if seq_b == 0:      return None          # never written
    read bid, ask, ts_ns, symbol, exchange, market
    seq_e2 = read seq_end
    if seq_e2 != seq_b: continue             # write happened during read
    return SlotData(...)
return None  # spin limit exceeded
```

### Why seqlock?

- **No kernel involvement** — readers never block, no futex, no syscall.
- **Single-writer assumption** — only one Python process writes (the normalizer). No write-side contention.
- **Reader is non-blocking** — the spread reader runs in a separate process or coroutine and can tolerate spinning for one slot.
- **Simple implementation** — two integer fields and four `struct.pack_into` calls per write.

### Python memory ordering caveat

Python does not expose explicit memory-barrier instructions. On **x86/x86-64** (Total Store Order), store–store ordering is guaranteed by hardware, so `struct.pack_into` sequences are safe. On **ARM** (relaxed memory model), a C extension with `__sync_synchronize()` or `atomic_thread_fence()` calls would be required. This is documented and acceptable for the MVP.

---

## Module Summary

### `shm/shm_layout.py` (T18)

Single source of truth for all constants and struct formats.

```python
from shm.shm_layout import (
    MAGIC_BYTES, VERSION,
    HEADER_SIZE, SLOT_SIZE, MAX_SLOTS,
    OFF_SEQ_BEGIN, OFF_BID, OFF_ASK, OFF_TS_NS,
    OFF_SYMBOL, OFF_EXCHANGE, OFF_MARKET, OFF_SEQ_END,
    STRUCT_HEADER, STRUCT_SLOT_DATA, STRUCT_SEQ,
    MARKET_CODE, MARKET_NAME,
    slot_offset,
)
```

Includes `assert` statements to catch layout drift at import time.

### `shm/shm_init.py` (T19)

```python
from shm.shm_init import open_or_create_shm

shm, buf = open_or_create_shm(name="/csm_quotes_v1", max_slots=2048)
```

- Opens existing segment if MAGIC matches.
- Recreates (unlink + create) if MAGIC mismatches.
- Creates fresh segment if it doesn't exist.
- Standalone: `python -m shm.shm_init` → used by `make init-shm`.

### `shm/shm_writer.py` (T20)

```python
from shm.shm_writer import ShmWriter

writer = ShmWriter(buf, max_slots=2048)
ok = writer.write(quote)   # True on success; False if MAX_SLOTS exhausted
print(writer.slots_used)   # number of allocated slots
```

- Maintains `(exchange, market_type, unified_symbol) → slot_id` mapping.
- Drops quotes (returns `False`, logs WARNING) when `MAX_SLOTS` is exhausted — no crash.

### `shm/shm_reader.py` (T21)

```python
from shm.shm_reader import ShmReader, SlotData

reader = ShmReader(buf, max_slots=2048)

slot: SlotData | None = reader.read_slot(0)
all_slots: list[SlotData] = reader.read_all()

# SlotData helpers
slot.age_ms               # ms since ts_ns
slot.is_stale(2000)       # True if older than 2 s
```

### `shm/shm_cleaner.py` (T22)

```python
from shm.shm_cleaner import cleanup_shm, register_cleanup

# Register once at startup
register_cleanup("/csm_quotes_v1", buf, shm)

# Or call manually
cleanup_shm("/csm_quotes_v1", buf, shm)
```

- Standalone: `python -m shm.shm_cleaner` → used by `make clean-shm`.
- Safe to call even if segment doesn't exist.

---

## Make Targets

```bash
# Create (or reuse) the SHM segment
make init-shm

# Remove the SHM segment
make clean-shm

# Recreate: clean then init
make clean-shm && make init-shm
```

---

## Usage Example: Full Round-Trip

```python
import time
import posix_ipc
from shm.shm_init    import open_or_create_shm
from shm.shm_writer  import ShmWriter
from shm.shm_reader  import ShmReader
from shm.shm_cleaner import cleanup_shm, register_cleanup
from normalizer.schema import Quote

# Open / create SHM
shm, buf = open_or_create_shm("/csm_quotes_v1", max_slots=2048)
register_cleanup("/csm_quotes_v1", buf, shm)

writer = ShmWriter(buf, max_slots=2048)
reader = ShmReader(buf, max_slots=2048)

# Write a quote
q = Quote(
    exchange="binance", market_type="futures", unified_symbol="BTC-USDT",
    bid=84000.0, ask=84001.5,
    ts_exchange_ns=time.time_ns(), ts_recv_ns=time.time_ns(),
)
writer.write(q)

# Read it back
slot = reader.read_slot(0)
print(slot.unified_symbol)   # BTC-USDT
print(slot.bid)              # 84000.0
print(slot.age_ms)           # ~0 ms
print(slot.is_stale(2000))   # False

# All written slots
all_data = reader.read_all()
print(len(all_data))         # 1
```

---

## Design Decisions

### Why fixed-size slots?

Random-access by `slot_id` without any allocation overhead. The spread reader can directly read slot N without scanning. Symbol–slot mapping is maintained by the writer process and rebuilt from the SHM on restart (or reset on `magic mismatch`).

### Why `MAX_SLOTS` exhaustion is a warning, not an error?

The set of actively traded USDT pairs is bounded (< 500 on both exchanges combined). `MAX_SLOTS = 2048` has a 4× headroom. If somehow exhausted, dropping new symbols silently is better than crashing the pipeline and losing all quotes.

### Why store `effective_ts_ns` in SHM (not both timestamps)?

The spread reader needs one timestamp for staleness checks. `Quote.effective_ts_ns` already encapsulates the "best available" choice (exchange ts if non-zero, otherwise receive ts). Storing both would double the timestamp footprint without benefit at read time.

### Why is `slot_id` not stored in the slot itself?

`slot_id` is the array index. The reader already knows it when it calls `read_slot(slot_id)`. Storing it would be redundant and waste 8 bytes per slot.
