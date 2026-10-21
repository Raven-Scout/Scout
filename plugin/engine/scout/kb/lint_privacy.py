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
- ``privacy-widening`` (**block**, scheduled runs only, i.e. ``SCOUT_MODE``
  set): the commit makes a private note normal, drops the ``visibility``
  line of a note that was ``private`` where its path default is private too,
  sets or widens ``federation_scope``, adds a marker (a line whose marker
  targets aren't among those of the lines it replaces, or more markers per
  target), or touches the ``privacy:`` block of ``scout-config.yaml``. The
  owner's interactive commits aren't checked.
- ``privacy-check-failed`` (**block**): raised as :class:`PolicyUnreadable`
  when a scheduled run's commit policy doesn't parse
  (``privacy-overlay-unreadable``); interactively that is a warning.
- ``visibility-value``, ``privacy-contradiction``, ``federation-reserved``,
  ``frontmatter-unparseable``: warnings (the last is an error in
  ``kb lint --report``).

No message names a private note, its title or a matched term: findings carry
the staged file, a line number and the rule id. The hook runs
:func:`privacy_staged` inside its own fail-closed ``try`` (``scout.kb.lint``).
"""

from __future__ import annotations

import dataclasses
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
from scout.kb.lint import Finding
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
    policy_from_overlay,
    read_head,
)
from scout.kb.read import all_labels, policy_hash

__all__ = ["ALLOW_MARKER_RE", "PolicyUnreadable", "format_report", "privacy_report", "privacy_staged"]

LABEL_WARNINGS = ("visibility-value", "privacy-contradiction", "frontmatter-unparseable")

_HINT_ALLOW = "remove it, or (owner only) add %%privacy:allow <note-slug>%% on that line"
_MESSAGES = {
    "private-link": "line {n} links a private note, and this file is shared — " + _HINT_ALLOW + " (a note made "
    "normal in this same commit still counts as private: commit that change first)",
    "private-mention": "line {n} names a private note, and this file is shared — " + _HINT_ALLOW,
    "visibility-value": "visibility isn't normal/private (the note counts as private), "
    "or federation_scope doesn't parse (it counts as none)",
    "privacy-contradiction": "visibility: private with a federation_scope other than none",
    "frontmatter-unparseable": "the frontmatter doesn't parse, so the note counts as private",
    "federation-reserved": "the privacy: block sets a Stage 2 key (federation*); it is ignored in v1",
    "config-widening": "a scheduled run may not change the privacy: block — leave that to the owner",
    "privacy-overlay-unreadable": "scout-config.yaml doesn't parse, so the engine's default privacy policy "
    "applies; a scheduled run's commit is refused until it does",
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


def policy_from_config_text(text: str | None) -> Policy:
    """:func:`~scout.kb.privacy.load_policy` for a ``scout-config.yaml`` text (None: the engine default)."""
    base = policy_from_overlay(None)
    if text is None:
        return base
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return dataclasses.replace(base, warnings=("privacy-overlay-unreadable",))
    if data is None:
        return base
    if not isinstance(data, dict):
        return dataclasses.replace(base, warnings=("privacy-overlay-unreadable",))
    return policy_from_overlay(data.get("privacy"))


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
        scheduled=bool(env.get("SCOUT_MODE")),
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
    return terms_of(notes)


def privacy_staged(repo: Path, *, env: Mapping[str, str]) -> list[Finding]:
    """The privacy findings for the staged changes in ``repo`` (raises on any internal error)."""
    ctx = _context(repo, env)
    out: list[Finding] = []
    if "privacy-overlay-unreadable" in ctx.policy.warnings:
        if ctx.scheduled:
            raise PolicyUnreadable("the commit's scout-config.yaml doesn't parse")
        out.append(Finding(CONFIG_FILE, "privacy-overlay-unreadable", False, _MESSAGES["privacy-overlay-unreadable"]))
    for sp in staged_paths(repo):
        rel = sp.rel
        if rel == CONFIG_FILE:
            out += _config_findings(ctx, sp)
            continue
        staged: str | None = None
        if not in_privacy_scope(rel, ctx.shared_globs):
            if not _md(rel):
                continue
            staged = staged_text_lossy(repo, rel)
            if _facts(rel, staged).declared == "none":
                continue  # R23: a note declaring federation_scope is shared wherever it lives
        out += _file_findings(ctx, sp, staged if staged is not None else staged_text_lossy(repo, rel))
    if ctx.scheduled and ctx.config_removed and _privacy_block(_config_data(ctx.head_config)) is not None:
        # Deleting the config drops its privacy: block (staged_paths lists no deletions).
        out.append(Finding(CONFIG_FILE, "privacy-widening", True, _MESSAGES["config-widening"]))
    return out


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
    for n in added_marker_lines(head_text, staged):
        block("add a %%privacy:allow%% marker — only the owner may", n)
    return out


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
        return yaml.safe_load(text)
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
    if isinstance(staged, dict) and "federation-reserved" in policy_from_overlay(staged.get("privacy")).warnings:
        out.append(Finding(sp.rel, "federation-reserved", False, _MESSAGES["federation-reserved"]))
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
    if "federation-reserved" in policy.warnings:
        rep.problems.append((CONFIG_FILE, "federation-reserved", "warn"))
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
        if not label.shared_file:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            rep.markers += [(rel, n, target) for target in markers(line)]
        if terms is None:
            terms = private_terms(repo, policy)
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
