"""Privacy checks for ``scoutctl kb lint`` (#453, spec §8.2, §8.3).

Run by the pre-commit hook on the staged files in the privacy scope
(:func:`~scout.kb.lint_config.in_privacy_scope`): markdown under
``knowledge-base/`` and ``action-items/``, every ``shared_files`` glob, and
``scout-config.yaml``.

Rules:

- ``private-link`` / ``private-mention`` (**block**): an added or changed line
  of a staged *shared file* links or names a private note. A shared file
  matches the policy's ``shared_files`` or declares a ``federation_scope``
  other than ``none`` in its staged text. Only added lines are checked, so an
  old link never blocks an unrelated edit. A same-line
  ``%%privacy:allow <slug>%%`` (:data:`ALLOW_MARKER_RE`) allows that line's
  hits on that one note. These block in every lint mode, and
  ``SCOUT_LINT_OVERRIDE`` doesn't apply.
- ``privacy-widening`` (**block**, scheduled runs only, i.e. ``SCOUT_MODE``
  set): the commit makes a private note normal, drops the ``visibility``
  line of a note that was ``private`` where its path default is private too,
  sets or widens ``federation_scope``, adds a marker, or touches the
  ``privacy:`` block of ``scout-config.yaml``. The owner's interactive
  commits aren't checked.
- ``visibility-value``, ``privacy-contradiction``, ``federation-reserved``,
  ``frontmatter-unparseable``: warnings (the last is an error in
  ``kb lint --report``).

No message names a private note, its title or a matched term: findings carry
the staged file, a line number and the rule id. The hook runs
:func:`privacy_staged` inside its own fail-closed ``try`` (``scout.kb.lint``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout.kb.git_io import (
    StagedPath,
    changed_lines_lossy,
    head_text_lossy,
    staged_paths,
    staged_text_lossy,
    tracked_files,
)
from scout.kb.lint import Finding
from scout.kb.lint_config import CONFIG_FILE, glob_match, in_privacy_scope
from scout.kb.outbound import (
    PrivateTerms,
    find_mentions,
    linked_slugs,
    marker_slugs,
    markers,
    private_terms,
)
from scout.kb.privacy import (
    FederationScope,
    Label,
    NoteFacts,
    Policy,
    decode_head,
    label_file,
    label_from_facts,
    load_policy,
    note_facts,
    policy_from_overlay,
)

# The marker syntax as documented (spec §8.2). Matching is a little more
# lenient (case, spacing: ``scout.kb.outbound``), so that the widening guard
# and the report catch every marker lint would honour.
ALLOW_MARKER_RE = r"%%privacy:allow ([a-z0-9._-]+)%%"

LABEL_WARNINGS = ("visibility-value", "privacy-contradiction", "frontmatter-unparseable")

_HINT_ALLOW = "remove it, or (owner only) add %%privacy:allow <note-slug>%% on that line"
_MESSAGES = {
    "private-link": "line {n} links a private note, and this file is shared — " + _HINT_ALLOW,
    "private-mention": "line {n} names a private note, and this file is shared — " + _HINT_ALLOW,
    "visibility-value": "visibility isn't normal/private (the note counts as private), "
    "or federation_scope doesn't parse (it counts as none)",
    "privacy-contradiction": "visibility: private with a federation_scope other than none",
    "frontmatter-unparseable": "the frontmatter doesn't parse, so the note counts as private",
    "federation-reserved": "the privacy: block sets a Stage 2 key (federation*); it is ignored in v1",
}


def _md(rel: str) -> bool:
    return rel.lower().endswith(".md")


def _facts(rel: str, text: str | None) -> NoteFacts:
    """The classifier's facts for a file's text (its leading window, as :func:`label_file` reads it)."""
    if text is None or not _md(rel):
        return note_facts(None)
    return note_facts(decode_head(text.encode("utf-8", errors="replace")))


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
    repo: Path
    policy: Policy
    scheduled: bool
    _terms: PrivateTerms | None = None

    def terms(self) -> PrivateTerms:
        if self._terms is None:
            self._terms = private_terms(self.repo, self.policy)
        return self._terms


def privacy_staged(repo: Path, *, env: Mapping[str, str]) -> list[Finding]:
    """The privacy findings for the staged changes in ``repo`` (raises on any internal error)."""
    ctx = _Ctx(repo=repo, policy=load_policy(repo), scheduled=bool(env.get("SCOUT_MODE")))
    out: list[Finding] = []
    for sp in staged_paths(repo):
        if not in_privacy_scope(sp.rel, ctx.policy.shared_files):
            continue
        if sp.rel == CONFIG_FILE:
            out += _config_findings(ctx, sp)
        else:
            out += _file_findings(ctx, sp)
    return out


def _file_findings(ctx: _Ctx, sp: StagedPath) -> list[Finding]:
    rel = sp.rel
    staged = staged_text_lossy(ctx.repo, rel)
    facts = _facts(rel, staged)
    label = label_from_facts(rel, facts, ctx.policy)
    out = [Finding(rel, w, False, _MESSAGES[w]) for w in label.warnings if w in LABEL_WARNINGS and _md(rel)]
    added: list[tuple[int, str]] | None = None

    def added_lines() -> list[tuple[int, str]]:
        nonlocal added
        if added is None:
            added = changed_lines_lossy(ctx.repo, rel, sp.head_rel)
        return added

    if label.shared_file:
        out += _shared_findings(ctx, rel, staged, {n for n, _ in added_lines()})
    if ctx.scheduled:
        out += _widening(ctx, sp, facts, label, added_lines)
    return out


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


def _widening(
    ctx: _Ctx, sp: StagedPath, facts: NoteFacts, label: Label, added_lines: Callable[[], list[tuple[int, str]]]
) -> list[Finding]:
    rel = sp.rel
    out: list[Finding] = []

    def block(message: str, line: int | None = None) -> None:
        out.append(Finding(rel, "privacy-widening", True, f"a scheduled run may not {message}", line))

    head_declared: FederationScope = "none"
    if sp.head_rel is not None:
        head_text = head_text_lossy(ctx.repo, sp.head_rel)
        if head_text is not None:
            head_facts = _facts(sp.head_rel, head_text)
            head = label_from_facts(sp.head_rel, head_facts, ctx.policy)
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
    for n, text in added_lines():
        if marker_slugs(text):
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
                    "a scheduled run may not change the privacy: block — leave that to the owner",
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
    ctx = _Ctx(repo=repo, policy=policy, scheduled=False)
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
        for w in label.warnings:
            if w in LABEL_WARNINGS:
                rep.problems.append((rel, w, "error" if w == "frontmatter-unparseable" else "warn"))
        if not label.shared_file:
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            rep.markers += [(rel, n, target) for target in markers(line)]
        rep.unmarked += [(rel, n) for n in find_mentions(text, ctx.terms(), allow_markers=True)]
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
