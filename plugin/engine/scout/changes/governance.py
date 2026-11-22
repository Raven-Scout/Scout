"""Pending governance changes and the owner's decisions about them.

A governance change (one that edits what Scout is allowed to do) waits for the owner's OK as one
``governance-pending/<slug>.md`` file: YAML frontmatter (``summary``, ``why``, optional ``targets``, optional
``governance: false`` for an ordinary improvement a run's harness wouldn't let it commit directly) and exactly one
fenced ``diff`` block. The folder is the source of truth. A decision is an owner-store ``approval`` row whose ref is
``approve:<id>@<hash>`` or ``reject:<id>@<hash>``, ``<hash>`` being :func:`item_hash` of the item the owner saw (its
diff and its frontmatter); the newest one for the file as it is now wins, so a file edited after the decision is
waiting again. Readers never
raise: a file that doesn't fit is counted."""

from __future__ import annotations

import hashlib
import json
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
_GIT_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_PATH_PREFIXES = ("--- ", "+++ ", "rename from ", "rename to ", "copy from ", "copy to ")


@dataclass(frozen=True)
class Pending:
    id: str
    slug: str
    summary: str
    why: str
    targets: tuple[str, ...]
    path: str  # vault-relative
    diff: str
    paths: tuple[str, ...] = ()  # what the diff patches (diff_paths): both sides of a rename, never /dev/null
    # False for a harness-fallback item: applying it deletes the file instead of moving it to applied/, so the
    # commit is an ordinary, testable change rather than a governance one.
    governance: bool = True

    @property
    def hash(self) -> str:
        return item_hash(
            self.diff, governance=self.governance, targets=self.targets, summary=self.summary, why=self.why
        )


def item_hash(diff: str, *, governance: bool, targets: Sequence[str], summary: str, why: str) -> str:
    """The full SHA-256 (64 hex digits) of everything the owner saw in a pending file: its diff block and its
    frontmatter (``governance``, ``targets``, ``summary``, ``why``), canonically encoded. An owner decision is bound
    to it, so flipping ``governance`` or adding a target after the OK voids the OK, as editing the diff does."""
    doc = {"diff": diff, "governance": governance, "summary": summary, "targets": list(targets), "why": why}
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_path(raw: str) -> bool:
    """A header path that stays inside the vault; fail closed on anything odd (whitespace, quotes, controls)."""
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    if not raw or re.search(r"[\s\"\\\x00-\x1f\x7f]", raw) or raw.startswith(("/", "~")):
        return False
    return ".." not in raw.split("/")


def _header_path_ok(raw: str, *, timestamped: bool) -> bool:
    if timestamped:
        raw = raw.split("\t", 1)[0]  # `--- a/x<TAB>2026-01-01 ...`
    return raw == "/dev/null" or _safe_path(raw)


def diff_paths(diff: str) -> list[str] | None:
    """The vault-relative paths the diff patches, sorted, once each (both sides of a rename, never ``/dev/null``),
    or None when a header names a path outside the vault, there is no header, or the diff doesn't parse.

    Only lines outside hunks are headers; a hunk's lines are counted off its ``@@`` line, so a removed line such as
    ``-- foo`` (which reads ``--- foo``) is content."""
    headers = 0
    found: set[str] = set()
    old_left = new_left = 0
    for line in diff.split("\n"):
        if old_left or new_left:
            if line.startswith("\\"):
                continue  # "\ No newline at end of file"
            tag = line[:1]
            if tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag in (" ", ""):
                old_left -= 1
                new_left -= 1
            else:
                return None
            if old_left < 0 or new_left < 0:
                return None
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            old_left, new_left = (int(g) if g is not None else 1 for g in hunk.groups())
            continue
        if line.startswith("@@"):
            return None
        if line.startswith("diff --git "):
            m = _GIT_HEADER_RE.match(line)
            if not (m and _safe_path(m.group(1)) and _safe_path(m.group(2))):
                return None
            found.update(m.groups())
            headers += 1
            continue
        for prefix in _PATH_PREFIXES:
            if line.startswith(prefix):
                timestamped = prefix in ("--- ", "+++ ")
                raw = line[len(prefix) :]
                if not _header_path_ok(raw, timestamped=timestamped):
                    return None
                side = raw.split("\t", 1)[0] if timestamped else raw
                if timestamped and side != "/dev/null":
                    if not side.startswith(("a/", "b/")):
                        return None  # git apply strips one leading folder (-p1): unprefixed, it patches another path
                    side = side[2:]
                if side != "/dev/null":
                    found.add(side)
                headers += timestamped
                break
    if headers == 0 or old_left or new_left:
        return None
    return sorted(found)


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
    governance = meta.get("governance", True)
    if not isinstance(governance, bool):
        return None
    if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX:
        return None
    if not isinstance(why, str) or not why.strip():
        return None
    if targets is None:
        targets = []
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        return None
    blocks = _FENCE_RE.findall(m.group(2))
    patched = diff_paths(blocks[0]) if len(blocks) == 1 else None
    if patched is None:
        return None
    return Pending(
        id=ID_PREFIX + slug,
        slug=slug,
        summary=summary,
        why=why,
        targets=tuple(targets),
        path=f"{PENDING_DIR}/{slug}.md",
        diff=blocks[0],
        paths=tuple(patched),
        governance=governance,
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


def decision_ref(verb: str, target_id: str, digest: str) -> str:
    """The ref an owner decision is recorded under: ``<verb>:<id>@<hash of the item the owner saw>``."""
    return f"{verb}:{target_id}@{digest}"


def decision(signals: Sequence[Signal], target_id: str, digest: str) -> str:
    """``approved``, ``rejected`` or ``awaiting-ok``: the newest ``approve:<id>@<digest>`` / ``reject:<id>@<digest>``
    row wins. A row for another digest (the file was changed after the decision) or with none counts for nothing."""
    wanted = {
        decision_ref("approve", target_id, digest): "approved",
        decision_ref("reject", target_id, digest): "rejected",
    }
    state, newest = "awaiting-ok", None
    for s in signals:
        if s.kind == "approval" and s.ref in wanted and (newest is None or s.ts >= newest):
            state, newest = wanted[s.ref], s.ts
    return state


def undo_requested(signals: Sequence[Signal], change_id: str) -> bool:
    """Whether the owner asked to undo ``change_id`` (an ``approval`` row with ref ``undo:<change_id>``)."""
    ref = f"undo:{change_id}"
    return any(s.kind == "approval" and s.ref == ref for s in signals)
