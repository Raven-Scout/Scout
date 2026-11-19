"""Pending governance changes and the owner's decisions about them.

A governance change (one that edits what Scout is allowed to do) waits for the owner's OK as one
``governance-pending/<slug>.md`` file: YAML frontmatter (``summary``, ``why``, optional ``targets``) and exactly one
fenced ``diff`` block. The folder is the source of truth. A decision is an owner-store ``approval`` row whose ref is
``approve:<id>`` or ``reject:<id>``; the newest one wins. Readers never raise: a file that doesn't fit is counted."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scout.changes.models import SUMMARY_MAX, Signal
from scout.yaml_safe import load_yaml

PENDING_DIR = "governance-pending"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
ID_PREFIX = "gov-"
_MAX_BYTES = 256 * 1024
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z", re.DOTALL)
_FENCE_RE = re.compile(r"^```[ \t]*diff[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_DIFF_PATH_RE = re.compile(
    r"^(?:diff --git a/(\S+) b/(\S+)|(?:---|\+\+\+) (?:[ab]/)?(\S+)|rename (?:from|to) (\S+))", re.M
)


@dataclass(frozen=True)
class Pending:
    id: str
    slug: str
    summary: str
    why: str
    targets: tuple[str, ...]
    path: str  # vault-relative
    diff: str


def _safe_path(raw: str) -> bool:
    if raw == "/dev/null":
        return True
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw):
        return False
    return ".." not in Path(raw).parts and "\\" not in raw


def _diff_paths_ok(diff: str) -> bool:
    """Every path a diff names stays inside the vault (no absolute path, no ``..``)."""
    for m in _DIFF_PATH_RE.finditer(diff):
        for group in m.groups():
            if group is not None and not _safe_path(group):
                return False
    return True


def _parse(path: Path, slug: str) -> Pending | None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
        return None
    m = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if m is None:
        return None
    meta = load_yaml(m.group(1))
    if not isinstance(meta, dict):
        return None
    summary, why, targets = meta.get("summary"), meta.get("why"), meta.get("targets", [])
    if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX:
        return None
    if not isinstance(why, str) or not why.strip():
        return None
    if targets is None:
        targets = []
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        return None
    blocks = _FENCE_RE.findall(m.group(2))
    if len(blocks) != 1 or not _diff_paths_ok(blocks[0]):
        return None
    return Pending(
        id=ID_PREFIX + slug,
        slug=slug,
        summary=summary,
        why=why,
        targets=tuple(targets),
        path=f"{PENDING_DIR}/{slug}.md",
        diff=blocks[0],
    )


def read_pending(vault: Path) -> tuple[list[Pending], int]:
    """Each readable ``governance-pending/<slug>.md`` (sorted by slug) and how many files didn't fit. Never raises.

    Only files directly in the folder count: ``applied/`` and any other subfolder are skipped. A missing folder is
    no files."""
    folder = vault / PENDING_DIR
    out: list[Pending] = []
    bad = 0
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_file() or p.is_symlink())
    except OSError:
        return [], 0
    for path in entries:
        if path.suffix != ".md":
            continue  # not a pending item (a .gitkeep, say)
        if not SLUG_RE.match(path.stem):
            bad += 1
            continue
        try:
            item = _parse(path, path.stem)
        except Exception:  # noqa: BLE001 - unreadable, not UTF-8, malformed YAML: counted, never raised
            item = None
        if item is None:
            bad += 1
        else:
            out.append(item)
    return out, bad


def decision(signals: Sequence[Signal], target_id: str) -> str:
    """``approved``, ``rejected`` or ``awaiting-ok``: the newest ``approve:<id>`` / ``reject:<id>`` row wins."""
    wanted = {f"approve:{target_id}": "approved", f"reject:{target_id}": "rejected"}
    state, newest = "awaiting-ok", None
    for s in signals:
        if s.kind == "approval" and s.ref in wanted and (newest is None or s.ts >= newest):
            state, newest = wanted[s.ref], s.ts
    return state


def undo_requested(signals: Sequence[Signal], change_id: str) -> bool:
    """Whether the owner asked to undo ``change_id`` (an ``approval`` row with ref ``undo:<change_id>``)."""
    ref = f"undo:{change_id}"
    return any(s.kind == "approval" and s.ref == ref for s in signals)
