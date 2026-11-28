"""Track S: set-valued KB tasks that a graph should help with, scored by recall of a gold set.

The 30-item Track R pilot (``docs/superpowers/specs/2026-11-27-kb-backend-track-r-pilot-findings.md``)
tied because every question named its starting note and had one short answer, so ripgrep found it
first try. Track S asks for *sets*: everything a meeting's prep should draw on, every link a vault
is missing, everything a slip affects. A graph can only show its value on tasks like these.

Three item builders, none of which needs hand labelling:

- **meeting_prep (hindsight):** for a past dated meeting note whose ``## Synthesis`` is filled,
  check the vault out as it stood before that day and ask for the notes the prep should draw on.
  Gold is every note the Synthesis later linked to. The meeting's home file and its earlier
  sessions are marked *trivial* (prep reads them anyway), so ``nontrivial_recall`` shows what a
  graph adds beyond the obvious.
- **link_recovery (masking):** unlink body ``[[wikilinks]]`` to plain text and drop typed
  frontmatter relationships whose target the body still names, in a copy of the vault. The edits
  are recorded in the item and replayed on each run's worktree; gold is the masked pairs.
- **blast_radius / shared_stakeholder / touched_both (computed):** enumerate the answer from the
  typed relationships with the same reader the run uses, so the gold never includes a note the
  run can't see.

Items, edits and results hold vault text, so they are written owner-only, never inside this
public repo (:func:`~tests.eval.harness.track_r_golden.refuse_public_repo_destination`).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import random
import re
import statistics
import subprocess
import tarfile
import tempfile
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import safe_write

from .arms import ArmSpec
from .checks.track_r import paired_cluster_bootstrap, strip_backend_revealing_text
from .metrics import graph_context_reads, graph_verb_breakdown, traversal_verb_calls
from .track_r_golden import refuse_public_repo_destination

TRACK_S_FAMILIES = ("meeting_prep", "link_recovery", "blast_radius", "shared_stakeholder", "touched_both")
GOLD_KINDS = ("path", "entity", "pair")
TRACK_S_REPORT_NAME = "track-s-report.json"
DEFAULT_TRACK_S_BUDGET = "0.60"
DEFAULT_TRACK_S_TIMEOUT_S = 300

# The eval plan's decision rule: a graph arm wins a family when its gold recall beats the
# reference by this much at no more than this token ratio, with zero leaks.
WIN_RECALL_DELTA = 0.15
WIN_TOKEN_RATIO = 1.10

_DATED_MEETING_RE = re.compile(r"^knowledge-base/meetings/([^/]+)/(\d{4}-\d{2}-\d{2})\.md$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#\\]+)(#[^\]|\\]*)?(?:\\?\|([^\]]+))?\]\]")
_ATTENDEES_RE = re.compile(r"^[*_]*Attendees[*_]*:[*_]*\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_REL_ENTRY_RE = re.compile(
    r"^(  - type:\s*([A-Za-z_]+)\s*\n    target:\s*(.+?)\s*\n)",
    re.MULTILINE,
)
# Notes a run never rewrites, logs and housekeeping files, not knowledge: never masked.
_MASK_SKIP = (
    "knowledge-base/from-agents/",
    "knowledge-base/session-log/",
    "/archive/",
    "knowledge-base/archive",
    "knowledge-base/ontology/",
    "knowledge-base/research-queue",
    "knowledge-base/review-queue",
    "knowledge-base/scout-mistake-audit",
    "/templates/",
    "/inbox",
)
# Link targets that are not notes: issue keys such as PROJ-1234, and placeholders in templates or examples.
_ISSUE_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")
_PLACEHOLDER_RE = re.compile(r"[<>{}]|\.\.\.|\b(?:example|placeholder|todo|tbd|xxx|foo|bar)\b", re.IGNORECASE)
_DEPENDENCY_OUT = {"dependency_of", "blocks"}  # src -> dst: dst is affected when src slips
_DEPENDENCY_IN = {"depends_on", "blocked_by"}  # src -> dst: src is affected when dst slips
_DONE_STATUS = re.compile(r"(?i)\b(done|archived|complete|completed|closed|cancel+ed|retired)\b")


@dataclass(frozen=True)
class MaskEdit:
    """One masked link: ``original`` becomes ``replacement`` in ``path``.

    A ``body_link`` occurs exactly once in the note's body, a ``typed_edge`` exactly once in its frontmatter.
    """

    path: str
    kind: str  # "body_link" | "typed_edge"
    target: str
    original: str
    replacement: str

    def pair(self) -> str:
        return f"{self.path} -> {self.target}"


@dataclass(frozen=True)
class SetItem:
    """One Track S task pinned to ``as_of_sha``, with a gold set to recall."""

    id: str
    family: str
    question: str
    as_of_sha: str
    gold: tuple[str, ...]
    gold_kind: str
    trivial: tuple[str, ...] = ()
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    oracle_paths: tuple[str, ...] = ()
    context_hint: str = ""
    project: str = ""
    exclude_paths: tuple[str, ...] = ()
    mask_id: str = ""
    mask_edits: tuple[MaskEdit, ...] = ()
    source_kind: str = "computed"

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.id.strip():
            problems.append("missing id")
        if self.family not in TRACK_S_FAMILIES:
            problems.append(f"{self.id}: unknown family {self.family!r}")
        if self.gold_kind not in GOLD_KINDS:
            problems.append(f"{self.id}: unknown gold_kind {self.gold_kind!r}")
        if not self.gold:
            problems.append(f"{self.id}: empty gold set")
        if not self.question.strip():
            problems.append(f"{self.id}: empty question")
        if not self.as_of_sha.strip():
            problems.append(f"{self.id}: missing as_of_sha")
        if set(self.trivial) - set(self.gold):
            problems.append(f"{self.id}: trivial items must be a subset of gold")
        if self.mask_edits and not self.mask_id:
            problems.append(f"{self.id}: mask edits need a mask_id")
        return problems

    def to_json(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["aliases"] = {k: list(v) for k, v in self.aliases.items()}
        return d

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SetItem:
        return cls(
            id=str(raw["id"]),
            family=str(raw["family"]),
            question=str(raw["question"]),
            as_of_sha=str(raw["as_of_sha"]),
            gold=tuple(str(g) for g in raw.get("gold") or ()),
            gold_kind=str(raw.get("gold_kind") or "path"),
            trivial=tuple(str(g) for g in raw.get("trivial") or ()),
            aliases={str(k): tuple(str(a) for a in v) for k, v in (raw.get("aliases") or {}).items()},
            oracle_paths=tuple(str(p) for p in raw.get("oracle_paths") or ()),
            context_hint=str(raw.get("context_hint") or ""),
            project=str(raw.get("project") or ""),
            exclude_paths=tuple(str(p) for p in raw.get("exclude_paths") or ()),
            mask_id=str(raw.get("mask_id") or ""),
            mask_edits=tuple(MaskEdit(**e) for e in raw.get("mask_edits") or ()),
            source_kind=str(raw.get("source_kind") or "computed"),
        )


# ----- storage ----------------------------------------------------------------------


def write_set_items(dest: Path, items: Iterable[SetItem]) -> Path:
    """Write ``items`` as owner-only JSONL at ``dest`` (refused inside the public repo)."""
    dest = dest.expanduser().resolve()
    refuse_public_repo_destination(dest)
    rows = list(items)
    problems = [p for it in rows for p in it.validate()]
    if problems:
        raise ValueError("invalid Track S items: " + "; ".join(problems[:10]))
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    text = "".join(json.dumps(it.to_json(), ensure_ascii=False, sort_keys=True) + "\n" for it in rows)
    safe_write.atomic_write_text(dest, text, mode=0o600)
    return dest


def load_set_items(src: Path) -> list[SetItem]:
    items = [
        SetItem.from_dict(json.loads(line)) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    problems = [p for it in items for p in it.validate()]
    if problems:
        raise ValueError("invalid Track S items: " + "; ".join(problems[:10]))
    return items


def summarize_set_items(items: Sequence[SetItem]) -> dict[str, Any]:
    by_family: dict[str, int] = dict.fromkeys(TRACK_S_FAMILIES, 0)
    for it in items:
        by_family[it.family] += 1
    by_source: dict[str, int] = {}
    for it in items:
        by_source[it.source_kind] = by_source.get(it.source_kind, 0) + 1
    golds = [len(it.gold) for it in items]
    return {
        "total": len(items),
        "by_family": by_family,
        "by_source_kind": by_source,
        "median_gold_size": statistics.median(golds) if golds else 0,
        "median_gold_size_by_family": {
            fam: statistics.median(len(it.gold) for it in items if it.family == fam)
            for fam in TRACK_S_FAMILIES
            if by_family[fam]
        },
        "masked_edits": sum(len(it.mask_edits) for it in {it.mask_id: it for it in items if it.mask_id}.values()),
    }


# ----- git helpers ------------------------------------------------------------------


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, check=True).stdout


def resolve_sha(vault: Path, ref: str = "HEAD") -> str:
    return _git(vault, "rev-parse", "--short=12", ref).strip()


def sha_before(vault: Path, day: dt.date, ref: str = "HEAD") -> str | None:
    """The last commit on ``ref`` made before ``day`` started (local time), or None."""
    out = _git(vault, "rev-list", "-1", f"--before={day.isoformat()}T00:00:00", ref).strip()
    return out[:12] or None


def kb_files_at(vault: Path, sha: str) -> list[str]:
    out = _git(vault, "ls-tree", "-r", "--name-only", sha, "--", "knowledge-base")
    return [p for p in out.splitlines() if p.endswith(".md")]


def file_at(vault: Path, sha: str, rel: str) -> str:
    return _git(vault, "show", f"{sha}:{rel}")


def export_tree(vault: Path, sha: str, dest: Path) -> Path:
    """Extract ``sha``'s tree (no ``.git``) into ``dest``, for building gold from a fixed snapshot."""
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    proc = subprocess.run(["git", "-C", str(vault), "archive", "--format=tar", sha], capture_output=True, check=True)
    with tempfile.TemporaryFile() as fh:
        fh.write(proc.stdout)
        fh.seek(0)
        with tarfile.open(fileobj=fh, mode="r:") as tar:
            tar.extractall(dest, filter="data")
    return dest


