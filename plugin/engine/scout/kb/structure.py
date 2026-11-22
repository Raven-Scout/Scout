"""`scoutctl kb structure`: does each project folder meet the KB page standard?

``kb lint`` measures bytes and lines. Nothing measured a page's *shape*: loose
files next to the front page, missing sections, run narration in the body, or
a front page whose "Where it stands" froze while new knowledge kept landing in
its folder. This is that measure, report-only, and ``--next`` picks the one
project the dreaming run's nightly KB pass rebuilds. ``kb lint``'s page checks
reuse the per-page parts on the staged diff, to stop new debt.

Per ``<projects_dir>/<name>/`` folder with a ``<name>.md`` front page:

- **loose**: top-level ``.md`` files other than the front page.
- **odd dirs**: subfolders outside ``allowed_dirs`` that aren't a sub-project
  (a ``<sub>/<sub>.md`` index).
- **size**: the front page over ``page_budget_bytes``; ``over_target`` marks
  one over ``page_target_bytes``.
- **sections**: the standard's ``##`` headings, all present.
- **narration**: run-log markers in the body (``narration_markers``).
- **bullets**: a ``bullet_sections`` section with more than ``max_bullets``
  entries, or an entry over ``max_bullet_chars``; an entry is a bullet with its
  wrapped lines, or a paragraph. ``kb lint`` checks what a commit adds.
- **as_of**: the front page's frontmatter has one.
- **stale**: the newest commit touching any other file in the folder (the
  front page and ``archive/`` aside), or a source or topic note under
  ``linked_dirs`` that links to the project, is more than ``stale_days`` after ``as_of``.

The settings live under ``kb_structure`` in scout-config.yaml; the defaults match
the standard the plugin seeds (``docs/kb-page-standard.md``). Folders, files and
front pages matching ``kb_lint.exclude_globs`` (vault-relative) or the KB
schema's ``graph.exclude`` (KB-relative) are left out. A private front page is
still checked, but its row carries only paths, counts and verdicts: no
priority, dates, section names or marker names.

``--next`` takes the first row in table order that the pass can act on, except
that a rebuild left half done in the last weeks, sections still missing, comes
first. Among pages with the same kind of problem, priority × staleness (days since
``as_of``) orders them. A page failing only on size, narration or bullets ranks after every other
failing page, so one that can't get small soon doesn't hold the pick. A page over
``nightly_max_bytes`` is never picked for a rebuild: it is marked ``interactive``, and
``--next`` names it on stderr for the run's report. It never picks a
page labelled private, shared or local-only, whose new copies could widen it or
hit a hard privacy block, or a folder where a rebuild could write into a share,
including a source note, which would skip every page. When that leaves it nothing
to pick, a line on stderr says so, for the run's report. Nor does it pick a folder
whose only problems are odd subfolders or loose files with such a label, which no
night can clear: they would hold the pick for good.

A front page with an effective ``federation_scope`` (not private or local-only) is the
exception. With ``privacy.shared_edits: checked`` (the default) ``--next`` treats it as
any other page: the hook scans each line a run adds to it for private note names
(:mod:`scout.kb.lint_privacy`), so a full rebuild is allowed when nothing narrower can
reach it and nothing it writes is wider: every other file in its folder reaches at least
the page's readers, the page declares no ``federation_scope`` of its own, and no note the
rebuild could write would be read by someone the page isn't shared with (moving text into
a narrower note is fine). The mode is the strictest of the committed, staged and
working-tree config, as in the hook. With ``refuse``, or when any of that fails, one over
budget or with narration
may be picked **trim-only**, after every other page it can act on. The pass then only
cuts whole lines from it, and moves detail only into the existing notes in its folder
that aren't shared (``move_to``), never into a new note or a copy. Removing lines
widens nothing, so no share where a rebuild would write can block it. A trim adds no
date or section, so the page's other problems wait for a rebuild (``checked``) or the
owner (``refuse``).

History comes from ONE ``git log --name-only`` over the projects folder, not a
call per folder, and what git tracks from one ``git ls-files``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from scout import plural
from scout.config import load_config, today
from scout.errors import KBError
from scout.kb.frontmatter import frontmatter_block
from scout.kb.git_io import head_text_lossy, index_text_lossy
from scout.kb.lint_config import CONFIG_FILE, any_match, glob_match, load_lint_config
from scout.kb.privacy import (
    FULL_READERS,
    FederationScope,
    Label,
    Policy,
    SharedEdits,
    classify,
    first_default,
    label_file,
    load_frontmatter_yaml,
    load_policy,
    policy_from_config_text,
    read_head,
    scope_widens,
)

KB_PREFIX = "knowledge-base/"
PRIORITY_RANK = {"🔴": 0, "🟡": 1, "🟢": 2}
# An archive copy older than this is a finished rebuild, or one that stopped making
# progress: either way the page goes back in line. A big page's rebuild runs for weeks.
RESUME_DAYS = 45
# How far back staleness looks for changes to linked notes: one `git log` walks no further.
LINKED_WINDOW_DAYS = 90
# The biggest front page the nightly pass rebuilds (`kb_structure.nightly_max_bytes`'s default).
NIGHTLY_MAX_BYTES = 12288
# Where the nightly pass writes, whatever `allowed_dirs` says: the phase names these folders.
_STEP_DIRS = frozenset({"knowledge", "documents", "research", "archive"})
# A source note's `kind` values (phases/core/write-protocol.md).
_SOURCE_KINDS = ("meeting", "doc", "thread", "pr", "email", "other")
_HEADING = re.compile(r"^## +(.+?)\s*$", re.M)


@dataclass(frozen=True)
class Marker:
    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class StructureConfig:
    projects_dir: str
    skip_projects: frozenset[str]
    allowed_dirs: frozenset[str]
    page_target_bytes: int
    page_budget_bytes: int
    stale_days: int
    required_sections: tuple[str, ...]
    narration_markers: tuple[Marker, ...]
    max_bullet_chars: int = 300
    bullet_sections: tuple[str, ...] = ("Where it stands",)
    max_bullets: int = 6
    nightly_max_bytes: int = NIGHTLY_MAX_BYTES
    linked_dirs: tuple[str, ...] = (f"{KB_PREFIX}sources", f"{KB_PREFIX}topics")


def _strs(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    value = raw.get(key) or []
    if not isinstance(value, list):
        raise KBError(f"kb_structure.{key} must be a list")
    return tuple(str(v) for v in value)


def _markers(raw: dict[str, Any]) -> tuple[Marker, ...]:
    out: list[Marker] = []
    for entry in raw.get("narration_markers") or []:
        if not isinstance(entry, dict) or "pattern" not in entry:
            raise KBError("kb_structure.narration_markers entries need a pattern (and a label)")
        label = str(entry.get("label") or entry["pattern"])
        try:
            out.append(Marker(label, re.compile(str(entry["pattern"]))))
        except re.error as e:
            raise KBError(f"kb_structure.narration_markers {label!r}: bad pattern ({e})") from e
    return tuple(out)


def load_structure_config(vault: Path | None = None) -> StructureConfig:
    raw = load_config(vault).get("kb_structure") or {}
    if not isinstance(raw, dict):
        raw = {}
    projects_dir = raw.get("projects_dir", "knowledge-base/projects")
    # Normalised, because git prints paths this way and `newest_changes` matches them on this prefix.
    parts = PurePosixPath(projects_dir.strip("/")).parts if isinstance(projects_dir, str) else ()
    if not parts or ".." in parts:
        raise KBError("kb_structure.projects_dir must be a folder inside the vault")
    try:
        max_bullet_chars = _positive(raw, "max_bullet_chars", 300)
        linked_dirs = _strs(raw, "linked_dirs") if "linked_dirs" in raw else StructureConfig.linked_dirs
        for d in linked_dirs:
            if not PurePosixPath(d.strip("/")).parts or ".." in PurePosixPath(d).parts:
                raise ValueError(f"linked_dirs entries must be folders inside the vault, not {d!r}")
        return StructureConfig(
            projects_dir="/".join(parts),
            skip_projects=frozenset(_strs(raw, "skip_projects")),
            allowed_dirs=frozenset(_strs(raw, "allowed_dirs")),
            page_target_bytes=int(raw.get("page_target_bytes", 8192)),
            page_budget_bytes=int(raw.get("page_budget_bytes", 15360)),
            stale_days=int(raw.get("stale_days", 3)),
            required_sections=_strs(raw, "required_sections"),
            narration_markers=_markers(raw),
            max_bullet_chars=max_bullet_chars,
            bullet_sections=_strs(raw, "bullet_sections") if "bullet_sections" in raw else ("Where it stands",),
            max_bullets=_positive(raw, "max_bullets", 6),
            nightly_max_bytes=_positive(raw, "nightly_max_bytes", NIGHTLY_MAX_BYTES),
            linked_dirs=tuple("/".join(PurePosixPath(d.strip("/")).parts) for d in linked_dirs),
        )
    except (TypeError, ValueError) as e:
        raise KBError(f"kb_structure: {e}") from e


def _positive(raw: dict[str, Any], key: str, default: int) -> int:
    value = int(raw.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be a positive number, not {value}")
    return value


# ----- per-page checks (pure: text in, findings out) ---------------------------


def split_page(text: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)``. Frontmatter that isn't a YAML mapping reads as ``{}``."""
    lines = text.splitlines(keepends=True)
    block = frontmatter_block(lines)
    if block is None:
        return {}, text
    try:
        fm = load_frontmatter_yaml("".join(block))
    except yaml.YAMLError:
        fm = None
    return (fm if isinstance(fm, dict) else {}), "".join(lines[len(block) + 2 :])


