"""Shared Areas S1b: the instant stop, and clearing it, through Agnes's A8b API.

Design: keboola/agnes-the-ai-analyst#6090 Part 2 §4 and §5.6; the contract is
keboola/agnes-the-ai-analyst#7450 (A8b). The vault stays the source of truth for
what is shared (ruling 5): Agnes may narrow a share, never widen one. So:

- ``kb unshare`` asks Agnes to **stop** the share (``POST
  /api/v1/me/knowledge-repository/shares/stop``, always ``write_back: false``)
  *before* it narrows the vault (ruling 15), so readers lose it from their next
  read instead of the next sync. A stop covers the share's glob: a note that
  lands under a stopped folder later stays stopped. Agnes stops only a share it
  lists, by its glob as written, for everyone or one person or group it admits
  (a group member included); it can't stop a subfolder of a broader share, which
  then waits for the next sync.
- ``kb shares`` reads the stops (``GET .../shares``): open ones per share, ones
  whose glob the synced vault no longer lists (``stops_not_listed``), and the
  ones cleared in the last 30 days (``cleared_stops``).
- Only ``kb share``, run by the owner at the keyboard, **clears** a stop
  (``POST .../shares/clear-stop``), and only for the people it names. Agnes can't
  tell a scheduled run on the owner's PAT from the owner, so Scout refuses the
  clear itself in a scheduled run, an agent run and a delegate checkout
  (:func:`clear_refusal`), and sends it only with the vault's link PAT as a
  bearer: Agnes answers ``404`` to any other credential and audits the refusal.
  The token is read from ``~/.scout-secrets`` for the request and never put in
  an environment, a log or an error message.
- While the A8b endpoints answer a bare ``404`` (an older Agnes) or the
  instance's sharing switch is off, everything falls back to S1a: the vault
  change is kept and the stop takes effect at the next ``scoutctl repo sync``.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHARES_PATH = "/api/v1/me/knowledge-repository/shares"
STOP_PATH = SHARES_PATH + "/stop"
CLEAR_PATH = SHARES_PATH + "/clear-stop"
CLEARED_DAYS = 30  # A8b keeps cleared stops in cleared_stops for this long

FALLBACK_NOTE = (
    "This Agnes has no instant stop yet (A8b), so the stop takes effect at your next `scoutctl repo sync`, once "
    "scout-config.yaml is committed."
)
OFFLINE_NOTE = (
    "Agnes couldn't be asked ({why}), so the stop takes effect at your next `scoutctl repo sync`, once "
    "scout-config.yaml is committed."
)
UNLINKED_NOTE = "This vault isn't linked to Agnes, so nothing is shared yet and there is nothing to stop there."
SHARING_OFF_NOTE = "Shared Areas are off on this Agnes, so nobody reads your shares there."
STOPPED_NOTE = "Agnes stopped it at once: from their next read, they no longer get it. No sync is needed for that."
VAULT_NOTE = "Commit scout-config.yaml and run `scoutctl repo sync` so the vault says so too."
RETRY_NOTE = (
    "Agnes didn't confirm every stop, so it may still serve the share until your next `scoutctl repo sync`. Run "
    "the same `scoutctl kb unshare` again to retry the stop at once: it asks Agnes again even though the vault is "
    "already narrowed. `scoutctl kb shares` shows what Agnes still serves."
)
RETRY_AFTER_SYNC = "Agnes keeps serving the share until your next `scoutctl repo sync` (fix the token first)."
EXPECTED_FALLBACKS = (FALLBACK_NOTE, SHARING_OFF_NOTE, UNLINKED_NOTE)  # an Agnes that can't stop: not a failure
PENDING_PULL_NOTE = (
    "Agnes has {n} commit(s) on top of your last push (a stop it wrote into scout-config.yaml, say). Run "
    "`scoutctl repo sync` before you commit this change, so the two privacy edits don't clash."
)

_TYP_RE = re.compile(r"^[a-z_]{1,24}$")


class StopsUnavailable(Exception):
    """Agnes can't stop, list or clear right now. ``reason`` is one of the codes below; ``str()`` says why.

    ``old_agnes`` (the endpoint answers a bare 404: before A8b), ``sharing_off``
    (the instance's ``knowledge_repository_sharing`` switch), ``no_repository``,
    ``auth`` (401/403), ``offline`` (a network error or timeout), ``busy`` (409)
    and ``error`` (anything else).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class StopRefused(Exception):
    """Agnes answered with a refusal code for this one request (``share_not_found``, ``share_stop_not_found``…)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ClearRefused(Exception):
    """Scout refuses to clear a stop here (not the owner at the keyboard, or not a PAT). Nothing was sent."""


# ----- what Agnes answers ------------------------------------------------------------------


@dataclass(frozen=True)
class Stop:
    stop_id: str
    glob: str
    principal: str | None  # None: the share is stopped for everyone
    surface: str
    write_back: str
    created_at: str
    cleared_at: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> Stop | None:
        if (
            not isinstance(raw, Mapping)
            or not isinstance(raw.get("stop_id"), str)
            or not isinstance(raw.get("glob"), str)
        ):
            return None
        principal = raw.get("principal")
        return cls(
            stop_id=raw["stop_id"],
            glob=raw["glob"],
            principal=principal if isinstance(principal, str) and principal else None,
            surface=str(raw.get("surface") or ""),
            write_back=str(raw.get("write_back") or ""),
            created_at=str(raw.get("created_at") or ""),
            cleared_at=str(raw["cleared_at"]) if raw.get("cleared_at") else None,
        )

    def who(self) -> str:
        if self.principal is None:
            return "everyone"
        return self.principal[len("person:") :] if self.principal.startswith("person:") else self.principal

    def to_json(self) -> dict[str, Any]:
        return {
            "stop_id": self.stop_id,
            "glob": self.glob,
            "principal": self.principal,
            "who": self.who(),
            "surface": self.surface,
            "write_back": self.write_back,
            "created_at": self.created_at,
            "cleared_at": self.cleared_at,
        }


@dataclass(frozen=True)
class SharePrincipal:
    principal: str
    kind: str
    name: str | None
    members: tuple[str, ...]  # a group's member emails (lower-case), as Agnes reports them now


@dataclass(frozen=True)
class AgnesShare:
    glob: str
    kind: str  # folder (a federation_defaults entry) | note (a note that shares itself in frontmatter)
    notes: int
    principals: tuple[SharePrincipal, ...]
    stops: tuple[Stop, ...]

    def names(self, principal: str) -> bool:
        return any(same_principal(p.principal, principal) for p in self.principals)

    def group_of(self, principal: str) -> str | None:
        """The group this share names that admits ``person:<email>``, else None."""
        if not principal.startswith("person:"):
            return None
        email = principal[len("person:") :]
        return next((p.principal for p in self.principals if p.kind == "group" and email in p.members), None)

    def admits(self, principal: str) -> bool:
        return self.names(principal) or self.group_of(principal) is not None


@dataclass(frozen=True)
class SharesView:
    federation_enabled: bool
    pending_server_commits: int
    mirrors_config: bool
    shares: tuple[AgnesShare, ...]
    stops_not_listed: tuple[Stop, ...]
    cleared_stops: tuple[Stop, ...]

    @classmethod
    def parse(cls, raw: Any) -> SharesView:
        if not isinstance(raw, Mapping):
            raise StopsUnavailable("error", "Agnes answered GET /shares with something that isn't an object")
        shares: list[AgnesShare] = []
        for s in raw.get("shares") or ():
            if not isinstance(s, Mapping) or not isinstance(s.get("glob"), str):
                continue
            principals = tuple(
                SharePrincipal(
                    principal=str(p["principal"]),
                    kind=str(p.get("kind") or "other"),
                    name=str(p["name"]) if p.get("name") else None,
                    members=tuple(
                        str(m.get("email") or "").strip().lower()
                        for m in p.get("members") or ()
                        if isinstance(m, Mapping) and m.get("email")
                    ),
                )
                for p in s.get("principals") or ()
                if isinstance(p, Mapping) and isinstance(p.get("principal"), str)
            )
            stops = tuple(x for x in (Stop.parse(r) for r in s.get("stops") or ()) if x is not None)
            notes = s.get("notes")
            shares.append(
                AgnesShare(
                    glob=s["glob"],
                    kind=str(s.get("kind") or "folder"),
                    notes=notes if isinstance(notes, int) else 0,
                    principals=principals,
                    stops=stops,
                )
            )
        pending = raw.get("pending_server_commits")
        return cls(
            federation_enabled=bool(raw.get("federation_enabled")),
            pending_server_commits=pending if isinstance(pending, int) and pending > 0 else 0,
            mirrors_config=bool(raw.get("mirrors_config")),
            shares=tuple(shares),
            stops_not_listed=_stops(raw.get("stops_not_listed")),
            cleared_stops=_stops(raw.get("cleared_stops")),
        )

    def share(self, glob: str) -> AgnesShare | None:
        return next((s for s in self.shares if s.glob == glob), None)

    def open_stops(self) -> list[Stop]:
        """Every open stop: the listed shares' and the ones whose glob the synced vault no longer lists."""
        seen: dict[str, Stop] = {}
        for stop in (*(x for s in self.shares for x in s.stops), *self.stops_not_listed):
            seen.setdefault(stop.stop_id, stop)
        return list(seen.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "available": True,
            "federation_enabled": self.federation_enabled,
            "pending_server_commits": self.pending_server_commits,
            "mirrors_config": self.mirrors_config,
            "stops": [s.to_json() for s in self.open_stops() if s not in self.stops_not_listed],
            "stops_not_listed": [s.to_json() for s in self.stops_not_listed],
            "cleared_stops": [s.to_json() for s in self.cleared_stops],
        }


def _stops(raw: Any) -> tuple[Stop, ...]:
    return tuple(x for x in (Stop.parse(r) for r in raw or ()) if x is not None) if isinstance(raw, list) else ()


# ----- the client ------------------------------------------------------------------------------


class StopsClient:
    """A8b's owner endpoints, authenticated with the vault's link PAT (``scoutctl repo link``)."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def token_kind(self) -> str | None:
        """The ``typ`` claim of the link token (Agnes tokens are JWTs: ``pat`` or ``session``), else None."""
        return token_kind(self._token)

    def _send(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """``(status, JSON body or None)``. Raises :class:`StopsUnavailable` for a network failure only."""
        import requests

        try:
            resp = requests.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            raise StopsUnavailable("offline", f"Agnes at {self.base_url} can't be reached ({type(e).__name__})") from e
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = None
        return resp.status_code, data

    def _call(self, method: str, path: str, body: Any = None, *, refusals: Sequence[str] = ()) -> Any:
        status, data = self._send(method, path, body)
        if 200 <= status < 300:
            return data
        detail = data.get("detail") if isinstance(data, Mapping) else None
        code = detail.get("code") if isinstance(detail, Mapping) else None
        message = str(detail.get("message") or code) if isinstance(detail, Mapping) else ""
        if isinstance(code, str) and code in refusals:
            raise StopRefused(code, message)
        if status in (401, 403):
            raise StopsUnavailable("auth", f"Agnes refused the vault's token ({status}); link again with a new PAT")
        if status == 404 and code == "knowledge_repository_sharing_disabled":
            raise StopsUnavailable("sharing_off", SHARING_OFF_NOTE)
        if status == 404 and code == "knowledge_repository_not_found":
            raise StopsUnavailable("no_repository", "Agnes has no Personal Knowledge Repository for this token")
        if status in (404, 405) and not isinstance(code, str):
            raise StopsUnavailable("old_agnes", FALLBACK_NOTE)
        if status == 409:
            raise StopsUnavailable("busy", f"Agnes is busy with your repository ({message or 409}); try again")
        raise StopsUnavailable(
            "error", f"Agnes answered {status} to {method} {path}" + (f": {message}" if message else "")
        )

    def shares(self) -> SharesView:
        return SharesView.parse(self._call("GET", SHARES_PATH))

    def stop(self, glob: str, principal: str | None) -> Stop:
        """Stop ``glob`` for ``principal`` (or everyone). Never asks Agnes to write it back: the vault is Scout's."""
        body: dict[str, Any] = {"glob": glob, "write_back": False}
        if principal is not None:
            body["person"] = principal
        data = self._call("POST", STOP_PATH, body, refusals=("share_not_found", "share_widen_in_vault_only"))
        stop = Stop.parse(data.get("stop") if isinstance(data, Mapping) else None)
        if stop is None:
            raise StopsUnavailable("error", "Agnes answered the stop without the stop it made")
        return stop

    def clear(self, stop_id: str) -> Stop:
        """Clear one stop. Only :func:`apply_clears` calls this, after :func:`clear_refusal`."""
        try:
            data = self._call("POST", CLEAR_PATH, {"stop_id": stop_id}, refusals=("share_stop_not_found",))
        except StopsUnavailable as e:
            if e.reason == "no_repository":
                # The one 404 Agnes gives every credential but the owner's PAT sent as a bearer (audited there).
                raise StopRefused(
                    "credential_refused",
                    "Agnes refused to clear it: it takes only your own personal access token, sent from your "
                    "keyboard. The stop is still in force",
                ) from e
            raise
        stop = Stop.parse(data.get("stop") if isinstance(data, Mapping) else None)
        if stop is None:
            raise StopsUnavailable("error", "Agnes answered the clear without the stop it cleared")
        return stop


def token_kind(token: str) -> str | None:
    """The ``typ`` claim of a JWT, read without verifying it (Agnes does that), else None. Never the token itself."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error):
        return None
    typ = payload.get("typ") if isinstance(payload, dict) else None
    return typ if isinstance(typ, str) and _TYP_RE.match(typ) else ("unknown" if typ is not None else None)


def connect(vault: Path) -> tuple[StopsClient | None, str | None]:
    """``(client, None)`` for a linked vault; ``(None, None)`` for an unlinked one; ``(None, why)`` when the vault is
    linked but its token can't be loaded (missing, loose permissions, unreadable). Never the token in ``why``."""
    from scout.repo_sync import RepoSyncError, load_state, load_token

    try:
        state = load_state(vault)
    except (RepoSyncError, OSError):
        return None, None
    if state is None or not state.agnes_url:
        return None, None
    try:
        return StopsClient(state.agnes_url, load_token(vault)), None
    except RepoSyncError as e:
        what = {"token_missing": "is missing", "token_insecure": "has loose permissions"}.get(e.code, "is unusable")
        return None, f"This vault is linked to Agnes, but the Agnes token {what} ({e}), so Agnes couldn't be asked"
    except OSError as e:  # a sandboxed run can't read ~/.scout-secrets (PermissionError)
        return None, (
            f"This vault is linked to Agnes, but the Agnes token can't be read ({type(e).__name__}), so Agnes "
            "couldn't be asked"
        )


def client_for(vault: Path) -> StopsClient | None:
    """The linked Agnes for ``vault``, or None when it isn't linked or its token can't be loaded (:func:`connect`)."""
    return connect(vault)[0]


def fetch_view(client: StopsClient | None) -> tuple[SharesView | None, str | None]:
    """``(the owner's shares in Agnes, None)``, or ``(None, why not)`` (unlinked, older Agnes, offline…)."""
    if client is None:
        return None, UNLINKED_NOTE
    try:
        return client.shares(), None
    except StopsUnavailable as e:
        return None, _unavailable_text(e)


def _unavailable_text(e: StopsUnavailable) -> str:
    if e.reason in ("old_agnes", "sharing_off"):
        return str(e)
    return OFFLINE_NOTE.format(why=str(e))


# ----- the gate ----------------------------------------------------------------------------------


def clear_refusal(vault: Path, env: Mapping[str, str], client: StopsClient) -> str | None:
    """Why Scout won't clear a stop here, else None. Checked before any request is sent.

    Clearing widens what is shared, so it is the owner's act at the keyboard
    only (as ``repo sync-only --accept`` and ``sandbox off``): refused when
    ``SCOUT_MODE`` is set (a scheduled run), the reader isn't ``owner`` (an
    engine-launched or agent run, ``delegate:*``, another person's agent), a
    live scheduled run of this vault is in this process's ancestry (read from
    the process table, so ``env -u SCOUT_MODE`` doesn't get past it, #595), the
    vault is a delegate checkout, or the link token isn't a personal access token.
    A clear takes effect in Agnes at once, with no commit-time check behind it,
    so the process check runs here and not only in the commit hook.
    """
    from scout.delegate_agent import ENV_FILE
    from scout.kb import live_run
    from scout.scripts.privacy_migrate import headless_reason

    why = headless_reason(env)
    if why:
        return f"refused: {why}. Only the owner, at the keyboard, may clear a stop in Agnes"
    src = live_run.commit_source_now(vault)
    if src is not None and src.runs:
        return (
            f"refused: this command runs inside a scheduled run ({src.mode}, by {src.by}), whatever its environment "
            "says. Only the owner, at the keyboard, may clear a stop in Agnes"
        )
    if (vault / ".git" / ENV_FILE).exists():
        return "refused: this is a delegate checkout. Only the owner, at the keyboard, may clear a stop in Agnes"
    kind = client.token_kind()
    if kind != "pat":
        return (
            "refused: the vault's Agnes token isn't a personal access token "
            f"({'its kind is ' + kind if kind else 'Scout can’t read its kind'}), and Agnes clears a stop only for "
            "your own PAT. Link again with a PAT (`scoutctl repo link`), then share again"
        )
    return None


# ----- what kb unshare stops ---------------------------------------------------------------------


@dataclass(frozen=True)
class StopRequest:
    glob: str
    principal: str | None  # None: everyone
    via: str | None = None  # the group that admits this person, when the share doesn't name them

    def who(self) -> str:
        if self.principal is None:
            return "everyone"
        who = self.principal[len("person:") :] if self.principal.startswith("person:") else self.principal
        return f"{who} (through {self.via})" if self.via else who

    def to_json(self) -> dict[str, Any]:
        return {"glob": self.glob, "principal": self.principal, "via": self.via}


def same_principal(a: str | None, b: str | None) -> bool:
    """Whether two principals are one: a ``person:`` email compares case-insensitively, a ``group:`` id exactly."""
    if a is None or b is None:
        return a is b
    if a.lower().startswith("person:") and b.lower().startswith("person:"):
        return a.lower() == b.lower()
    return a == b


def _covered(view: SharesView, glob: str, principal: str | None) -> bool:
    """Whether an open stop already keeps ``principal`` (None: everyone) off ``glob``."""
    for stop in view.open_stops():
        if stop.glob == glob and (stop.principal is None or same_principal(stop.principal, principal)):
            return True
    return False


def unshare_stops(view: SharesView, target: Any, remove: Sequence[str] | None) -> tuple[list[StopRequest], list[str]]:
    """``(stops to make, notes)`` for ``kb unshare <target> [--with …]``.

    ``target`` is a :class:`scout.kb.share.Target`. Each share Agnes lists at
    the target or inside it is stopped: for everyone, or for each person or
    group it admits among ``remove``, a person admitted only through a group
    included (the vault can't say that; the stop lives in Agnes). A note that
    shares itself in frontmatter isn't stopped: its own line decides, and
    ``kb unshare`` lists it. A broader share holding the target can't be
    stopped for the target alone, so it is only noted.
    """
    from scout.kb.share import _broader, _inside

    out: list[StopRequest] = []
    notes: list[str] = []
    for share in view.shares:
        if share.kind == "note":
            continue
        if _inside(share.glob, target):
            if remove is None:
                if not _covered(view, share.glob, None):
                    out.append(StopRequest(share.glob, None))
                continue
            for principal in remove:
                if _covered(view, share.glob, principal):
                    continue
                if share.names(principal):
                    out.append(StopRequest(share.glob, principal))
                elif (group := share.group_of(principal)) is not None:
                    out.append(StopRequest(share.glob, principal, via=group))
        elif _broader(share.glob, target) and (remove is None or any(share.admits(p) for p in remove)):
            notes.append(
                f"{target.glob} is inside the share {share.glob}, and Agnes stops only a whole share, so for "
                f"{target.glob} alone the stop takes effect at your next `scoutctl repo sync`."
            )
    return out, notes


@dataclass
class StopOutcome:
    """What ``kb unshare`` got from Agnes."""

    status: str  # stopped | partial (some refused) | nothing | unavailable | unlinked | preview
    made: list[Stop] = field(default_factory=list)
    planned: list[StopRequest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failed: bool = False  # a stop Agnes should have made wasn't confirmed: kb unshare exits 4

    @property
    def pending_until_sync(self) -> bool:
        return self.status != "stopped"

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failed": self.failed,
            "stops": [s.to_json() for s in self.made],
            "planned": [r.to_json() for r in self.planned],
            "notes": self.notes,
        }

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.status == "preview":
            out += [f"Would stop in Agnes at once: {r.glob} for {r.who()}" for r in self.planned]
        for stop in self.made:
            out.append(f"Stopped in Agnes: {stop.glob} for {stop.who()} (stop {stop.stop_id})")
        if self.status == "stopped":
            out.append(STOPPED_NOTE)
            if any(r.via for r in self.planned):
                out.append(
                    "A stop for one member of a group lives in Agnes only (your vault names the group, not them): "
                    "it stays until you share with that person again with `scoutctl kb share`."
                )
        out += self.notes
        if self.failed:
            out.append(RETRY_NOTE)
        return out


def make_stops(
    client: StopsClient | None,
    view: SharesView | None,
    why: str | None,
    requests_: list[StopRequest],
    notes: list[str],
    *,
    dry_run: bool,
    link_problem: str | None = None,
) -> StopOutcome:
    """Send ``requests_`` (``kb unshare`` calls this before it writes the vault). Never raises.

    ``link_problem``: the vault is linked but its token can't be loaded (:func:`connect`). That is a failure,
    not an unlinked vault: Agnes keeps serving the share until the next sync.
    """
    if client is None and link_problem:
        return StopOutcome("unavailable", notes=[*notes, f"{link_problem}. {RETRY_AFTER_SYNC}"], failed=True)
    if client is None:
        return StopOutcome("unlinked", notes=[*notes, UNLINKED_NOTE])
    if view is None:
        why = why or FALLBACK_NOTE
        return StopOutcome("unavailable", notes=[*notes, why], failed=why not in EXPECTED_FALLBACKS)
    if dry_run:
        return StopOutcome("preview", planned=requests_, notes=notes)
    if not requests_:
        extra = [] if notes else ["Agnes isn't serving that share to them (nothing synced, or already stopped)."]
        return StopOutcome("nothing", notes=[*notes, *extra])
    outcome = StopOutcome("stopped", planned=requests_, notes=list(notes))
    for req in requests_:
        try:
            outcome.made.append(client.stop(req.glob, req.principal))
        except StopRefused as e:
            outcome.status = "partial"
            outcome.failed = True
            outcome.notes.append(f"Agnes didn't stop {req.glob} for {req.who()}: {e.code} ({e})")
        except StopsUnavailable as e:
            outcome.status = "unavailable"
            outcome.failed = True
            outcome.notes.append(_unavailable_text(e))
            break
    if view.pending_server_commits:
        outcome.notes.append(PENDING_PULL_NOTE.format(n=view.pending_server_commits))
    return outcome


# ----- what kb share clears ----------------------------------------------------------------------


@dataclass
class ClearPlan:
    """The stops ``kb share`` clears (and, before clearing a stop for everyone, the people it keeps stopped)."""

    clears: list[Stop] = field(default_factory=list)
    keep_stopped: list[StopRequest] = field(default_factory=list)
    kept: list[tuple[Stop, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    available: bool = False

    def __bool__(self) -> bool:
        return bool(self.clears)

    def to_json(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "clears": [s.to_json() for s in self.clears],
            "keeps_stopped": [r.to_json() for r in self.keep_stopped],
            "still_stopped": [{**s.to_json(), "why": why} for s, why in self.kept],
            "notes": self.notes,
        }

    def lines(self) -> list[str]:
        out = [f"Clears the stop in Agnes on {s.glob} for {s.who()} (since {when(s.created_at)})" for s in self.clears]
        if self.keep_stopped:
            out.append("  and keeps it stopped for the people you didn't name now:")
            out += [f"    {r.glob} for {r.who()}" for r in self.keep_stopped]
        out += [
            f"Still stopped in Agnes: {s.glob} for {s.who()} (since {when(s.created_at)}): {why}"
            for s, why in self.kept
        ]
        out += self.notes
        return out


def share_clears(view: SharesView, target: Any, principals: Sequence[str]) -> ClearPlan:
    """The stops re-sharing ``target`` with ``principals`` clears, and the ones it leaves (``kb share``).

    A stop on the target's glob or a glob inside it is cleared when it names
    one of ``principals``. A stop for everyone is cleared too, but only after a
    stop is made for each person or group Agnes still lists on that share and
    the owner didn't name now: the synced vault may still list people the owner
    unshared, and clearing must never re-admit someone who wasn't named. A stop
    for anyone else, or on a broader share holding the target, stays.
    """
    from scout.kb.share import _broader, _inside

    plan = ClearPlan(available=True)
    wanted = set(principals)
    for stop in view.open_stops():
        if _inside(stop.glob, target):
            if stop.principal is None:
                plan.clears.append(stop)
                listed = view.share(stop.glob)
                for p in listed.principals if listed is not None else ():
                    if not any(same_principal(p.principal, w) for w in wanted) and not _covered_by_person(
                        view, stop.glob, p.principal
                    ):
                        plan.keep_stopped.append(StopRequest(stop.glob, p.principal))
            elif any(same_principal(stop.principal, w) for w in wanted):
                plan.clears.append(stop)
            else:
                plan.kept.append((stop, f"share with {stop.who()} to clear it"))
        elif _broader(stop.glob, target):
            plan.kept.append((stop, "a stop on the broader share; `kb share` that folder to clear it"))
    if view.pending_server_commits:
        plan.notes.append(PENDING_PULL_NOTE.format(n=view.pending_server_commits))
    return plan


def _covered_by_person(view: SharesView, glob: str, principal: str) -> bool:
    return any(s.glob == glob and same_principal(s.principal, principal) for s in view.open_stops())


def apply_clears(vault: Path, env: Mapping[str, str], client: StopsClient, plan: ClearPlan) -> list[str]:
    """Clear ``plan``'s stops; lines saying what happened. Raises :class:`ClearRefused` before sending anything.

    The people the owner didn't name are stopped first, so a stop for
    everyone is never lifted before they are kept out. Any failure leaves the
    stop in force (narrower is the safe side) and says so.
    """
    refusal = clear_refusal(vault, env, client)
    if refusal:
        raise ClearRefused(refusal)
    out: list[str] = []
    blocked: set[str] = set()
    for req in plan.keep_stopped:
        try:
            client.stop(req.glob, req.principal)
            out.append(f"Kept stopped in Agnes: {req.glob} for {req.who()}")
        except (StopRefused, StopsUnavailable) as e:
            blocked.add(req.glob)
            out.append(f"Couldn't keep {req.glob} stopped for {req.who()} ({e}), so its stop for everyone stays")
    for stop in plan.clears:
        if stop.principal is None and stop.glob in blocked:
            continue
        try:
            client.clear(stop.stop_id)
            out.append(f"Cleared the stop in Agnes on {stop.glob} for {stop.who()}")
        except StopRefused as e:
            if e.code == "share_stop_not_found":
                out.append(f"The stop on {stop.glob} for {stop.who()} was already cleared")
            else:
                out.append(f"{e} ({stop.glob} for {stop.who()})")
        except StopsUnavailable as e:
            out.append(
                f"Couldn't clear the stop on {stop.glob} for {stop.who()}: {e}. It is still in force; run "
                "`scoutctl kb share` again later"
            )
    return out


# ----- kb shares ---------------------------------------------------------------------------------


def when(iso: str) -> str:
    """``2026-11-13T21:24:00.48Z`` → ``2026-11-13 21:24 UTC`` (as given when it isn't that shape)."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", iso)
    return f"{m.group(1)} {m.group(2)} UTC" if m else (iso or "?")


def still_served(view: SharesView, granted: Mapping[str, Sequence[str]]) -> list[tuple[str, list[str]]]:
    """``[(glob, principals)]`` Agnes still serves that the vault no longer grants, and no open stop covers.

    ``granted`` is the vault's ``federation_defaults`` scope per glob (nothing while sharing is off there). This
    is what a failed or never-made stop leaves behind (an unshare on an older Agnes, offline, refused); it lasts
    until the vault is synced or the stop is made. Agnes lists shares from the last pushed commit.
    """
    out: list[tuple[str, list[str]]] = []
    for share in view.shares:
        if share.kind != "folder" or _covered(view, share.glob, None):
            continue
        vault = granted.get(share.glob, ())
        extra = [
            p.principal
            for p in share.principals
            if not any(same_principal(p.principal, g) for g in vault) and not _covered(view, share.glob, p.principal)
        ]
        if extra:
            out.append((share.glob, extra))
    return out


def still_served_lines(served: Sequence[tuple[str, list[str]]], granted: Mapping[str, Sequence[str]]) -> list[str]:
    import shlex

    out: list[str] = []
    for glob, principals in served:
        target = glob[:-3] if glob.endswith("/**") else glob
        who = ", ".join(p[len("person:") :] if p.startswith("person:") else p for p in principals)
        if granted.get(glob):
            args = " ".join(f"--with {shlex.quote(p)}" for p in principals)
            command = f"scoutctl kb unshare {shlex.quote(target)} {args}"
        else:
            command = f"scoutctl kb unshare {shlex.quote(target)}"
        out.append(
            f"Agnes still serves {glob} to {who}, which this vault no longer shares: until you commit and run "
            f"`scoutctl repo sync`, or run `{command}` to retry the stop at once."
        )
    return out


def stop_lines(view: SharesView | None, why: str | None, listed_globs: Sequence[str]) -> list[str]:
    """The Agnes half of ``kb shares``: open stops per share, stops on globs the vault no longer lists, cleared ones."""
    if view is None:
        return [f"Stops in Agnes: not shown. {why}"] if why and why != UNLINKED_NOTE else []
    out: list[str] = []
    listed = set(listed_globs)
    open_ = view.open_stops()
    elsewhere = [s for s in open_ if s.glob not in listed]
    if not open_:
        out.append("Stops in Agnes: none.")
    if elsewhere:
        out.append("Stopped in Agnes, on shares this vault no longer lists:")
        out += [f"  {s.glob}  STOPPED for {s.who()} since {when(s.created_at)}" for s in elsewhere]
    if view.cleared_stops:
        out.append(f"Stops cleared in the last {CLEARED_DAYS} days:")
        out += [
            f"  {s.glob}  for {s.who()}: stopped {when(s.created_at)}, cleared {when(s.cleared_at or '')}"
            for s in view.cleared_stops
        ]
    if view.pending_server_commits:
        out.append(
            f"Agnes has {view.pending_server_commits} commit(s) on top of your last push; `scoutctl repo sync` pulls "
            "them before it pushes."
        )
    return out


def row_stops(view: SharesView | None, glob: str) -> list[Stop]:
    return [s for s in view.open_stops() if s.glob == glob] if view is not None else []
