"""Shared Areas, the owner's side: ``scoutctl kb share | unshare | shares`` (slice S1a).

Design: keboola/agnes-the-ai-analyst#6090 Part 2 §4, which implements Stage 2 of
the per-note privacy design (#486 §12). Sharing is marked in the vault: these
commands edit ``scout-config.yaml → privacy:`` (``federation.enabled`` and
``federation_defaults``) and nothing else. Agnes reads the result from
``.scout-state/privacy-policy.json`` in each synced commit and enforces it at
every read; until a vault is synced, nothing is shared anywhere.

What each command does:

- :func:`plan_share` adds people or groups to the ``federation_defaults``
  entry for a folder (``<folder>/**``) or a single note (its exact path),
  turns sharing on, and previews the notes that become shared, the notes it
  leaves out and why, and the copy warning. It refuses a principal above the
  target's ceiling, ``org`` (it admits nobody, ruling 7), a folder that holds
  ``people``-ceiling notes (meetings and threads are chosen note by note,
  ruling 6), and a note that is private, local-only, or carries its own
  ``federation_scope``. Folder shares reach the notes under them: the new
  people are added to narrower entries inside the folder too, and the new
  entry goes before any broader entry, so first-match-wins never hides it.
- :func:`plan_unshare` removes people (or everyone) from a folder or note,
  narrower entries inside it included, inserting an exclusion before a broader
  entry when that is what shares it. Vault only in S1a: Agnes's ``shares/stop``
  call is slice S1b, so the stop takes effect at the next sync (ruling 18).
- :func:`list_shares` is the effective-shares view: per ``federation_defaults``
  entry (and per note that shares itself in frontmatter), the people, the
  notes shared, and the notes left out as private, local-only, by the ceiling
  or by their own frontmatter.

People are checked against the linked Agnes where it can: :class:`Directory`
looks emails up with ``GET /api/v1/colleagues`` (#5753) and refuses one that
doesn't resolve; while that endpoint is missing it warns that the email is
checked only at read time. Groups come from the owner's share targets
(``GET /api/sharing/groups``, Everyone refused) and are written as
``group:<id>``. Every call fails soft: no link, no token, a timeout or an
error status leaves the share to Agnes's read-time check, with a warning.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from scout import paths
from scout.kb.privacy import (
    CeilingLevel,
    FederationScope,
    Label,
    Policy,
    first_default,
    folder_ceiling,
    load_policy,
    normalize_rel,
    note_facts,
    parse_principal,
    policy_from_overlay,
    read_head,
    scope_admits,
)

COPY_WARNING = (
    "Once someone's Scout has read a shared note, it can keep what it learned in their own vault. "
    "Un-sharing stops future reads; it can't recall what was already read."
)
SYNC_NOTE = "Nothing is shared until your next `scoutctl repo sync`; Agnes checks every read against it."
GROUP_MEMBERS_NOTE = "members are shown once Agnes reports them (A8b)"
GROUP_OWNER_NOTE = (
    "A group share works only while you are a member of that group: Agnes rechecks your membership and the "
    "reader's at every read (ruling 7). Scout can't see your membership yet, so it doesn't check it here."
)
UNSHARE_PENDING = (
    "The stop takes effect in Agnes at your next `scoutctl repo sync` "
    "(an instant stop from the laptop comes with Agnes's shares/stop, slice S1b)."
)

_GROUP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WILDCARDS = re.compile(r"[*?\[]")
_EVERYONE = "everyone"

ExcludeReason = Literal["private", "local-only", "ceiling", "frontmatter", "earlier-entry"]


class ShareRefused(Exception):
    """The share or unshare was refused: nothing was written (the CLI exits 1)."""


class ShareUsage(ValueError):
    """A bad argument (the CLI exits 2)."""


# ----- Agnes lookups (all fail soft) ------------------------------------------------


@dataclass(frozen=True)
class ShareTarget:
    """One of the owner's Agnes share targets (``GET /api/sharing/groups``)."""

    id: str
    name: str
    is_everyone: bool = False


ColleagueCheck = Literal["known", "unknown", "unchecked"]