def missing_sections(body: str, required: Sequence[str]) -> list[str]:
    """The required ``##`` headings the body lacks, in the standard's order."""
    present = set(_HEADING.findall(body))
    return [s for s in required if s not in present]


def narration_found(body: str, markers: Sequence[Marker]) -> list[str]:
    """The labels of the markers that occur in the body, sorted."""
    return sorted({m.label for m in markers if m.pattern.search(body)})


_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_SECTION_START = re.compile(r"^#{1,2}\s")


def section_entries(text: str, sections: Sequence[str]) -> list[tuple[int, int, str]]:
    """``(first_line, last_line, text)`` per entry under the ``## <section>`` headings named:
    a bullet with its wrapped lines, or a paragraph. 1-based; the text is its lines
    stripped and joined with spaces. Headings, tables, fenced code and blank lines end an
    entry, and nothing inside a fence counts: a ``# comment`` there is not a heading."""
    wanted = set(sections)
    out: list[tuple[int, int, str]] = []
    entry: list[str] = []
    start = 0
    inside = in_fence = False

    def flush(end: int) -> None:
        if entry:
            out.append((start, end, " ".join(entry)))
            entry.clear()

    lines = text.split("\n")
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            flush(n - 1)
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _SECTION_START.match(line):
            flush(n - 1)
            heading = _HEADING.match(line)
            inside = heading is not None and heading.group(1) in wanted
            continue
        if not inside:
            continue
        if not line.strip() or line.lstrip().startswith(("#", "|")):
            flush(n - 1)
            continue
        if _BULLET.match(line):
            flush(n - 1)
        if not entry:
            start = n
        entry.append(line.strip())
    flush(len(lines))
    return out


@dataclass(frozen=True)
class BulletCheck:
    """A front page's ``bullet_sections`` against the standard: how many entries each holds, and
    how many entries in all run over ``max_bullet_chars``."""

    counts: dict[str, int]  # section -> entries, for each section present
    long: int

    def over(self, limit: int) -> dict[str, int]:
        """The sections holding more than ``limit`` entries."""
        return {s: n for s, n in self.counts.items() if n > limit}


def check_bullets(text: str, cfg: StructureConfig) -> BulletCheck:
    """Count ``text``'s entries per bullet section (a bullet with its wrapped lines, or a paragraph,
    as :func:`section_entries` reads them) and those over the per-entry limit."""
    counts: dict[str, int] = {}
    long = 0
    for section in cfg.bullet_sections:
        entries = section_entries(text, [section])
        if entries:
            counts[section] = len(entries)
        long += sum(len(t) > cfg.max_bullet_chars for _, _, t in entries)
    return BulletCheck(counts, long)


def page_as_of(fm: dict[str, Any]) -> dt.date | None:
    value = fm.get("as_of")
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip()[:10]) if value else None
    except ValueError:
        return None


# ----- history: one pass --------------------------------------------------------


