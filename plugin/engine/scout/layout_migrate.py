"""The layout move: a v1 vault to layout v2 (spec 2026-11-26 §4, ADR 0011).

Pure core, thin IO shell:

- :func:`gather` reads the vault once into an :class:`Inventory` (git's file lists, the heads of person and
  organization notes, both meetings registries, the space registry).
- :func:`build_plan` turns an inventory into a :class:`Plan`: every move, deletion, identity/role split,
  meetings merge and path reference, in execution order. ``scoutctl vault migrate --plan`` prints it, and
  :func:`apply` runs that same object, so the applied result equals the plan (a test checks it).
- :func:`check_preconditions` decides whether the move may run now (§4.3). A failure defers it, which is not
  a failure of the upgrade.
- :func:`apply` runs a plan: checkpoint, journal, moves, commit 1 (renames only), content (meetings merge,
  identity notes, path rewrite, re-keyed state), label parity, seeds, the report, commit 2.
- :func:`recover` finishes or reverses an interrupted move from its journal (§4.9).

Moves are ``os.rename`` for every file, then one ``git update-index --add --remove`` over the tracked old and
new paths: the same index as ``git mv`` (git stores no renames), one index write, and a journal that always
describes the state on disk.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout import config as scout_config
from scout import layout, safe_write
from scout.config import Space
from scout.layout_rewrite import Ref, check_refs, rewrite, unplaced_globs
from scout.plural import are, count

KB = "knowledge-base"
# Frontmatter keys an identity note holds (spec §3.2): the identity itself, handles, spaces, and every key
# kb/privacy.py reads (facts_from_frontmatter), so the identity note is at least as private as its origin.
PRIVACY_KEYS = ("visibility", "federation_scope", "delegate_scope", "kind", "source", "promoted_from")
HANDLE_KEYS = {"person": ("email", "slack_id", "github"), "organization": ("domain", "website")}
IDENTITY_KEYS = frozenset(
    {"name", "type", "aliases", "spaces", *PRIVACY_KEYS, "email", "slack_id", "github", "domain", "website"}
    | {"last_updated", "last_verified"}  # bookkeeping, not facts about the person
)
ROLE_TYPE = "role"
BRAIN_FILES = tuple(f"{k}.md" for k in layout.BRAIN_KINDS)
SIDECAR = ".proposed-merge"
COMMIT1 = "scout-update: move the vault to layout v2 ({n} files)"
COMMIT2 = "scout-update: move the vault to layout v2 (content)"
CHECKPOINT = "Scout: checkpoint before the layout move"
_DATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_SELF_LOCATING_RE = re.compile(r"dirname|parent\.parent|BASH_SOURCE|\.\./")
_CONFLICT_RE = re.compile(r"^(<{7}|>{7})( |$)", re.M)
_TEXT_LIMIT = 4 << 20

# v1 roots of the path map (§4.2), in the order their rows appear.
_RECORD_ROWS: tuple[tuple[str, str, bool, int], ...] = (
    # (v1, v2, is_dir, row of the spec's path map)
    (f"{KB}/session-log.md", "scout/records/session-log.md", False, 6),
    (f"{KB}/session-log", "scout/records/session-log", True, 6),
    (f"{KB}/scout-mistake-audit.md", "scout/records/scout-mistake-audit.md", False, 7),
    (f"{KB}/scout-mistake-audit", "scout/records/scout-mistake-audit", True, 7),
    (f"{KB}/connector-health.md", "scout/records/connector-health.md", False, 8),
    ("dreaming-proposals.md", "scout/records/dreaming-proposals.md", False, 9),
    ("dreaming-proposals", "scout/records/dreaming-proposals", True, 9),
    ("dreaming-proposals-archive.md", "scout/records/dreaming-proposals-archive.md", False, 9),
    ("docs/wishlist", "scout/records/wishlist", True, 10),
)
_SYSTEM_ROWS: tuple[tuple[str, str, bool, int], ...] = (
    ("scripts", "scout/scripts", True, 3),
    ("hooks", "scout/hooks", True, 4),
    ("docs/kb-page-standard.md", "scout/kb-page-standard.md", False, 5),
    ("docs/kb-page-standard.md" + SIDECAR, "scout/kb-page-standard.md" + SIDECAR, False, 5),
)
_INPUT_ROWS: tuple[tuple[str, str, bool, int], ...] = (
    ("inbox.md", "inbox/inbox.md", False, 11),
    ("drop", "inbox/drop", True, 12),
)
_PER_SPACE = ("projects", "topics", "sources")


class MigrationError(RuntimeError):
    """The move failed at ``step``; ``recovered`` says whether the vault is back as it was."""

    def __init__(self, step: str, detail: str, *, recovered: bool) -> None:
        super().__init__(f"layout move failed at {step}: {detail}")
        self.step = step
        self.detail = detail
        self.recovered = recovered


# ----------------------------------------------------------------------------------------------------- data


@dataclass(frozen=True)
class Move:
    src: str
    dst: str
    tracked: bool
    row: int


@dataclass(frozen=True)
class Split:
    """A person or organization note split into a role note (moved, commit 1) and an identity note (written)."""

    kind: str  # person | organization
    origin: str  # its v1 path
    role: str  # where it moves (v2)
    identity: str  # the identity note written (v2)
    space: str
    private_origin: bool


@dataclass(frozen=True)
class SeriesClash:
    dst: str
    kept: str
    renamed: str  # where the other copy went ("" when identical bytes, one dropped)


@dataclass
class Inventory:
    vault: Path
    is_repo: bool
    tracked: frozenset[str]
    untracked: frozenset[str]  # untracked, not ignored
    ignored: frozenset[str]
    spaces: tuple[Space, ...]
    spaces_written: bool  # the vault's own config has spaces:
    spaces_problem: str | None
    heads: Mapping[str, Mapping[str, Any]]  # frontmatter of person/organization candidates
    labels_private: frozenset[str]  # every moved .md whose pre label is private
    newest: Mapping[str, str]  # path -> commit time (ISO) of its last commit, for the meetings merge
    blobs: Mapping[str, str]  # path -> content sha256, for identical-copy checks
    root_entries: tuple[str, ...]
    worktrees: tuple[str, ...]


@dataclass
class Plan:
    layout_version: int
    spaces: tuple[Space, ...]
    default_space: str
    write_spaces: bool
    moves: list[Move] = field(default_factory=list)
    removes: list[str] = field(default_factory=list)  # tracked files git rm'd in commit 1
    splits: list[Split] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    registry_dst: str | None = None
    registry_sources: list[str] = field(default_factory=list)  # [kept (moved), merged (removed)]
    series_clashes: list[SeriesClash] = field(default_factory=list)
    newest_side: dict[str, str] = field(default_factory=dict)  # series slug -> side holding its newest dated file
    compat_links: list[tuple[str, str]] = field(default_factory=list)  # (v1 path, relative target)
    empty_dirs: list[str] = field(default_factory=list)
    left_dirs: list[str] = field(default_factory=list)
    unknown_root: list[str] = field(default_factory=list)
    worktrees: list[str] = field(default_factory=list)
    prefix_hints: list[str] = field(default_factory=list)
    untracked_left: list[str] = field(default_factory=list)  # untracked files under a deleted folder, left
    notes: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.layout_version == 2

    def summary(self) -> dict[str, int]:
        return {
            "moved": len(self.moves),
            "removed": len(self.removes),
            "identity_notes": len(self.splits),
            "meeting_clashes": len(self.series_clashes),
            "compat_links": len(self.compat_links),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "layout_version": self.layout_version,
            "spaces": [s.to_json() for s in self.spaces],
            "write_spaces": self.write_spaces,
            "summary": self.summary(),
            "moves": [dataclasses.asdict(m) for m in self.moves],
            "removes": list(self.removes),
            "splits": [dataclasses.asdict(s) for s in self.splits],
            "refs": [dataclasses.asdict(r) for r in self.refs],
            "registry": {"dst": self.registry_dst, "sources": list(self.registry_sources)},
            "series_clashes": [dataclasses.asdict(c) for c in self.series_clashes],
            "newest_side": dict(self.newest_side),
            "compat_links": [list(x) for x in self.compat_links],
            "empty_dirs": list(self.empty_dirs),
            "left_dirs": list(self.left_dirs),
            "unknown_root": list(self.unknown_root),
            "worktrees": list(self.worktrees),
            "prefix_hints": list(self.prefix_hints),
            "untracked_left": list(self.untracked_left),
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Plan:
        spaces = tuple(Space(**s) for s in d["spaces"])
        p = cls(
            layout_version=d["layout_version"],
            spaces=spaces,
            default_space=scout_config.default_space(spaces).key,
            write_spaces=d["write_spaces"],
        )
        p.moves = [Move(**m) for m in d["moves"]]
        p.removes = list(d["removes"])
        p.splits = [Split(**s) for s in d["splits"]]
        p.refs = [Ref(**r) for r in d["refs"]]
        p.registry_dst = d["registry"]["dst"]
        p.registry_sources = list(d["registry"]["sources"])
        p.series_clashes = [SeriesClash(**c) for c in d["series_clashes"]]
        p.newest_side = dict(d.get("newest_side", {}))
        p.compat_links = [(a, b) for a, b in d["compat_links"]]
        for k in ("empty_dirs", "left_dirs", "unknown_root", "worktrees", "prefix_hints", "untracked_left", "notes"):
            setattr(p, k, list(d.get(k, [])))
        return p


@dataclass
class LayoutOutcome:
    """What the layout stage did on one upgrade: ``skipped``, ``applied``, ``deferred``, ``recovered``, ``rehomed``."""

    state: str
    reason: str = ""
    summary: dict[str, int] = field(default_factory=dict)
    report: str | None = None
    rehomed: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": layout.MIGRATION_ID,
            "state": self.state,
            "reason": self.reason,
            "summary": dict(self.summary),
            "report": self.report,
            "rehomed": list(self.rehomed),
        }


# ------------------------------------------------------------------------------------------------------ git


def _git(
    vault: Path, *args: str, input: str | None = None, check: bool = True, env: Mapping[str, str] | None = None
) -> str:
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        ["git", "-C", str(vault), *args], capture_output=True, text=True, input=input, env=full_env, timeout=600
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def _is_repo(vault: Path) -> bool:
    return (vault / ".git").exists() and subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True
    ).returncode == 0


def _z(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


# ------------------------------------------------------------------------------------------------- gathering


def _v1_roots(vault: Path) -> list[str]:
    roots = [
        *BRAIN_FILES,
        *(f + SIDECAR for f in BRAIN_FILES),
        *(r[0] for r in (*_RECORD_ROWS, *_SYSTEM_ROWS, *_INPUT_ROWS)),
        "meetings",
        f"{KB}/meetings",
        f"{KB}/inbox",
        f"{KB}/people",
        f"{KB}/ontology/entities",
        f"{KB}/personal",
        "action-items/meeting-prep",
        *(f"{KB}/{d}" for d in _PER_SPACE),
    ]
    roots += sorted(p.name for p in vault.glob("run-*.sh") if not p.is_dir())
    return roots


def _head(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm, _ = split_frontmatter(text)
    if fm is None:
        return None
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _walk(vault: Path, roots: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for r in roots:
        p = vault / r
        if p.is_symlink() or p.is_file():
            out.add(r)
        elif p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if d != ".git"]
                for name in [*filenames, *(d for d in dirnames if (Path(dirpath) / d).is_symlink())]:
                    out.add((Path(dirpath) / name).relative_to(vault).as_posix())
    return out


def gather(vault: Path) -> Inventory:
    """Read what the plan needs, and nothing else (§4.5 step 2)."""
    from scout.kb import privacy

    is_repo = _is_repo(vault)
    roots = [r for r in _v1_roots(vault) if (vault / r).exists() or (vault / r).is_symlink()]
    on_disk = _walk(vault, roots)
    if is_repo and roots:
        tracked = set(_git(vault, "ls-files", "-z", "--", *roots).split("\0")) - {""}
        untracked = set(_z(_git(vault, "ls-files", "-o", "-z", "--exclude-standard", "--", *roots)))
    else:
        tracked, untracked = set(), set(on_disk)
    tracked &= on_disk | {t for t in tracked if (vault / t).is_symlink()}
    ignored = on_disk - tracked - untracked
    raw_spaces = scout_config.user_value("spaces", vault)
    problem = scout_config.spaces_problem(vault)
    spaces = scout_config.DEFAULT_SPACES if problem else scout_config.parse_spaces(raw_spaces)
    heads: dict[str, dict[str, Any]] = {}
    for d in (f"{KB}/people", f"{KB}/ontology/entities"):
        for rel in sorted(on_disk):
            if rel.startswith(d + "/") and rel.count("/") == d.count("/") + 1 and rel.endswith(".md"):
                h = _head(vault / rel)
                if h is not None:
                    heads[rel] = h
    policy = privacy.load_policy(vault)
    private: set[str] = set()
    for rel in on_disk:
        if rel.endswith(".md") and rel.startswith(KB + "/"):
            with contextlib.suppress(OSError):
                if privacy.label_file(vault, rel, policy).visibility == "private":
                    private.add(rel)
    newest: dict[str, str] = {}
    blobs: dict[str, str] = {}
    meeting_files = [p for p in on_disk if p.startswith(("meetings/", f"{KB}/meetings/"))]
    for rel in meeting_files:
        with contextlib.suppress(OSError):
            blobs[rel] = hashlib.sha256((vault / rel).read_bytes()).hexdigest()
        if is_repo and rel in tracked:
            newest[rel] = _git(vault, "log", "-1", "--format=%cI", "--", rel, check=False).strip()
        if not newest.get(rel):
            with contextlib.suppress(OSError):
                newest[rel] = _dt.datetime.fromtimestamp((vault / rel).stat().st_mtime, _dt.UTC).isoformat()
    worktrees: list[str] = []
    if is_repo:
        out = _git(vault, "worktree", "list", "--porcelain", check=False)
        paths = [ln[len("worktree ") :] for ln in out.splitlines() if ln.startswith("worktree ")]
        worktrees = [Path(p).name for p in paths[1:]]
    root_entries = tuple(sorted(p.name for p in vault.iterdir()))
    return Inventory(
        vault=vault,
        is_repo=is_repo,
        tracked=frozenset(tracked),
        untracked=frozenset(untracked),
        ignored=frozenset(ignored),
        spaces=spaces,
        spaces_written=raw_spaces is not None,
        spaces_problem=problem,
        heads=heads,
        labels_private=frozenset(private),
        newest=newest,
        blobs=blobs,
        root_entries=root_entries,
        worktrees=tuple(worktrees),
    )


# --------------------------------------------------------------------------------------------------- planning


def _space_of_personal(rel: str, spaces: Sequence[Space], default: str) -> str | None:
    """Rule 1 and 2 of §4.4 for a path under knowledge-base/personal/: the space it belongs to."""
    rest = rel[len(f"{KB}/personal/") :]
    first = rest.split("/", 1)[0]
    for s in spaces:
        if s.key not in (default, "personal") and first.startswith(s.key + "-"):
            return s.key
    return "personal" if any(s.key == "personal" for s in spaces) else None


def _cells(row: str) -> list[str]:
    """A Markdown table row's cells, split on pipes that aren't escaped (``\\|`` inside a wikilink)."""
    body = row.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


def _registry_key(row: str) -> str | None:
    cells = _cells(row)
    if not cells or not cells[0]:
        return None
    first = cells[0]
    m = re.search(r"\[\[([^\]|#]+)", first)
    if m:
        # The series is the folder: [[slug]], [[meetings/slug/slug]] or an occurrence, [[slug/2026-04-20]].
        parts = [x for x in m.group(1).rstrip("\\").removesuffix(".md").split("/") if x]
        if parts[:1] == [KB]:
            parts = parts[1:]
        if len(parts) > 1 and parts[0] == "meetings":
            parts = parts[1:]
        return parts[0].lower() if parts else None
    return re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-") or None


def build_plan(inv: Inventory) -> Plan:
    """The move for ``inv``: pure, deterministic, no model call (§4.4, §4.5 step 2)."""
    vault = inv.vault
    lay_version = layout.layout_version(vault)
    default = scout_config.default_space(inv.spaces).key
    plan = Plan(lay_version, inv.spaces, default, write_spaces=not inv.spaces_written)
    if lay_version == 2:
        return plan
    files = sorted(inv.tracked | inv.untracked | inv.ignored)
    taken: dict[str, str] = {}  # dst (casefolded) -> src

    def add(src: str, dst: str, row: int) -> None:
        plan.moves.append(Move(src, dst, src in inv.tracked, row))
        taken[dst.casefold()] = src

    def ref(old: str, new: str, is_dir: bool, **kw: Any) -> None:
        plan.refs.append(Ref(old, new, is_dir, **kw))

    # rows 1-12: system, records, input
    for name in BRAIN_FILES:
        for suffix in ("", SIDECAR):
            if name + suffix in files:
                add(name + suffix, layout.brain_path(name[:-3]) + suffix, 1)
        ref(name, layout.brain_path(name[:-3]), False, bare_ok=False)
        ref(name + SIDECAR, layout.brain_path(name[:-3]) + SIDECAR, False, bare_ok=False)
    for f in files:
        if "/" not in f and re.fullmatch(r"run-[\w.-]+\.sh", f):
            add(f, layout.runner_path(f), 2)
            ref(f, layout.runner_path(f), False, bare_ok=False)
    for old, new, is_dir, row in (*_SYSTEM_ROWS, *_RECORD_ROWS, *_INPUT_ROWS):
        for f in files:
            if (is_dir and f.startswith(old + "/")) or (not is_dir and f == old):
                add(f, new + f[len(old) :], row)
        ref(old, new, is_dir)
    # rows 13-14: meetings, both homes into the default space (rule 3: a series as a whole)
    meet_dst = layout.space_dir(default, "meetings")
    registries = [r for r in ("meetings/meetings.md", f"{KB}/meetings/meetings.md") if r in files]
    if registries:
        plan.registry_dst = layout.V2["meetings_registry"]

        def size(r: str) -> int:
            try:
                return (vault / r).stat().st_size
            except OSError:
                return 0

        kept = max(registries, key=lambda r: (size(r), r == "meetings/meetings.md"))
        plan.registry_sources = [kept, *(r for r in registries if r != kept)]
        add(kept, plan.registry_dst, 13)
        for r in registries:
            if r != kept:
                if r in inv.tracked:
                    plan.removes.append(r)
                else:
                    plan.untracked_left.append(r)
    ref("meetings/meetings.md", layout.V2["meetings_registry"], False)
    ref(f"{KB}/meetings/meetings.md", layout.V2["meetings_registry"], False)
    sides = {"meetings": "root-meetings", f"{KB}/meetings": "kb-meetings"}
    by_dst: dict[str, list[str]] = {}
    for side in sides:
        for f in files:
            if f.startswith(side + "/") and f not in registries:
                by_dst.setdefault(meet_dst + f[len(side) :], []).append(f)
    for dst, srcs in sorted(by_dst.items()):
        if len(srcs) == 1:
            add(srcs[0], dst, 14)
            continue
        a, b = srcs  # one per side
        if inv.blobs.get(a) and inv.blobs.get(a) == inv.blobs.get(b):
            keep, drop = (a, b) if inv.newest.get(a, "") >= inv.newest.get(b, "") else (b, a)
            add(keep, dst, 14)
            if drop in inv.tracked:
                plan.removes.append(drop)
            else:
                plan.untracked_left.append(drop)
            plan.series_clashes.append(SeriesClash(dst, keep, ""))
            continue
        keep, other = (a, b) if inv.newest.get(a, "") >= inv.newest.get(b, "") else (b, a)
        other_side = next(v for k, v in sides.items() if other.startswith(k + "/"))
        stem, dot, ext = dst.rpartition(".")
        renamed = f"{stem}.from-{other_side}.{ext}" if dot else f"{dst}.from-{other_side}"
        add(keep, dst, 14)
        add(other, renamed, 14)
        plan.series_clashes.append(SeriesClash(dst, keep, renamed))
    # Which side holds each series' newest dated file: its registry row wins a clash (§4.7).
    # A tie (the same dated file on both sides) goes to the side with the series' most recent commit.
    per_side: dict[tuple[str, str], tuple[str, str]] = {}
    for side, label in sides.items():
        for f in files:
            parts = f.split("/")
            if not f.startswith(side + "/") or len(parts) - side.count("/") != 3:
                continue
            slug = parts[-2].lower()
            dated, touched = per_side.get((slug, label), ("", ""))
            if _DATED_RE.match(parts[-1]):
                dated = max(dated, parts[-1][:10])
            touched = max(touched, inv.newest.get(f, ""))
            per_side[(slug, label)] = (dated, touched)
    best: dict[str, tuple[tuple[str, str], str]] = {}
    for (slug, label), when in per_side.items():
        if when[0] and (slug not in best or when > best[slug][0]):
            best[slug] = (when, label)
    plan.newest_side = {slug: label for slug, (_, label) in best.items()}
    ref("meetings", meet_dst, True)
    ref(f"{KB}/meetings", meet_dst, True)
    # rows 15-17: projects, topics, sources (everything to the default space, index included)
    for d in _PER_SPACE:
        old = f"{KB}/{d}"
        new = layout.space_dir(default, d)
        for f in files:
            if f.startswith(old + "/"):
                add(f, new + f[len(old) :], 15 + _PER_SPACE.index(d))
        ref(old, layout.SPACE_PATHS[d], True, per_space=True)
    # rows 18-19: identity and role split, decided by frontmatter type
    for origin, head in sorted(inv.heads.items()):
        kind = head.get("type")
        folder = origin.rsplit("/", 1)[0]
        if (kind, folder) not in (("person", f"{KB}/people"), ("organization", f"{KB}/ontology/entities")):
            continue
        if not isinstance(head.get("name"), str):
            continue
        stem = origin.rsplit("/", 1)[1][:-3]
        sub = "people" if kind == "person" else "organizations"
        role = f"{layout.space_dir(default, sub)}/{stem}-{default}.md"
        identity = origin if kind == "person" else f"{KB}/organizations/{stem}.md"
        add(origin, role, 18 if kind == "person" else 19)
        plan.splits.append(Split(str(kind), origin, role, identity, default, origin in inv.labels_private))
        if kind == "organization":
            ref(origin, identity, False)
    # row 20: knowledge-base/personal/<key>-* to its space
    prefixes: dict[str, int] = {}
    for f in files:
        if not f.startswith(f"{KB}/personal/"):
            continue
        space = _space_of_personal(f, inv.spaces, default)
        first = f[len(f"{KB}/personal/") :].split("/", 1)[0]
        if space and space != "personal":
            add(f, f"{KB}/{space}/" + f[len(f"{KB}/personal/") :], 20)
        elif "-" in first:
            prefixes[first.split("-", 1)[0]] = prefixes.get(first.split("-", 1)[0], 0) + 1
        note = "no personal space is registered: knowledge-base/personal/ stays as it is"
        if space is None and note not in plan.notes:
            plan.notes.append(note)
    for key in sorted(prefixes):
        n = prefixes[key]
        if n >= 3 and scout_config.SPACE_KEY_RE.match(key) and key not in scout_config.RESERVED_SPACE_KEYS:
            plan.prefix_hints.append(f"{n} files are named `{key}-*`; add a space `{key}` to file them there")
    moved_personal = {m.src for m in plan.moves if m.row == 20}
    for top in sorted({m.src[len(f"{KB}/personal/") :].split("/", 1)[0] for m in plan.moves if m.row == 20}):
        space = _space_of_personal(f"{KB}/personal/{top}", inv.spaces, default)
        full = f"{KB}/personal/{top}"
        is_dir = any(s.startswith(full + "/") for s in moved_personal)
        ref(full, f"{KB}/{space}/{top}", is_dir)
    # row 21: knowledge-base/inbox/ deleted (tracked files; untracked ones are left and reported)
    for f in files:
        if f.startswith(f"{KB}/inbox/"):
            if f in inv.tracked:
                plan.removes.append(f)
            else:
                plan.untracked_left.append(f)
    # row 22: action-items/meeting-prep/
    if (vault / "action-items/meeting-prep").is_dir():
        if any(f.startswith("action-items/meeting-prep/") for f in files):
            plan.left_dirs.append("action-items/meeting-prep")
        else:
            plan.empty_dirs.append("action-items/meeting-prep")
    # compatibility links (§4.5 step 5)
    for name in ("scripts", "hooks"):
        if any(m.src.startswith(name + "/") for m in plan.moves):
            plan.compat_links.append((name, f"scout/{name}"))
    for m in plan.moves:
        if m.row == 1 and not m.src.endswith(SIDECAR):
            plan.compat_links.append((m.src, m.dst))
        elif m.row == 2:
            plan.compat_links.append((m.src, m.dst))
    # unknown root entries (Q8) and other work trees
    moved_tops = {m.src.split("/", 1)[0] for m in plan.moves}
    plan.unknown_root = [
        e
        for e in inv.root_entries
        if e not in layout.KNOWN_ROOT_ENTRIES
        and e not in moved_tops
        and e not in ("meetings", "docs", "drop", "scripts", "hooks")
        and not re.fullmatch(r"run-[\w.-]+\.sh|(SKILL|DREAMING|RESEARCH|RESPOND)\.md(\.proposed-merge)?", e)
    ]
    plan.worktrees = list(inv.worktrees)
    check_refs(plan.refs)
    return plan


def plan(vault: Path) -> Plan:
    return build_plan(gather(vault))


# ---------------------------------------------------------------------------------------------- preconditions


@dataclass(frozen=True)
class LiveFacts:
    queued_runners: int
    git_problem: str | None
    brain_sidecars: tuple[str, ...]
    old_plugins: tuple[str, ...]
    conflict_files: tuple[str, ...]


def _git_problem(vault: Path) -> str | None:
    if not _is_repo(vault):
        return "the vault is not a git repository with a commit"
    git_dir = Path(_git(vault, "rev-parse", "--absolute-git-dir").strip())
    for name, what in (
        ("MERGE_HEAD", "a merge"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
        ("BISECT_LOG", "a bisect"),
    ):
        if (git_dir / name).exists():
            return f"{what} is in progress in the vault"
    if (git_dir / "index.lock").exists():
        return "git's index is locked (.git/index.lock)"
    if _git(vault, "diff", "--name-only", "--diff-filter=U").strip():
        return "the vault has unmerged paths"
    return None


def installed_old_plugins(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Installed Scout Claude Code plugins older than layout v2 (precondition 6).

    A plugin is layout-v2-aware when its engine ships ``scout/layout.py``. The release number isn't known at
    code time, and a file check needs none. Missing or unreadable registry: none.
    """
    from scout.agent_runtimes.gate_probe import claude_config_dir

    cfg = claude_config_dir(env)
    try:
        registry = json.loads((cfg / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
        plugins = registry["plugins"]
    except (OSError, ValueError, KeyError, TypeError):
        return ()
    old: list[str] = []
    for name, entries in plugins.items() if isinstance(plugins, dict) else ():
        if not str(name).startswith("scout@"):
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            try:
                root = Path(str(entry["installPath"]))
            except (KeyError, TypeError):
                continue
            if root.exists() and not (root / "engine" / "scout" / "layout.py").exists():
                old.append(f"{name} {entry.get('version', '?')}")
    return tuple(old)


def live_facts(vault: Path, plan_: Plan, *, holder: int | None, env: Mapping[str, str] | None = None) -> LiveFacts:
    from scout.kb import live_run
    from scout.scripts import brain_merge

    try:
        table = live_run.process_table()
        queued = len(live_run.queued_runners(vault, table, holder=holder))
    except Exception:  # noqa: BLE001 - without a process table a waiting run can't be ruled out: fail closed
        queued = -1
    conflicts = []
    for m in plan_.moves:
        if m.tracked and m.src.endswith((".md", ".sh", ".py", ".yaml")):
            with contextlib.suppress(OSError, UnicodeDecodeError):
                if _CONFLICT_RE.search((vault / m.src).read_text(encoding="utf-8")):
                    conflicts.append(m.src)
    return LiveFacts(
        queued_runners=queued,
        git_problem=_git_problem(vault),
        brain_sidecars=tuple(brain_merge.pending_brain_sidecars(vault)),
        old_plugins=installed_old_plugins(env),
        conflict_files=tuple(conflicts),
    )


def check_preconditions(vault: Path, plan_: Plan, live: LiveFacts) -> str | None:
    """Why the move must wait, or None (§4.3). Checked before anything is written."""
    if live.queued_runners < 0:
        return "the process table can't be read, so a Scout run waiting for the session lock can't be ruled out"
    if live.queued_runners:
        return (
            f"{count(live.queued_runners, 'Scout run')} {are(live.queued_runners)} waiting for the session lock; "
            "they would wake on old paths"
        )
    if live.git_problem:
        return live.git_problem
    if live.brain_sidecars:
        return (
            f"brain skill merge sidecars are pending ({', '.join(live.brain_sidecars)}); resolve them first "
            "with `scoutctl bootstrap resolve`"
        )
    if live.conflict_files:
        return (
            f"{count(len(live.conflict_files), 'file')} to move hold conflict markers (first: {live.conflict_files[0]})"
        )
    if live.old_plugins:
        return f"the installed Claude Code plugin is older than layout v2 ({live.old_plugins[0]}); update it first"
    srcs = {m.src for m in plan_.moves}
    seen: set[str] = set()
    for m in plan_.moves:
        key = m.dst.casefold()
        if key in seen:
            return f"two files would move to {m.dst}"
        seen.add(key)
        target = vault / m.dst
        if (target.exists() or target.is_symlink()) and m.dst not in srcs:
            return f"a file already exists where one would move: {m.dst}"
    for s in plan_.splits:
        if s.identity != s.origin and (vault / s.identity).exists():
            return f"an identity note already exists: {s.identity}"
    root = vault.resolve()
    for m in plan_.moves:
        for rel in (m.src, m.dst):
            parent = (vault / rel).parent
            probe = parent
            while not probe.exists():
                probe = probe.parent
            try:
                probe.resolve().relative_to(root)
            except ValueError:
                return f"a planned path leaves the vault through a symlink: {rel}"
    return None


# --------------------------------------------------------------------------------------- frontmatter editing


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(frontmatter text without fences, body) or (None, text)."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, text


def _blocks(fm: str) -> list[tuple[str | None, list[str]]]:
    """Top-level key blocks: (key, lines). A key line starts at column 0; what follows belongs to it."""
    out: list[tuple[str | None, list[str]]] = []
    for line in fm.split("\n"):
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", line)
        if m:
            out.append((m.group(1), [line]))
        elif out:
            out[-1][1].append(line)
        else:
            out.append((None, [line]))
    return out


def _join(blocks: Sequence[tuple[str | None, list[str]]]) -> str:
    return "\n".join(line for _, lines in blocks for line in lines)


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def split_note(
    text: str, *, kind: str, space: str, identity_link: str, role_link: str, private: bool
) -> tuple[str, str]:
    """(role note, identity note) from a person or organization note (§4.6). Text-level: formatting is kept.

    The role note drops ``name`` and ``aliases`` and gains ``type: role``, ``role_of`` and ``space``; every
    other key, ``relationships`` and the body stay. The identity note holds the identity keys verbatim,
    ``spaces: [<space>]``, ``visibility: private`` when its origin was private (by path or frontmatter), and
    one link line to the role note.
    """
    fm, body = split_frontmatter(text)
    if fm is None:
        raise ValueError("no frontmatter")
    blocks = _blocks(fm)
    name_block = next((b for b in blocks if b[0] == "name"), None)
    name = ""
    with contextlib.suppress(yaml.YAMLError, TypeError, AttributeError):
        name = str((yaml.safe_load("\n".join(name_block[1])) or {}).get("name", "")) if name_block else ""
    role_blocks: list[tuple[str | None, list[str]]] = []
    for key, lines in blocks:
        if key in ("name", "aliases"):
            continue
        if key == "type":
            role_blocks.append(("type", [f"type: {ROLE_TYPE}"]))
            role_blocks.append(("role_of", [f"role_of: {_yaml_scalar('[[' + identity_link + ']]')}"]))
            role_blocks.append(("space", [f"space: {space}"]))
            continue
        role_blocks.append((key, lines))
    if private and not any(k == "visibility" for k, _ in role_blocks):
        # Private by path before the move (say an owner's people/** default): the role note keeps it in its own
        # frontmatter, wherever its new folder's default lands.
        role_blocks.append(("visibility", ["visibility: private"]))
    role = f"---\n{_join(role_blocks)}\n---\n{body}"
    keep = {"name", "type", "aliases", *PRIVACY_KEYS, *HANDLE_KEYS.get(kind, ())}
    id_blocks = [(k, lines) for k, lines in blocks if k in keep]
    if private and not any(k == "visibility" for k, _ in id_blocks):
        id_blocks.append(("visibility", ["visibility: private"]))
    id_blocks.append(("spaces", [f"spaces: [{space}]"]))
    label = space.capitalize()
    identity = f"---\n{_join(id_blocks)}\n---\n\n# {name}\n\n- {label}: [[{role_link}]]\n"
    return role, identity


def identity_keys_only(text: str) -> list[str]:
    """The frontmatter keys of an identity note that aren't identity keys (the kb lint rule, §3.2)."""
    fm, _ = split_frontmatter(text)
    if fm is None:
        return []
    return [k for k, _ in _blocks(fm) if k and k not in IDENTITY_KEYS]


# ------------------------------------------------------------------------------------------- meetings registry


_REG_TABLES = ("Active Recurring Meetings", "One-Off Meetings")
_KEY_COLUMNS = ("meeting", "series", "session", "name")
_ONE_OFF_HEADING = re.compile(r"one[- ]?off|ad[- ]?hoc", re.I)
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_SIDE_PATHS = {"root-meetings": "meetings/meetings.md", "kb-meetings": f"{KB}/meetings/meetings.md"}
KEPT_HEADING = "## Kept from the old registries"


@dataclass
class _RegTable:
    """One series table of a v1 registry: which canonical table it feeds and where its key and date sit."""

    one_off: bool
    heads: list[str]
    key_col: int
    date_col: int | None
    rows: list[str]


def _head_name(cell: str) -> str:
    return re.sub(r"[*_`]", "", cell).strip().lower()


def _series_columns(heads: list[str]) -> tuple[int, int | None] | None:
    """(key column, date column) when ``heads`` is a series list, else None.

    A series list's first column names the series (Meeting, Series, Session, Name), or is a Date followed by one
    (a one-off table). A table that only mentions a series further right (an event log keyed by time) is not one.
    """
    names = [_head_name(h) for h in heads]
    if names and names[0] in _KEY_COLUMNS:
        return 0, names.index("date") if "date" in names else None
    if len(names) > 1 and names[0] == "date" and names[1] in _KEY_COLUMNS:
        return 1, 0
    return None


def _registry_parts(text: str) -> tuple[list[_RegTable], list[str]]:
    """A v1 registry's series tables, and every other line as written (frontmatter and the title dropped).

    A table's header is the row right before its ``|---|`` separator. Rows before any ``##`` heading belong to
    a recurring table. A struck-through row (``~~...~~``) is a retired series: kept as text, never a series row.
    """
    lines = text.split("\n")
    i = 0
    if lines and lines[0].strip() == "---":
        end = next((j for j in range(1, len(lines)) if lines[j].strip() == "---"), None)
        if end is not None:
            i = end + 1
    tables: list[_RegTable] = []
    kept: list[str] = []
    heading = ""
    while i < len(lines):
        line = lines[i]
        h = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if h:
            if len(h.group(1)) == 1:
                i += 1
                continue  # the file's title: the merged registry has its own
            heading = h.group(2)
            kept.append(line)
            i += 1
            continue
        is_table = line.strip().startswith("|") and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1])
        if not is_table:
            kept.append(line)
            i += 1
            continue
        heads = _cells(line)
        j = i + 2
        body: list[str] = []
        while j < len(lines) and lines[j].strip().startswith("|"):
            body.append(lines[j])
            j += 1
        cols = _series_columns(heads)
        if cols is None:
            kept.extend(lines[i:j])
            i = j
            continue
        key_col, date_col = cols
        table = _RegTable(bool(_ONE_OFF_HEADING.search(heading)) or key_col == 1, heads, key_col, date_col, [])
        retired = [r for r in body if "~~" in (_cells(r)[key_col] if len(_cells(r)) > key_col else "")]
        table.rows = [r for r in body if r not in retired]
        if retired:
            kept.extend([line, lines[i + 1], *retired])
        tables.append(table)
        i = j
    return tables, kept


