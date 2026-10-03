"""The one temp-file + ``os.replace`` write used for the index, both caches and the digest (spec §4.12)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, fsync: bool = False) -> None:
    """Replace *path* with *text* atomically. Raises OSError on failure.

    The temp file is made by ``mkstemp`` next to the target (same filesystem, so the
    replace is atomic) under a name unique to this call: two builds writing the same
    file at once never write into each other's temp file — the last replace wins with
    a whole file. ``mkstemp`` creates it 0600, so the target ends up 0600. The temp
    file is removed on any failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = ["atomic_write_text"]
