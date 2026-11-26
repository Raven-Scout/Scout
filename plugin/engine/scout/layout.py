"""Where everything in a vault lives: the one owner of vault paths (layout v2, ADR 0011).

Layout v2 keeps five kinds of file apart (spec 2026-11-26 §3):

- Scout's system: ``scout-config.yaml``, the generated ``README.md`` and ``scout/``
  (``brain/``, ``runners/``, ``scripts/``, ``hooks/``, ``kb-page-standard.md``);
- Scout's records of its own runs: ``scout/records/``;
- what Scout makes for the user: ``action-items/``, ``drafts/``, ``reports/``;
- what Scout knows: ``knowledge-base/``, with one ``knowledge-base/<space>/`` per space;
- user input: ``inbox/``.

:data:`V2` is the table every writer uses. :data:`V1` is the old layout, kept
for read-only callers (the changes scanner reading old commits, ``vault layout``
on a vault that hasn't moved, the move itself). Which one a vault has is decided
from disk (:func:`layout_version`), never from config: the ``layout-v2`` entry in
``plugin.applied_migrations`` only records that the move finished.

Every path here is vault-relative and POSIX. ``""`` is the vault root.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from scout.config import DEFAULT_SPACES, Space, default_space

SCHEMA_VERSION = 1
MIGRATION_ID = "layout-v2"
BRAIN_KINDS: tuple[str, ...] = ("SKILL", "DREAMING", "RESEARCH", "RESPOND")
RUNNER_NAMES: tuple[str, ...] = ("run-scout.sh", "run-dreaming.sh", "run-research.sh", "run-respond.sh")
README_MARKER = "<!-- scout:generated layout-readme v2 -->"
# Where the move keeps its journal and the untracked state a commit can't carry (spec §4.5 step 3).
MIGRATION_STATE_DIR = ".scout-state/migrations/layout-v2"
JOURNAL_REL = f"{MIGRATION_STATE_DIR}/journal.json"
DEFERRAL_REL = f"{MIGRATION_STATE_DIR}/deferred.json"
# Written when a move finishes; removed by `vault space-candidates --done` or the first `vault move`.
REVIEW_PENDING_REL = f"{MIGRATION_STATE_DIR}/space-review-pending"
PRE_REF = "refs/scout/migrations/layout-v2/pre"
POST_REF = "refs/scout/migrations/layout-v2/post"

_V2_PATHS: dict[str, str] = {
    "config": "scout-config.yaml",
    "readme": "README.md",
    "system_dir": "scout",
    "brain_dir": "scout/brain",
    "runners_dir": "scout/runners",
    "scripts_dir": "scout/scripts",
    "hooks_dir": "scout/hooks",
    "page_standard": "scout/kb-page-standard.md",
    "records_dir": "scout/records",
    "mistake_audit": "scout/records/scout-mistake-audit.md",
    "mistake_audit_dir": "scout/records/scout-mistake-audit",
    "session_log": "scout/records/session-log.md",
    "session_log_dir": "scout/records/session-log",
    "connector_health": "scout/records/connector-health.md",
    "dreaming_proposals": "scout/records/dreaming-proposals.md",
    "dreaming_proposals_dir": "scout/records/dreaming-proposals",
    "wishlist_dir": "scout/records/wishlist",
    "knowledge_base": "knowledge-base",
    "kb_index": "knowledge-base/knowledge-base.md",
    "people_index": "knowledge-base/people.md",
    "channels_index": "knowledge-base/channels.md",
    "meetings_registry": "knowledge-base/meetings.md",
    "decisions_log": "knowledge-base/decisions-log.md",
    "review_queue": "knowledge-base/review-queue.md",
    "review_queue_dir": "knowledge-base/review-queue",
    "research_queue": "knowledge-base/research-queue.md",
    "research_queue_dir": "knowledge-base/research-queue",
    "recurring_tasks_dir": "knowledge-base/recurring-tasks",
    "ontology_dir": "knowledge-base/ontology",
    "entities_dir": "knowledge-base/ontology/entities",
    "people_dir": "knowledge-base/people",
    "organizations_dir": "knowledge-base/organizations",
    "action_items_dir": "action-items",
    "action_items_archive_dir": "action-items/archive",
    "context_notes_dir": "action-items/context",
    "drafts_dir": "drafts",
    "reports_dir": "reports",
    "inbox_dir": "inbox",
    "inbox": "inbox/inbox.md",
    "drop_dir": "inbox/drop",
}

# The v1 value of each key. A key with no v1 home (records_dir) carries the folder its files sat in.
_V1_PATHS: dict[str, str] = {
    **_V2_PATHS,
    "system_dir": "",
    "brain_dir": "",
    "runners_dir": "",
    "scripts_dir": "scripts",
    "hooks_dir": "hooks",
    "page_standard": "docs/kb-page-standard.md",
    "records_dir": "knowledge-base",
    "mistake_audit": "knowledge-base/scout-mistake-audit.md",
    "mistake_audit_dir": "knowledge-base/scout-mistake-audit",
    "session_log": "knowledge-base/session-log.md",
    "session_log_dir": "knowledge-base/session-log",
    "connector_health": "knowledge-base/connector-health.md",
    "dreaming_proposals": "dreaming-proposals.md",
    "dreaming_proposals_dir": "dreaming-proposals",
    "wishlist_dir": "docs/wishlist",
    "meetings_registry": "meetings/meetings.md",
    "organizations_dir": "knowledge-base/ontology/entities",
    "inbox_dir": "",
    "inbox": "inbox.md",
    "drop_dir": "drop",
}

# Per-space folders: ``{space}`` is the space key.
SPACE_PATHS: Mapping[str, str] = MappingProxyType(
    {
        "root": "knowledge-base/{space}",
        "projects": "knowledge-base/{space}/projects",
        "topics": "knowledge-base/{space}/topics",
        "sources": "knowledge-base/{space}/sources",
        "meetings": "knowledge-base/{space}/meetings",
        "people": "knowledge-base/{space}/people",
        "organizations": "knowledge-base/{space}/organizations",
    }
)
# The v1 equivalents (one shared folder, no space).
V1_SPACE_PATHS: Mapping[str, str] = MappingProxyType(
    {
        "root": "knowledge-base",
        "projects": "knowledge-base/projects",
        "topics": "knowledge-base/topics",
        "sources": "knowledge-base/sources",
        "meetings": "meetings",
        "people": "knowledge-base/people",
        "organizations": "knowledge-base/ontology/entities",
    }
)
# Index files inside a space's per-kind folder.
SPACE_INDEXES: Mapping[str, str] = MappingProxyType(
    {"projects": "projects.md", "topics": "topics.md", "sources": "sources.md"}
)

CATEGORIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "system": ("scout-config.yaml", "README.md", "scout"),
        "records": ("scout/records",),
        "generated": ("action-items", "drafts", "reports"),
        "knowledge": ("knowledge-base",),
        "user_input": ("inbox",),
    }
)

# Root entries Scout or the app owns besides the five categories: never "unknown" (spec §3, Q8).
KNOWN_ROOT_ENTRIES = frozenset(
    {
        "scout-config.yaml",
        "README.md",
        "scout",
        "knowledge-base",
        "action-items",
        "drafts",
        "reports",
        "inbox",
        "connectors.custom.yaml",
        "connector-probes.local.yaml",
        ".scout-state",
        ".scout-logs",
        ".scout-cache",
        ".scout-inbox",
        ".scout-feedback",
        "scout-display.json",
        ".git",
        ".gitignore",
        ".gitattributes",
        ".obsidian",
        ".claude",
        ".DS_Store",
        ".trash",
    }
)
# v1 entry points the move leaves as relative symlinks into scout/ (spec §4.5 step 5).
COMPAT_LINK_DIRS: tuple[str, ...] = ("scripts", "hooks")


@dataclass(frozen=True)
class Layout:
    """One layout's path table. ``paths`` maps each key above to a vault-relative path."""

    version: int
    paths: Mapping[str, str]

    def __getitem__(self, key: str) -> str:
        return self.paths[key]

    def brain(self, kind: str) -> str:
        return _join(self.paths["brain_dir"], f"{kind}.md")

    def runner(self, name: str) -> str:
        return _join(self.paths["runners_dir"], name)

    def script(self, name: str) -> str:
        return _join(self.paths["scripts_dir"], name)

    def hook(self, name: str) -> str:
        return _join(self.paths["hooks_dir"], name)

    def space_dir(self, space: str, kind: str = "root") -> str:
        if self.version < 2:
            return V1_SPACE_PATHS[kind]
        return SPACE_PATHS[kind].format(space=space)


