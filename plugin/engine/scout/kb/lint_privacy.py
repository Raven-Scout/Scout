"""Privacy checks for ``scoutctl kb lint`` (#453, spec §8.2, §8.3).

Run by the pre-commit hook on the staged files in the privacy scope: markdown
under ``knowledge-base/`` and ``action-items/``, every ``shared_files`` glob,
``scout-config.yaml``, and any staged ``.md`` that declares a
``federation_scope`` other than ``none`` wherever it lives (ruling R23, so
§5.3 step 4 holds).

**What is checked is the commit, not the working tree.** The policy comes
from the index copy of ``scout-config.yaml`` (else HEAD's; with neither, the
working tree's). A note is private when it is private at HEAD, in the index
or on disk, each version under its own policy, so the private terms are the
union over all three: an unstaged edit can't make a staged link look safe.

Rules:

- ``private-link`` / ``private-mention`` (**block**): an added or changed line
  of a staged *shared file* links or names a private note. A shared file
  matches ``shared_files`` or declares a ``federation_scope`` other than
  ``none`` in its staged text. Only added lines are checked, so an old link
  never blocks an unrelated edit; a file that *becomes* shared in this commit
  (new, renamed into ``drafts/``, or given a ``federation_scope``) has every
  line checked. Added lines are computed in Python over the HEAD and staged
  blobs, so git's binary heuristics and ``.gitattributes`` can't hide one. A
  same-line ``%%privacy:allow <slug>%%`` (:data:`ALLOW_MARKER_RE`) allows
  that line's hits on that one note. These block in every lint mode, and
  ``SCOUT_LINT_OVERRIDE`` doesn't apply.
- ``privacy-widening`` (**block**, scheduled runs only: ``SCOUT_MODE`` set
  or a reader other than ``owner``, :func:`scout.kb.lint.is_scheduled`, or a
  live run holding the session lock, :mod:`scout.kb.live_run`): the
  commit makes a private note normal, drops the ``visibility``
  line of a note that was ``private`` where its path default is private too,
  sets or widens ``federation_scope``, adds a marker (a line whose marker
  targets aren't among those of the lines it replaces, or more markers per
  target), touches the ``privacy:`` block of ``scout-config.yaml``, moves
  any file from a path repo sync keeps home to one it uploads
  (:func:`_local_only_moves`, #562), widens a note's effective share
  whatever the cause (a move into a shared folder, a dropped
  ``federation_scope: none``, a ``kind`` change; #586), or adds text to a
  note already shared (an effective share at HEAD and in the commit, #569).
  The last is a content check: the label doesn't change, but the people the
  note is shared with would read whatever the run brought in from notes they
  can't see. A removed or moved line, or a new note in a shared folder, isn't
  refused. The owner's interactive commits aren't checked.
- ``privacy-check-failed`` (**block**): raised as :class:`PolicyUnreadable`
  when a scheduled run's commit policy doesn't parse
  (``privacy-overlay-unreadable``); interactively that is a warning.
- ``visibility-value``, ``privacy-contradiction``, ``federation-ceiling``,
  ``frontmatter-unparseable``: warnings (the last is an error in
  ``kb lint --report``).

A note with an effective share (Stage 2, a ``federation_defaults`` entry with
sharing on) is a shared file too: :attr:`Label.shared_file` says so, so the two
blocking checks cover it (Agnes #6090 §4). Those checks also refuse the path and
text of notes other people shared with this person (:mod:`scout.kb.shared_reads`).

**Provenance** (Agnes #6090 §7): a staged note whose ``source:`` starts with
``shared:`` was built from someone's shared note, wherever it lives, and whatever
this vault's own sharing switch says. ``shared-source-private`` blocks one that
isn't private; an explicit ``visibility: normal`` is the owner's call (a warning)
and blocks only in a scheduled run. ``shared-source-reshared`` (**block**, always)
refuses one in a shared file. ``shared-source-malformed`` warns. Other ``source:``
values are left alone. The classifier never reads ``source:``, so labels don't change.

No message names a private note, its title or a matched term: findings carry
the staged file, a line number and the rule id. The hook runs
:func:`privacy_staged` inside its own fail-closed ``try`` (``scout.kb.lint``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout.kb.git_io import (
    StagedPath,
    head_text_lossy,
    index_text_lossy,
    staged_paths,
    staged_text_lossy,
    tracked_files,
    uncommitted_paths,
)
from scout.kb.lint import Finding, is_scheduled
from scout.kb.lint_config import CONFIG_FILE, glob_match, in_privacy_scope
from scout.kb.lint_rules import added_line_numbers, changed_blocks
from scout.kb.outbound import (
    ALLOW_MARKER_RE,
    PrivateNote,
    PrivateTerms,
    find_mentions,
    linked_slugs,
    marker_slugs,
    markers,
    note_from_text,
    private_terms,
    terms_of,
    with_shared,
)
from scout.kb.privacy import (
    FederationScope,
    Label,
    NoteFacts,
    Policy,
    classify,
    decode_head,
    label_file,
    label_from_facts,
    load_policy,
    note_facts,
    policy_from_config_text,
    read_head,
)
from scout.kb.read import all_labels, policy_hash
from scout.kb.shared_reads import parse_shared_source, shared_sources
from scout.repo_sync import LocalOnlyRules, RepoSyncError, load_state
from scout.yaml_safe import load_yaml

__all__ = ["ALLOW_MARKER_RE", "PolicyUnreadable", "format_report", "privacy_report", "privacy_staged"]

LABEL_WARNINGS = ("visibility-value", "privacy-contradiction", "federation-ceiling", "frontmatter-unparseable")

_HINT_ALLOW = "remove it, or (owner only) add %%privacy:allow <note-slug>%% on that line"
_MESSAGES = {
    "private-link": "line {n} links a private note, and this file is shared — " + _HINT_ALLOW + " (a note made "
    "normal in this same commit still counts as private: commit that change first)",
    "private-mention": "line {n} names a private note or quotes a note shared with you, and this file is shared — "
    + _HINT_ALLOW,
    "shared-source-private": "this note cites a note someone shared with you (source: shared:…), so it stays "
    "visibility: private; only the owner of this vault may choose otherwise",
    "shared-source-reshared": "this note cites a note someone shared with you (source: shared:…), and this file is "
    "shared — your sharing must not pass on theirs; move it out of the shared file or the share",
    "shared-source-malformed": "source: shared: isn't shared:<owner-email>:<vault path>",
    "visibility-value": "visibility isn't normal/private (the note counts as private), "
    "or federation_scope doesn't parse (it counts as none)",
    "privacy-contradiction": "visibility: private with a federation_scope other than none",
    "frontmatter-unparseable": "the frontmatter doesn't parse, so the note counts as private",
    "federation-ceiling": "federation_scope is wider than this folder may share (people notes, emails: none; "
    "meetings and threads: named people only), so part of it is dropped",
    "config-widening": "a scheduled run may not change the privacy: block — leave that to the owner",
    "local-only-move": "a scheduled run may not move a file out of a local-only path (repo sync would upload it "
    "from its new path) — leave that to the owner",
    "privacy-overlay-unreadable": "scout-config.yaml doesn't parse, so the engine's default privacy policy applies, "
    "plus the stricter settings in its privacy: block if that part reads cleanly; "
    "a scheduled run's commit is refused until it does",
}


class PolicyUnreadable(Exception):
    """A scheduled run's commit carries a ``scout-config.yaml`` that doesn't parse (fail closed)."""


def _md(rel: str) -> bool:
    return rel.lower().endswith(".md")


def _head_window(rel: str, text: str | None) -> str | None:
    """A file's text as the classifier sees it: the leading window of a markdown file, else None."""
    if text is None or not _md(rel):
        return None
    return decode_head(text.encode("utf-8", errors="replace"))


