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
- **as_of**: the front page's frontmatter has one.
- **stale**: the newest commit touching any other file in the folder (the
  front page and ``archive/`` aside) is more than ``stale_days`` after ``as_of``.

The settings live under ``kb_structure`` in scout-config.yaml; the defaults match
the standard the plugin seeds (``docs/kb-page-standard.md``). Folders, files and
front pages matching ``kb_lint.exclude_globs`` (vault-relative) or the KB
schema's ``graph.exclude`` (KB-relative) are left out. A private front page is
still checked, but its row carries only paths, counts and verdicts: no
priority, dates, section names or marker names.

``--next`` takes the first row in table order that the pass can act on, except
that a rebuild left half done in the last weeks, sections still missing, comes
first. A page failing only on size or narration ranks after every other
failing page, so one that can't get small soon doesn't hold the pick. It never picks a
page labelled private, shared or local-only, whose new copies could widen it or
hit a hard privacy block, or a folder where a rebuild could write into a share,
including a source note, which would skip every page. When that leaves it nothing
to pick, a line on stderr says so, for the run's report. Nor does it pick a folder
whose only problems are odd subfolders or loose files with such a label, which no
night can clear: they would hold the pick for good.

A shared front page (not private or local-only) over budget or with narration is the
exception: ``--next`` may pick it **trim-only**, ranked like a page failing only on
size. The pass then only cuts whole lines from it, and moves detail only into the
existing notes in its folder that aren't shared (``move_to``), never into a new note
or a copy. Removing lines widens nothing, so no share where a rebuild would write can
block it. Adding a date or a section would add to a shared note, so the page's other
problems stay the owner's.

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

from scout.config import load_config, today
from scout.errors import KBError
from scout.kb.frontmatter import frontmatter_block
from scout.kb.lint_config import glob_match, lint_any, load_lint_config
from scout.kb.privacy import (
    FULL_READERS,
    Label,
    Policy,
    classify,
    first_default,
    label_file,
    load_frontmatter_yaml,
    load_policy,
    read_head,
)

KB_PREFIX = "knowledge-base/"
PRIORITY_RANK = {"🔴": 0, "🟡": 1, "🟢": 2}
# An archive copy older than this is a finished rebuild, or one that stopped making
# progress: either way the page goes back in line. A big page's rebuild runs for weeks.
RESUME_DAYS = 45
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
        return StructureConfig(
            projects_dir="/".join(parts),
            skip_projects=frozenset(_strs(raw, "skip_projects")),
            allowed_dirs=frozenset(_strs(raw, "allowed_dirs")),
            page_target_bytes=int(raw.get("page_target_bytes", 8192)),
            page_budget_bytes=int(raw.get("page_budget_bytes", 15360)),
            stale_days=int(raw.get("stale_days", 3)),
            required_sections=_strs(raw, "required_sections"),
            narration_markers=_markers(raw),
        )
    except (TypeError, ValueError) as e:
        raise KBError(f"kb_structure: {e}") from e


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


def newest_changes(vault: Path, pathspec: str, now: dt.date) -> dict[str, dt.date]:
    """Each path ever committed under ``pathspec`` → the date of the newest commit touching it.

    One ``git log`` for the whole folder, newest first, so a path's first sighting
    is its newest change. The date is the author date, which an amend or a rebase
    leaves alone. A date after ``now`` doesn't count: one commit from a wrong
    clock would otherwise keep the page stale until that date, whatever a night did.
    Paths are relative to ``vault``, which may sit inside a larger repo. ``{}``
    outside a repo, before the first commit, or without git.
    """
    cmd = ["git", "log", "--relative", "--no-renames", "--name-only", "-z", "--format=%x00%as", "--", pathspec]
    try:
        proc = subprocess.run(cmd, cwd=vault, capture_output=True)
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    newest: dict[str, dt.date] = {}
    day: dt.date | None = None
    # NUL-separated: empty, date, then the paths, the first with a leading newline.
    for raw in proc.stdout.decode("utf-8", errors="replace").split("\0"):
        token = raw.removeprefix("\n")
        if token.startswith(pathspec + "/"):
            # The newest date wins, not the first sighting: author dates needn't follow commit order.
            if day is not None and day <= now and day > newest.get(token, dt.date.min):
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


def _pinned(label: Label) -> bool:
    """A file the nightly pass must not copy or move (the phase's rule, enforced here for its pick)."""
    return label.visibility == "private" or label.shared_file or label.local_only