class Directory:
    """People and group lookups on the linked Agnes, authenticated with the vault's link PAT.

    Every method fails soft: a network error, a timeout, an auth failure or an
    unexpected answer is "unchecked" (``colleague``) or None (``share_targets``).
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> tuple[int, Any] | None:
        import requests

        try:
            resp = requests.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                params=dict(params or {}),
                timeout=self._timeout,
            )
        except requests.RequestException:
            return None
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = None
        return resp.status_code, body

    def colleague(self, email: str) -> ColleagueCheck:
        """``known``/``unknown`` once Agnes has ``GET /api/v1/colleagues`` (#5753), else ``unchecked``.

        The capability check is the call itself: any answer but a 200 with a
        ``data`` list (404 before #5753, the switch off, an auth error) means
        the email can't be checked here.
        """
        got = self._get("/api/v1/colleagues", {"q": email})
        if got is None:
            return "unchecked"
        status, body = got
        rows = body.get("data") if isinstance(body, dict) else None
        if status != 200 or not isinstance(rows, list):
            return "unchecked"
        found = any(isinstance(r, dict) and str(r.get("email") or "").strip().lower() == email for r in rows)
        return "known" if found else "unknown"

    def share_targets(self) -> list[ShareTarget] | None:
        """The owner's share targets, or None when Agnes can't be asked."""
        got = self._get("/api/sharing/groups")
        if got is None or got[0] != 200 or not isinstance(got[1], list):
            return None
        out: list[ShareTarget] = []
        for row in got[1]:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                out.append(
                    ShareTarget(
                        id=row["id"],
                        name=str(row.get("name") or row["id"]),
                        is_everyone=bool(row.get("is_everyone")) or row["id"] == _EVERYONE,
                    )
                )
        return out


def directory_for(vault: Path) -> Directory | None:
    """The linked Agnes for ``vault`` (``scoutctl repo link``), or None when it isn't linked."""
    from scout.repo_sync import RepoSyncError, load_state, load_token

    try:
        state = load_state(vault)
        if state is None or not state.agnes_url:
            return None
        return Directory(state.agnes_url, load_token(vault))
    except RepoSyncError:
        return None


# ----- targets, principals, entries -------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A folder (shared as ``<rel>/**``) or a single note (its exact path)."""

    rel: str
    folder: bool

    @property
    def glob(self) -> str:
        return f"{self.rel}/**" if self.folder else self.rel

    def covers(self, rel: str) -> bool:
        return rel == self.rel or (self.folder and rel.startswith(self.rel + "/"))


def resolve_target(vault: Path, raw: str) -> Target:
    try:
        rel = normalize_rel(vault, raw)
    except ValueError as e:
        raise ShareUsage(str(e)) from e
    path = vault / rel
    if path.is_dir():
        return Target(rel=rel, folder=True)
    if path.is_file() and rel.lower().endswith(".md"):
        return Target(rel=rel, folder=False)
    raise ShareUsage(f"not a folder or a markdown note in the vault: {raw}")


def parse_with(values: Iterable[str]) -> list[str]:
    """``--with`` values → principals: an email or ``person:<email>`` → ``person:``; ``group:<id or name>``; ``org``.

    Group names are resolved later (:func:`resolve_groups`); here they are only trimmed.
    """
    out: list[str] = []
    for raw in values:
        v = raw.strip()
        low = v.lower()
        if low == "org":
            principal = "org"
        elif low.startswith("group:"):
            name = v[len("group:") :].strip()
            if not name:
                raise ShareUsage(f"--with {raw!r}: name a group (group:<id or name>)")
            principal = f"group:{name}"
        else:
            principal = parse_principal(v if low.startswith("person:") else f"person:{v}") or ""
            if not principal:
                raise ShareUsage(f"--with {raw!r}: give an email, person:<email>, or group:<id or name>")
        if principal not in out:
            out.append(principal)
    if not out:
        raise ShareUsage("--with needs at least one person or group")
    return out


def resolve_groups(
    principals: Sequence[str], directory: Directory | None
) -> tuple[list[str], dict[str, str], list[str]]:
    """``(principals with group:<id>, {group principal: display name}, warnings)``.

    With Agnes reachable, a group must be one of the owner's share targets,
    named by id or (case-insensitively, unambiguously) by name; Everyone is
    refused. Without it, an id-shaped value is kept with a warning: Agnes
    admits a group only while both the owner and the reader belong to it.
    """
    groups = [p for p in principals if p.startswith("group:")]
    if not groups:
        return list(principals), {}, []
    targets = directory.share_targets() if directory is not None else None
    names: dict[str, str] = {}
    warnings: list[str] = []
    out: list[str] = []
    for p in principals:
        if not p.startswith("group:"):
            out.append(p)
            continue
        wanted = p[len("group:") :]
        if targets is None:
            if wanted.lower() == _EVERYONE:
                raise ShareRefused("refused: Everyone can't be named; share with people or your own groups (ruling 7)")
            if not _GROUP_ID_RE.match(wanted):
                raise ShareUsage(f"{p}: Agnes can't be asked, so give the group's id (letters, digits, - and _)")
            warnings.append(
                f"group:{wanted}: couldn't check it with Agnes; it admits a reader only while you and they are "
                "both members"
            )
            resolved = f"group:{wanted}"
        else:
            hits = [t for t in targets if t.id == wanted] or [
                t for t in targets if t.name.casefold() == wanted.casefold()
            ]
            if len(hits) > 1:
                raise ShareUsage(f"{p}: more than one of your groups has that name; give its id")
            if not hits:
                raise ShareRefused(f"refused: {p} is not one of your share targets in Agnes")
            if hits[0].is_everyone:
                raise ShareRefused("refused: Everyone can't be named; share with people or your own groups (ruling 7)")
            resolved = f"group:{hits[0].id}"
            names[resolved] = hits[0].name
        if resolved not in out:
            out.append(resolved)
    return out, names, warnings


def check_people(principals: Sequence[str], directory: Directory | None) -> list[str]:
    """Warnings for the ``person:`` principals; raises :class:`ShareRefused` for one Agnes doesn't know."""
    people = [p[len("person:") :] for p in principals if p.startswith("person:")]
    if not people:
        return []
    if directory is None:
        return [
            f"{', '.join(people)}: this vault isn't linked to Agnes, so "
            f"{'this email is' if len(people) == 1 else 'these emails are'} checked only when someone reads"
        ]
    unchecked: list[str] = []
    for email in people:
        found = directory.colleague(email)
        if found == "unknown":
            raise ShareRefused(f"refused: {email} isn't a user of the linked Agnes")
        if found == "unchecked":
            unchecked.append(email)
    if unchecked:
        return [
            f"{', '.join(unchecked)}: this Agnes can't look people up yet, so "
            f"{'this email is' if len(unchecked) == 1 else 'these emails are'} checked only when someone reads"
        ]
    return []


def _union(scope: FederationScope, add: Sequence[str]) -> FederationScope:
    if scope == "org":
        return "org"
    have = list(scope) if isinstance(scope, tuple) else []
    return tuple(dict.fromkeys((*have, *add))) or "none"


def _minus(scope: FederationScope, remove: Sequence[str] | None) -> FederationScope:
    if remove is None or (scope == "org" and "org" in remove):
        return "none"
    if not isinstance(scope, tuple):
        return scope  # "none" stays; "org" isn't a list to take a person out of
    return tuple(p for p in scope if p not in remove) or "none"


def _principals_of(scope: FederationScope) -> list[str]:
    return list(scope) if isinstance(scope, tuple) else (["org"] if scope == "org" else [])


def _has(scope: FederationScope, principal: str) -> bool:
    """Whether ``scope`` names ``principal`` (``org`` names only ``org``; it admits nobody in Agnes)."""
    return scope_admits(scope, principal) or (principal == "org" and scope == "org")


def _scope_json(scope: FederationScope) -> str | list[str]:
    return list(scope) if isinstance(scope, tuple) else scope


def _glob_root(glob: str) -> tuple[str, bool] | None:
    """``(path, is_folder)`` for the two glob forms these commands write; None for any other glob."""
    if glob.endswith("/**") and not _WILDCARDS.search(glob[:-3]):
        return glob[:-3], True
    if not _WILDCARDS.search(glob):
        return glob, False
    return None


def _inside(glob: str, target: Target) -> bool:
    """Whether entry ``glob`` names ``target`` itself or something inside it."""
    root = _glob_root(glob)
    return root is not None and target.covers(root[0]) and (target.folder or not root[1])


def _broader(glob: str, target: Target) -> bool:
    """Whether entry ``glob`` is a folder entry strictly above ``target``."""
    root = _glob_root(glob)
    return root is not None and root[1] and target.rel.startswith(root[0] + "/")


@dataclass
class _Entry:
    """A ``federation_defaults`` entry as the overlay holds it: the raw mapping, and its parsed scope."""

    raw: dict[str, Any]
    scope: FederationScope | None  # None: an entry these commands don't understand, kept as is

    @property
    def glob(self) -> str:
        return str(self.raw.get("glob"))

    def set_scope(self, scope: FederationScope) -> None:
        self.raw = {**self.raw, "scope": _scope_json(scope)}
        self.scope = scope


# ----- the overlay -------------------------------------------------------------------


@dataclass
class Overlay:
    """``scout-config.yaml`` and its ``privacy:`` block, for editing the federation keys."""

    text: str
    data: dict[str, Any]
    privacy: dict[str, Any]
    entries: list[_Entry]

    def with_federation(self, *, enabled: bool | None) -> dict[str, Any]:
        """The config data with ``entries`` written back (and ``federation.enabled`` set, unless None)."""
        privacy = dict(self.privacy)
        if self.entries or "federation_defaults" in privacy:
            privacy["federation_defaults"] = [e.raw for e in self.entries]
        if enabled is not None:
            fed = privacy.get("federation")
            privacy["federation"] = {**(fed if isinstance(fed, dict) else {}), "enabled": enabled}
        return {**self.data, "privacy": privacy}


def load_overlay(vault: Path) -> Overlay:
    config = paths.config_path(vault)
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        raise ShareRefused("refused: scout-config.yaml doesn't parse; fix it first") from e
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ShareRefused("refused: scout-config.yaml isn't a mapping; fix it first")
    privacy = data.get("privacy")
    privacy = {} if privacy is None else privacy
    if not isinstance(privacy, dict):
        raise ShareRefused("refused: scout-config.yaml → privacy isn't a mapping; fix it by hand first")
    raw = privacy.get("federation_defaults")
    raw = [] if raw is None else raw
    if not isinstance(raw, list):
        raise ShareRefused("refused: privacy.federation_defaults isn't a list; fix it by hand first")
    entries: list[_Entry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ShareRefused("refused: privacy.federation_defaults holds an entry that isn't a mapping; fix it first")
        parsed = policy_from_overlay({"federation_defaults": [item]}).federation_defaults
        entries.append(_Entry(raw=dict(item), scope=parsed[0].scope if parsed else None))
    return Overlay(text=text, data=data, privacy=dict(privacy), entries=entries)


def write_overlay(vault: Path, overlay: Overlay, data: dict[str, Any]) -> None:
    """Write ``data`` over ``scout-config.yaml``, keeping every block but ``privacy:`` byte for byte."""
    from scout.scripts.bootstrap import _dump_keeping_comments
    from scout.scripts.privacy_migrate import _atomic_write_bytes

    config = paths.config_path(vault)
    result = _dump_keeping_comments(overlay.text, data)
    if (yaml.safe_load(result) or {}) != data:
        raise RuntimeError("could not write scout-config.yaml → privacy")
    if config.exists():
        _atomic_write_bytes(config, result.encode("utf-8"))
    else:
        config.write_text(result, encoding="utf-8")


# ----- notes under a target ----------------------------------------------------------


def _labels(vault: Path, policy: Policy, *, persist: bool) -> dict[str, Label]:
    from scout.kb.read import all_labels

    return all_labels(vault.resolve(), policy, persist=persist)


def _notes(labels: Mapping[str, Label], target: Target) -> list[str]:
    return sorted(rel for rel in labels if rel.lower().endswith(".md") and target.covers(rel))


def _facts(vault: Path, rel: str) -> tuple[bool, str | None]:
    """``(declares federation_scope, frontmatter kind)`` read from the note."""
    text, unreadable = read_head(vault / rel)
    facts = note_facts(text, unreadable=unreadable)
    return facts.declared_present, facts.kind


def _admits_level(principal: str, ceiling: CeilingLevel) -> bool:
    if ceiling == "org":
        return True
    if ceiling == "people":
        return principal.startswith("person:")
    return False


def _exclude_reason(label: Label, *, below_ceiling: bool) -> ExcludeReason:
    """Why a note under a share doesn't get it. ``below_ceiling``: its folder ceiling doesn't admit the people."""
    if label.visibility == "private":
        return "private"
    if label.local_only:
        return "local-only"
    if below_ceiling or label.federation_source == "ceiling":
        return "ceiling"
    if label.federation_source == "frontmatter":
        return "frontmatter"
    return "earlier-entry"


_REASON_TEXT: dict[str, str] = {
    "private": "private (private always wins)",
    "local-only": "local-only (never leaves the laptop)",
    "ceiling": "its folder can't be shared this widely",
    "frontmatter": "its own federation_scope decides (edit the note to share it)",
    "earlier-entry": "an earlier federation_defaults entry decides",
    "sharing-off": "sharing is off",
}


def _ceiling_refusal(principal: str, ceiling: CeilingLevel, what: str) -> str:
    if ceiling == "people":
        return (
            f"refused: {principal} is above the ceiling of {what}: meetings and threads are shared with named "
            "people only, never a group or the whole organisation"
        )
    return (
        f"refused: {what} can't be shared (its ceiling is none: people notes, action items, logs, emails and "
        "documents never are)"
        if ceiling == "none"
        else f"refused: {principal} is above the ceiling of {what}"
    )


def _people_ceiling_folders(policy: Policy) -> list[str]:
    """The literal folder prefixes of every ``people`` ceiling (``knowledge-base/sources/``)."""
    out: list[str] = []
    for r in (*policy.federation_ceilings, *policy.federation_ceilings_overlay):
        if r.ceiling == "people":
            m = _WILDCARDS.search(r.glob)
            out.append(r.glob[: m.start()] if m else r.glob)
    return out


# ----- kb share ----------------------------------------------------------------------


@dataclass
class SharePlan:
    target: Target
    principals: list[str]
    names: dict[str, str]
    warnings: list[str]
    newly: list[tuple[str, list[str]]] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    private_links: list[tuple[str, list[int]]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    overlay: Overlay | None = None
    changed: bool = False
    enabling: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target.rel,
            "glob": self.target.glob,
            "folder": self.target.folder,
            "principals": self.principals,
            "group_names": self.names,
            "groups": [
                {
                    "id": g[len("group:") :],
                    "name": self.names.get(g),
                    "members": None,
                    "members_note": GROUP_MEMBERS_NOTE,
                }
                for g in self._groups()
            ],
            "group_owner_note": GROUP_OWNER_NOTE if self._groups() else None,
            "newly_shared": [{"path": p, "principals": w} for p, w in self.newly],
            "already_shared": self.already,
            "excluded": [{"path": p, "reason": r} for p, r in self.excluded],
            "private_links": [{"path": p, "lines": n} for p, n in self.private_links],
            "warnings": self.warnings,
            "copy_warning": COPY_WARNING,
            "changes_overlay": self.changed,
            "turns_sharing_on": self.enabling,
        }

    def _groups(self) -> list[str]:
        return [p for p in self.principals if p.startswith("group:")]

    def lines(self) -> list[str]:
        who = ", ".join(_display(p, self.names) for p in self.principals)
        out = [f"Share {self.target.glob} with {who}"]
        for g in self._groups():
            name = self.names.get(g) or "(name not checked with Agnes)"
            out.append(f"  group {g[len('group:') :]}: {name}; {GROUP_MEMBERS_NOTE}")
        if self._groups():
            out.append(f"  {GROUP_OWNER_NOTE}")
        out += [f"  warning: {w}" for w in self.warnings]
        out.append(f"Newly shared: {len(self.newly)} note(s)")
        out += [f"  {p}  +{', '.join(_display(x, self.names) for x in w)}" for p, w in self.newly]
        if self.already:
            out.append(f"Already shared with them: {len(self.already)} note(s)")
        if self.excluded:
            out.append(f"Not shared: {len(self.excluded)} note(s)")
            out += [f"  {p}  {_REASON_TEXT[r]}" for p, r in self.excluded]
        if self.private_links:
            out.append(
                f"Warning: {len(self.private_links)} note(s) about to be shared link or name a private note "
                "(Agnes shows a link to an unshared note as its text):"
            )
            out += [f"  {p}  line(s) {', '.join(str(n) for n in ns)}" for p, ns in self.private_links]
        if self.enabling:
            out.append("This turns sharing on for this vault (privacy.federation.enabled: true).")
        out.append(COPY_WARNING)
        return out


def _display(principal: str, names: Mapping[str, str]) -> str:
    if principal.startswith("person:"):
        return principal[len("person:") :]
    if principal in names:
        return f"{names[principal]} ({principal})"
    return principal


def plan_share(
    vault: Path,
    raw_target: str,
    with_values: Sequence[str],
    *,
    directory: Directory | None,
) -> SharePlan:
    """Everything ``kb share`` would do, refusals raised, nothing written."""
    vault = vault.resolve()
    target = resolve_target(vault, raw_target)
    principals = parse_with(with_values)
    overlay = load_overlay(vault)
    before = load_policy(vault)
    if "privacy-overlay-unreadable" in before.warnings:
        raise ShareRefused("refused: scout-config.yaml can't be read; fix it first")
    labels_before = _labels(vault, before, persist=True)
    notes = _notes(labels_before, target)
    if not target.folder and not notes:
        notes = [target.rel]

    if target.folder:
        inside = target.rel + "/"
        for prefix in _people_ceiling_folders(before):
            if prefix.startswith(inside) or inside.startswith(prefix):
                raise ShareRefused(
                    f"refused: {target.rel} holds meeting and thread notes, which are shared note by note "
                    f"(ruling 6): run kb share on each note under {prefix.rstrip('/')}"
                )

    facts = {rel: _facts(vault, rel) for rel in notes}
    ceilings: dict[str, CeilingLevel] = {rel: folder_ceiling(before, rel, facts[rel][1]) for rel in notes}
    if not notes:  # an empty folder: the ceiling a new note there would have
        ceilings = {"": folder_ceiling(before, f"{target.rel}/_.md", None)}
    what = target.rel + ("/" if target.folder else "")
    for p in principals:
        if not any(_admits_level(p, c) for c in ceilings.values()):
            widest = max(ceilings.values(), key=("none", "people", "org").index)
            raise ShareRefused(_ceiling_refusal(p, widest, what))
    if "org" in principals:
        raise ShareRefused("refused: org admits nobody in Agnes (ruling 7); share with named people or your own groups")

    if not target.folder:
        label = labels_before.get(target.rel)
        if label is not None and label.visibility == "private":
            raise ShareRefused(f"refused: {target.rel} is private, and private always wins")
        if label is not None and label.local_only:
            raise ShareRefused(f"refused: {target.rel} is local-only and never leaves the laptop")
        if facts[target.rel][0]:
            raise ShareRefused(
                f"refused: {target.rel} sets its own federation_scope, which decides for it; edit that line instead"
            )

    principals, names, warnings = resolve_groups(principals, directory)
    warnings += check_people(principals, directory)

    entries = overlay.entries
    same = next((e for e in entries if e.glob == target.glob), None)
    if same is not None:
        if same.scope is None:
            raise ShareRefused(
                f"refused: the federation_defaults entry for {target.glob} has a bad scope; fix it first"
            )
        same.set_scope(_union(same.scope, principals))
    else:
        at = next((i for i, e in enumerate(entries) if _broader(e.glob, target)), len(entries))
        # The new entry shadows the broader one for these notes: carry its people over, so adding never removes.
        outer = entries[at].scope if at < len(entries) else None
        scope = _union(outer if isinstance(outer, tuple) else "none", principals)
        entries.insert(at, _Entry(raw={"glob": target.glob, "scope": _scope_json(scope)}, scope=scope))
    for e in entries:
        if e is not same and e.glob != target.glob and e.scope is not None and _inside(e.glob, target):
            e.set_scope(_union(e.scope, principals))
    data = overlay.with_federation(enabled=True)
    after = policy_from_overlay(data.get("privacy"))
    labels_after = _labels(vault, after, persist=False)

    plan = SharePlan(target=target, principals=principals, names=names, warnings=warnings, overlay=overlay)
    plan.data = data
    plan.enabling = not before.federation_enabled
    plan.changed = data != overlay.data
    for rel in notes:
        old, new = labels_before.get(rel), labels_after.get(rel)
        if new is None:
            continue
        had = old.federation_scope if old is not None else "none"
        gained = [p for p in principals if _has(new.federation_scope, p) and not _has(had, p)]
        if gained:
            plan.newly.append((rel, gained))
        elif all(_has(new.federation_scope, p) for p in principals):
            plan.already.append(rel)
        else:
            below = not all(_admits_level(p, ceilings.get(rel, "none")) for p in principals)
            plan.excluded.append((rel, _exclude_reason(new, below_ceiling=below)))
    if plan.newly:
        plan.private_links = _private_links(vault, after, [rel for rel, _ in plan.newly])
    return plan


def _private_links(vault: Path, policy: Policy, rels: Sequence[str]) -> list[tuple[str, list[int]]]:
    from scout.kb.outbound import find_mentions, private_terms

    terms = private_terms(vault, policy, persist=False)
    out: list[tuple[str, list[int]]] = []
    for rel in rels:
        try:
            text = (vault / rel).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        hits = find_mentions(text, terms, allow_markers=True)
        if hits:
            out.append((rel, hits))
    return out


def apply(vault: Path, plan: SharePlan | UnsharePlan) -> None:
    assert plan.overlay is not None
    write_overlay(vault.resolve(), plan.overlay, plan.data)


# ----- kb unshare --------------------------------------------------------------------


@dataclass
class UnsharePlan:
    target: Target
    principals: list[str] | None
    stopped: list[tuple[str, list[str]]] = field(default_factory=list)
    still_by_frontmatter: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    overlay: Overlay | None = None
    changed: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target.rel,
            "glob": self.target.glob,
            "principals": self.principals,
            "stopped": [{"path": p, "principals": w} for p, w in self.stopped],
            "still_shared_by_frontmatter": self.still_by_frontmatter,
            "changes_overlay": self.changed,
            "pending_until_sync": True,
        }

    def lines(self) -> list[str]:
        who = "everyone" if self.principals is None else ", ".join(_display(p, {}) for p in self.principals)
        out = [f"Stop sharing {self.target.glob} with {who}"]
        out.append(f"No longer shared: {len(self.stopped)} note(s)")
        out += [f"  {p}  -{', '.join(_display(x, {}) for x in w)}" for p, w in self.stopped]
        if self.still_by_frontmatter:
            out.append(
                f"Still shared by their own federation_scope: {len(self.still_by_frontmatter)} note(s) "
                "(edit those lines to stop them):"
            )
            out += [f"  {p}" for p in self.still_by_frontmatter]
        out.append(UNSHARE_PENDING)
        return out


def plan_unshare(
    vault: Path, raw_target: str, with_values: Sequence[str], *, directory: Directory | None = None
) -> UnsharePlan:
    """Everything ``kb unshare`` would do in the vault (no ``shares/stop`` call in S1a).

    ``directory`` only turns a group's name into its id; offline, give the id.
    """
    vault = vault.resolve()
    target = resolve_target(vault, raw_target)
    remove = parse_with(with_values) if with_values else None
    if remove is not None and directory is not None and any(p.startswith("group:") for p in remove):
        by_name = {t.name.casefold(): f"group:{t.id}" for t in directory.share_targets() or []}
        remove = list(
            dict.fromkeys(
                by_name.get(p[len("group:") :].casefold(), p) if p.startswith("group:") else p for p in remove
            )
        )
    overlay = load_overlay(vault)
    before = load_policy(vault)
    if "privacy-overlay-unreadable" in before.warnings:
        raise ShareRefused("refused: scout-config.yaml can't be read; fix it first")

    entries = overlay.entries
    has_same = any(e.glob == target.glob for e in entries)
    for i, e in enumerate(entries):
        if e.scope is not None and _inside(e.glob, target):
            entries[i].set_scope(_minus(e.scope, remove))
    if not has_same:
        at = next((i for i, e in enumerate(entries) if _broader(e.glob, target)), None)
        if at is not None and entries[at].scope is not None:
            outer = entries[at].scope
            assert outer is not None
            narrowed = _minus(outer, remove)
            if narrowed != outer:
                entries.insert(at, _Entry(raw={"glob": target.glob, "scope": _scope_json(narrowed)}, scope=narrowed))
    keep: list[_Entry] = []
    for i, e in enumerate(entries):
        drop = (
            e.scope == "none"
            and _inside(e.glob, target)
            and not any(
                later.scope not in (None, "none") and _covers_glob(later.glob, e.glob) for later in entries[i + 1 :]
            )
        )
        if not drop:
            keep.append(e)
    overlay.entries[:] = keep
    data = overlay.with_federation(enabled=None)
    after = policy_from_overlay(data.get("privacy"))

    labels_before = _labels(vault, before, persist=True)
    labels_after = _labels(vault, after, persist=False)
    plan = UnsharePlan(target=target, principals=remove, overlay=overlay, data=data, changed=data != overlay.data)
    for rel in _notes(labels_before, target):
        old, new = labels_before[rel], labels_after.get(rel)
        if new is None:
            continue
        old_p = _principals_of(old.federation_scope)
        targets = old_p if remove is None else [p for p in old_p if p in remove]
        if new.federation_source == "frontmatter":
            if any(_has(new.federation_scope, p) for p in targets):
                plan.still_by_frontmatter.append(rel)
            continue
        if any(_has(new.federation_scope, p) for p in targets):
            raise ShareRefused(
                f"refused: couldn't narrow {rel} with the federation_defaults order in scout-config.yaml; "
                "edit privacy.federation_defaults by hand"
            )
        gone = [p for p in targets if not _has(new.federation_scope, p)]
        if gone:
            plan.stopped.append((rel, gone))
    return plan


def _covers_glob(outer: str, inner: str) -> bool:
    o, i = _glob_root(outer), _glob_root(inner)
    if o is None or i is None or not o[1]:
        return False
    return i[0] == o[0] or i[0].startswith(o[0] + "/")


# ----- kb shares ---------------------------------------------------------------------


@dataclass
class ShareRow:
    glob: str
    source: Literal["default", "frontmatter"]
    principals: list[str]
    shared: list[str] = field(default_factory=list)
    excluded: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "glob": self.glob,
            "source": self.source,
            "principals": self.principals,
            "notes": len(self.shared),
            "shared": self.shared,
            "excluded": self.excluded,
        }


def list_shares(vault: Path, policy: Policy | None = None) -> tuple[bool, list[ShareRow]]:
    """``(sharing on, rows)``: each ``federation_defaults`` entry and each self-sharing note, with its notes."""
    vault = vault.resolve()
    policy = policy or load_policy(vault)
    labels = _labels(vault, policy, persist=True)
    rows: list[ShareRow] = []
    by_glob: dict[str, ShareRow] = {}
    for d in policy.federation_defaults:
        if d.glob not in by_glob and d.scope != "none":
            by_glob[d.glob] = ShareRow(glob=d.glob, source="default", principals=_principals_of(d.scope))
            rows.append(by_glob[d.glob])
    for rel in sorted(labels):
        if not rel.lower().endswith(".md"):
            continue
        label = labels[rel]
        default = first_default(policy, rel)
        row = by_glob.get(default.glob) if default is not None else None
        if label.federation_source == "frontmatter" and label.federation_scope != "none":
            rows.append(ShareRow(glob=rel, source="frontmatter", principals=_principals_of(label.federation_scope)))
            rows[-1].shared.append(rel)
            if row is not None:  # under a shared folder, but its own line decides
                row.excluded.setdefault("frontmatter", []).append(rel)
            continue
        if row is None:
            continue
        if label.federation_scope != "none":
            row.shared.append(rel)
            continue
        reason: str = _exclude_reason(label, below_ceiling=False)
        if not policy.federation_enabled and reason not in ("private", "local-only"):
            reason = "sharing-off"
        row.excluded.setdefault(reason, []).append(rel)
    return policy.federation_enabled, rows


def shares_lines(enabled: bool, rows: Sequence[ShareRow], names: Mapping[str, str]) -> list[str]:
    out = [f"Sharing is {'on' if enabled else 'off (privacy.federation.enabled is false)'}."]
    if not rows:
        out.append("Nothing is shared.")
        return out
    for row in rows:
        who = ", ".join(_display(p, names) for p in row.principals)
        own = ", its own frontmatter" if row.source == "frontmatter" else ""
        out.append(f"{row.glob}  with {who}  ({len(row.shared)} note(s){own})")
        for reason, rels in sorted(row.excluded.items()):
            out.append(f"  excluded, {_REASON_TEXT.get(reason, reason)}: {len(rels)}")
            out += [f"    {p}" for p in rels]
    return out


def group_names(directory: Directory | None, rows: Sequence[ShareRow]) -> dict[str, str]:
    """Current display names for the groups in ``rows`` (empty when Agnes can't be asked)."""
    if directory is None or not any(p.startswith("group:") for r in rows for p in r.principals):
        return {}
    targets = directory.share_targets() or []
    return {f"group:{t.id}": t.name for t in targets}