def _facts(rel: str, text: str | None) -> NoteFacts:
    return note_facts(_head_window(rel, text))


def _path_default(rel: str, policy: Policy) -> str | None:
    return next((v for g, v in policy.visibility_defaults if glob_match(g, rel)), None)


def _widens(head: FederationScope, staged: FederationScope) -> bool:
    """Whether ``staged`` admits someone ``head`` didn't (``org`` admits every listed principal)."""
    if staged == "none" or staged == head:
        return False
    if staged == "org":
        return True
    if head == "org":
        return False
    if head == "none":
        return True
    return bool(set(staged) - set(head))


@dataclass
class _Ctx:
    """The three policies a commit is checked under, and the union of private terms (lazy)."""

    repo: Path
    policy: Policy  # the commit's: index copy of scout-config.yaml, else HEAD's, else the working tree's
    head_policy: Policy
    disk_policy: Policy
    scheduled: bool
    config_removed: bool = False  # HEAD has scout-config.yaml, the index doesn't
    head_config: str | None = None
    _terms: PrivateTerms | None = None

    @property
    def shared_globs(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys((*self.policy.shared_files, *self.head_policy.shared_files, *self.disk_policy.shared_files))
        )

    def is_shared(self, rel: str, label: Label) -> bool:
        return label.shared_file or any(glob_match(g, rel) for g in self.shared_globs)

    def terms(self) -> PrivateTerms:
        if self._terms is None:
            self._terms = _union_terms(self)
        return self._terms


