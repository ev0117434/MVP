"""
POSIX SHM initialisation (T19).

Opens or creates the shared-memory segment ``/csm_quotes_v1``.

Lifecycle
---------

1. Attempt to open an **existing** segment (no O_CREAT).
2. If found, verify the MAGIC header field:
   - Match  → attach, return the existing ``(shm, buf)`` with live data.
   - Mismatch → log warning, unlink stale segment, proceed to step 3.
3. If not found (ExistentialError) → proceed to step 3.
4. Create a **fresh** segment: ftruncate, mmap, write header, zero all slots.

The caller is responsible for closing ``buf`` and ``shm`` when done
(see ``shm/shm_cleaner.py``).

Usage
-----

    from shm.shm_init import open_or_create_shm

    shm, buf = open_or_create_shm()
    # … use ShmWriter / ShmReader …

Standalone (``make init-shm``)
-------------------------------

    python -m shm.shm_init

Reads ``config/config.yaml`` and opens or creates the configured segment.
"""

from __future__ import annotations

import logging
import mmap
import os
import sys
from pathlib import Path

import posix_ipc
import yaml

from shm.shm_layout import (
    HEADER_SIZE,
    MAGIC_BYTES,
    SLOT_SIZE,
    STRUCT_HEADER,
    VERSION,
)

logger = logging.getLogger(__name__)

# Fallback defaults (overridden by config at runtime)
SHM_NAME: str = "/csm_quotes_v1"
_DEFAULT_MAX_SLOTS: int = 2048


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_or_create_shm(
    name: str = SHM_NAME,
    max_slots: int = _DEFAULT_MAX_SLOTS,
) -> tuple[posix_ipc.SharedMemory, mmap.mmap]:
    """
    Open an existing SHM segment or create a fresh one.

    Parameters
    ----------
    name:
        POSIX SHM name (must start with ``/``).
    max_slots:
        Maximum number of quote slots.  Used only when creating a new
        segment; ignored when attaching to an existing one.

    Returns
    -------
    tuple[posix_ipc.SharedMemory, mmap.mmap]
        The open SHM object and a writable memory-mapped view.
    """
    total_size = HEADER_SIZE + max_slots * SLOT_SIZE

    # --- Try to attach to existing segment ---
    try:
        shm = posix_ipc.SharedMemory(name, flags=0)  # open only, no O_CREAT
        buf = _mmap(shm.fd, total_size)

        magic_found = bytes(buf[0:8])
        if magic_found == MAGIC_BYTES:
            logger.info("Attached to existing SHM segment", extra={"name": name})
            return shm, buf

        # Stale segment from a different version / format
        logger.warning(
            "SHM magic mismatch — recreating segment",
            extra={"name": name, "found": magic_found.hex(), "expected": MAGIC_BYTES.hex()},
        )
        buf.close()
        shm.close_fd()
        posix_ipc.unlink_shared_memory(name)

    except posix_ipc.ExistentialError:
        pass  # segment doesn't exist yet — create below

    # --- Create fresh segment ---
    return _create_shm(name, max_slots, total_size)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _create_shm(
    name: str,
    max_slots: int,
    total_size: int,
) -> tuple[posix_ipc.SharedMemory, mmap.mmap]:
    """Allocate, initialise, and return a brand-new SHM segment."""
    shm = posix_ipc.SharedMemory(
        name,
        flags=posix_ipc.O_CREAT | posix_ipc.O_EXCL,
        size=total_size,
    )
    buf = _mmap(shm.fd, total_size)

    # Write header
    STRUCT_HEADER.pack_into(buf, 0, MAGIC_BYTES, VERSION, max_slots, SLOT_SIZE)

    # Zero-fill all slots
    buf[HEADER_SIZE : HEADER_SIZE + max_slots * SLOT_SIZE] = bytes(max_slots * SLOT_SIZE)

    buf.flush()

    logger.info(
        "Created new SHM segment",
        extra={"name": name, "total_bytes": total_size, "max_slots": max_slots},
    )
    return shm, buf


def _mmap(fd: int, size: int) -> mmap.mmap:
    """Return a MAP_SHARED read-write mmap over the given file descriptor."""
    return mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)


# ---------------------------------------------------------------------------
# Standalone entry point  (make init-shm)
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    cfg_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with cfg_path.open() as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = _load_config()
    shm_cfg = cfg.get("shm", {})
    name = shm_cfg.get("name", SHM_NAME)
    max_slots = shm_cfg.get("max_slots", _DEFAULT_MAX_SLOTS)

    shm, buf = open_or_create_shm(name=name, max_slots=max_slots)

    total = HEADER_SIZE + max_slots * SLOT_SIZE
    print(f"SHM segment ready: name={name}  size={total} bytes  max_slots={max_slots}")

    buf.close()
    shm.close_fd()
    sys.exit(0)