def newest_changes(
    vault: Path, pathspec: str | Sequence[str], now: dt.date, since: dt.date | None = None
) -> dict[str, dt.date]:
    """Each path ever committed under ``pathspec`` → the date of the newest commit touching it.

    One ``git log`` for the whole folder (or folders), newest first, so a path's first sighting
    is its newest change. The date is the author date, which an amend or a rebase
    leaves alone. A date after ``now`` doesn't count: one commit from a wrong
    clock would otherwise keep the page stale until that date, whatever a night did.
    ``since`` leaves out commits made before that day (git's commit date) and changes
    dated before it, so a caller that only cares about recent changes doesn't walk
    the whole history. Paths are relative to ``vault``, which may sit inside a
    larger repo. ``{}`` outside a repo, before the first commit, or without git.
    """
    specs = [pathspec] if isinstance(pathspec, str) else list(pathspec)
    if not specs:
        return {}
    window = [f"--since={since.isoformat()}"] if since is not None else []
    cmd = ["git", "log", *window, "--relative", "--no-renames", "--name-only", "-z", "--format=%x00%as", "--", *specs]
    try:
        proc = subprocess.run(cmd, cwd=vault, capture_output=True)
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    newest: dict[str, dt.date] = {}
    day: dt.date | None = None
    # NUL-separated: empty, date, then the paths, the first with a leading newline.
    prefixes = tuple(s + "/" for s in specs)
    floor = since or dt.date.min
    for raw in proc.stdout.decode("utf-8", errors="replace").split("\0"):
        token = raw.removeprefix("\n")
        if token.startswith(prefixes):
            # The newest date wins, not the first sighting: author dates needn't follow commit order.
            if day is not None and floor <= day <= now and day > newest.get(token, dt.date.min):
                newest[token] = day
        elif token:
            day = dt.date.fromisoformat(token)
    return newest


@dataclass(frozen=True)
class _Listing:
    """What git knows about the projects folder, from one ``git ls-files``."""

    files: frozenset[str]  # tracked, or untracked and not ignored
    tracked_dirs: frozenset[str]  # ``<project>/<subfolder>`` holding a tracked file


def _listing(vault: Path, projects_dir: str) -> _Listing | None:
    """None outside a repo or without git: the folder's own contents are then all there is to go on."""
    cmd = ["git", "ls-files", "-z", "-t", "--cached", "--others", "--exclude-standard", "--", projects_dir]
    try:
        proc = subprocess.run(cmd, cwd=vault, capture_output=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    files: set[str] = set()
    tracked_dirs: set[str] = set()
    prefix = projects_dir + "/"
    for entry in proc.stdout.decode("utf-8", errors="replace").split("\0"):
        if not entry:
            continue
        tag, path = entry[0], entry[2:]
        files.add(path)
        parts = path.removeprefix(prefix).split("/")
        if tag != "?" and len(parts) >= 3:
            tracked_dirs.add(f"{parts[0]}/{parts[1]}")
    return _Listing(frozenset(files), frozenset(tracked_dirs))


# ----- the folder check ---------------------------------------------------------


@dataclass
class _Folder:
    name: str
    rel: str  # vault-relative folder, no trailing slash
    loose: list[str]
    odd_dirs: list[str]
    other_dirs: set[str]  # subfolders whose changes count toward staleness (all but archive/)


def _scan(
    vault: Path,
    rel: str,
    name: str,
    cfg: StructureConfig,
    excluded: Callable[[str], bool],
    listing: _Listing | None,
) -> _Folder:
    """A folder of images or editor state git doesn't track isn't odd, and a gitignored file isn't loose."""
    folder = _Folder(name, rel, [], [], set())
    for entry in sorted((vault / rel).iterdir(), key=lambda p: p.name):
        entry_rel = f"{rel}/{entry.name}"
        if entry.is_dir():
            if excluded(entry_rel + "/"):
                continue
            tracked = listing is None or f"{name}/{entry.name}" in listing.tracked_dirs
            if tracked and entry.name not in cfg.allowed_dirs and not (entry / f"{entry.name}.md").is_file():
                folder.odd_dirs.append(entry.name)
            if entry.name != "archive":
                folder.other_dirs.add(entry.name)
        elif entry.name.endswith(".md") and entry.name != f"{name}.md" and not excluded(entry_rel):
            if listing is None or entry_rel in listing.files:
                folder.loose.append(entry.name)
    return folder


def _folder_last_change(
    folder: _Folder, history: dict[str, dt.date], excluded: Callable[[str], bool]
) -> dt.date | None:
    prefix = folder.rel + "/"
    loose = set(folder.loose)
    newest: dt.date | None = None
    for path, day in history.items():
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix) :]
        first, sep, _ = rest.partition("/")
        counts = first in folder.other_dirs if sep else rest in loose
        if counts and not excluded(path) and (newest is None or day > newest):
            newest = day
    return newest


@dataclass(frozen=True)
class LinkedChange:
    """The newest change to a source or topic note that links to a project.

    ``shown`` is the newest such change to a note that isn't private: what a row may print."""

    newest: dt.date
    shown: dt.date | None


# A linked note is read whole to find its links; the standard keeps them under 10 KB, so this is generous.
_LINKED_READ_CAP = 256 * 1024


def _project_keys(vault: Path, folders: Sequence[_Folder]) -> dict[str, str]:
    """Each name a wikilink may use for a project's front page, lowercased → the project.

    The file's stem, as Obsidian resolves a link, and the front page's ``aliases``."""
    keys: dict[str, str] = {}
    for folder in folders:
        keys.setdefault(folder.name.lower(), folder.name)
    for folder in folders:
        text, _ = read_head(vault / folder.rel / f"{folder.name}.md")
        fm, _ = split_page(text or "")
        aliases = fm.get("aliases")
        for alias in aliases if isinstance(aliases, list) else [aliases] if isinstance(aliases, str) else []:
            if isinstance(alias, str) and alias.strip():
                keys.setdefault(alias.strip().lower(), folder.name)
    return keys


def _linked_projects(text: str, projects_dir: str, keys: Mapping[str, str]) -> set[str]:
    """The projects a note's wikilinks point at: a front page by name or alias, or any path
    inside a project folder (``[[projects/<name>/knowledge/x]]``)."""
    from scout.kb.lint_rules import wikilink_targets

    folder_word = PurePosixPath(projects_dir).name.lower()
    out: set[str] = set()
    for target in wikilink_targets(text):
        parts = [p for p in target.strip().removesuffix(".md").split("/") if p]
        if not parts:
            continue
        lowered = [p.lower() for p in parts]
        # A path into a project folder: the segment after `projects/` is the folder's own name.
        inside = {
            name
            for i, word in enumerate(lowered[:-1])
            if word == folder_word and (name := keys.get(lowered[i + 1])) is not None and name.lower() == lowered[i + 1]
        }
        out |= inside
        # A bare name resolves by file stem or alias; a path elsewhere names some other file.
        if len(parts) == 1 and (name := keys.get(lowered[0])) is not None:
            out.add(name)
    return out