def _context(repo: Path, env: Mapping[str, str]) -> _Ctx:
    disk = load_policy(repo)
    head_text = head_text_lossy(repo, CONFIG_FILE)
    index_text = index_text_lossy(repo, CONFIG_FILE)
    head = policy_from_config_text(head_text) if head_text is not None else disk
    if index_text is not None:
        commit = policy_from_config_text(index_text)
    elif head_text is not None:
        commit = head
    else:
        commit = disk
    return _Ctx(
        repo=repo,
        policy=commit,
        head_policy=head,
        disk_policy=disk,
        scheduled=is_scheduled(env),
        config_removed=head_text is not None and index_text is None,
        head_config=head_text,
    )


def _union_terms(ctx: _Ctx) -> PrivateTerms:
    """Every note private at HEAD, in the index or on disk (each under its own policy)."""
    repo = ctx.repo
    notes: list[PrivateNote] = list(private_terms(repo, ctx.disk_policy).notes)
    disk_hash = policy_hash(ctx.disk_policy)
    seen_hashes = {disk_hash}
    for policy in (ctx.policy, ctx.head_policy):
        h = policy_hash(policy)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        root = repo.resolve()
        for rel, label in all_labels(root, policy).items():
            if label.visibility == "private":
                notes.append(note_from_text(rel, read_head(root / rel)[0]))
    # Versions that aren't on disk: whatever differs between HEAD, the index and the working tree.
    for rel in sorted(uncommitted_paths(repo)):
        for text, policy in ((index_text_lossy(repo, rel), ctx.policy), (head_text_lossy(repo, rel), ctx.head_policy)):
            if text is None:
                continue
            window = _head_window(rel, text)
            if classify(rel, window, policy).visibility == "private":
                notes.append(note_from_text(rel, window))
    return with_shared(terms_of(notes), repo)


def privacy_staged(repo: Path, *, env: Mapping[str, str]) -> list[Finding]:
    """The privacy findings for the staged changes in ``repo`` (raises on any internal error)."""
    ctx = _context(repo, env)
    out: list[Finding] = []
    if "privacy-overlay-unreadable" in ctx.policy.warnings:
        if ctx.scheduled:
            raise PolicyUnreadable("the commit's scout-config.yaml doesn't parse")
        out.append(Finding(CONFIG_FILE, "privacy-overlay-unreadable", False, _MESSAGES["privacy-overlay-unreadable"]))
    paths = staged_paths(repo)
    if ctx.scheduled:
        out += _local_only_moves(ctx, paths)
    for sp in paths:
        rel = sp.rel
        if rel == CONFIG_FILE:
            out += _config_findings(ctx, sp)
            continue
        staged: str | None = None
        if not in_privacy_scope(rel, ctx.shared_globs):
            if not _md(rel):
                continue
            staged = staged_text_lossy(repo, rel)
            if _facts(rel, staged).declared == "none" and not shared_sources(_head_window(rel, staged)):
                continue  # R23: a note declaring federation_scope is shared wherever it lives
        out += _file_findings(ctx, sp, staged if staged is not None else staged_text_lossy(repo, rel))
    if ctx.scheduled and ctx.config_removed and _privacy_block(_config_data(ctx.head_config)) is not None:
        # Deleting the config drops its privacy: block (staged_paths lists no deletions).
        out.append(Finding(CONFIG_FILE, "privacy-widening", True, _MESSAGES["config-widening"]))
    return out