# ----- name and path matching -------------------------------------------------------


def norm_name(text: str) -> str:
    """Normalize a note path, wikilink or entity name for matching (``people/priya-shah.md`` -> ``priya shah``)."""
    t = text.strip().strip("`").strip()
    m = _WIKILINK_RE.search(t)
    if m:
        t = m.group(1)
    t = t.split("#", 1)[0]
    t = t.rsplit("/", 1)[-1]
    if t.lower().endswith(".md"):
        t = t[:-3]
    t = re.sub(r"[-_\s]+", " ", t).strip().lower()
    return t


def _stem_index(paths: Iterable[str]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for p in paths:
        idx.setdefault(norm_name(p), []).append(p)
    return idx


def resolve_link(target: str, paths: Sequence[str], stems: Mapping[str, list[str]]) -> str | None:
    """Resolve a wikilink target to one vault path: a path suffix first, then a unique note stem."""
    t = target.strip().split("#", 1)[0].strip()
    if not t:
        return None
    suffix = t if t.endswith(".md") else f"{t}.md"
    hits = [p for p in paths if p == suffix or p.endswith("/" + suffix)]
    if len(hits) == 1:
        return hits[0]
    cands = stems.get(norm_name(t)) or []
    return cands[0] if len(cands) == 1 else None


# ----- builder 1: hindsight meeting prep --------------------------------------------

# Where a vault keeps dated prep notes: meeting-prep.md's meetings/<slug>/YYYY-MM-DD.md (in the KB or at the
# vault root), and the older flat action-items/meeting-prep/ files. Matched against every tracked file.
DEFAULT_PREP_GLOBS = (
    "knowledge-base/meetings/*/*.md",
    "meetings/*/*.md",
    "action-items/meeting-prep/**/*.md",
)
# Post-meeting sections, best first: notes linked here were what the meeting actually touched.
_HINDSIGHT_SECTIONS = ("Synthesis", "Decisions", "Action Items", "Outcomes", "Follow-ups", "Notes")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_PREP_HOMES = ("knowledge-base/meetings/{slug}/{slug}.md", "meetings/{slug}/{slug}.md")


def _glob_re(pattern: str) -> re.Pattern[str]:
    """A path glob as a regex: ``*`` and ``?`` stay inside one folder, ``**/`` spans any number of them."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def all_files_at(vault: Path, sha: str) -> list[str]:
    return _git(vault, "ls-tree", "-r", "--name-only", sha).splitlines()


def find_prep_notes(files: Iterable[str], globs: Sequence[str] = DEFAULT_PREP_GLOBS) -> list[tuple[str, str]]:
    """``(path, meeting slug)`` for every prep note ``globs`` match, leaving out meeting home files."""
    pats = [_glob_re(g) for g in globs]
    out: list[tuple[str, str]] = []
    for rel in files:
        if not rel.endswith(".md") or not any(p.match(rel) for p in pats):
            continue
        parts = rel.split("/")
        stem = parts[-1][:-3]
        parent = parts[-2] if len(parts) >= 2 else ""
        if stem == parent or stem in ("meetings", "README", "index"):
            continue  # a meeting's home file or a folder index, not a session
        if _DATE_RE.fullmatch(stem) and parent not in ("meeting-prep", "archive"):
            slug = parent  # meetings/<slug>/YYYY-MM-DD.md
        else:
            slug = _DATE_RE.sub("", stem).strip("-_ .") or "meeting"
        out.append((rel, slug))
    return out


def _added_in(vault: Path, rel: str, head: str) -> str | None:
    """The commit on ``head`` that first added ``rel``, or None."""
    shas = _git(vault, "log", "--format=%H", "--diff-filter=A", head, "--", rel).split()
    return shas[-1] if shas else None


def _commit_day(vault: Path, sha: str) -> dt.date:
    return dt.date.fromisoformat(_git(vault, "show", "-s", "--format=%cs", sha).strip())


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    rest = text[m.end() :]
    nxt = re.search(r"^##\s+\S", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _hindsight_links(text: str) -> tuple[list[str], str]:
    """Wikilink targets from the post-meeting sections, else every link in the note, with which it was."""
    for heading in _HINDSIGHT_SECTIONS:
        links = [m.group(1) for m in _WIKILINK_RE.finditer(_section(text, heading))]
        if links:
            return links, "hindsight"
    return [m.group(1) for m in _WIKILINK_RE.finditer(text)], "prep_links"


def build_meeting_prep_items(
    vault: Path,
    *,
    ref: str = "HEAD",
    since: dt.date | None = None,
    until: dt.date | None = None,
    max_items: int = 40,
    min_gold: int = 2,
    prep_globs: Sequence[str] = DEFAULT_PREP_GLOBS,
    allow_prep_links: bool = True,
) -> list[SetItem]:
    """F1 items: one per past prep note that links at least ``min_gold`` KB notes, asked of the vault before it.

    The vault is checked out at the parent of the commit that first added the prep note. Gold is what the
    note's post-meeting sections (Synthesis, Decisions, Action Items...) link, as ``source_kind="hindsight"``.
    A prep note with none of those (the older ``action-items/meeting-prep/`` files) falls back to every link
    it holds, as ``source_kind="prep_links"``: the notes an earlier prep drew on, a weaker gold, kept apart in
    the report. ``allow_prep_links=False`` drops those items.
    """
    head = resolve_sha(vault, ref)
    preps = find_prep_notes(all_files_at(vault, head), prep_globs)
    dated: list[tuple[str, str, str, str]] = []  # (day, slug, rel, added-in sha)
    for rel, slug in preps:
        added = _added_in(vault, rel, head)
        if added is None:
            continue
        m = _DATE_RE.search(rel.rsplit("/", 1)[-1])
        day = m.group(1) if m else _commit_day(vault, added).isoformat()
        dated.append((day, slug, rel, added))
    dated.sort()
    items: list[SetItem] = []
    seen_ids: set[str] = set()
    for day_s, slug, rel, added in reversed(dated):  # newest first
        day = dt.date.fromisoformat(day_s)
        if (since and day < since) or (until and day > until):
            continue
        parent = _git(vault, "rev-parse", "--verify", "--quiet", f"{added}^").strip() if added else ""
        as_of = parent[:12] if parent else sha_before(vault, day, head)
        if not as_of:
            continue
        text = file_at(vault, head, rel)
        links, source_kind = _hindsight_links(text)
        if source_kind == "prep_links" and not allow_prep_links:
            continue
        files_then = kb_files_at(vault, as_of)
        stems = _stem_index(files_then)
        homes = {h.format(slug=slug) for h in _PREP_HOMES}
        gold: list[str] = []
        for target in links:
            path = resolve_link(target, files_then, stems)
            if path and path not in gold and path != rel and path not in homes:
                gold.append(path)
        if len(gold) < min_gold:
            continue
        # Prep reads the meeting's home file and its last session anyway: what they link is trivial.
        trivial: set[str] = set()
        then_all = set(all_files_at(vault, as_of))
        earlier = [r for d, s, r, _a in dated if s == slug and d < day_s and r in then_all]
        for src in [h for h in homes if h in then_all] + earlier[-1:]:
            trivial |= {
                p
                for t in _WIKILINK_RE.finditer(file_at(vault, as_of, src))
                if (p := resolve_link(t.group(1), files_then, stems))
            }
            trivial.add(src)
        name_m = _H1_RE.search(text)
        name = (name_m.group(1).split(" — ")[0].strip() if name_m else slug.replace("-", " ")) or slug
        att_m = _ATTENDEES_RE.search(text)
        attendees = att_m.group(1).strip().strip("*").strip() if att_m else "not listed"
        question = (
            f'Scout is preparing the {day_s} session of the meeting "{name}". Attendees: {attendees}. '
            "Using the vault as it stands now, list the notes this prep should draw on: the projects, people, "
            "decisions and open threads most likely to come up. List up to 15 notes, most important first."
        )
        item_id = f"s-prep-{norm_name(slug).replace(' ', '-')}-{day_s}"
        while item_id in seen_ids:
            item_id += "-b"
        seen_ids.add(item_id)
        items.append(
            SetItem(
                id=item_id,
                family="meeting_prep",
                question=question,
                as_of_sha=as_of,
                gold=tuple(gold),
                gold_kind="path",
                trivial=tuple(p for p in gold if p in trivial),
                oracle_paths=tuple(gold),
                context_hint=f"{name} {attendees}",
                project=slug,
                exclude_paths=(rel,),
                source_kind=source_kind,
            )
        )
        if len(items) >= max_items:
            break
    return items


# ----- builder 2: masked link recovery ----------------------------------------------


def _body_offset(text: str) -> int:
    m = _FRONTMATTER_RE.match(text)
    return m.end() if m else 0


def _mask_candidates(rel: str, text: str) -> list[MaskEdit]:
    out: list[MaskEdit] = []
    off = _body_offset(text)
    body = text[off:]
    by_target: dict[str, list[re.Match[str]]] = {}
    for m in _WIKILINK_RE.finditer(body):
        by_target.setdefault(norm_name(m.group(1)), []).append(m)
    for _key, ms in by_target.items():
        if len(ms) != 1:  # masking one of several links to a target leaves the note still linked
            continue
        m = ms[0]
        original = m.group(0)
        if body.count(original) != 1:
            continue
        display = (m.group(3) or m.group(1).rsplit("/", 1)[-1]).strip()
        out.append(MaskEdit(rel, "body_link", m.group(1).strip(), original, display))
    fm = _FRONTMATTER_RE.match(text)
    if fm:
        body_names = {norm_name(m.group(1)) for m in _WIKILINK_RE.finditer(body)}
        body_lower = body.lower()
        for m in _REL_ENTRY_RE.finditer(fm.group(0)):
            block, raw_target = m.group(1), m.group(3).strip().strip("'\"")
            name = norm_name(raw_target)
            # Only edges the body still names are recoverable (what `kb link-gaps` looks for).
            if not name or (name not in body_names and name not in body_lower):
                continue
            if fm.group(0).count(block) != 1:
                continue
            target = _WIKILINK_RE.search(raw_target) or re.match(r"(.+)", raw_target)
            out.append(MaskEdit(rel, "typed_edge", (target.group(1) if target else raw_target).strip(), block, ""))
    return out


def _placeholder_target(target: str) -> bool:
    """Link targets that aren't notes: issue keys, template placeholders and examples."""
    t = target.strip()
    return bool(_ISSUE_KEY_RE.fullmatch(t) or _PLACEHOLDER_RE.search(t))


def select_mask(
    files: Mapping[str, str],
    *,
    fraction: float = 0.07,
    max_edits: int = 120,
    seed: int = 20261128,
    notes: Sequence[str] | None = None,
) -> list[MaskEdit]:
    """Pick a deterministic sample of maskable links across ``files`` ({rel: text}).

    With ``notes`` (the vault paths the run's reader can see), a link is maskable only when both its own
    note and the note it points to are among them, so no item rests on a link the reader couldn't follow.
    """
    stems = _stem_index(notes) if notes is not None else {}
    visible = set(notes) if notes is not None else None
    cands: list[MaskEdit] = []
    for rel in sorted(files):
        if any(s in rel for s in _MASK_SKIP) or _DATED_MEETING_RE.match(rel):
            continue
        if visible is not None and rel not in visible:
            continue
        for e in _mask_candidates(rel, files[rel]):
            if _placeholder_target(e.target):
                continue
            if notes is not None and resolve_link(e.target, notes, stems) is None:
                continue  # points at no note this reader can see (hidden, local-only, or not a note)
            cands.append(e)
    if not cands:
        return []
    k = min(max_edits, max(1, round(len(cands) * fraction)))
    rng = random.Random(seed)
    picked = rng.sample(cands, k)
    # At most one edit per (file, target), and typed edits whose body link is also masked stay
    # recoverable only through the plain-text mention, which is the realistic case.
    seen: set[tuple[str, str]] = set()
    out: list[MaskEdit] = []
    for e in sorted(picked, key=lambda e: (e.path, e.kind, e.original)):
        key = (e.path, norm_name(e.target))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def apply_mask(root: Path, edits: Sequence[MaskEdit]) -> None:
    """Replay ``edits`` on a checked-out vault at ``root``; refuses an edit whose text moved."""
    by_path: dict[str, list[MaskEdit]] = {}
    for e in edits:
        by_path.setdefault(e.path, []).append(e)
    for rel, es in by_path.items():
        f = root / rel
        text = f.read_text(encoding="utf-8")
        off = _body_offset(text)
        head, body = text[:off], text[off:]
        for e in es:
            # Body links are unique within the body, typed edges within the frontmatter.
            part = body if e.kind == "body_link" else head
            if part.count(e.original) != 1:
                raise ValueError(f"mask edit no longer applies cleanly in {rel} ({e.kind})")
            part = part.replace(e.original, e.replacement, 1)
            if e.kind == "body_link":
                body = part
            else:
                head = part
        f.write_text(head + body, encoding="utf-8")


def _mask_scope(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) >= 3 and parts[1] in ("projects", "meetings"):
        return "/".join(parts[:3]) + "/"
    if len(parts) >= 3:
        return "/".join(parts[:2]) + "/"
    return "knowledge-base/"


def visible_kb_notes(vault: Path, sha: str, reader: str) -> list[str]:
    """The KB notes ``reader`` can see at ``sha``, by the same labels the run applies."""
    from scout.kb.read import visibility_map

    with tempfile.TemporaryDirectory(prefix="track-s-") as tmp:
        root = export_tree(vault, sha, Path(tmp) / "vault")
        labels = visibility_map(root, reader, ["knowledge-base/**"], who="track-s-build")
    return sorted(p for p in labels if p.endswith(".md"))


def build_link_recovery_items(
    vault: Path,
    *,
    ref: str = "HEAD",
    reader: str = "autonomous",
    fraction: float = 0.07,
    max_edits: int = 120,
    seed: int = 20261128,
    min_gold: int = 2,
) -> list[SetItem]:
    """F3 items: one shared masked snapshot, one item per scope folder with at least ``min_gold`` masked links."""
    sha = resolve_sha(vault, ref)
    notes = visible_kb_notes(vault, sha, reader)
    files = {rel: file_at(vault, sha, rel) for rel in notes}
    edits = select_mask(files, fraction=fraction, max_edits=max_edits, seed=seed, notes=notes)
    mask_id = f"mask-{sha}-{seed}"
    by_scope: dict[str, list[MaskEdit]] = {}
    for e in edits:
        by_scope.setdefault(_mask_scope(e.path), []).append(e)
    items: list[SetItem] = []
    for scope, es in sorted(by_scope.items()):
        if len(es) < min_gold:
            continue
        slug = scope.strip("/").replace("knowledge-base/", "").replace("/", "-") or "root"
        question = (
            f"Run a structural check of `{scope}` in this vault. Find links that are missing: places where a "
            "note names a person, project or note without a `[[wikilink]]`, and entity notes whose frontmatter "
            "relationships leave out an entity their text clearly links to. List each as "
            "`<note path> -> <target>`."
        )
        items.append(
            SetItem(
                id=f"s-links-{slug}",
                family="link_recovery",
                question=question,
                as_of_sha=sha,
                gold=tuple(e.pair() for e in es),
                gold_kind="pair",
                oracle_paths=tuple(dict.fromkeys(e.path for e in es)),
                context_hint=scope,
                project=slug,
                mask_id=mask_id,
                mask_edits=tuple(edits),  # every item replays the whole mask; gold is its scope's share
                source_kind="masked",
            )
        )
    return items


# ----- builder 3: computed-gold set questions ---------------------------------------


@dataclass
class _Ent:
    name: str
    type: str
    status: str
    path: str


def _entities(graph: Any, root: Path) -> dict[str, _Ent]:
    out: dict[str, _Ent] = {}
    base = root.resolve()
    for name, fm in graph.entities.items():
        src = Path(str(fm.get("_source_path") or ""))
        try:
            rel = src.resolve().relative_to(base).as_posix() if src.parts else ""
        except ValueError:
            rel = ""
        out[str(name)] = _Ent(
            name=str(name),
            type=str(fm.get("type") or ""),
            status=str(fm.get("status") or ""),
            path=rel,
        )
    return out


def _aliases(ent: _Ent) -> tuple[str, ...]:
    return tuple(dict.fromkeys(a for a in (ent.name, ent.path, ent.path.rsplit("/", 1)[-1]) if a))


def _affected_by(graph: Any, start: str, max_hops: int = 4) -> dict[str, int]:
    """Entities a slip of ``start`` reaches through dependency edges, with their hop distance."""
    out_edges: dict[str, set[str]] = {}
    for r in graph.relationships:
        src, typ, dst = r["source"], r["type"], r["target"]
        if typ in _DEPENDENCY_OUT:
            out_edges.setdefault(src, set()).add(dst)
        elif typ in _DEPENDENCY_IN:
            out_edges.setdefault(dst, set()).add(src)
    dist: dict[str, int] = {start: 0}
    q: deque[str] = deque([start])
    while q:
        cur = q.popleft()
        if dist[cur] >= max_hops:
            continue
        for nxt in sorted(out_edges.get(cur, ())):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    dist.pop(start)
    return dist


def _people_of(graph: Any, ents: Mapping[str, _Ent]) -> dict[str, set[str]]:
    """project name -> people with any typed edge to it."""
    out: dict[str, set[str]] = {}
    for r in graph.relationships:
        a, b = ents.get(r["source"]), ents.get(r["target"])
        if a and b and a.type == "person" and b.type == "project":
            out.setdefault(b.name, set()).add(a.name)
    return out


def find_hubs(
    people_of: Mapping[str, set[str]],
    active: set[str],
    *,
    hub_share: float = 0.25,
    min_hub_projects: int = 4,
    exclude_people: Sequence[str] = (),
) -> tuple[set[str], int]:
    """People linked to so many active projects that they connect everything, and the cutoff used.

    A person on more than ``max(min_hub_projects, hub_share × active projects)`` active projects is a hub
    (the vault's owner usually is); so is anyone named in ``exclude_people``.
    """
    cutoff = max(min_hub_projects, int(hub_share * len(active)))
    degree: dict[str, int] = {}
    for proj, people in people_of.items():
        if proj in active:
            for person in people:
                degree[person] = degree.get(person, 0) + 1
    named = {norm_name(p) for p in exclude_people}
    hubs = {p for p, n in degree.items() if n > cutoff or norm_name(p) in named}
    return hubs, cutoff


def _hub_clause(hubs: set[str], cutoff: int) -> str:
    if not hubs:
        return ""
    names = sorted(hubs)
    shown = ", ".join(names[:6]) + (f" and {len(names) - 6} others" if len(names) > 6 else "")
    return f" Count only people on {cutoff} or fewer active projects, which leaves out {shown}."


def build_computed_items(
    vault: Path,
    *,
    ref: str = "HEAD",
    reader: str = "autonomous",
    max_per_family: int = 10,
    min_gold: int = 2,
    max_gold: int = 12,
    hub_share: float = 0.25,
    exclude_people: Sequence[str] = (),
    seed: int = 20261128,
) -> list[SetItem]:
    """F2 and F6 items whose gold is enumerated from the typed relationships the run's reader can see.

    Items whose answer has more than ``max_gold`` entries are dropped: a question whose answer is most of
    the vault can't tell the arms apart. The people questions leave out hubs (:func:`find_hubs`) and say so.
    """
    from scout.kb.read import load_graph

    sha = resolve_sha(vault, ref)
    with tempfile.TemporaryDirectory(prefix="track-s-") as tmp:
        root = export_tree(vault, sha, Path(tmp) / "vault")
        graph = load_graph(root, reader)
        ents = _entities(graph, root)
        people_of = _people_of(graph, ents)
    rng = random.Random(seed)
    items: list[SetItem] = []

    def mk(family: str, slug: str, question: str, gold_names: Sequence[str], project: str) -> SetItem:
        gold_ents = [ents[g] for g in gold_names]
        return SetItem(
            id=f"s-{family.replace('_', '-')}-{norm_name(slug).replace(' ', '-')}",
            family=family,
            question=question,
            as_of_sha=sha,
            gold=tuple(e.name for e in gold_ents),
            gold_kind="entity",
            aliases={e.name: _aliases(e) for e in gold_ents},
            oracle_paths=tuple(e.path for e in gold_ents if e.path),
            context_hint=project,
            project=project,
            source_kind="computed",
        )

    def fits(gold: Sequence[str]) -> bool:
        return min_gold <= len(gold) <= max_gold

    # F2 blast radius: what a slip reaches, directly or through a dependency chain.
    blast: list[SetItem] = []
    for name in sorted(ents):
        if _DONE_STATUS.search(ents[name].status):
            continue
        reach = _affected_by(graph, name)
        live = [n for n in reach if n in ents and not _DONE_STATUS.search(ents[n].status)]
        if fits(live):
            blast.append(
                mk(
                    "blast_radius",
                    name,
                    f"If {name} slips by two weeks, which other projects and tasks are affected, directly or "
                    "through a chain of dependencies? List every one.",
                    sorted(live, key=lambda n: (reach[n], n)),
                    name,
                )
            )
    rng.shuffle(blast)
    items.extend(blast[:max_per_family])

    active = {n for n, e in ents.items() if e.type == "project" and not _DONE_STATUS.search(e.status)}
    hubs, cutoff = find_hubs(people_of, active, hub_share=hub_share, exclude_people=exclude_people)
    hub_note = _hub_clause(hubs, cutoff)
    people = {p: s - hubs for p, s in people_of.items()}
    # F6 shared stakeholder: other active projects that share a non-hub person with this one.
    shared: list[SetItem] = []
    for proj in sorted(active):
        mine = people.get(proj, set())
        if not mine:
            continue
        others = sorted(p for p in active - {proj} if people.get(p, set()) & mine)
        if fits(others):
            shared.append(
                mk(
                    "shared_stakeholder",
                    proj,
                    f"Which other active projects share at least one person with {proj}?{hub_note} List every one.",
                    others,
                    proj,
                )
            )
    rng.shuffle(shared)
    items.extend(shared[:max_per_family])

    # F6 touched both: non-hub people with a typed edge to both of two projects.
    both: list[SetItem] = []
    projs = sorted(people)
    for i, a in enumerate(projs):
        for b in projs[i + 1 :]:
            common = sorted(people[a] & people[b])
            if fits(common):
                both.append(
                    mk(
                        "touched_both",
                        f"{a}-{b}",
                        f"Who has worked on both {a} and {b}?{hub_note} List every person.",
                        common,
                        a,
                    )
                )
    rng.shuffle(both)
    items.extend(both[:max_per_family])
    return items


# ----- prompt, parsing and scoring --------------------------------------------------


def build_track_s_prompt(item: SetItem, arm: ArmSpec) -> str:
    from .track_r_runner import oracle_hint

    overlay = f"\n\n{arm.brain_overlay.strip()}" if arm.brain_overlay else ""
    overlay += oracle_hint(item.oracle_paths) if arm.oracle else ""
    shape = {
        "path": "one vault-relative `.md` path per line",
        "entity": "one name per line, as the vault names it",
        "pair": "one `<note path> -> <target>` per line",
    }[item.gold_kind]
    return (
        "You are doing a read-only Scout evaluation task on the vault in your current working directory.\n"
        "- Ground everything in this vault's notes; never guess from outside knowledge.\n"
        "- Do not modify any file in the vault.\n"
        "- Read the knowledge base via `scripts/scoutctl kb` and inspect "
        "`.scout-cache/session-context.json` if precomputed project/people context is helpful.\n"
        "- Aim to be complete: listing something wrong costs less than missing something that matters, "
        "but keep each line to the item itself.\n\n"
        'Standard KB commands: `scripts/scoutctl kb search "<words>"`, `scripts/scoutctl kb read <path>`, '
        '`scripts/scoutctl kb files [dir]`, `scripts/scoutctl kb entity "<name>"`, '
        '`scripts/scoutctl kb related "<name>"`, `scripts/scoutctl kb resolve "<name>"`.'
        f"{overlay}\n\n"
        f"Task ({item.id}):\n{item.question}\n\n"
        "End your reply with a section that starts with the line `Items:` followed by "
        f"{shape}, each starting with `- `, and nothing after it.\n"
    )


_ITEMS_HEADER_RE = re.compile(r"^[\s#>*_`]*items[\s*_`]*:?[\s*_`]*$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.+?)\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_ARROW_RE = re.compile(r"\s*(?:->|→|=>|⟶|—>)\s*")
_TAIL_RE = re.compile(r"\s+[—–]\s+|\s+-\s+|:\s|\s+\(|\s+—|\s+–")


def _clean(text: str) -> str:
    t = _MD_LINK_RE.sub(lambda m: m.group(2), text)
    t = re.sub(r"\*\*|__|`", "", t)
    return t.strip().strip("*_").strip()


def parse_listed(answer: str) -> list[str]:
    """The bullet lines after the last ``Items:`` heading, cleaned of markdown and trailing explanations.

    The heading may be bold or a markdown heading; bullets may be ``-``, ``*``, ``•`` or numbered; a pair
    may use ``->``, ``→`` or ``=>``. Without a heading, every bullet in the answer counts.
    """
    lines = answer.splitlines()
    start = max((i for i, ln in enumerate(lines) if _ITEMS_HEADER_RE.match(ln)), default=-1)
    out: list[str] = []
    for line in lines[start + 1 :]:
        m = _BULLET_RE.match(line)
        if not m:
            continue
        text = _clean(m.group(1))
        parts = _ARROW_RE.split(text, maxsplit=1)
        if len(parts) == 2:
            right = _TAIL_RE.split(parts[1], maxsplit=1)[0]
            text = f"{_clean(parts[0])} -> {_clean(right)}"
        else:
            text = _clean(_TAIL_RE.split(text, maxsplit=1)[0])
        out.append(text)
    return list(dict.fromkeys(t for t in out if t))


def _strip_kb(path: str) -> str:
    p = path.strip().strip("/")
    if p.startswith("[[") and p.endswith("]]"):
        p = p[2:-2].split("|", 1)[0]
    p = p.removeprefix("./").removeprefix("knowledge-base/")
    return p if p.endswith(".md") else f"{p}.md"


def _path_matches(listed: str, gold_paths: Sequence[str]) -> list[str]:
    """Gold paths ``listed`` names: by path (with or without ``knowledge-base/`` and ``.md``) or by a stem
    that only one gold path has."""
    want = _strip_kb(listed)
    by_path = [g for g in gold_paths if _strip_kb(g) == want or _strip_kb(g).endswith("/" + want)]
    if by_path:
        return by_path
    stem = norm_name(listed)
    by_stem = [g for g in gold_paths if norm_name(g) == stem]
    return by_stem if len(by_stem) == 1 else []


def _split_pair(text: str) -> tuple[str, str] | None:
    parts = _ARROW_RE.split(text, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def match_listed(item: SetItem, listed: Sequence[str]) -> tuple[set[str], list[str]]:
    """Which gold entries ``listed`` hit, and the listed lines that hit nothing."""
    hit: set[str] = set()
    extra: list[str] = []
    for line in listed:
        matched: list[str] = []
        if item.gold_kind == "pair":
            lp = _split_pair(line)
            if lp:
                for g in item.gold:
                    gp = _split_pair(g)
                    if gp and _path_matches(lp[0], [gp[0]]) and norm_name(lp[1]) == norm_name(gp[1]):
                        matched.append(g)
        elif item.gold_kind == "path":
            matched = _path_matches(line, item.gold)
        else:
            cand = norm_name(line)
            matched = [g for g in item.gold if cand in {norm_name(g), *(norm_name(a) for a in item.aliases.get(g, ()))}]
        hit.update(matched)
        if not matched:
            extra.append(line)
    return hit, extra


def _pair_extra_verdict(line: str, vault_root: Path) -> str:
    """For a listed missing link that isn't in the mask: ``wrong`` or ``unverifiable``.

    The vault already has real missing links the mask didn't make, so a pair is only *wrong* when its note
    doesn't exist, already links the target, or never mentions it. A note that names the target without a
    link may well be a real missing link: it's left out of precision rather than counted against it.
    """
    lp = _split_pair(line)
    if not lp:
        return "wrong"
    rel = "knowledge-base/" + _strip_kb(lp[0])
    note = vault_root / rel
    if not note.is_file():
        return "wrong"
    text = note.read_text(encoding="utf-8", errors="replace")
    target = norm_name(lp[1])
    if not target or target in {norm_name(m.group(1)) for m in _WIKILINK_RE.finditer(text)}:
        return "wrong"
    flat = re.sub(r"[-_\s]+", " ", text.lower())
    return "unverifiable" if target in flat else "wrong"


def score_set_answer(
    item: SetItem,
    answer: str,
    *,
    visible: Mapping[str, str] | None = None,
    vault_root: Path | None = None,
) -> dict[str, Any]:
    """Gold recall, recall beyond the trivial notes, precision and leaks for one sample.

    For link recovery with ``vault_root`` (the masked worktree), listed links that may be real missing
    links outside the mask are counted in ``n_unverifiable`` and left out of ``precision``;
    ``precision_raw`` keeps the plain hits / listed.
    """
    listed = parse_listed(answer)
    hit, extra = match_listed(item, listed)
    gold = set(item.gold)
    nontrivial = gold - set(item.trivial)
    recall = len(hit) / len(gold) if gold else 0.0
    unverifiable = (
        [e for e in extra if _pair_extra_verdict(e, vault_root) == "unverifiable"]
        if item.gold_kind == "pair" and vault_root is not None
        else []
    )
    judged = len(listed) - len(unverifiable)
    precision = len(hit) / judged if judged else 0.0
    leaks: list[str] = []
    if visible is not None:
        for line in listed:
            left = _split_pair(line)
            path = "knowledge-base/" + _strip_kb(left[0] if left else line)
            if ("/" in line or line.endswith(".md")) and (vault_root is None or (vault_root / path).is_file()):
                if path not in visible:
                    leaks.append(path)
    return {
        "recall": round(recall, 4),
        "nontrivial_recall": round(len(hit & nontrivial) / len(nontrivial), 4) if nontrivial else None,
        "precision": round(precision, 4),
        "precision_raw": round(len(hit) / len(listed), 4) if listed else 0.0,
        "f1": round(2 * recall * precision / (recall + precision), 4) if recall + precision else 0.0,
        "n_gold": len(gold),
        "n_listed": len(listed),
        "n_unverifiable": len(unverifiable),
        "hits": sorted(hit),
        "missed": sorted(gold - hit),
        "extra": extra,
        "unverifiable": unverifiable,
        "leaked_paths": leaks,
    }


# ----- runner -----------------------------------------------------------------------


def _worktree_key(item: SetItem) -> tuple[str, tuple[str, ...], str]:
    return (item.as_of_sha, item.exclude_paths, item.mask_id)


def run_track_s(
    items_path: Path,
    *,
    vault_repo: Path,
    arm_specs: Sequence[ArmSpec],
    results_root: Path,
    real_claude: str,
    model: str = "sonnet",
    budget: str = DEFAULT_TRACK_S_BUDGET,
    timeout_s: int = DEFAULT_TRACK_S_TIMEOUT_S,
    reader: str = "autonomous",
    families: Sequence[str] | None = None,
    limit: int | None = None,
    reps: int = 1,
    extra_pythonpath: str | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    """Run Track S items across ``arm_specs``, interleaved per item, and write an owner-only report."""
    from scout.kb.read import visibility_map

    from .track_r_runner import (
        _build_offline_session_contexts,
        _cleanup_worktree,
        _prepare_worktree,
        _with_extra_sys_path,
        run_eval_session,
    )

    refuse_public_repo_destination(results_root)
    vault_resolved = vault_repo.expanduser().resolve()
    results_resolved = results_root.expanduser().resolve()
    results_resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(results_resolved, 0o700)

    items = load_set_items(items_path.expanduser().resolve())
    if families:
        items = [it for it in items if it.family in set(families)]
    if limit is not None:
        items = items[: max(0, limit)]
    if not items:
        raise ValueError("no Track S items matched the requested filters")

    run_dir = results_resolved / f"track-s-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    need_relate = any(a.backend_kind == "relate" for a in arm_specs)
    need_arm_c = any(a.precompute_graph_context for a in arm_specs)
    rows: dict[str, list[dict[str, Any]]] = {a.id: [] for a in arm_specs}
    worktrees: dict[tuple[str, tuple[str, ...], str], Any] = {}

    with _with_extra_sys_path(extra_pythonpath):
        try:
            for wt_idx, key in enumerate(dict.fromkeys(_worktree_key(it) for it in items)):
                first = next(it for it in items if _worktree_key(it) == key)
                edits = first.mask_edits

                def _mutate(root: Path, edits: Sequence[MaskEdit] = edits) -> None:
                    apply_mask(root, edits)

                worktrees[key] = _prepare_worktree(
                    vault_resolved,
                    run_dir / "_worktrees" / f"wt-{wt_idx:03d}-{key[0][:12]}",
                    as_of_sha=key[0],
                    exclude_paths=key[1],
                    reader=reader,
                    need_relate=need_relate,
                    need_arm_c=need_arm_c,
                    mutate=_mutate if edits else None,
                )

            for idx, item in enumerate(items, start=1):
                prep = worktrees[_worktree_key(item)]
                if need_arm_c and item.context_hint:
                    # Arm C's graph_context ranked for this item, standing in for a per-meeting precompute.
                    _, prep.arm_c_session_ctx = _build_offline_session_contexts(
                        prep.path,
                        reader=reader,
                        need_relate=False,
                        need_arm_c=True,
                        as_of_sha=item.as_of_sha,
                        query_hint=item.context_hint,
                    )
                visible = visibility_map(prep.path, reader, ["knowledge-base/**"], who="track-s-runner")
                for rep in range(1, reps + 1):
                    for arm in arm_specs:
                        if progress:
                            print(
                                f"… [{idx}/{len(items)}] {item.id} ({item.family}) arm-{arm.id} rep {rep}", flush=True
                            )
                        sdir = run_dir / "samples" / f"{idx:03d}-{item.id}-arm-{arm.id}-r{rep}"
                        ans, _lines, metrics, aborted = run_eval_session(
                            f"track-s-{item.id}-{arm.id}-r{rep}",
                            build_track_s_prompt(item, arm),
                            arm,
                            prep=prep,
                            sample_dir=sdir,
                            real_claude=real_claude,
                            model=model,
                            budget=budget,
                            timeout_s=timeout_s,
                            reader=reader,
                            extra_pythonpath=extra_pythonpath,
                        )
                        if aborted is None and not ans.strip():
                            # A session that crashed or printed nothing is not a zero-recall answer.
                            aborted = {"reason": "empty answer"}
                        calls = metrics.calls if metrics is not None else ()
                        grade = (
                            score_set_answer(item, ans, visible=visible, vault_root=prep.path)
                            if aborted is None
                            else {}
                        )
                        rec: dict[str, Any] = {
                            "id": item.id,
                            "family": item.family,
                            "source_kind": item.source_kind,
                            "project": item.project,
                            "rep": rep,
                            "arm": arm.id,
                            "slug": arm.slug,
                            "aborted": aborted,
                            **grade,
                            "tool_calls": metrics.tool_call_count if metrics is not None else 0,
                            "graph_verb_breakdown": graph_verb_breakdown(calls),
                            "traversal_verb_calls": traversal_verb_calls(calls),
                            "graph_context_reads": graph_context_reads(calls),
                            "total_tokens": (
                                metrics.input_tokens
                                + metrics.output_tokens
                                + metrics.cache_read_tokens
                                + (metrics.cache_creation_tokens or 0)
                                if metrics is not None
                                else 0
                            ),
                            "cost_usd": metrics.cost_usd if metrics is not None else None,
                            "blinded_answer": strip_backend_revealing_text(ans),
                            "sample_dir": str(sdir),
                        }
                        safe_write.atomic_write_text(
                            sdir / "item-result.json", json.dumps(rec, indent=2, ensure_ascii=False) + "\n", mode=0o600
                        )
                        rows[arm.id].append(rec)
        finally:
            for prep in worktrees.values():
                _cleanup_worktree(vault_resolved, prep)

    report = summarize_track_s(rows, arm_specs)
    report["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["run_dir"] = str(run_dir)
    safe_write.atomic_write_text(
        run_dir / TRACK_S_REPORT_NAME, json.dumps(report, indent=2, ensure_ascii=False) + "\n", mode=0o600
    )
    return report


def _mean(xs: Sequence[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _arm_summary(arm: ArmSpec, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("aborted") is None]
    by_family: dict[str, dict[str, Any]] = {}
    for fam in TRACK_S_FAMILIES:
        fr = [r for r in scored if r["family"] == fam]
        if fr:
            by_family[fam] = {
                "n": len(fr),
                "recall": _mean([float(r["recall"]) for r in fr]),
                "nontrivial_recall": _mean(
                    [float(r["nontrivial_recall"]) for r in fr if r.get("nontrivial_recall") is not None]
                ),
                "precision": _mean([float(r["precision"]) for r in fr]),
                "median_tokens": float(statistics.median(int(r["total_tokens"]) for r in fr)),
            }
    by_source: dict[str, float | None] = {}
    for src in sorted({str(r.get("source_kind") or "") for r in scored}):
        by_source[src] = _mean([float(r["recall"]) for r in scored if str(r.get("source_kind") or "") == src])
    verbs: dict[str, int] = {}
    for r in scored:
        for v, n in (r.get("graph_verb_breakdown") or {}).items():
            verbs[v] = verbs.get(v, 0) + int(n)
    toks = [int(r["total_tokens"]) for r in scored]
    return {
        "arm": arm.id,
        "slug": arm.slug,
        "n_scored": len(scored),
        "n_aborted": len(rows) - len(scored),
        "recall": _mean([float(r["recall"]) for r in scored]),
        "precision": _mean([float(r["precision"]) for r in scored]),
        "median_tokens": float(statistics.median(toks)) if toks else 0.0,
        "total_cost_usd": round(sum(float(r["cost_usd"]) for r in scored if r.get("cost_usd") is not None), 4),
        "traversal_uptake": _mean([1.0 if int(r.get("traversal_verb_calls") or 0) else 0.0 for r in scored]),
        "graph_context_read_rate": _mean([1.0 if int(r.get("graph_context_reads") or 0) else 0.0 for r in scored]),
        "verb_calls": verbs,
        "leaks": sum(len(r.get("leaked_paths") or ()) for r in scored),
        "by_family": by_family,
        "recall_by_source_kind": by_source,
    }


def summarize_track_s(rows: Mapping[str, Sequence[Mapping[str, Any]]], arm_specs: Sequence[ArmSpec]) -> dict[str, Any]:
    """Per-arm summaries and, for each arm against each of Arms A and R, the plan's decision rule per family."""
    arms = {a.id: _arm_summary(a, rows[a.id]) for a in arm_specs}
    comparisons: list[dict[str, Any]] = []
    refs = [a for a in arm_specs if a.id in ("a", "r")]
    for ref in refs:
        for cand in arm_specs:
            if cand.id == ref.id or cand.id in ("a", "r"):
                continue
            ref_map = {(r["id"], r["rep"]): r for r in rows[ref.id] if r.get("aborted") is None}
            for fam in TRACK_S_FAMILIES:
                pairs = [
                    (ref_map[(r["id"], r["rep"])], r)
                    for r in rows[cand.id]
                    if r.get("aborted") is None and r["family"] == fam and (r["id"], r["rep"]) in ref_map
                ]
                if not pairs:
                    continue
                boot = paired_cluster_bootstrap(
                    [
                        {
                            "id": c["id"],
                            "project": c["project"],
                            "ref_pass": float(rf["recall"]),
                            "cand_pass": float(c["recall"]),
                        }
                        for rf, c in pairs
                    ]
                )
                ref_tok = statistics.median(int(rf["total_tokens"]) for rf, _ in pairs) or 1
                cand_tok = statistics.median(int(c["total_tokens"]) for _, c in pairs)
                leaks = sum(len(c.get("leaked_paths") or ()) for _, c in pairs)
                token_ratio = round(cand_tok / ref_tok, 4)
                comparisons.append(
                    {
                        "family": fam,
                        "ref": ref.id,
                        "cand": cand.id,
                        "n": len(pairs),
                        "recall_diff": boot,
                        "token_ratio": token_ratio,
                        "leaks": leaks,
                        "wins": bool(
                            boot["mean_diff"] >= WIN_RECALL_DELTA and token_ratio <= WIN_TOKEN_RATIO and leaks == 0
                        ),
                    }
                )
    return {"arms": arms, "comparisons": comparisons, "items_by_arm": {k: list(v) for k, v in rows.items()}}


def format_track_s_summary(report: Mapping[str, Any]) -> str:
    lines = ["Track S summary"]
    for a in report["arms"].values():
        lines.append(
            f"  arm-{a['arm']} ({a['slug']}): recall={a['recall']} precision={a['precision']} "
            f"tokens={a['median_tokens']:.0f} cost=${a['total_cost_usd']} traversal-uptake={a['traversal_uptake']} "
            f"ctx-reads={a['graph_context_read_rate']} leaks={a['leaks']} (n={a['n_scored']}, aborted={a['n_aborted']})"
        )
    for c in report["comparisons"]:
        d = c["recall_diff"]
        lines.append(
            f"  {c['family']}: {c['cand']} vs {c['ref']} recall {d['mean_diff']:+.3f} "
            f"[{d['ci_low']:+.3f}, {d['ci_high']:+.3f}] tokens x{c['token_ratio']} n={c['n']}"
            + ("  WINS" if c["wins"] else "")
        )
    return "\n".join(lines)