def linked_changes(
    vault: Path,
    folders: Sequence[_Folder],
    cfg: StructureConfig,
    since: dt.date | None,
    now: dt.date,
    excluded: Callable[[str], bool],
    policy: Policy,
) -> dict[str, LinkedChange]:
    """Each project → the newest change, from ``since`` on, to a note under ``linked_dirs``
    (source and topic notes) that links to it.

    A project page is current only if what landed about it since ``as_of`` is in it. New
    knowledge often lands outside the folder: a meeting's source note, a cross-project topic.
    One ``git log`` over ``linked_dirs``; each note changed in the window is read once, as it
    stands now. A note's later edit that drops the link no longer counts, which is right: the
    page doesn't need it."""
    if since is None or not folders:
        return {}
    dirs = [d for d in cfg.linked_dirs if (vault / d).is_dir()]
    if not dirs:
        return {}
    changed = newest_changes(vault, dirs, now, since=since)
    if not changed:
        return {}
    keys = _project_keys(vault, folders)
    out: dict[str, LinkedChange] = {}
    for rel, day in changed.items():
        if not rel.endswith(".md") or excluded(rel):
            continue
        path = vault / rel
        try:
            with path.open("rb") as fh:
                text = fh.read(_LINKED_READ_CAP).decode("utf-8", errors="replace")
        except OSError:
            continue  # deleted since, or unreadable: nothing links from it now
        names = _linked_projects(text, cfg.projects_dir, keys)
        if not names:
            continue
        private = label_file(vault, rel, policy).visibility == "private"
        for name in names:
            seen = out.get(name)
            newest = day if seen is None or day > seen.newest else seen.newest
            shown = None if seen is None else seen.shown
            if not private and (shown is None or day > shown):
                shown = day
            out[name] = LinkedChange(newest, shown)
    return out


def _pinned(label: Label, shared_edits: SharedEdits = "refuse") -> bool:
    """A file the nightly pass must not copy or move (the phase's rule, enforced here for its pick).

    With ``shared_edits: checked`` a note with an effective share isn't pinned: the hook scans
    every line a run adds to it. One shared only by path, or private, or local-only, still is."""
    if label.visibility == "private" or label.local_only:
        return True
    return label.shared_file and not (shared_edits == "checked" and _trimmable(label))


def _trimmable(label: Label) -> bool:
    """A page with an effective share, and nothing else: the hook's shared-note check (#569) guards
    it, so the pass may cut lines from it (``refuse``) or rebuild it (``checked``). It checks the
    effective ``federation_scope`` only, so a page shared by path or one that declares a scope
    while sharing is off has no such guard, and stays skipped."""
    return label.federation_scope != "none" and label.visibility != "private" and not label.local_only


def _wider(label: Label, scope: FederationScope) -> bool:
    """Whether a note labelled ``label`` could be read by someone a page shared with ``scope`` isn't.

    A note shared by path, or one declaring a scope while sharing is off, has no audience to
    compare, so it counts as wider. With ``scope`` ``none`` this is ``label.shared_file``."""
    if not label.shared_file:
        return False
    return label.federation_scope == "none" or scope_widens(scope, label.federation_scope)


def _any_file_name(share_glob: str) -> str:
    """``share_glob`` with a wildcard file name opened up. A rebuild picks its notes' names,
    so a share on ``knowledge/*-decisions.md`` covers ``knowledge/`` for this check. An exact
    name stays exact: a rebuild won't pick it (the one note it does name is probed as
    such), one that exists is found in the folder, and ``kb share`` writes these for one note."""
    head, sep, last = share_glob.rpartition("/")
    if "*" not in last and "?" not in last:
        return share_glob
    return f"{head}{sep}{'**' if '**' in last else '*'}"


def _probe_policy(policy: Policy) -> Policy:
    """``policy`` for labelling notes a rebuild would write, whatever it names them.

    Entries that share nothing go, or one opened up here could shadow a real share
    behind it: ``federation_defaults`` is first match wins."""
    return dataclasses.replace(
        policy,
        shared_files=tuple(_any_file_name(g) for g in policy.shared_files),
        federation_defaults=tuple(
            dataclasses.replace(d, glob=_any_file_name(d.glob)) for d in policy.federation_defaults if d.scope != "none"
        ),
    )


def _sources_wide(probe_glob: str) -> str:
    """``probe_glob`` as all source notes, if its wildcard name could reach any of them. A
    note files under its own date and a rebuild moves old meetings, so no one month stands
    for the rest."""
    sources = f"{KB_PREFIX}sources/"
    literal = re.split(r"[*?]", probe_glob, maxsplit=1)[0]
    reaches = literal != probe_glob and (sources.startswith(literal) or literal.startswith(sources))
    last = probe_glob.rpartition("/")[2]
    return f"{sources}**" if reaches and ("*" in last or "?" in last) else probe_glob


def _new_sources_shared(probes: Policy, day: dt.date, scope: FederationScope = "none") -> bool:
    """Whether a source note a rebuild writes would be shared wider than ``scope`` (the front
    page's; ``none``: shared at all). They sit outside every project folder, and a folder
    ceiling can depend on the note's ``kind``, so each kind is checked."""
    wide = dataclasses.replace(
        probes,
        shared_files=tuple(_sources_wide(g) for g in probes.shared_files),
        federation_defaults=tuple(
            dataclasses.replace(d, glob=_sources_wide(d.glob)) for d in probes.federation_defaults
        ),
    )
    rel = f"{KB_PREFIX}sources/{day:%Y-%m}/{day}-new-note.md"
    return any(_wider(classify(rel, f"---\nkind: {kind}\n---\n", wide), scope) for kind in _SOURCE_KINDS)


def _may_declare_a_share(text: str | None) -> bool:
    """Whether a note's text could set ``federation_scope``, without parsing it.

    Fails closed: anchors and merges still spell the key out somewhere in the document,
    so an escape in a quoted key (``"federation\\x5fscope"``) is the only way to hide
    it, and any backslash in the frontmatter block counts."""
    if text is None:
        return False
    if "federation_scope" in text:
        return True
    if "\\" not in text:
        return False
    block = frontmatter_block(text.removeprefix("\ufeff").splitlines())
    return block is not None and any("\\" in line for line in block)