def _trimmable(label: Label) -> bool:
    """A pinned page the pass may still cut lines from: shared, and nothing else. The hook's
    shared-note check (#569) refuses a run's added or reworded line there, not a removed one."""
    return label.shared_file and label.visibility != "private" and not label.local_only


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


def _new_sources_shared(probes: Policy, day: dt.date) -> bool:
    """Whether a source note a rebuild writes would be shared. They sit outside every project
    folder, and a folder ceiling can depend on the note's ``kind``, so each kind is checked."""
    wide = dataclasses.replace(
        probes,
        shared_files=tuple(_sources_wide(g) for g in probes.shared_files),
        federation_defaults=tuple(
            dataclasses.replace(d, glob=_sources_wide(d.glob)) for d in probes.federation_defaults
        ),
    )
    rel = f"{KB_PREFIX}sources/{day:%Y-%m}/{day}-new-note.md"
    return any(classify(rel, f"---\nkind: {kind}\n---\n", wide).shared_file for kind in _SOURCE_KINDS)


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
    vault: Path, folder: _Folder, cfg: StructureConfig, policy: Policy, probes: Policy
) -> bool:
    """Whether a rebuild of this folder could put page facts where someone else reads them.

    The front page's own label doesn't say: a rebuild moves its facts into the
    subfolders, and a share on one reaches its readers at the next sync, with no
    way to recall them. So a new note in any subfolder the step writes to, and
    every note already in the folder, must be unshared. The caller has already
    labelled the front page unshared.

    Folders can hold many notes and few match a share rule, so a note is only classified
    if a rule matches its path or :func:`_may_declare_a_share`: with neither, nothing
    can make it shared."""
    for sub in sorted(cfg.allowed_dirs | _STEP_DIRS):
        if classify(f"{folder.rel}/{sub}/{folder.name}-new-note.md", None, probes).shared_file:
            return True
    # The one note the step names itself, so an exact share on it counts.
    if classify(f"{folder.rel}/knowledge/{folder.name}-decisions.md", None, probes).shared_file:
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
        if (by_path or _may_declare_a_share(text)) and classify(rel, text, policy, unreadable=unreadable).shared_file:
            return True
    return False


def _rebuild_under_way(vault: Path, folder: _Folder, day: dt.date) -> bool:
    """Whether the folder has an archive copy from the last :data:`RESUME_DAYS`."""
    archive = vault / folder.rel / "archive"
    if not archive.is_dir():
        return False
    copy = re.compile(re.escape(folder.name) + r"-(\d{4}-\d{2}-\d{2})-pre-restructure\.md")
    for entry in archive.iterdir():
        match = copy.fullmatch(entry.name)
        if match:
            try:
                if (day - dt.date.fromisoformat(match.group(1))).days <= RESUME_DAYS:
                    return True
            except ValueError:
                continue
    return False