V2 = Layout(2, MappingProxyType(dict(_V2_PATHS)))
V1 = Layout(1, MappingProxyType(dict(_V1_PATHS)))


def _join(directory: str, name: str) -> str:
    return posixpath.join(directory, name) if directory else name


# ---- the v2 helpers every writer uses -------------------------------------------------------------------


def brain_path(kind: str) -> str:
    """``scout/brain/<KIND>.md``."""
    return V2.brain(kind)


def sidecar_path(kind: str) -> str:
    return brain_path(kind) + ".proposed-merge"


def runner_path(name: str) -> str:
    return V2.runner(name)


def script_path(name: str) -> str:
    return V2.script(name)


def hook_path(name: str) -> str:
    return V2.hook(name)


def record_path(key: str) -> str:
    """A record's v2 path by its table key (``session_log``, ``connector_health``, …)."""
    return V2.paths[key]


def space_dir(space: str, kind: str = "root") -> str:
    """``knowledge-base/<space>`` or one of its per-kind folders (``projects``, ``meetings``, …)."""
    return V2.space_dir(space, kind)


def legacy_rel(key: str) -> str:
    """A key's v1 path, for read-only callers (old commits, a vault that hasn't moved)."""
    return V1.paths[key]


def spaces_default(spaces: Iterable[Space] = DEFAULT_SPACES) -> Space:
    return default_space(tuple(spaces))