def _rebuild_writes_into_a_share(
    vault: Path, folder: _Folder, cfg: StructureConfig, policy: Policy, probes: Policy, scope: FederationScope = "none"
) -> bool:
    """Whether a rebuild of this folder could put page facts where someone else reads them.

    The front page's own label doesn't say: a rebuild moves its facts into the
    subfolders, and a share on one reaches its readers at the next sync, with no
    way to recall them. So a new note in any subfolder the step writes to, and
    every note already in the folder, must be shared no wider than the front
    page: ``scope``, its effective ``federation_scope`` (``none``: unshared, so
    they must be unshared too; :func:`_wider`).

    Folders can hold many notes and few match a share rule, so a note is only classified
    if a rule matches its path or :func:`_may_declare_a_share`: with neither, nothing
    can make it shared."""
    for sub in sorted(cfg.allowed_dirs | _STEP_DIRS):
        if _wider(classify(f"{folder.rel}/{sub}/{folder.name}-new-note.md", None, probes), scope):
            return True
    # The one note the step names itself, so an exact share on it counts.
    if _wider(classify(f"{folder.rel}/knowledge/{folder.name}-decisions.md", None, probes), scope):
        return True
    front = vault / folder.rel / f"{folder.name}.md"
    for path in (vault / folder.rel).rglob("*.md"):
        if path == front:
            continue
        rel = path.relative_to(vault).as_posix()
        text, unreadable = read_head(path)
        by_path = any(glob_match(g, rel) for g in policy.shared_files) or (
            policy.federation_enabled and first_default(policy, rel) is not None
        )
        if (by_path or _may_declare_a_share(text)) and _wider(
            classify(rel, text, policy, unreadable=unreadable), scope
        ):
            return True
    return False


def _shared_edits(vault: Path, disk: Policy) -> SharedEdits:
    """The commit hook's mode: ``checked`` only when the committed, staged and working-tree config all say so."""
    policies = [disk]
    for text in (head_text_lossy(vault, CONFIG_FILE), index_text_lossy(vault, CONFIG_FILE)):
        if text is not None:
            policies.append(policy_from_config_text(text))
    return "checked" if all(p.shared_edits == "checked" for p in policies) else "refuse"


def _shared_rebuild_unsafe(vault: Path, folder: _Folder, policy: Policy, front: Label, day: dt.date) -> bool:
    """Whether a rebuild of a shared front page could carry narrower material into it, or can't commit.

    A rebuild reads the whole folder and rewrites the page from it, so a paraphrase of a
    note its readers can't see would reach them, and the hook can't see a paraphrase.
    So every other file in the folder (``archive/`` included) must reach at least the
    page's readers: none private, local-only, unshared, or shared with fewer people.
    A page declaring its own ``federation_scope`` can't be rebuilt either: step 5.1's
    archive copy would declare it too, a new file setting a scope, which the hook
    refuses every night. Either way the caller falls back to a trim.

    One exception keeps a rebuild going across nights (:func:`_rebuild_own_files`): an
    unshared normal file the rebuild itself added (its archive copy, and the notes a page
    shared on its own moves its text into, which are unshared) doesn't count. Nothing
    else is exempt: not a note the owner added meanwhile, not one shared with fewer people,
    and never a private or local-only one."""
    if front.federation_scope_declared != "none":
        return True
    own = _rebuild_own_files(vault, folder, day) if _rebuild_under_way(vault, folder, day) else set()
    page = vault / folder.rel / f"{folder.name}.md"
    for path in (vault / folder.rel).rglob("*"):
        if path == page or not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(vault).as_posix()
        label = label_file(vault, rel, policy)
        if label.visibility == "private" or label.local_only:
            return True
        if label.federation_scope == "none":
            if rel in own:
                continue
            return True
        if scope_widens(label.federation_scope, front.federation_scope):
            return True
    return False


REBUILD_SUBJECT = "nightly KB pass"


def rebuild_subject(front: str) -> str:
    """The commit summary ``kb structure --next`` prints for a page's nightly pass, for every commit of it."""
    return f"{REBUILD_SUBJECT} — {front}"


def _is_rebuild_subject(subject: str, front: str) -> bool:
    """Whether ``subject`` names this page's nightly pass, in any case, with any prefix (``dreaming: ``)
    and any dash. Tied to ``front``, so another page's pass can't vouch for files here."""
    pattern = re.escape(REBUILD_SUBJECT.casefold()) + r"\s*[—–-]+\s*" + re.escape(front.casefold()) + r"(?![\w./-])"
    return re.search(pattern, subject.casefold()) is not None


def _git_out(vault: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=vault, capture_output=True)
    except OSError:
        return None
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def _rebuild_own_files(vault: Path, folder: _Folder, day: dt.date) -> set[str]:
    """Files the rebuild under way added to the folder, by its commits.

    The window opens at the commit that added the folder's oldest archive copy (its commit
    date, not the date in its name, which a run could write), and only a copy added within
    :data:`RESUME_DAYS` and still tracked counts; an uncommitted or untracked copy opens
    none. A file counts when a commit in the window added it (a move in counts as an add)
    and that commit is the rebuild's: its subject names this page's pass
    (:func:`rebuild_subject`, matched by :func:`_is_rebuild_subject`). A move in (git's
    ``R``) isn't an add: the file was the vault's before. Changing the front page doesn't
    make a commit the rebuild's either: an owner's commit may add a note and edit the page."""
    front = f"{folder.rel}/{folder.name}.md"
    archive = vault / folder.rel / "archive"
    copy = re.compile(re.escape(folder.name) + r"-\d{4}-\d{2}-\d{2}-pre-restructure\.md")
    opened: list[dt.datetime] = []
    tracked = set((_git_out(vault, "ls-files", "-z", "--", f"{folder.rel}/archive") or "").split("\0"))
    for entry in sorted(archive.iterdir()) if archive.is_dir() else []:
        if not copy.fullmatch(entry.name) or f"{folder.rel}/archive/{entry.name}" not in tracked:
            continue  # a copy git doesn't track now opens no window, whatever its history says
        out = _git_out(
            vault, "log", "--relative", "--diff-filter=A", "--format=%cI", "--", f"{folder.rel}/archive/{entry.name}"
        )
        for stamp in (out or "").split():
            when = dt.datetime.fromisoformat(stamp)
            if 0 <= (day - when.date()).days <= RESUME_DAYS:
                opened.append(when)
    if not opened:
        return set()
    since = min(opened)
    out = _git_out(
        # --full-diff: a move in from another folder must show as git's R, which needs both paths in the diff.
        vault, "log", "--relative", "--full-diff", "--find-renames", "--name-status", "--format=%x01%cI%x02%s",
        f"--since={since.isoformat()}", "--", folder.rel,
    )  # fmt: skip
    own: set[str] = set()
    for block in (out or "").split("\x01")[1:]:
        head, _, body = block.partition("\n")
        stamp, _, subject = head.partition("\x02")
        if dt.datetime.fromisoformat(stamp) < since:
            continue
        if not _is_rebuild_subject(subject, front):
            continue
        changes = [line.split("\t") for line in body.splitlines() if "\t" in line]
        own |= {parts[-1] for parts in changes if parts[0].startswith("A") and parts[-1].startswith(folder.rel + "/")}
    return own