def _local_only_moves(ctx: _Ctx, paths: list[StagedPath]) -> list[Finding]:
    """Renames that take a file from a path repo sync keeps home to one it uploads (#562).

    Any file, in the privacy scope or not, because repo sync excludes by path.
    Local-only is what :class:`scout.repo_sync.LocalOnlyRules` says: the
    engine's lists, ``local_only_add`` and the upload allowlist (pinned for the
    link, or in the config), so a move into ``sync_only`` from outside it counts
    too. The source is judged under every policy in play, the destination under
    the commit's alone. Git pairs the two paths (``git mv``, or a delete and an
    add of similar content in one commit); a copy, or a delete in a later
    commit, isn't seen.
    """
    moves = [(sp.head_rel, sp.rel) for sp in paths if sp.head_rel is not None and sp.head_rel != sp.rel]
    if not moves:
        return []
    try:
        state = load_state(ctx.repo)
    except RepoSyncError:
        state = None  # repo sync refuses this state, so it uploads nothing; the config's allowlist still counts
    pinned = state.sync_only if state is not None else None
    was = LocalOnlyRules.build([ctx.head_policy, ctx.policy, ctx.disk_policy], (), pinned=pinned)
    now = LocalOnlyRules.build([ctx.policy], (), pinned=pinned)
    return [
        Finding(rel, "privacy-widening", True, _MESSAGES["local-only-move"])
        for src, rel in moves
        if was.local_only(src) and not now.local_only(rel)
    ]


def _file_findings(ctx: _Ctx, sp: StagedPath, staged: str) -> list[Finding]:
    rel = sp.rel
    facts = _facts(rel, staged)
    label = label_from_facts(rel, facts, ctx.policy)
    out = [Finding(rel, w, False, _MESSAGES[w]) for w in label.warnings if w in LABEL_WARNINGS and _md(rel)]
    head_text = head_text_lossy(ctx.repo, sp.head_rel) if sp.head_rel else None
    head_facts: NoteFacts | None = None
    head_label: Label | None = None
    if head_text is not None and sp.head_rel is not None:
        head_facts = _facts(sp.head_rel, head_text)
        head_label = label_from_facts(sp.head_rel, head_facts, ctx.head_policy)

    out += _provenance_findings(ctx, rel, staged, facts, label)
    if ctx.is_shared(rel, label):
        # A file that wasn't shared at HEAD (or didn't exist) has every line checked.
        was_shared = (
            head_label is not None and sp.head_rel is not None and _head_was_shared(ctx, sp.head_rel, head_label)
        )
        base = head_text if was_shared else None
        out += _shared_findings(ctx, rel, staged, added_line_numbers(base, staged))
    if ctx.scheduled:
        out += _widening(ctx, sp, staged, facts, label, head_text, head_facts, head_label)
    return out


def _provenance_findings(ctx: _Ctx, rel: str, staged: str, facts: NoteFacts, label: Label) -> list[Finding]:
    """A note built from someone's shared note stays private and out of the reader's own sharing (Agnes §7)."""
    sources = shared_sources(_head_window(rel, staged))
    if not sources:
        return []
    out: list[Finding] = []

    def finding(check: str, blocking: bool) -> None:
        out.append(Finding(rel, check, blocking, _MESSAGES[check]))

    if any(parse_shared_source(s) is None for s in sources):
        finding("shared-source-malformed", False)
    if ctx.is_shared(rel, label):
        finding("shared-source-reshared", True)
    if label.visibility != "private":
        finding("shared-source-private", ctx.scheduled or facts.visibility != "normal")
    return out


