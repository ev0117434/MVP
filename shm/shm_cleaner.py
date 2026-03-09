"""
SHM segment cleanup (T22).

Provides safe removal of the POSIX SHM segment on process shutdown, and a
signal/atexit handler registration helper.

Behaviours
----------

``cleanup_shm(name, buf, shm)``
    1. Closes the mmap view (``buf.close()``).
    2. Closes the SHM file descriptor (``shm.close_fd()``).
    3. Unlinks the SHM name via ``posix_ipc.unlink_shared_memory()``.
    All three steps are wrapped individually so that a failure in one
    does not prevent the others.

``register_cleanup(name, buf, shm)``
    Registers ``cleanup_shm`` as:
    - an ``atexit`` handler (normal exit / unhandled exception)
    - a ``SIGINT`` handler (Ctrl-C)
    - a ``SIGTERM`` handler (``kill`` / ``systemd`` stop)

Standalone usage (``make clean-shm``)
--------------------------------------

    python -m shm.shm_cleaner

Reads the SHM name from ``config/config.yaml`` and removes the segment.
Exits 0 on success (including "already gone").
"""

from __future__ import annotations

import atexit
import logging
import mmap
import signal
import sys
from pathlib import Path
from typing import Optional

import posix_ipc
import yaml

logger = logging.getLogger(__name__)

_SHM_NAME_DEFAULT: str = "/csm_quotes_v1"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cleanup_shm(
    name: str,
    buf: Optional[mmap.mmap],
    shm: Optional[posix_ipc.SharedMemory],
) -> None:
    """
    Close and unlink the SHM segment.

    Safe to call even if *buf* or *shm* is ``None``, or if the segment
    was already removed.

    Parameters
    ----------
    name:
        POSIX SHM name, e.g. ``"/csm_quotes_v1"``.
    buf:
        The ``mmap.mmap`` view opened over the segment.  Pass ``None`` if
        not yet opened.
    shm:
        The ``posix_ipc.SharedMemory`` object.  Pass ``None`` if not yet
        opened.
    """
    if buf is not None:
        try:
            buf.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error closing mmap: %s", exc)

    if shm is not None:
        try:
            shm.close_fd()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Error closing SHM fd: %s", exc)

    try:
        posix_ipc.unlink_shared_memory(name)
        logger.info("SHM segment unlinked", extra={"name": name})
    except posix_ipc.ExistentialError:
        logger.debug("SHM segment not found (already removed)", extra={"name": name})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error unlinking SHM: %s", exc, extra={"name": name})


def register_cleanup(
    name: str,
    buf: Optional[mmap.mmap],
    shm: Optional[posix_ipc.SharedMemory],
) -> None:
    """
    Register ``cleanup_shm`` for automatic execution on process exit.

    Installs handlers for:
    - ``atexit`` — normal exit and unhandled exceptions
    - ``SIGINT``  — Ctrl-C
    - ``SIGTERM`` — graceful shutdown from the OS or service manager

    Parameters
    ----------
    name, buf, shm:
        Same as ``cleanup_shm``.
    """

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        logger.info("Signal received — cleaning up SHM", extra={"signal": signum})
        cleanup_shm(name, buf, shm)
        sys.exit(0)

    atexit.register(cleanup_shm, name, buf, shm)
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    logger.debug("SHM cleanup handlers registered", extra={"name": name})


# ---------------------------------------------------------------------------
# Standalone entry point  (make clean-shm)
# ---------------------------------------------------------------------------

def _load_config_name() -> str:
    cfg_path = Path(__file__).parent.parent / "config" / "config.yaml"
    try:
        with cfg_path.open() as f:
            cfg = yaml.safe_load(f)
        return cfg.get("shm", {}).get("name", _SHM_NAME_DEFAULT)
    except Exception:  # noqa: BLE001
        return _SHM_NAME_DEFAULT


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    name = _load_config_name()

    try:
        posix_ipc.unlink_shared_memory(name)
        print(f"SHM segment '{name}' removed.")
    except posix_ipc.ExistentialError:
        print(f"SHM segment '{name}' not found (already clean).")

    sys.exit(0)