def _rebuild_started(vault: Path, folder: _Folder, day: dt.date) -> dt.date | None:
    """The date of the folder's oldest archive copy from the last :data:`RESUME_DAYS`, if any."""
    archive = vault / folder.rel / "archive"
    if not archive.is_dir():
        return None
    copy = re.compile(re.escape(folder.name) + r"-(\d{4}-\d{2}-\d{2})-pre-restructure\.md")
    dates: list[dt.date] = []
    for entry in archive.iterdir():
        match = copy.fullmatch(entry.name)
        if not match:
            continue
        try:
            made = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if (day - made).days <= RESUME_DAYS:
            dates.append(made)
    return min(dates, default=None)


def _rebuild_under_way(vault: Path, folder: _Folder, day: dt.date) -> bool:
    """Whether the folder has an archive copy from the last :data:`RESUME_DAYS`."""
    return _rebuild_started(vault, folder, day) is not None


def check_folder(
    vault: Path,
    folder: _Folder,
    cfg: StructureConfig,
    history: dict[str, dt.date],
    excluded: Callable[[str], bool],
    label: Label,
    pinned_loose: int,
    day: dt.date,
    shared_edits: SharedEdits = "refuse",
    linked: LinkedChange | None = None,
) -> dict[str, Any]:
    private = label.visibility == "private"
    front = f"{folder.rel}/{folder.name}.md"
    raw = (vault / front).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    fm, body = split_page(text)
    size = len(raw)
    missing = missing_sections(body, cfg.required_sections)
    narration = narration_found(body, cfg.narration_markers)
    bullets = check_bullets(text, cfg)
    too_many = bullets.over(cfg.max_bullets)
    as_of = page_as_of(fm)
    newest = _folder_last_change(folder, history, excluded)
    # New knowledge about the project often lands outside its folder: a source or topic note linking to it.
    linked_newest = linked.newest if linked is not None else None
    behind = max((d for d in (newest, linked_newest) if d is not None), default=None)
    stale = bool(as_of and behind and (behind - as_of).days > cfg.stale_days)
    by_link = stale and linked_newest is not None and (newest is None or linked_newest > newest)
    priority = str(fm.get("priority") or "").strip()[:1]
    priority = priority if priority in PRIORITY_RANK else ""

    problems: list[str] = []
    if folder.loose:
        problems.append(f"{len(folder.loose)} loose")
    if folder.odd_dirs:
        problems.append("odd dirs: " + ",".join(folder.odd_dirs))
    if size > cfg.page_budget_bytes:
        problems.append(f"{size // 1024} KB")
    if missing:
        problems.append(f"{len(missing)} sections missing")
    if narration:
        problems.append(
            f"narration: {plural.count(len(narration), 'marker')}" if private else "narration: " + ",".join(narration)
        )
    for section, count in too_many.items():
        where = "" if private else f" in {section}"
        problems.append(f"{count} bullets{where} (limit {cfg.max_bullets})")
    if bullets.long:
        problems.append(f"{plural.count(bullets.long, 'bullet')} over {cfg.max_bullet_chars} chars")
    if as_of is None:
        # A date the check can't read is its own problem: the night must rewrite it as YYYY-MM-DD.
        problems.append("unreadable as_of" if fm.get("as_of") not in (None, "") else "no as_of")
    if stale:
        if private:
            problems.append("stale")
        elif not by_link:
            problems.append(f"stale (as_of {as_of}, folder changed {newest})")
        elif linked is not None and linked.shown is not None and as_of is not None and linked.shown > as_of:
            problems.append(f"stale (as_of {as_of}, linked note changed {linked.shown})")
        else:
            problems.append(f"stale (as_of {as_of}, a linked note changed)")
    # A night can clear the page's own problems and any loose file that isn't pinned. Nothing
    # in the step clears an odd subfolder, so one alone must not hold the pick night after night.
    page_problems = len(problems) - bool(folder.loose) - bool(folder.odd_dirs)
    actionable = page_problems > 0 or len(folder.loose) > pinned_loose
    # A page with its sections and a current date only needs shrinking, so it waits behind the
    # pages a reader can't follow. Odd subfolders and pinned files don't count: no night clears them.
    in_shape = not (missing or as_of is None or stale or len(folder.loose) > pinned_loose)
    under_way = _rebuild_under_way(vault, folder, day)  # an archive copy from the last weeks
    half_rebuilt = bool(missing) and under_way
    # Cutting lines fixes size, narration and a section with too many bullets only. It ranks after
    # every page that only needs shrinking: a trim may find nothing it can cut, and must not hold
    # the pick night after night. With `checked` a shared page is rebuilt instead; structure_rows
    # falls back to a trim when a rebuild could write wider than the page's share.
    trimmable = _trimmable(label) and (size > cfg.page_budget_bytes or bool(narration) or bool(too_many))
    pinned = _pinned(label, shared_edits)
    trim_only = trimmable and pinned
    # A full rebuild of a page this big can take several nights and still not be done: it goes
    # to an interactive session, where the owner can rebuild it in one sitting.
    # A page that only needs a refresh keeps its nightly refresh, a rebuild already under way
    # finishes, and a trim only cuts lines.
    needs_rebuild = bool(missing) or size > cfg.page_budget_bytes
    interactive = (
        actionable
        and not pinned
        and not trim_only
        and not under_way  # a rebuild or shrink already started finishes, sections or not
        and needs_rebuild
        and size > cfg.nightly_max_bytes
    )
    front_change = history.get(front)
    return {
        "project": folder.name,
        "path": front,
        "private": private,
        "priority": None if private else (priority or None),
        "size": size,
        "over_target": size > cfg.page_target_bytes,
        "over_budget": size > cfg.page_budget_bytes,
        "loose": [f"{folder.rel}/{n}" for n in folder.loose],
        "odd_dirs": [f"{folder.rel}/{n}" for n in folder.odd_dirs],
        "missing_sections": None if private else missing,
        "missing_count": len(missing),
        "narration": None if private else narration,
        "narration_count": len(narration),
        "as_of": None if private or as_of is None else as_of.isoformat(),
        "folder_last_change": None if private or newest is None else newest.isoformat(),
        # The front page's newest commit: how long a page left for an interactive session has waited.
        "front_last_change": None if private or front_change is None else front_change.isoformat(),
        "linked_last_change": None if private or linked is None or linked.shown is None else linked.shown.isoformat(),
        "bullets": None if private else bullets.counts,
        "bullets_over": sum(too_many.values()),
        "long_bullets": bullets.long,
        "stale": stale,
        "conforms": not problems,
        "problems": problems,
        "rebuild_target": (actionable and not pinned and not interactive) or trim_only,
        "resume": half_rebuilt and actionable and not pinned,
        "trim_only": trim_only,
        "interactive": interactive,
        "share_blocked": False,
        "_trimmable": trimmable,
        "_rank": (
            not problems,
            in_shape or trim_only,
            trim_only,
            -pick_score(priority, as_of, day),
            -size,
            folder.name,
        ),
    }