def _demote(line: str) -> str:
    h = re.match(r"^(#{1,6})(\s.*)$", line)
    return f"{'#' * min(6, len(h.group(1)) + 2)}{h.group(2)}" if h else line


def merge_registries(
    texts: Sequence[tuple[str, str]],
    *,
    space: str,
    newest_side: Mapping[str, str],
    header: str,
    matches: Mapping[str, str] | None = None,
) -> tuple[str, list[str], dict[str, list[str]]]:
    """(registry text, clashing slugs, slug -> labelled extra cells for the home file) from (side, text) pairs.

    Rows are keyed by their link target's slug, else the normalised meeting name, and unioned into the recurring
    and the one-off table, whatever their v1 heading. On a clash the row from the side holding the series'
    newest dated file wins (``newest_side[slug]``; with none known, the first row read). Rows are reduced to the
    meeting link, the space and the calendar match pattern (§3.3: ``matches[slug]``, from the series' home
    file); every other cell moves to the home file as ``<column>: <value>``, a losing row's cells too, marked
    with their registry (§4.7: nothing is dropped). Text that isn't a series row (notes, event logs, retired
    series) is kept as written under one closing section, per source.
    """
    matches = matches or {}
    rows: dict[bool, dict[str, list[tuple[str, list[str], _RegTable]]]] = {False: {}, True: {}}
    clashes: list[str] = []
    extras: dict[str, list[str]] = {}
    kept_by_side: list[tuple[str, list[str]]] = []
    for side, text in texts:
        tables, kept = _registry_parts(text)
        kept_rows: list[str] = []
        kept_by_side.append((side, kept))
        for table in tables:
            bucket = rows[table.one_off]
            for line in table.rows:
                cells = _cells(line)
                key = _registry_key("| " + cells[table.key_col] + " |") if len(cells) > table.key_col else None
                if key is None or key == "meetings":
                    kept_rows.append(line)  # no series to key it by, or the registry itself: kept as text
                    continue
                entries = bucket.setdefault(key, [])
                if entries and key not in clashes:
                    clashes.append(key)
                entries.append((side, cells, table))
        if kept_rows:
            kept += ["", "Rows with no series of their own:", "", *kept_rows]

    def winner(key: str, entries: list[tuple[str, list[str], _RegTable]]) -> int:
        want = newest_side.get(key)
        return next((n for n, e in enumerate(entries) if e[0] == want), 0)

    def keep(key: str, cells: list[str], table: _RegTable, skip: set[int], source: str | None) -> None:
        seen = set(extras.get(key, []))
        for n, c in enumerate(cells):
            if n in skip or not c or c in ("—", "-"):
                continue
            label = table.heads[n] if n < len(table.heads) and table.heads[n] else f"Column {n + 1}"
            if source:
                label = f"{label} (from {source})"
            item = f"{label}: {c.replace(chr(92) + '|', '|')}"
            if item not in seen:
                extras.setdefault(key, []).append(item)
                seen.add(item)

    def emit(one_off: bool) -> list[str]:
        out: list[str] = []
        for key, entries in sorted(rows[one_off].items()):
            w = winner(key, entries)
            _side, cells, table = entries[w]
            cell = _meeting_cell(cells[table.key_col], key, space)
            if one_off:
                date = cells[table.date_col] if table.date_col is not None and len(cells) > table.date_col else ""
                out.append(f"| {cell} | {space} | {date} |")
            else:
                out.append(f"| {cell} | {space} | {matches.get(key, '')} |")
            skip = {table.date_col} if one_off and table.date_col is not None else set()
            if _bare_link(cells[table.key_col]):
                skip.add(table.key_col)
            keep(key, cells, table, skip, None)
            for n, (side, other, t) in enumerate(entries):
                if n != w:
                    keep(
                        key,
                        other,
                        t,
                        {t.key_col} if _bare_link(other[t.key_col]) else set(),
                        _SIDE_PATHS.get(side, side),
                    )
        return out

    out = [header.rstrip(), "", "## Active Recurring Meetings", ""]
    out += ["| Meeting | Space | Calendar match |", "|---------|-------|----------------|", *emit(False)]
    out += ["", "## One-Off Meetings", "", "| Meeting | Space | Date |", "|---------|-------|------|", *emit(True)]
    sections = []
    for side, kept in kept_by_side:
        while kept and not kept[0].strip():
            kept = kept[1:]
        while kept and not kept[-1].strip():
            kept = kept[:-1]
        if kept:
            sections += ["", f"### From `{_SIDE_PATHS.get(side, side)}`", "", *(_demote(x) for x in kept)]
    if sections:
        out += ["", KEPT_HEADING, "", "What the old registries held besides their series rows, as it was.", *sections]
    return "\n".join(out) + "\n", sorted(clashes), extras