# ---- which layout a vault has -----------------------------------------------------------------------------


def _is_real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def layout_version(vault: Path) -> int:
    """2 when ``scout/brain/`` exists and either holds a real ``SKILL.md`` or there is no real root ``SKILL.md``,
    else 1 (spec §4.1).

    A compatibility link at the root (a symlink into ``scout/brain/``) is not a root ``SKILL.md``. A legacy vault
    (``.scout-state`` without config) has neither, so it reads as 1.
    """
    brain = vault / V2.paths["brain_dir"]
    if not brain.is_dir() or brain.is_symlink():
        return 1
    # A real scout/brain/SKILL.md settles it: a real root SKILL.md beside it is a stray an older writer left
    # (safe_write replaces a compatibility link with a file), which the doctor reports, not a v1 vault.
    if _is_real_file(brain / "SKILL.md") or not _is_real_file(vault / "SKILL.md"):
        return 2
    return 1


def for_vault(vault: Path) -> Layout:
    """The layout ``vault`` has on disk."""
    return V2 if layout_version(vault) == 2 else V1


def migration_state(vault: Path, applied: Iterable[str] = ()) -> str:
    """``applied``, ``interrupted`` (a journal is left), ``deferred`` (the last attempt deferred) or ``pending``."""
    if (vault / JOURNAL_REL).exists():
        return "interrupted"
    if layout_version(vault) == 2 and MIGRATION_ID in set(applied):
        return "applied"
    if layout_version(vault) == 2:
        return "applied"  # moved and seeded (an install writes v2 directly)
    if (vault / DEFERRAL_REL).exists():
        return "deferred"
    return "pending"


def move_in_progress(vault: Path) -> bool:
    """A layout move started and hasn't finished: runs skip while this is true (spec §4.9)."""
    return (vault / JOURNAL_REL).exists()