# How much a page's priority weighs in the nightly pick: a 🔴 page a week behind outranks a 🟢 page
# two weeks behind, and a 🟢 page a month behind outranks a 🔴 page three days behind. A page with
# no priority ranks below 🟢, so small reference pages (a skill or a how-to) don't take the night
# from a project.
PRIORITY_WEIGHT = {"🔴": 4, "🟡": 3, "🟢": 2, "": 1}
# Days a page with no `as_of` counts as behind, and the most any page counts.
STALENESS_CAP_DAYS = 365


def pick_score(priority: str, as_of: dt.date | None, day: dt.date) -> int:
    """The nightly pick's order among pages with the same kind of problem: priority × staleness,
    where staleness is one plus the days since ``as_of`` (capped; a missing ``as_of`` counts as the cap)."""
    lag = STALENESS_CAP_DAYS if as_of is None else min(max((day - as_of).days, 0), STALENESS_CAP_DAYS)
    return PRIORITY_WEIGHT.get(priority, 1) * (1 + lag)


def _graph_excludes(vault: Path) -> tuple[str, ...]:
    from scout.kb.ontology import KnowledgeGraph
    from scout.kb.read import ENGINE_SCHEMA

    kb = vault / "knowledge-base"
    own = kb / "ontology" / "schema.yaml"
    schema = own if own.is_file() else ENGINE_SCHEMA
    return KnowledgeGraph(str(schema), str(kb), vault=vault).exclude_globs()


def _excluder(vault: Path, lint_excludes: Sequence[str]) -> Callable[[str], bool]:
    """Whether a vault-relative path is left out. The schema is read on the first KB path
    that gets this far, so a commit that touches no project page never opens it.

    ``graph.exclude`` is read as ``kb stats`` and ``kb validate`` read it: KB-relative,
    through ``glob_match``."""
    lint_globs = tuple(lint_excludes)
    graph_excludes: tuple[str, ...] | None = None

    def excluded(rel: str) -> bool:
        nonlocal graph_excludes
        if any_match(lint_globs, rel):
            return True
        if not rel.startswith(KB_PREFIX):
            return False
        if graph_excludes is None:
            graph_excludes = _graph_excludes(vault)
        return any_match(graph_excludes, rel[len(KB_PREFIX) :])

    return excluded


@dataclass(frozen=True)
class PageRules:
    """What ``kb lint``'s page checks need: the settings, and where a path sits in a project folder."""

    cfg: StructureConfig
    excluded: Callable[[str], bool]

    def kind(self, rel: str) -> str | None:
        """``front`` for a project's front page, ``loose`` for a ``.md`` beside it, else None.

        Raises ``KBError`` when the KB schema is malformed."""
        prefix = self.cfg.projects_dir + "/"
        if not rel.startswith(prefix) or not rel.endswith(".md"):
            return None
        parts = rel[len(prefix) :].split("/")
        if len(parts) != 2 or parts[0] in self.cfg.skip_projects:
            return None
        front = f"{prefix}{parts[0]}/{parts[0]}.md"
        if self.excluded(front) or self.excluded(rel):
            return None
        return "front" if rel == front else "loose"


def load_page_rules(vault: Path, lint_excludes: Sequence[str]) -> PageRules:
    """``kb lint``'s view of the settings. Raises ``KBError`` when they are unusable."""
    return PageRules(load_structure_config(vault), _excluder(vault, lint_excludes))


def _oldest_as_of(vault: Path, folders: Sequence[_Folder]) -> dt.date | None:
    """The oldest ``as_of`` among the front pages: no linked change before it can make one stale."""
    dates = []
    for folder in folders:
        text, _ = read_head(vault / folder.rel / f"{folder.name}.md")
        as_of = page_as_of(split_page(text or "")[0])
        if as_of is not None:
            dates.append(as_of)
    oldest = min(dates, default=None)
    # A page this far behind is stale on any linked change in the window, so the walk stops there.
    return None if oldest is None else max(oldest, today(vault) - dt.timedelta(days=LINKED_WINDOW_DAYS))


def structure_rows(vault: Path) -> list[dict[str, Any]]:
    """Every checked project folder's row, rebuild order first: non-conforming, then pages
    failing on more than size, narration or bullets, then :func:`pick_score` (priority ×
    staleness), highest first, then the largest front page."""
    cfg = load_structure_config(vault)
    excluded = _excluder(vault, load_lint_config(vault).exclude_globs)
    root = vault / cfg.projects_dir
    if not root.is_dir():
        return []
    listing = _listing(vault, cfg.projects_dir)
    folders: list[_Folder] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        rel = f"{cfg.projects_dir}/{entry.name}"
        front = f"{rel}/{entry.name}.md"
        if entry.name in cfg.skip_projects or not (vault / front).is_file() or excluded(front):
            continue
        folders.append(_scan(vault, rel, entry.name, cfg, excluded, listing))
    # Grouped once: scanning the whole history for each folder is quadratic on a big vault.
    day = today(vault)
    history: dict[str, dict[str, dt.date]] = {}
    if folders:
        prefix = cfg.projects_dir + "/"
        for path, changed in newest_changes(vault, cfg.projects_dir, day).items():
            history.setdefault(path.removeprefix(prefix).split("/", 1)[0], {})[path] = changed
    policy = load_policy(vault)
    edits = _shared_edits(vault, policy)
    probes = _probe_policy(policy)
    sources_shared = _new_sources_shared(probes, day)
    linked = linked_changes(vault, folders, cfg, _oldest_as_of(vault, folders), day, excluded, policy)
    rows = []
    for folder in folders:
        front = f"{folder.rel}/{folder.name}.md"
        # A loose shared file stays put in either mode: moving it could change who it is shared with.
        pinned_loose = sum(_pinned(label_file(vault, f"{folder.rel}/{n}", policy)) for n in folder.loose)
        label = label_file(vault, front, policy)
        row = check_folder(
            vault,
            folder,
            cfg,
            history.get(folder.name, {}),
            excluded,
            label,
            pinned_loose,
            day,
            shared_edits=edits,
            linked=linked.get(folder.name),
        )
        scope = label.federation_scope  # `none` unless an effective share; a page shared otherwise is pinned
        sources_wider = sources_shared if scope == "none" else _new_sources_shared(probes, day, scope)
        # A page left for an interactive session is checked too: if a rebuild could write wider than its
        # share, a nightly trim is still open to it, and the size limit is for full rebuilds only.
        if (
            (row["rebuild_target"] or row["interactive"])
            and not row["trim_only"]
            and (
                sources_wider
                or (scope != "none" and _shared_rebuild_unsafe(vault, folder, policy, label, day))
                or _rebuild_writes_into_a_share(vault, folder, cfg, policy, probes, scope)
            )
        ):
            was_target = row["rebuild_target"]
            row["rebuild_target"] = row["resume"] = False
            if row["_trimmable"]:
                # A shared page a rebuild can't leave alone may still have lines cut (`checked`).
                row["rebuild_target"] = row["trim_only"] = True
                row["interactive"] = False
                rank = list(row["_rank"])
                rank[1:3] = [True, True]
                row["_rank"] = tuple(rank)
            elif was_target:
                row["share_blocked"] = True
        rows.append(row)
    rows.sort(key=lambda r: r["_rank"])
    for r in rows:
        for key in ("_rank", "_trimmable"):
            del r[key]
    return rows