def _calendar_matches(series_dir: Path) -> dict[str, str]:
    """slug -> its home file's ``calendar_match`` patterns, comma-joined (pipes dropped: a table cell)."""
    out: dict[str, str] = {}
    if not series_dir.is_dir():
        return out
    for home in sorted(series_dir.glob("*/*.md")):
        if home.stem != home.parent.name:
            continue
        fm = _head(home) or {}
        raw = fm.get("calendar_match")
        values = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
        joined = ", ".join(str(v).replace("|", "/") for v in values if str(v).strip())
        if joined:
            out[home.stem.lower()] = joined
    return out


def _bare_link(cell: str) -> bool:
    """True when ``cell`` is one wikilink and nothing else: the merged row's link says all it said."""
    return bool(re.fullmatch(r"\[\[[^\]]+\]\]", cell.strip()))


def _meeting_cell(cell: str, slug: str, space: str) -> str:
    """The merged row's link to the series home. Its label is the link's alias, the slug for a link with none, or
    the cell's own text when it names the series without a link; text after a link stays out (it can carry
    attendees, which belong in the home file)."""
    m = re.search(r"\[\[([^\]|#\\]+)\\?(?:\|([^\]]*))?\]\]", cell)
    if m:
        label = (m.group(2) or "").strip() or slug
    else:
        label = re.sub(r"[*_`]", "", cell).strip() or slug
    return f"[[{space}/meetings/{slug}/{slug}\\|{label}]]"