# ---- the document behind `scoutctl vault layout --json` ---------------------------------------------------


def _json_path(rel: str) -> str:
    return rel or "."


def layout_document(vault: Path, *, spaces: tuple[Space, ...], applied: Iterable[str] = ()) -> dict[str, object]:
    """``scoutctl vault layout --json`` (spec §6): vault-relative paths only, never a note name or content."""
    lay = for_vault(vault)
    v2 = lay.version == 2
    return {
        "schema_version": SCHEMA_VERSION,
        "layout_version": lay.version,
        "migration": {"id": MIGRATION_ID, "state": migration_state(vault, applied)},
        "paths": {k: _json_path(v) for k, v in lay.paths.items()},
        "brain": {k: lay.brain(k) for k in BRAIN_KINDS},
        "runners": {n: lay.runner(n) for n in RUNNER_NAMES},
        "space_paths": dict(SPACE_PATHS) if v2 else {},
        "spaces": [{**s.to_json(), "root": space_dir(s.key)} for s in spaces] if v2 else [],
        "categories": {k: list(v) for k, v in CATEGORIES.items()},
    }


# ---- the generated README (spec §3.4) ------------------------------------------------------------------


def render_readme(spaces: Iterable[Space], *, engine_version: str) -> str:
    """The root README.md: where things are, from the same table as ``vault layout`` so the two can't drift."""
    spaces = tuple(spaces)
    p = V2.paths
    space_lines = [
        f"- `{space_dir(s.key)}/`: the {s.key} space"
        + (" (the default)" if s.default else "")
        + (", private by default" if s.visibility == "private" else "")
        for s in spaces
    ]
    lines = [
        README_MARKER,
        "# This Scout vault",
        "",
        "Scout rewrites this file on every update. To keep your own version, delete the first line.",
        "",
        "## Where to put things",
        "",
        f"- `{p['inbox']}`: notes and requests for Scout, picked up on the next run.",
        f"- `{p['drop_dir']}/`: files for Scout to read (they stay out of git).",
        "",
        "## What Scout makes for you",
        "",
        f"- `{p['action_items_dir']}/`: the daily action items, with `{p['context_notes_dir']}/` for context notes.",
        f"- `{p['drafts_dir']}/`: drafts to send.",
        f"- `{p['reports_dir']}/`: one-off reports.",
        "",
        "## What Scout knows",
        "",
        f"- `{p['knowledge_base']}/`: the knowledge base. People and organizations "
        f"(`{p['people_dir']}/`, `{p['organizations_dir']}/`), the indexes, the review and research queues "
        "and decisions are Scout-wide. Everything else is in a space:",
        *space_lines,
        "",
        "## How Scout runs",
        "",
        f"- `{p['system_dir']}/`: the brain skills (`{p['brain_dir']}/`), runners, scripts and hooks.",
        f"- `{p['config']}`: settings, including the spaces.",
        "- `.scout-state/`, `.scout-logs/`, `.scout-cache/`: Scout's state, logs and cache.",
        "- `.scout-inbox/`, `.scout-feedback/`, `scout-display.json`: written by the Scout app and agents.",
        "",
        "## Scout's records",
        "",
        f"- `{p['records_dir']}/`: the session log, mistake audit, connector health, dreaming proposals and wishlist.",
        "",
        "---",
        f"Layout v{V2.version}, Scout {engine_version}.",
        "",
    ]
    return "\n".join(lines)


def legacy_of(v2_rel: str) -> str | None:
    """A managed file's v1 path from its v2 one (``scout/scripts/x`` -> ``scripts/x``), or None when it didn't move."""
    for prefix, old in (
        ("scout/brain/", ""),
        ("scout/runners/", ""),
        ("scout/scripts/", "scripts/"),
        ("scout/hooks/", "hooks/"),
    ):
        if v2_rel.startswith(prefix):
            return old + v2_rel[len(prefix) :]
    if v2_rel == V2.paths["page_standard"]:
        return V1.paths["page_standard"]
    return None