def trim_destinations(vault: Path, front: str) -> list[Label]:
    """Where a trim of ``front`` may move detail: the folder's existing notes that aren't shared, by path.

    Not the front page or ``archive/``, nor a note left out of the KB or gitignored.
    Unshared is narrower than any share the page has; a note shared with the same or
    other people isn't."""
    cfg = load_structure_config(vault)
    excluded = _excluder(vault, load_lint_config(vault).exclude_globs)
    listing = _listing(vault, cfg.projects_dir)
    policy = load_policy(vault)
    folder = PurePosixPath(front).parent
    out: list[Label] = []
    for path in sorted((vault / folder).rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        inside = PurePosixPath(rel).relative_to(folder).parts
        if rel == front or inside[0] == "archive" or excluded(rel):
            continue
        if listing is not None and rel not in listing.files:
            continue
        label = label_file(vault, rel, policy)
        if not label.shared_file:
            out.append(label)
    return out


def interactive_waits(rows: Sequence[dict[str, Any]], day: dt.date) -> list[tuple[str, int | None]]:
    """Each page left for an interactive session, with the days since its front page last
    changed (None when git has no history for it), longest wait first."""
    out: list[tuple[str, int | None]] = []
    for r in rows:
        if r["interactive"]:
            since = r.get("front_last_change")
            out.append((r["path"], (day - dt.date.fromisoformat(since)).days if since else None))
    return sorted(out, key=lambda w: (-(w[1] if w[1] is not None else -1), w[0]))


def _waited(days: int | None) -> str:
    return "no history" if days is None else f"waiting {days} day{'s' if days != 1 else ''}"


def next_target(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The nightly pass's page: a half-done rebuild first, else the first row it can act on."""
    targets = [r for r in rows if r["rebuild_target"]]
    return next((r for r in targets if r["resume"]), targets[0] if targets else None)


def cmd_structure(vault: Path, reader: str, next_only: bool) -> Any:
    """The ``scoutctl kb structure`` result. A reader outside the full readers gets
    an empty table, and no file is opened."""
    from scout.kb.read import Output

    rows = structure_rows(vault) if reader in FULL_READERS else []
    labels: list[Mapping[str, Any]] = [
        {"path": r["path"], "visibility": "private" if r["private"] else "normal"} for r in rows
    ]
    if next_only:
        target = next_target(rows)
        # An empty line reads as "nothing to do", so say when it isn't. On stderr: stdout is the pick.
        blocked = sum(r["share_blocked"] for r in rows)
        if target is None and blocked:
            print(
                f"No pick: {plural.count(blocked, 'failing page')} skipped because a rebuild's new notes could be "
                "shared (`scoutctl kb structure` marks them). Say in this run's report that the nightly KB pass "
                "stopped.",
                file=sys.stderr,
            )
        waiting = interactive_waits(rows, today(vault))
        if waiting:
            cfg = load_structure_config(vault)
            # Never private: a private page is never picked, so it is never left for a session either.
            listed = ", ".join(f"{path} ({_waited(days)})" for path, days in waiting)
            print(
                f"Left for an interactive session: {plural.count(len(waiting), 'page')} over "
                f"{cfg.nightly_max_bytes // 1024} KB "
                f"that {plural.agree(len(waiting), 'needs', 'need')} a full rebuild, too big for a night: {listed}. "
                "List them in this run's report for the owner.",
                file=sys.stderr,
            )
        if target is None:
            return Output(data=None, lines=[""], labels=[])
        commit = rebuild_subject(target["path"])
        data: dict[str, Any] = {"project": target["project"], "path": target["path"], "commit": commit}
        picked = [label for label in labels if label["path"] == target["path"]]
        lines = [target["project"]]
        if target["trim_only"]:
            move_to = trim_destinations(vault, target["path"])
            data |= {"mode": "trim", "move_to": [d.path for d in move_to]}
            picked += [{"path": d.path, "visibility": d.visibility} for d in move_to]
            lines += ["trim-only", *(f"move-to: {d.path}" for d in move_to)]
        lines.append(f"commit: {commit}")  # last, so `trim-only` stays the second line
        return Output(data=data, lines=lines, labels=picked)
    ok = sum(1 for r in rows if r["conforms"])
    lines = [f"{ok}/{len(rows)} project folders meet the KB page standard", ""]
    for r in rows:
        mark = "✓" if r["conforms"] else "✗"
        pri = r["priority"] or "·"
        name = r["project"] + (" [private]" if r["private"] else "")
        skipped = " [--next skips]" if not r["conforms"] and not r["rebuild_target"] else ""
        skipped = " [interactive]" if r["interactive"] else skipped
        skipped = " [trim-only]" if r["trim_only"] else skipped
        lines.append(f"{mark} {pri} {name:<30} {r['size'] // 1024:>4} KB  {'; '.join(r['problems']) or 'ok'}{skipped}")
    return Output(data=rows, lines=lines, labels=labels)
