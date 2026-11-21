"""Where each lab file lives under the vault."""

from __future__ import annotations

from pathlib import Path


def lab_dir(vault: Path) -> Path:
    return vault / "knowledge-base" / "lab"


def index_path(vault: Path) -> Path:
    return lab_dir(vault) / "lab.md"


def thread_dir(vault: Path, slug: str) -> Path:
    return lab_dir(vault) / slug


def front_page(vault: Path, slug: str) -> Path:
    return thread_dir(vault, slug) / f"{slug}.md"


def timeline_path(vault: Path, slug: str) -> Path:
    return thread_dir(vault, slug) / f"{slug}-timeline.md"


def experiment_note(vault: Path, slug: str, eid: str) -> Path | None:
    """The note for ``eid``, or None when no ``<slug>-<eid>-*.md`` exists."""
    matches = sorted((thread_dir(vault, slug) / "experiments").glob(f"{slug}-{eid}-*.md"))
    return matches[0] if matches else None


def experiment_dir(note: Path) -> Path:
    """The sibling folder (same stem) holding an experiment's scripts and outputs."""
    return note.with_suffix("")