def _head_was_shared(ctx: _Ctx, head_rel: str, head_label: Label) -> bool:
    """Whether the HEAD version was a shared file under HEAD's policy."""
    return head_label.shared_file or any(glob_match(g, head_rel) for g in ctx.head_policy.shared_files)


def _shared_findings(ctx: _Ctx, rel: str, staged: str, added: set[int]) -> list[Finding]:
    if not added:
        return []
    terms = ctx.terms()
    lines = staged.split("\n")
    out: list[Finding] = []
    for n in find_mentions(staged, terms, allow_markers=True):
        if n not in added:
            continue
        line = lines[n - 1] if n <= len(lines) else ""
        check = "private-link" if linked_slugs(line, terms) - marker_slugs(line) else "private-mention"
        out.append(Finding(rel, check, True, _MESSAGES[check].format(n=n), n))
    return out


def added_marker_lines(old: str | None, new: str) -> list[int]:
    """1-based lines of ``new`` that add a ``%%privacy:allow%%`` marker.

    A line adds one when its marker targets aren't all among the targets of the
    lines it replaces (an inserted line replaces nothing). Re-editing a line
    that keeps the owner's marker adds nothing. If a target's count grows in
    the file without that rule firing (one marked line split into two), the
    first new line carrying that target counts.
    """
    new_lines = new.split("\n")
    old_lines = old.split("\n") if old is not None else []
    out: set[int] = set()
    for tag, i1, i2, j1, j2 in changed_blocks(old_lines, new_lines):
        if tag not in ("insert", "replace"):
            continue
        before: set[str] = set().union(*(marker_slugs(ln) for ln in old_lines[i1:i2]))
        out.update(j + 1 for j in range(j1, j2) if marker_slugs(new_lines[j]) - before)
    grown = Counter(s for ln in new_lines for s in marker_slugs(ln)) - Counter(
        s for ln in old_lines for s in marker_slugs(ln)
    )
    if grown and not out:
        old_set = set(old_lines)
        first = next(
            (n for n, ln in enumerate(new_lines, 1) if marker_slugs(ln) & grown.keys() and ln not in old_set),
            next(n for n, ln in enumerate(new_lines, 1) if marker_slugs(ln) & grown.keys()),
        )
        out.add(first)
    return sorted(out)


def _widening(
    ctx: _Ctx,
    sp: StagedPath,
    staged: str,
    facts: NoteFacts,
    label: Label,
    head_text: str | None,
    head_facts: NoteFacts | None,
    head: Label | None,
) -> list[Finding]:
    rel = sp.rel
    out: list[Finding] = []

    def block(message: str, line: int | None = None) -> None:
        out.append(Finding(rel, "privacy-widening", True, f"a scheduled run may not {message}", line))

    head_declared: FederationScope = "none"
    if head is not None and head_facts is not None:
        head_declared = head.federation_scope_declared
        if head.visibility == "private" and label.visibility != "private":
            block("make a private note normal — leave that to the owner")
        elif (
            head_facts.visibility == "private"
            and facts.state == "ok"
            and facts.visibility is None
            and _path_default(rel, ctx.policy) == "private"
        ):
            block("remove the visibility: private line of a note — leave that to the owner")
    if _widens(head_declared, label.federation_scope_declared):
        block("set or widen federation_scope — leave that to the owner")
    elif head is not None and _widens(head.federation_scope, label.federation_scope):
        # The effective scope: a move into a shared folder, a dropped `federation_scope: none`, a kind change.
        block("widen who this note is shared with — leave that to the owner")
    for n in added_marker_lines(head_text, staged):
        block("add a %%privacy:allow%% marker — only the owner may", n)
    if head is not None and head.federation_scope != "none" and label.federation_scope != "none":
        first = _first_new_line(head_text, staged)
        if first is not None:
            block(
                "add to a note that is already shared — the people it is shared with would read it; "
                "put the new material in a different note that isn't shared, or propose the edit in the "
                "review queue; never narrow this note's share to make room",
                first,
            )
    return out