REGISTRY_HEADER = """# Meetings
**Parent:** [[knowledge-base]]

One row per tracked meeting series: its home file, its space and its calendar match patterns (the same as
`calendar_match` in the home file's frontmatter). Attendees and themes live in each series' home file, inside its
space."""


# ---------------------------------------------------------------------------------------------------- apply


def _journal(vault: Path) -> Path:
    return vault / layout.JOURNAL_REL


def _progress(vault: Path) -> Path:
    return vault / layout.MIGRATION_STATE_DIR / "progress.jsonl"


def _log_progress(vault: Path, entry: Mapping[str, Any]) -> None:
    path = _progress(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    with safe_write.open_append(path) as fh:
        fh.write(json.dumps(entry) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _read_progress(vault: Path) -> list[dict[str, Any]]:
    try:
        lines = _progress(vault).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            out.append(json.loads(ln))
    return out


def _phase_done(vault: Path, phase: str) -> bool:
    return any(e.get("phase") == phase for e in _read_progress(vault))


def _vault_abs(vault: Path) -> list[str]:
    out = [str(vault)]
    with contextlib.suppress(OSError):
        out.append(str(vault.resolve()))
    home = str(Path.home())
    for v in list(out):
        if v.startswith(home + "/"):
            out.append("~" + v[len(home) :])
    return list(dict.fromkeys(out))


def _rename(vault: Path, src: str, dst: str) -> None:
    s, d = vault / src, vault / dst
    d.parent.mkdir(parents=True, exist_ok=True)
    os.rename(s, d)
    _prune_empty(vault, s.parent)


def _prune_empty(vault: Path, p: Path) -> None:
    """Drop ``p`` and its parents while empty, up to the vault."""
    while p != vault and p.is_dir() and not p.is_symlink():
        try:
            p.rmdir()
        except OSError:
            break
        p = p.parent


_ZERO = "0" * 40


def _index_entries(vault: Path, paths: Iterable[str]) -> dict[str, tuple[str, str]]:
    """path -> (mode, blob sha) of each tracked path, from the index."""
    want = sorted(set(paths))
    if not want:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for rec in _z(_git(vault, "ls-files", "-s", "-z", "--", *want)):
        meta, _, path = rec.partition("\t")
        mode, sha, _stage = meta.split()
        out[path] = (mode, sha)
    return out


def _index_renames(vault: Path, pairs: Sequence[tuple[str, str]], removes: Sequence[str]) -> None:
    """Rename and remove index entries without reading the work tree (a compatibility link may already stand
    where a renamed file's old folder was, and git refuses paths "beyond a symbolic link")."""
    entries = _index_entries(vault, [s for s, _ in pairs] + list(removes))
    lines = []
    for src, dst in pairs:
        if src not in entries:
            continue
        mode, sha = entries[src]
        lines.append(f"0 {_ZERO}\t{src}")
        lines.append(f"{mode} {sha}\t{dst}")
    lines += [f"0 {_ZERO}\t{r}" for r in removes if r in entries]
    if lines:
        _git(vault, "update-index", "-z", "--index-info", input="\0".join(lines) + "\0")


def _update_index(vault: Path, paths: Iterable[str]) -> None:
    listed = "\0".join(sorted(set(paths)))
    if listed:
        _git(vault, "update-index", "--add", "--remove", "-z", "--stdin", input=listed + "\0")


def _commit(vault: Path, message: str, *, hooks: bool) -> str:
    args = ["-c", "commit.gpgsign=false"]
    if not hooks:
        args += ["-c", "core.hooksPath=/dev/null"]
    args += ["commit", "-q", "--allow-empty", "-m", message]
    if not hooks:
        args.append("--no-verify")
    _git(vault, *args)
    return _git(vault, "rev-parse", "HEAD").strip()


def _checkpoint(vault: Path) -> str | None:
    """Commit tracked edits, staged or not, before the move (§4.3). Untracked files are never added."""
    if not _git(vault, "status", "--porcelain", "--untracked-files=no").strip():
        return None
    _git(vault, "add", "-u")
    return _commit(vault, CHECKPOINT, hooks=True)


def _gitignore_rewrite(text: str, refs: Sequence[Ref], links: Sequence[str]) -> str:
    """Patterns that start with a moved folder rewritten in place (``drop/*`` to ``inbox/drop/*``), and the
    compatibility links ignored (``/scripts``: no trailing slash, a symlink is not a directory)."""
    dirs = sorted((r for r in refs if r.is_dir and "{space}" not in r.new), key=lambda r: -len(r.old))
    out = []
    for line in text.split("\n"):
        s = line.rstrip()
        if not s or s.startswith("#"):
            out.append(line)
            continue
        neg = s.startswith("!")
        body = s[1:] if neg else s
        lead = "/" if body.startswith("/") else ""
        body = body[len(lead) :]
        for r in dirs:
            if body == r.old or body.startswith(r.old + "/") or body == r.old + "/":
                body = r.new + body[len(r.old) :]
                break
        out.append(("!" if neg else "") + lead + body)
    text = "\n".join(out)
    present = {ln.strip() for ln in text.split("\n")}
    add = [f"/{x}" for x in links if f"/{x}" not in present]
    extra = [p for p in ("inbox/drop/*", "!inbox/drop/README.md", ".scout-state/migrations/") if p not in present]
    if add or extra:
        if not text.endswith("\n"):
            text += "\n"
        if add:
            text += "\n# Layout v2 compatibility links (removed by the next upgrade once nothing uses them)\n"
            text += "\n".join(add) + "\n"
        if extra:
            text += "\n# Layout v2\n" + "\n".join(extra) + "\n"
    return text


def _is_text_file(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _TEXT_LIMIT:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def rewrite_targets(vault: Path, plan_: Plan, today: str) -> list[tuple[str, str]]:
    """Every current file whose old paths the content step rewrites, with its kind (§4.10 table)."""
    out: list[tuple[str, str]] = []
    spaces = {s.key for s in plan_.spaces}
    for kind_dir, kind in (
        ("scout/brain", "brain"),
        ("scout/scripts", "script"),
        ("scout/runners", "script"),
        ("scout/hooks", "script"),
    ):
        d = vault / kind_dir
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file() and not p.is_symlink() and not p.name.endswith(SIDECAR):
                    out.append((p.relative_to(vault).as_posix(), kind))
    out.append(("scout-config.yaml", "config"))
    out.append((f"{KB}/ontology/schema.yaml", "schema"))
    note_roots = [KB, "scout/records", "inbox", "action-items/context", "scout/kb-page-standard.md"]
    for root in note_roots:
        base = vault / root
        if base.is_file():
            out.append((root, "note"))
            continue
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            if p.is_symlink():
                continue
            rel = p.relative_to(vault).as_posix()
            parts = rel.split("/")
            if (
                root == KB
                and len(parts) > 3
                and parts[1] in spaces
                and parts[2] == "sources"
                and _DATED_RE.match(parts[-1])
            ):
                continue  # dated source files stay as written (R3)
            out.append((rel, "note"))
    daily = f"action-items/action-items-{today}.md"
    out.append((daily, "note"))
    return [(r, k) for r, k in out if (vault / r).is_file()]


def _brain_snapshot_matches(vault: Path) -> dict[str, bool]:
    """For each brain kind: does its snapshot's provenance hash match the snapshot? (re-recorded only then, §4.8)."""
    from scout.scripts import brain_merge

    snap_dir = vault / ".scout-state" / "last-assembled"
    records = brain_merge.load_provenance(snap_dir) if snap_dir.is_dir() else {}
    out: dict[str, bool] = {}
    for kind in layout.BRAIN_KINDS:
        name = f"{kind}.md"
        prov = records.get(name)
        snap = snap_dir / name
        out[name] = bool(
            prov
            and prov.sha256
            and snap.is_file()
            and prov.sha256 == brain_merge.sha256_text(snap.read_text(encoding="utf-8"))
        )
    return out


def _rekey_state(vault: Path, plan_: Plan, *, apply_rewrite: Any) -> list[str]:
    """Move merge bases and parked drift copies with their file, rewritten like it (§4.8, row 23). Returns paths."""
    from scout.scripts import vault_drift

    moved: list[str] = []
    pairs = [(m.src, m.dst) for m in plan_.moves]
    tracked_state = set(_z(_git(vault, "ls-files", "-z", "--", ".scout-state", check=False)))
    for base_dir in (vault_drift.SNAPSHOT_DIR, vault_drift.LEGACY_SNAPSHOT_DIR):
        for src, dst in pairs:
            if base_dir == vault_drift.LEGACY_SNAPSHOT_DIR and dst.startswith(layout.V2["brain_dir"] + "/"):
                continue  # brain_merge's own snapshots, keyed by file name on every layout: they stay put
            s = vault / base_dir / src
            if s.is_file() and not s.is_symlink():
                d = vault / base_dir / dst
                d.parent.mkdir(parents=True, exist_ok=True)
                os.rename(s, d)
                moved.append(d.relative_to(vault).as_posix())
                if s.relative_to(vault).as_posix() in tracked_state:
                    _update_index(vault, [s.relative_to(vault).as_posix(), moved[-1]])
    drift = vault / vault_drift.DRIFT_DIR
    if drift.is_dir():
        for p in sorted(drift.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(drift).as_posix()
            for src, dst in pairs:
                if rel.startswith(src + ".") or rel.startswith(src + "/"):
                    d = drift / (dst + rel[len(src) :])
                    d.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(p, d)
                    moved.append(d.relative_to(vault).as_posix())
                    if p.relative_to(vault).as_posix() in tracked_state:
                        _update_index(vault, [p.relative_to(vault).as_posix(), moved[-1]])
                    break
    for rel in moved:
        p = vault / rel
        text = _is_text_file(p)
        if text is not None:
            kind = "brain" if "/scout/brain/" in f"/{rel}" else "script" if "/scout/" in f"/{rel}" else "note"
            new = apply_rewrite(text, kind)
            if new != text:
                safe_write.atomic_write_text(p, new)
    return moved


def _seed_unrecorded_bases(vault: Path, plan_: Plan, plugin_root: Path | None, rw: Any = None) -> list[str]:
    """For each managed file with no recorded base whose live text is an unedited render, record that render
    as its base before the rewrite, so the upgrade still fast-forwards it (otherwise no older signature
    matches the rewritten text and it would be parked as replaced)."""
    from scout.scripts import vault_drift

    if plugin_root is None:
        return []
    history = vault_drift.load_render_history()
    out: list[str] = []
    for m in plan_.moves:
        if m.row not in (2, 3, 4):
            continue
        sigs = history.get(m.src, ()) or history.get(m.dst, ())
        if not sigs:
            continue
        snap = vault_drift.snapshot_path(vault, m.dst)
        if snap.exists() or (vault / vault_drift.LEGACY_SNAPSHOT_DIR / m.dst).exists():
            continue
        live = _is_text_file(vault / m.dst)
        if live is None:
            continue
        if any(vault_drift.matches(live, s) for s in sigs):
            # The base gets the rewrite its live file is about to get: equal before, equal after.
            snap.parent.mkdir(parents=True, exist_ok=True)
            kind = "script"
            safe_write.atomic_write_text(snap, rw(live, kind) if rw is not None else live)
            out.append(m.dst)
    return out


def _write_spaces(vault: Path, spaces: tuple[Space, ...]) -> None:
    """Add ``spaces:`` to the vault's config: appended as a commented block when the file is block-style YAML,
    else through the comment-keeping writer (a ``{}`` or flow-style file can't take an appended block)."""
    from scout.scripts.bootstrap import _dump_keeping_comments

    path = vault / "scout-config.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    appended = (
        (text if text.endswith("\n") or not text else text + "\n") + "\n" + scout_config.render_spaces_yaml(spaces)
    )
    try:
        ok = isinstance(yaml.safe_load(appended), dict)
    except yaml.YAMLError:
        ok = False
    if not ok:
        data = yaml.safe_load(text) or {}
        data["spaces"] = [s.to_json() for s in spaces]
        appended = _dump_keeping_comments(text, data)
    safe_write.atomic_write_text(path, appended)


def _record_applied(vault: Path) -> None:
    from scout.scripts.bootstrap import _dump_keeping_comments

    path = vault / "scout-config.yaml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    plugin = data.setdefault("plugin", {})
    applied = plugin.get("applied_migrations")
    if not isinstance(applied, list):
        applied = []
    if layout.MIGRATION_ID in applied:
        return
    plugin["applied_migrations"] = [*applied, layout.MIGRATION_ID]
    safe_write.atomic_write_text(path, _dump_keeping_comments(text, data))


def _force_private(path: Path) -> None:
    """Set ``visibility: private`` in a note's frontmatter (adding a frontmatter block when it has none)."""
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    if fm is None:
        new = f"---\nvisibility: private\n---\n{text}"
    elif re.search(r"^visibility:", fm, re.M):
        new = "---\n" + re.sub(r"^visibility:.*$", "visibility: private", fm, count=1, flags=re.M) + "\n---\n" + body
    else:
        new = f"---\n{fm}\nvisibility: private\n---\n{body}"
    safe_write.atomic_write_text(path, new)


def parity_problem(vault: Path, plan_: Plan) -> str | None:
    """Why the move would make a note less private, or None (§4.5 step 10, checked before anything is written).

    Each moved note is labelled at its new path under the policy the move leaves behind (the rewritten config,
    plus the space registry it writes); a split's role note keeps a private origin's label (split_note). The
    reason names folders and counts, never a note.
    """
    from scout.kb import privacy

    if plan_.done:
        return None
    config = vault / "scout-config.yaml"
    try:
        text = config.read_text(encoding="utf-8") if config.exists() else ""
    except (OSError, UnicodeDecodeError):
        return None  # the policy can't be read now either: the move's own parity check stays the guard
    keys = [s.key for s in plan_.spaces]
    after = rewrite(text, plan_.refs, kind="config", space=plan_.default_space, spaces=keys)
    if plan_.write_spaces and scout_config.user_value("spaces", vault) is None:
        after = (after if after.endswith("\n") or not after else after + "\n") + scout_config.render_spaces_yaml(
            plan_.spaces
        )
    before_policy = privacy.load_policy(vault)
    after_policy = privacy.policy_from_config_text(after)
    split_roles = {s.origin for s in plan_.splits if s.private_origin}
    worse: dict[str, int] = {}
    for m in plan_.moves:
        if not m.src.endswith(".md") or m.src in split_roles:
            continue
        try:
            head, unreadable = privacy.read_head(vault / m.src)
        except OSError:
            continue
        if privacy.classify(m.src, head, before_policy, unreadable=unreadable).visibility != "private":
            continue
        if privacy.classify(m.dst, head, after_policy, unreadable=unreadable).visibility == "normal":
            folder = "/".join(m.dst.split("/")[:2])
            worse[folder] = worse.get(folder, 0) + 1
    if not worse:
        return None
    where = ", ".join(f"{count(n, 'note')} in {f}/" for f, n in sorted(worse.items()))
    return (
        f"the move would make notes less private ({where}); register that space with `visibility: private` in "
        "spaces:, or mark the notes `visibility: private`, then run this again"
    )


def _labels(vault: Path, rels: Iterable[str]) -> dict[str, str]:
    from scout.kb import privacy

    policy = privacy.load_policy(vault)
    out: dict[str, str] = {}
    for rel in rels:
        with contextlib.suppress(OSError):
            out[rel] = privacy.label_file(vault, rel, policy).visibility
    return out


@dataclass
class ApplyResult:
    commit1: str = ""
    commit2: str = ""
    report_rel: str = ""
    more_private: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    config_left: list[str] = field(default_factory=list)
    scripts_to_check: list[str] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)
    checkpoint: str | None = None
    lint_leftovers: int = 0
    created_homes: list[str] = field(default_factory=list)
    made_private: list[str] = field(default_factory=list)  # parity kept by a `visibility: private` line
    edited_homes: list[str] = field(default_factory=list)  # meeting home files given the old registry's cells


def apply(vault: Path, plan_: Plan, *, plugin_root: Path | None = None, today: str | None = None) -> ApplyResult:
    """Run ``plan_`` (§4.5 steps 3-14). The caller holds the session lock and checked the preconditions.

    Before commit 1 any failure is reversed from the journal (:func:`recover`) and raised as
    :class:`MigrationError` with ``recovered=True``. After it, the content step finishes forward.
    """
    today = today or scout_config.today(vault).isoformat()
    res = ApplyResult()
    state_dir = vault / layout.MIGRATION_STATE_DIR
    step = "checkpoint"
    try:
        res.checkpoint = _checkpoint(vault)
        pre = _git(vault, "rev-parse", "HEAD").strip()
        _git(vault, "update-ref", layout.PRE_REF, pre)
        step = "journal"
        state_dir.mkdir(parents=True, exist_ok=True)
        _progress(vault).unlink(missing_ok=True)
        safe_write.atomic_write_text(
            _journal(vault),
            json.dumps(
                {
                    "id": layout.MIGRATION_ID,
                    "pre": pre,
                    "today": today,
                    "engine": _engine_version(),
                    "brain_matched": _brain_snapshot_matches(vault),
                    "plan": plan_.to_json(),
                },
                indent=1,
            ),
        )
        # The untracked state a commit can't carry: brain snapshots and the privacy labels of every moved file.
        pre_dir = state_dir / "pre"
        if pre_dir.exists():
            shutil.rmtree(pre_dir)
        la = vault / ".scout-state" / "last-assembled"
        if la.is_dir():
            for f in sorted(la.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    d = pre_dir / "last-assembled" / f.relative_to(la)
                    d.parent.mkdir(parents=True, exist_ok=True)
                    safe_write.atomic_write_bytes(d, f.read_bytes())
        pre_dir.mkdir(parents=True, exist_ok=True)
        safe_write.atomic_write_text(
            pre_dir / "labels.json", json.dumps(_labels(vault, [m.src for m in plan_.moves if m.src.endswith(".md")]))
        )
        # The registry text the merge needs, before its file goes in commit 1.
        for i, src in enumerate(plan_.registry_sources):
            if (vault / src).is_file():
                safe_write.atomic_write_bytes(pre_dir / f"registry-{i}.md", (vault / src).read_bytes())
        step = "gitignore"
        before_untracked = set(_z(_git(vault, "ls-files", "-o", "-z", "--exclude-standard")))
        gi = vault / ".gitignore"
        old_gi = gi.read_text(encoding="utf-8") if gi.exists() else ""
        # What recovery puts back: the file as it was on disk (tracked or not), or nothing.
        safe_write.atomic_write_text(pre_dir / "gitignore.json", json.dumps({"text": old_gi if gi.exists() else None}))
        new_gi = _gitignore_rewrite(old_gi, plan_.refs, [a for a, _ in plan_.compat_links])
        if new_gi != old_gi:
            safe_write.atomic_write_text(gi, new_gi)
        _log_progress(vault, {"phase": "gitignore"})
        step = "move"
        # The heartbeat runs scripts/heartbeat.sh, and it is what starts self-update (§4.5 step 5): a folder with a
        # compatibility link moves in one rename, with its link straight after, and each linked file gets its link
        # as soon as it has moved, so a crash anywhere in this step leaves every old entry point resolving.
        dir_links = {a: t for a, t in plan_.compat_links if a in layout.COMPAT_LINK_DIRS}
        file_links = {a: t for a, t in plan_.compat_links if a not in dir_links}
        whole: set[str] = set()
        for top, target in dir_links.items():
            if (vault / top).is_dir() and not (vault / top).is_symlink() and not (vault / target).exists():
                (vault / target).parent.mkdir(parents=True, exist_ok=True)
                os.rename(vault / top, vault / target)
                os.symlink(os.path.relpath(vault / target, vault), vault / top)
                whole.add(top)
                _log_progress(vault, {"dir": top})
        for i, m in enumerate(plan_.moves):
            if m.src.split("/", 1)[0] not in whole:
                _rename(vault, m.src, m.dst)
                link = file_links.get(m.src)
                if link is not None and not (vault / m.src).exists() and not (vault / m.src).is_symlink():
                    os.symlink(os.path.relpath(vault / link, (vault / m.src).parent), vault / m.src)
            _log_progress(vault, {"i": i})
        for rel in plan_.removes:
            with contextlib.suppress(FileNotFoundError):
                (vault / rel).unlink()
            _prune_empty(vault, (vault / rel).parent)
            _log_progress(vault, {"rm": rel})
        for empty in plan_.empty_dirs:
            with contextlib.suppress(OSError):
                (vault / empty).rmdir()
        step = "links"  # any link the move step didn't make (a folder whose target already existed)
        for link, target in plan_.compat_links:
            lp = vault / link
            if not lp.exists() and not lp.is_symlink():
                os.symlink(os.path.relpath(vault / target, lp.parent), lp)
        _log_progress(vault, {"phase": "links"})
        step = "index"
        mapping = {m.src: m.dst for m in plan_.moves}
        _index_renames(vault, [(m.src, m.dst) for m in plan_.moves if m.tracked], plan_.removes)
        if new_gi != old_gi:
            _update_index(vault, [".gitignore"])
        step = "ignore check"
        after_untracked = set(_z(_git(vault, "ls-files", "-o", "-z", "--exclude-standard")))
        expected = {mapping.get(p, p) for p in before_untracked if p not in plan_.removes}
        newly_visible = sorted(after_untracked - expected - {".gitignore"})
        if newly_visible:
            raise RuntimeError(
                f"{count(len(newly_visible), 'file')} would become committable (first: {newly_visible[0]})"
            )
        step = "commit 1"
        res.commit1 = _commit(vault, COMMIT1.format(n=len(plan_.moves)) + "\n\nScout-Migration: layout-v2", hooks=False)
        _log_progress(vault, {"phase": "commit1", "sha": res.commit1})
    except Exception as e:  # noqa: BLE001 - reversed below, then raised with the step
        recovered = False
        with contextlib.suppress(Exception):
            recover(vault)
            recovered = True
        raise MigrationError(step, f"{type(e).__name__}: {e}", recovered=recovered) from e
    _content(vault, plan_, res, plugin_root=plugin_root, today=today)
    return res


def _engine_version() -> str:
    import scout

    return scout.__version__


_WROTE_LISTS = ("rewritten", "made_private", "created_homes", "edited_homes")


def _log_wrote(vault: Path, kind: str, rel: str) -> None:
    """Record a file the content step wrote, so a pass that finishes an interrupted one commits it too (§4.9)."""
    _log_progress(vault, {"wrote": rel, "as": kind})


def _content(vault: Path, plan_: Plan, res: ApplyResult, *, plugin_root: Path | None, today: str) -> None:
    """Steps 8-14: repeatable, so a crash here is finished by :func:`recover`."""
    journal = json.loads(_journal(vault).read_text(encoding="utf-8"))
    pre_dir = vault / layout.MIGRATION_STATE_DIR / "pre"
    space = plan_.default_space
    keys = [s.key for s in plan_.spaces]
    vault_abs = _vault_abs(vault)

    def rw(text: str, kind: str) -> str:
        return rewrite(text, plan_.refs, kind=kind, space=space, spaces=keys, vault_abs=vault_abs)

    if plan_.write_spaces and scout_config.user_value("spaces", vault) is None:
        _write_spaces(vault, plan_.spaces)
    # Meetings merge (§4.7)
    if plan_.registry_dst:
        texts = []
        sides = {"meetings/meetings.md": "root-meetings", f"{KB}/meetings/meetings.md": "kb-meetings"}
        for i, src in enumerate(plan_.registry_sources):
            p = pre_dir / f"registry-{i}.md"
            if p.is_file():
                texts.append((sides.get(src, src), p.read_text(encoding="utf-8")))
        text, clashes, extras = merge_registries(
            texts,
            space=space,
            newest_side=plan_.newest_side,
            header=REGISTRY_HEADER,
            matches=_calendar_matches(vault / layout.space_dir(space, "meetings")),
        )
        safe_write.atomic_write_text(vault / plan_.registry_dst, text)
        if clashes and len(plan_.registry_sources) > 1:
            pre_ref = journal.get("pre", "")
            lost = plan_.registry_sources[1]
            sha = _git(vault, "rev-parse", f"{pre_ref}:{lost}", check=False).strip() if pre_ref else ""
            res.unresolved.append(
                f"{count(len(clashes), 'registry row')} clashed ({', '.join(f'`{c}`' for c in clashes)}): the row "
                f"from the side with the newer dated file was kept; the other registry was `{lost}`, blob `{sha}`"
            )
        for slug, cells in extras.items():
            home = vault / layout.space_dir(space, "meetings") / slug / f"{slug}.md"
            if not home.is_file():
                # A row with no series folder yet: its cells still move, into a new home file (§4.7: nothing dropped).
                home.parent.mkdir(parents=True, exist_ok=True)
                safe_write.atomic_write_text(home, f"# {slug}\n")
                res.created_homes.append(home.relative_to(vault).as_posix())
                _log_wrote(vault, "created_homes", res.created_homes[-1])
            body = home.read_text(encoding="utf-8")
            marker = "## From the old meetings registry"
            if marker in body:
                continue
            add = "\n".join(f"- {c}" for c in cells)
            safe_write.atomic_write_text(home, body.rstrip("\n") + f"\n\n{marker}\n\n{add}\n")
            res.edited_homes.append(home.relative_to(vault).as_posix())
            _log_wrote(vault, "edited_homes", res.edited_homes[-1])
    # Identity and role split (§4.6)
    for s in plan_.splits:
        role_path = vault / s.role
        text = role_path.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        if fm is not None and re.search(r"^type:\s*role\s*$", fm, re.M):
            continue  # done by an earlier attempt
        identity_link = s.identity[len(KB) + 1 :].removesuffix(".md")
        role_link = s.role[len(KB) + 1 :].removesuffix(".md")
        role, identity = split_note(
            text, kind=s.kind, space=s.space, identity_link=identity_link, role_link=role_link, private=s.private_origin
        )
        # The identity first: the role note's `type: role` is what marks the split done, and the identity is
        # built from the role note's original text (its name and aliases), which the role write removes.
        ip = vault / s.identity
        ip.parent.mkdir(parents=True, exist_ok=True)
        safe_write.atomic_write_text(ip, identity)
        safe_write.atomic_write_text(role_path, role)
    # Seeds (folders, reports/), before the rewrite so new seeds aren't missed
    from scout.scripts.bootstrap import _dir_layout

    for rel in _dir_layout(plan_.spaces):
        (vault / rel).mkdir(parents=True, exist_ok=True)
    # Merge bases first, so an unedited managed file still fast-forwards after the rewrite
    _seed_unrecorded_bases(vault, plan_, plugin_root, rw)
    _rekey_state(vault, plan_, apply_rewrite=rw)
    # Rewrite old paths (§4.10)
    for rel, kind in rewrite_targets(vault, plan_, today):
        p = vault / rel
        maybe = _is_text_file(p)
        if maybe is None:
            continue
        text = maybe
        new = rw(text, kind)
        if new != text:
            safe_write.atomic_write_text(p, new)
            res.rewritten.append(rel)
            _log_wrote(vault, "rewritten", rel)
        if kind == "config":
            res.config_left = unplaced_globs(new, [r for r in plan_.refs if r.is_dir])
    # Brain snapshots: rewritten in place; provenance re-recorded only where it matched before (§4.8)
    from scout.scripts import brain_merge

    snap_dir = vault / ".scout-state" / "last-assembled"
    if snap_dir.is_dir():
        records = brain_merge.load_provenance(snap_dir)
        matched = journal.get("brain_matched", {})
        for kind in layout.BRAIN_KINDS:
            name = f"{kind}.md"
            for p in (snap_dir / name, snap_dir / brain_merge.PROPOSED_DIR / name):
                snap_text = _is_text_file(p)
                if snap_text is None:
                    continue
                text = snap_text
                new = rw(text, "brain")
                if new != text:
                    safe_write.atomic_write_text(p, new)
            if matched.get(name) and (snap_dir / name).is_file():
                records[name] = dataclasses.replace(
                    records[name], sha256=brain_merge.sha256_text((snap_dir / name).read_text(encoding="utf-8"))
                )
        if records:
            safe_write.atomic_write_text(snap_dir / brain_merge.PROVENANCE_FILE, brain_merge.dumps_provenance(records))
    # Label parity (step 10). The preconditions already refused a move that would make a note less private
    # (parity_problem); a note that still comes out less private here is made private in its own frontmatter,
    # finishing forward, rather than stranding the vault between the commits (§4.9).
    try:
        pre_labels = json.loads((pre_dir / "labels.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pre_labels = {}
    mapping = {m.src: m.dst for m in plan_.moves}
    post = _labels(vault, [mapping[s] for s in pre_labels if s in mapping])
    post_ids = _labels(vault, [s.identity for s in plan_.splits])
    for src, vis in pre_labels.items():
        dst = mapping.get(src)
        if dst is None:
            continue
        if vis == "private" and post.get(dst) == "normal":
            _force_private(vault / dst)
            res.made_private.append(dst)
            _log_wrote(vault, "made_private", dst)
        if vis == "normal" and post.get(dst) == "private":
            res.more_private.append(dst)
    for s in plan_.splits:
        if s.private_origin and post_ids.get(s.identity) == "normal":
            _force_private(vault / s.identity)
            res.made_private.append(s.identity)
            _log_wrote(vault, "made_private", s.identity)
    # Scripts that locate files relative to themselves
    for m in plan_.moves:
        if m.row in (2, 3):
            script = _is_text_file(vault / m.dst)
            if script and _SELF_LOCATING_RE.search(script):
                res.scripts_to_check.append(m.dst)
    # README, record, report (steps 11-12)
    readme = vault / "README.md"
    if not readme.exists() or readme.read_text(encoding="utf-8", errors="replace").startswith(layout.README_MARKER):
        safe_write.atomic_write_text(readme, layout.render_readme(plan_.spaces, engine_version=_engine_version()))
    _record_applied(vault)
    # An earlier, interrupted pass wrote files this one found already done: they go in the report and commit 2 too.
    for e in _read_progress(vault):
        if e.get("as") in _WROTE_LISTS and e.get("wrote"):
            lst = getattr(res, e["as"])
            if e["wrote"] not in lst:
                lst.append(e["wrote"])
    res.report_rel = f"{layout.V2['reports_dir']}/{today}-layout-v2.md"
    report = render_report(plan_, res, pre=journal.get("pre", ""), commit1=_commit1_sha(vault))
    safe_write.atomic_write_text(vault / res.report_rel, report)
    _log_progress(vault, {"phase": "content"})
    # Commit 2: every tracked change plus the files the move created (never other untracked files)
    # Only what the move wrote: an edit the app, Obsidian or a session made meanwhile stays out of Scout's commit.
    touched = [
        *res.rewritten,
        *res.made_private,
        *res.created_homes,
        *(p for s in plan_.splits for p in (s.identity, s.role)),
        *([plan_.registry_dst] if plan_.registry_dst else []),
        *res.edited_homes,
        "scout-config.yaml",
        res.report_rel,
        "README.md",
    ]
    tracked_state = set(_z(_git(vault, "ls-files", "-z", "--", ".scout-state", check=False)))
    touched += sorted(tracked_state)  # re-keyed bases, already renamed in the index
    _git(vault, "add", "-A", "--", *sorted({t for t in touched if (vault / t).exists() or t in tracked_state}))
    res.commit2 = _commit(vault, COMMIT2 + "\n\nScout-Migration: layout-v2 content", hooks=False)
    _git(vault, "update-ref", layout.POST_REF, res.commit2)
    res.commit1 = res.commit1 or _commit1_sha(vault)
    _finish(vault)


def _commit1_sha(vault: Path) -> str:
    for e in _read_progress(vault):
        if e.get("phase") == "commit1":
            return str(e.get("sha", ""))
    return ""


def _finish(vault: Path) -> None:
    state_dir = vault / layout.MIGRATION_STATE_DIR
    with contextlib.suppress(OSError):
        safe_write.atomic_write_text(vault / layout.REVIEW_PENDING_REL, "")
    _journal(vault).unlink(missing_ok=True)
    _progress(vault).unlink(missing_ok=True)
    (vault / layout.DEFERRAL_REL).unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        shutil.rmtree(state_dir / "pre")


# -------------------------------------------------------------------------------------------------- recovery


def recover(vault: Path, *, plugin_root: Path | None = None) -> str:
    """Finish or reverse an interrupted move from its journal (§4.9). Returns ``reversed``, ``finished`` or ``none``.

    Before commit 1: reverse the recorded renames (newest first), restore the removed files and the
    ``.gitignore`` from the pre ref, remove the links, drop the journal. Never ``git reset --hard``: the
    session lock doesn't cover the app's own git writes, Obsidian or an interactive session. After commit 1:
    finish the content step forward (it is repeatable).
    """
    jp = _journal(vault)
    if not jp.exists():
        return "none"
    journal = json.loads(jp.read_text(encoding="utf-8"))
    plan_ = Plan.from_json(journal["plan"])
    if _phase_done(vault, "commit1"):
        _content(
            vault,
            plan_,
            ApplyResult(),
            plugin_root=plugin_root,
            today=journal.get("today") or scout_config.today(vault).isoformat(),
        )
        return "finished"
    pre = journal["pre"]
    progress = _read_progress(vault)
    done = {int(e["i"]) for e in progress if "i" in e}
    # The links go first: through a folder's link its old files still resolve, and would look not yet moved.
    for link, _target in plan_.compat_links:
        lp = vault / link
        if lp.is_symlink():
            lp.unlink()
    # A folder moved in one rename goes back in one rename.
    targets = dict(plan_.compat_links)
    for top in reversed([str(e["dir"]) for e in progress if "dir" in e]):
        target = targets.get(top)
        if target and (vault / target).is_dir() and not (vault / top).exists() and not (vault / top).is_symlink():
            os.rename(vault / target, vault / top)
            _prune_empty(vault, (vault / target).parent)
    # A move can complete without its record (a crash between the rename and the log): every move whose source
    # is gone and whose destination is there counts as done.
    for i, m in enumerate(plan_.moves):
        src, dst = vault / m.src, vault / m.dst
        if i not in done and (dst.exists() or dst.is_symlink()) and not (src.exists() or src.is_symlink()):
            done.add(i)
    for i in sorted(done, reverse=True):
        m = plan_.moves[i]
        if (vault / m.dst).exists() or (vault / m.dst).is_symlink():
            _rename(vault, m.dst, m.src)
    pre_dir = vault / layout.MIGRATION_STATE_DIR / "pre"
    restore = [r for r in plan_.removes if not (vault / r).exists()]
    try:
        old_gi = json.loads((pre_dir / "gitignore.json").read_text(encoding="utf-8"))["text"]
    except (OSError, ValueError, KeyError, TypeError):
        restore.append(".gitignore")  # no saved copy (the move stopped before its gitignore step): from the pre ref
    else:
        if old_gi is None:
            (vault / ".gitignore").unlink(missing_ok=True)
        else:
            safe_write.atomic_write_text(vault / ".gitignore", old_gi)
    for rel in restore:
        if (
            subprocess.run(["git", "-C", str(vault), "cat-file", "-e", f"{pre}:{rel}"], capture_output=True).returncode
            == 0
        ):
            blob = subprocess.run(["git", "-C", str(vault), "show", f"{pre}:{rel}"], capture_output=True).stdout
            (vault / rel).parent.mkdir(parents=True, exist_ok=True)
            safe_write.atomic_write_bytes(vault / rel, blob)
    # Only the listed paths go back in the index.
    touched = sorted(
        {p for m in plan_.moves if m.tracked for p in (m.src, m.dst)} | set(plan_.removes) | {".gitignore"}
    )
    with contextlib.suppress(RuntimeError):
        _git(vault, "reset", "-q", pre, "--", *[t for t in touched])
    if (pre_dir / "last-assembled").is_dir():
        la = vault / ".scout-state" / "last-assembled"
        for p in (pre_dir / "last-assembled").rglob("*"):
            if p.is_file():
                d = la / p.relative_to(pre_dir / "last-assembled")
                d.parent.mkdir(parents=True, exist_ok=True)
                safe_write.atomic_write_bytes(d, p.read_bytes())
    jp.unlink(missing_ok=True)
    _progress(vault).unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        shutil.rmtree(pre_dir)
    return "reversed"


# ---------------------------------------------------------------------------------------------------- report


def render_report(plan_: Plan, res: ApplyResult, *, pre: str, commit1: str) -> str:
    """``reports/YYYY-MM-DD-layout-v2.md`` (§4.11): names, counts and SHAs only, never note content."""
    per_space: dict[str, int] = {}
    for m in plan_.moves:
        parts = m.dst.split("/")
        if parts[0] == KB and len(parts) > 2 and parts[1] in {s.key for s in plan_.spaces}:
            per_space[parts[1]] = per_space.get(parts[1], 0) + 1
    lines = [
        "---",
        "type: report",
        "---",
        "",
        "# Layout v2 move",
        "",
        "## Summary",
        "",
        f"- Files moved: {len(plan_.moves)}; removed: {len(plan_.removes)}; "
        f"identity notes written: {len(plan_.splits)}.",
        f"- Files with path updates: {len(res.rewritten)}.",
        f"- Before the move: `{pre}` (`{layout.PRE_REF}`). Renames: `{commit1}`.",
        f"- After the move: `{layout.POST_REF}`. Undo: `git revert` both commits, then upgrade on the release "
        "before layout v2 (see the upgrade notes).",
        "",
        "## Files per space",
        "",
        *(f"- `{k}`: {v}" for k, v in sorted(per_space.items())),
        *(f"- {h}" for h in plan_.prefix_hints),
        "",
        "## Meetings clashes",
        "",
        *(
            [
                f"- `{c.dst}`: kept `{c.kept}`"
                + (f", the other copy is `{c.renamed}`" if c.renamed else ", identical copy dropped")
                for c in plan_.series_clashes
            ]
            or ["- None."]
        ),
        "",
        "## Identity notes written",
        "",
        *([f"- `{s.identity}` (role note `{s.role}`)" for s in plan_.splits] or ["- None."]),
        "",
        "## References that may not resolve",
        "",
        *([f"- {u}" for u in res.unresolved] or ["- None found."]),
        "",
        "## Config globs left as written",
        "",
        *([f"- `{g}`" for g in res.config_left] or ["- None."]),
        "",
        "## Scripts to check by hand",
        "",
        "These moved scripts locate files relative to themselves (`dirname`, `BASH_SOURCE`, `../`). After the "
        "move they sit two levels deeper.",
        "",
        *([f"- `{s}`" for s in res.scripts_to_check] or ["- None."]),
        "",
        "## Notes that became more private",
        "",
        *([f"- `{p}`" for p in res.more_private] or ["- None."]),
        *(
            ["", "## Notes made private to keep their label", "", *(f"- `{p}`" for p in res.made_private)]
            if res.made_private
            else []
        ),
        "",
        "## Other work trees",
        "",
        *([f"- `{w}`: still holds the old layout; merging it will conflict" for w in plan_.worktrees] or ["- None."]),
        "",
        "## Unknown root entries",
        "",
        *([f"- `{e}`" for e in plan_.unknown_root] or ["- None."]),
        *(
            ["", "## Left in place", "", *(f"- `{d}`" for d in [*plan_.left_dirs, *plan_.untracked_left])]
            if plan_.left_dirs or plan_.untracked_left
            else []
        ),
        "",
        "## Next step",
        "",
        "Most notes were filed in the default space. Review which belong in another space with /scout-update.",
        "",
        "## Move list",
        "",
        "```json",
        json.dumps([[m.src, m.dst] for m in plan_.moves] + [[r, None] for r in plan_.removes], indent=0),
        "```",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------- record re-homing


def rehome_records(vault: Path, clashes: list[str] | None = None) -> list[str]:
    """On a v2 vault, move v1 record files an older app or plugin recreated (§4.12). Returns the moved paths;
    ``clashes`` collects the v1 files left because something is already at their v2 path.

    Each file moves only when nothing is at its v2 path; a clash is left and reported. Tracked files are
    staged as renames; the caller's next commit (a run's or the upgrade's) carries them.
    """
    if layout.layout_version(vault) != 2:
        return []
    moved: list[str] = []
    if clashes is None:
        clashes = []
    is_repo = _is_repo(vault)
    tracked = set(_z(_git(vault, "ls-files", "-z"))) if is_repo else set()
    for old, new, _is_dir, _row in _RECORD_ROWS:
        src_root = vault / old
        if src_root.is_symlink() or not src_root.exists():
            continue
        files = sorted(_walk(vault, [old]))
        for rel in files:
            dst = new + rel[len(old) :]
            if (vault / dst).exists():
                clashes.append(rel)  # left where it is, and reported
                continue
            _rename(vault, rel, dst)
            if rel in tracked:
                _update_index(vault, [rel, dst])
            moved.append(dst)
    return moved


# -------------------------------------------------------------------------------------- compatibility links


def compat_links_present(vault: Path) -> list[str]:
    out = []
    for name in (*layout.COMPAT_LINK_DIRS, *BRAIN_FILES):
        if (vault / name).is_symlink():
            out.append(name)
    out += sorted(p.name for p in vault.glob("run-*.sh") if p.is_symlink())
    return out


def _jobs_on_v2(vault: Path) -> bool:
    """The heartbeat and tick jobs, the pointer and the git hook don't name a v1 path of this vault."""
    home = Path.home()
    v1 = [f"{vault}/scripts/", f"{vault}/hooks/", *(f"{vault}/{n}" for n in (*BRAIN_FILES,))]
    candidates = [
        home / "Library" / "LaunchAgents" / "com.scout.heartbeat.plist",
        home / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist",
        home / ".local" / "state" / "scout" / "engine.json",
        vault / ".git" / "hooks" / "pre-commit",
    ]
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(x in text for x in v1):
            return False
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        cron = ""
    return not any(x in cron for x in v1)


def remove_compat_links_if_unused(vault: Path) -> list[str]:
    """Remove the compatibility links once nothing uses them (§4.5 step 5). Returns the removed names.

    Only on a v2 vault with no move in progress, when the jobs, pointer and git hook name no v1 path and no
    installed Claude Code plugin predates layout v2. A link that isn't a symlink into ``scout/`` is left.
    """
    if layout.layout_version(vault) != 2 or layout.move_in_progress(vault):
        return []
    links = compat_links_present(vault)
    if not links or installed_old_plugins() or not _jobs_on_v2(vault):
        return []
    removed = []
    for name in links:
        p = vault / name
        try:
            target = os.readlink(p)
        except OSError:
            continue
        if not target.startswith("scout/"):
            continue
        p.unlink()
        removed.append(name)
    _drop_link_ignores(vault, removed)
    return removed


def _drop_link_ignores(vault: Path, names: Iterable[str]) -> None:
    """Take the removed links' ``/<name>`` lines (and their header) out of .gitignore, so a stray file at an old
    entry point shows in ``git status`` again."""
    gi = vault / ".gitignore"
    try:
        text = gi.read_text(encoding="utf-8")
    except OSError:
        return
    drop = {f"/{n}" for n in names}
    lines = [ln for ln in text.split("\n") if ln.strip() not in drop]
    out = "\n".join(lines)
    header = "# Layout v2 compatibility links (removed by the next upgrade once nothing uses them)\n"
    if header in out:
        after = out.split(header, 1)[1]
        if not after.strip() or after.startswith("\n"):
            out = out.replace("\n" + header, "\n").replace(header, "")
    if out != text:
        safe_write.atomic_write_text(gi, out)


# ------------------------------------------------------------------------------------------------ the stage


def migrate_layout(vault: Path, *, plugin_root: Path | None = None, holder: int | None = None) -> LayoutOutcome:
    """The layout stage of every upgrade (§4.1); see :func:`_migrate_layout`.

    Any failure is a deferral, never a failed upgrade: self-update and Scout.app retry a deferral on their next
    attempt and never count it toward a release's failure cap (§4.3, §4.9). While a journal is left, runs stay
    paused and run health is red, so an interrupted move is never silent.
    """
    try:
        return _migrate_layout(vault, plugin_root=plugin_root, holder=holder)
    except Exception as e:  # noqa: BLE001 - reported as the deferral's reason, retried by the next upgrade
        what = "interrupted" if layout.move_in_progress(vault) else "failed before it changed anything"
        step = f" at {e.step}" if isinstance(e, MigrationError) else ""
        return _defer(vault, f"the layout move {what}{step}: {type(e).__name__}: {e}")


def _migrate_layout(vault: Path, *, plugin_root: Path | None = None, holder: int | None = None) -> LayoutOutcome:
    """The layout stage of every upgrade (§4.1). The caller holds the session lock.

    Recover an interrupted move first; skip a v2 vault (after re-homing any v1 record a version-skewed
    client recreated); otherwise plan, check the preconditions and apply. A precondition that fails, or a
    failure before commit 1, defers: ``state == "deferred"`` with the reason, recorded in
    ``.scout-state/migrations/layout-v2/deferred.json`` for run health.
    """
    if layout.move_in_progress(vault):
        how = recover(vault, plugin_root=plugin_root)
        if how == "finished":
            return LayoutOutcome("recovered", "finished an interrupted layout move")
    if layout.layout_version(vault) == 2:
        left: list[str] = []
        moved = rehome_records(vault, left)
        _clear_deferral(vault)
        if moved or left:
            why = f"moved {count(len(moved), 'record file')} an older client wrote to v1 paths"
            if left:
                why += f"; left {count(len(left), 'file')} whose v2 path is taken (first: {left[0]})"
            return LayoutOutcome("rehomed", why, rehomed=moved)
        return LayoutOutcome("skipped")
    problem = scout_config.spaces_problem(vault)
    if problem:
        return _defer(vault, f"scout-config.yaml spaces: is invalid ({problem})")
    the_plan = plan(vault)
    if holder is None:
        from scout.kb import live_run

        holder = live_run.lock_holder(vault)
    reason = check_preconditions(vault, the_plan, live_facts(vault, the_plan, holder=holder)) or parity_problem(
        vault, the_plan
    )
    if reason:
        return _defer(vault, reason)
    try:
        res = apply(vault, the_plan, plugin_root=plugin_root)
    except MigrationError as e:
        if e.recovered:
            return _defer(vault, f"the move failed at {e.step} and was reversed: {e.detail}")
        raise  # migrate_layout turns it into an "interrupted" deferral
    return LayoutOutcome("applied", "", the_plan.summary(), report=res.report_rel)


def _defer(vault: Path, reason: str) -> LayoutOutcome:
    path = vault / layout.DEFERRAL_REL
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write.atomic_write_text(
            path, json.dumps({"reason": reason, "at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")})
        )
    return LayoutOutcome("deferred", reason)


def _clear_deferral(vault: Path) -> None:
    (vault / layout.DEFERRAL_REL).unlink(missing_ok=True)


def deferral(vault: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((vault / layout.DEFERRAL_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