def check_folder(
    vault: Path,
    folder: _Folder,
    cfg: StructureConfig,
    history: dict[str, dt.date],
    excluded: Callable[[str], bool],
    label: Label,
    pinned_loose: int,
    day: dt.date,
) -> dict[str, Any]:
    private = label.visibility == "private"
    front = f"{folder.rel}/{folder.name}.md"
    raw = (vault / front).read_bytes()
    fm, body = split_page(raw.decode("utf-8", errors="replace"))
    size = len(raw)
    missing = missing_sections(body, cfg.required_sections)
    narration = narration_found(body, cfg.narration_markers)
    as_of = page_as_of(fm)
    newest = _folder_last_change(folder, history, excluded)
    stale = bool(as_of and newest and (newest - as_of).days > cfg.stale_days)
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
        problems.append(f"narration: {len(narration)} marker(s)" if private else "narration: " + ",".join(narration))
    if as_of is None:
        problems.append("no as_of")
    if stale:
        problems.append("stale" if private else f"stale (as_of {as_of}, folder changed {newest})")
    # A night can clear the page's own problems and any loose file that isn't pinned. Nothing
    # in the step clears an odd subfolder, so one alone must not hold the pick night after night.
    page_problems = len(problems) - bool(folder.loose) - bool(folder.odd_dirs)
    actionable = page_problems > 0 or len(folder.loose) > pinned_loose
    # A page with its sections and a current date only needs shrinking, so it waits behind the
    # pages a reader can't follow. Odd subfolders and pinned files don't count: no night clears them.
    in_shape = not (missing or as_of is None or stale or len(folder.loose) > pinned_loose)
    half_rebuilt = bool(missing) and _rebuild_under_way(vault, folder, day)
    # Cutting lines fixes size and narration only. It ranks as a page that only needs shrinking.
    trim_only = _trimmable(label) and (size > cfg.page_budget_bytes or bool(narration))
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
        "stale": stale,
        "conforms": not problems,
        "problems": problems,
        "rebuild_target": (actionable and not _pinned(label)) or trim_only,
        "resume": half_rebuilt and actionable and not _pinned(label),
        "trim_only": trim_only,
        "share_blocked": False,
        "_rank": (not problems, in_shape or trim_only, PRIORITY_RANK.get(priority, 3), -size, folder.name),
    }


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

    Both lists go through ``lint_any``, which drops an over-complex glob: this runs in
    the commit hook, and such a glob on a long path backtracks for hours."""
    lint_globs = tuple(lint_excludes)
    graph_excludes: tuple[str, ...] | None = None

    def excluded(rel: str) -> bool:
        nonlocal graph_excludes
        if lint_any(lint_globs, rel):
            return True
        if not rel.startswith(KB_PREFIX):
            return False
        if graph_excludes is None:
            graph_excludes = _graph_excludes(vault)
        return lint_any(graph_excludes, rel[len(KB_PREFIX) :])

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


def structure_rows(vault: Path) -> list[dict[str, Any]]:
    """Every checked project folder's row, rebuild order first: non-conforming, then pages
    failing on more than size or narration, then 🔴 > 🟡 > 🟢 > none, then the largest front page."""
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
    probes = _probe_policy(policy)
    sources_shared = _new_sources_shared(probes, day)
    rows = []
    for folder in folders:
        front = f"{folder.rel}/{folder.name}.md"
        pinned_loose = sum(_pinned(label_file(vault, f"{folder.rel}/{n}", policy)) for n in folder.loose)
        label = label_file(vault, front, policy)
        row = check_folder(vault, folder, cfg, history.get(folder.name, {}), excluded, label, pinned_loose, day)
        if (
            row["rebuild_target"]
            and not row["trim_only"]
            and (sources_shared or _rebuild_writes_into_a_share(vault, folder, cfg, policy, probes))
        ):
            row["rebuild_target"] = row["resume"] = False
            row["share_blocked"] = True
        rows.append(row)
    rows.sort(key=lambda r: r["_rank"])
    for r in rows:
        del r["_rank"]
    return rows


def trim_destinations(vault: Path, front: str) -> list[Label]:
    """Where a trim of ``front`` may move detail: the folder's existing notes that aren't shared, by path.

    Not the front page or ``archive/``, nor a note left out of the KB or gitignored.
    Unshared is narrower than any share the page has; a note shared with the same or
    other people isn't, and the hook's shared-note check (#569) refuses a run's addition to it anyway."""
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
                f"No pick: {blocked} failing page(s) skipped because a rebuild's new notes could be shared "
                "(`scoutctl kb structure` marks them). Say in this run's report that the nightly KB pass stopped.",
                file=sys.stderr,
            )
        if target is None:
            return Output(data=None, lines=[""], labels=[])
        data: dict[str, Any] = {"project": target["project"], "path": target["path"]}
        picked = [label for label in labels if label["path"] == target["path"]]
        lines = [target["project"]]
        if target["trim_only"]:
            move_to = trim_destinations(vault, target["path"])
            data |= {"mode": "trim", "move_to": [d.path for d in move_to]}
            picked += [{"path": d.path, "visibility": d.visibility} for d in move_to]
            lines += ["trim-only", *(f"move-to: {d.path}" for d in move_to)]
        return Output(data=data, lines=lines, labels=picked)
    ok = sum(1 for r in rows if r["conforms"])
    lines = [f"{ok}/{len(rows)} project folders meet the KB page standard", ""]
    for r in rows:
        mark = "✓" if r["conforms"] else "✗"
        pri = r["priority"] or "·"
        name = r["project"] + (" [private]" if r["private"] else "")
        skipped = " [--next skips]" if not r["conforms"] and not r["rebuild_target"] else ""
        skipped = " [trim-only]" if r["trim_only"] else skipped
        lines.append(f"{mark} {pri} {name:<30} {r['size'] // 1024:>4} KB  {'; '.join(r['problems']) or 'ok'}{skipped}")
    return Output(data=rows, lines=lines, labels=labels)