def _first_new_line(old: str | None, new: str) -> int | None:
    """The first added line of ``new`` with text ``old`` lacks (#569); a removed or moved line adds nothing."""
    old_lines = set(old.split("\n")) if old is not None else set()
    lines = new.split("\n")
    return next(
        (n for n in sorted(added_line_numbers(old, new)) if lines[n - 1].strip() and lines[n - 1] not in old_lines),
        None,
    )


class _Unparseable:
    """A ``scout-config.yaml`` that doesn't parse: equal only to the same text."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Unparseable) and other.text == self.text

    __hash__ = None  # type: ignore[assignment]


def _config_data(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return load_yaml(text)
    except yaml.YAMLError:
        return _Unparseable(text)


def _privacy_block(data: Any) -> Any:
    if data is None or isinstance(data, _Unparseable):
        return data
    if not isinstance(data, dict):
        return _Unparseable(repr(data))
    return data.get("privacy")


def _config_findings(ctx: _Ctx, sp: StagedPath) -> list[Finding]:
    staged = _config_data(staged_text_lossy(ctx.repo, sp.rel))
    out: list[Finding] = []
    if ctx.scheduled:
        head = _config_data(head_text_lossy(ctx.repo, sp.head_rel)) if sp.head_rel else None
        if _privacy_block(head) != _privacy_block(staged):
            out.append(
                Finding(
                    sp.rel,
                    "privacy-widening",
                    True,
                    _MESSAGES["config-widening"],
                )
            )
    return out


# ----- kb lint --report ---------------------------------------------------------


@dataclass
class PrivacyReport:
    """What ``kb lint --report`` lists about privacy (spec §8.2).

    ``markers``: every ``%%privacy:allow%%`` marker in a shared file, as
    ``(file, line, target)`` (the owner wrote the target there). ``unmarked``:
    lines of shared files that link or name a private note without a marker,
    as ``(file, line)``. ``problems``: ``(file, rule, severity)`` for label
    warnings; ``frontmatter-unparseable`` is an ``error`` here.
    """

    markers: list[tuple[str, int, str]] = field(default_factory=list)
    unmarked: list[tuple[str, int]] = field(default_factory=list)
    problems: list[tuple[str, str, str]] = field(default_factory=list)


def privacy_report(repo: Path) -> PrivacyReport:
    policy = load_policy(repo)
    terms: PrivateTerms | None = None
    rep = PrivacyReport()
    for rel in tracked_files(repo):
        if rel == CONFIG_FILE or not in_privacy_scope(rel, policy.shared_files):
            continue
        path = repo / rel
        if not path.is_file():
            continue
        label = label_file(repo, rel, policy)
        if "file-unreadable" in label.warnings:
            rep.problems.append((rel, "file-unreadable", "error"))  # private; its text can't be checked
            continue
        rep.problems += [
            (rel, w, "error" if w == "frontmatter-unparseable" else "warn")
            for w in label.warnings
            if w in LABEL_WARNINGS
        ]
        if _md(rel) and label.visibility != "private" and shared_sources(read_head(path)[0]):
            rep.problems.append((rel, "shared-source-private", "warn"))
        if not label.shared_file:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            rep.markers += [(rel, n, target) for target in markers(line)]
        if terms is None:
            terms = with_shared(private_terms(repo, policy), repo)
        rep.unmarked += [(rel, n) for n in find_mentions(text, terms, allow_markers=True)]
    return rep


def format_report(rep: PrivacyReport) -> list[str]:
    lines = [
        f"privacy: {len(rep.markers)} marker(s), {len(rep.unmarked)} unmarked private link(s)/mention(s) "
        f"in shared files, {len(rep.problems)} frontmatter problem(s)"
    ]
    lines += [f"  marker    {rel}:{n}  allow {target}" for rel, n, target in rep.markers]
    lines += [f"  unmarked  {rel}:{n}  links or names a private note" for rel, n in rep.unmarked]
    lines += [f"  {sev:<8}  {rel}  [{rule}]" for rel, rule, sev in rep.problems]
    return lines
