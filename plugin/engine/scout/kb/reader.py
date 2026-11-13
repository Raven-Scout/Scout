"""Reader resolution for the privacy commands (#453, spec §4.1).

A reader says whose eyes a command's output is for: ``owner`` and
``autonomous`` see every file (labelled); ``agent:<principal>`` sees nothing in
v1. ``<principal>`` is ``person:<email>`` or ``group:<id>`` (spec §12.2).
``delegate:<id>`` is one of the owner's own agents running off the laptop
(#550 §4.1): it sees what ``privacy.delegates.<id>`` in the policy lets it
(:func:`scout.kb.privacy.visible_to`), and nothing when it isn't listed.

Readers are canonicalised (scheme case-insensitive, person email lower-cased).

Resolution: ``SCOUT_READER`` if set; else ``autonomous`` if ``SCOUT_MODE`` is
set (a scheduled run); else ``owner``. ``--reader`` may only *narrow* that
(``owner`` > ``autonomous`` > ``delegate:*`` > ``agent:*``). A reader can't be
swapped for another of its own rank either (an agent principal for another, a
delegate for another): the two may have different rights, so it's refused.

A malformed ``SCOUT_READER`` fails closed: it resolves to
:data:`FAIL_CLOSED_READER`, an ``agent:`` reader with no rights, which nothing
can widen. The ``indexer`` reader isn't a CLI reader in v1.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# No module-level typer import: the privacy hooks (``python -m scout.hooks``) resolve the reader
# on every tool call, and typer alone costs ~0.35 s to import. Only a bad ``--reader`` needs it.

FAIL_CLOSED_READER = "agent:invalid"

_PERSON_RE = re.compile(r"^person:[^\s@,\[\]]+@[^\s@,\[\]]+$", re.IGNORECASE)
_GROUP_RE = re.compile(r"^group:[A-Za-z0-9_-]{1,64}$")
_AGENT_PREFIX = "agent:"
_DELEGATE_PREFIX = "delegate:"
# A delegate id (#550 §4.5): the key under ``privacy.delegates``. Match it with is_delegate_id.
DELEGATE_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def is_delegate_id(value: str) -> bool:
    """Whether ``value`` is a delegate id: ASCII, matched whole (no trailing newline)."""
    return value.isascii() and DELEGATE_ID_RE.fullmatch(value) is not None


def _canonical(reader: str) -> str | None:
    """The canonical spelling of a valid reader, else None.

    The ``agent:`` and ``person:`` schemes are case-insensitive and a person's
    email is lower-cased (spec §12.2, like ``_parse_principal``); a ``group:``
    id is opaque and kept as written. A ``delegate:`` reader is lower-cased
    whole. ``owner``/``autonomous`` are exact.
    """
    if reader in ("owner", "autonomous"):
        return reader
    if reader[: len(_DELEGATE_PREFIX)].lower() == _DELEGATE_PREFIX:
        if not reader.isascii():  # before lower(): a non-ASCII letter may lower to an ASCII one
            return None
        lowered = reader.lower()
        return lowered if is_delegate_id(lowered[len(_DELEGATE_PREFIX) :]) else None
    if reader[: len(_AGENT_PREFIX)].lower() != _AGENT_PREFIX:
        return None
    principal = reader[len(_AGENT_PREFIX) :]
    if _PERSON_RE.match(principal):
        return _AGENT_PREFIX + principal.lower()
    if _GROUP_RE.match(principal):
        return _AGENT_PREFIX + principal[:5].lower() + principal[5:]
    return None


def delegate_id(reader: str) -> str | None:
    """The id of a canonical ``delegate:<id>`` reader, else None."""
    if not reader.startswith(_DELEGATE_PREFIX):
        return None
    rest = reader[len(_DELEGATE_PREFIX) :]
    return rest if is_delegate_id(rest) else None


def _rank(reader: str) -> int:
    if delegate_id(reader) is not None:
        return 1
    return {"owner": 3, "autonomous": 2}.get(reader, 0)


def resolve_reader(flag: str | None, env: Mapping[str, str]) -> str:
    """The reader a command runs as. ``typer.BadParameter`` on a bad or widening ``--reader``."""
    if "SCOUT_READER" in env:
        base = _canonical(env["SCOUT_READER"]) or FAIL_CLOSED_READER
    elif env.get("SCOUT_MODE"):
        base = "autonomous"
    else:
        base = "owner"
    if flag is None:
        return base
    import typer

    canon = _canonical(flag)
    if canon is None:
        raise typer.BadParameter(
            f"unknown reader {flag!r}: use owner, autonomous, delegate:<id>, agent:person:<email> or agent:group:<id>",
            param_hint="--reader",
        )
    if _rank(canon) > _rank(base) or (_rank(canon) == _rank(base) and canon != base):
        raise typer.BadParameter(
            f"--reader {canon} would widen the ambient reader {base}; --reader can only narrow",
            param_hint="--reader",
        )
    return canon


def is_scheduled(env: Mapping[str, str]) -> bool:
    """Whether a session or commit counts as a scheduled run's (ruling R27; #517): fail closed.

    True when ``SCOUT_MODE`` is non-empty, or the resolved reader isn't the
    owner: ``autonomous``, any ``agent:*``, and a set ``SCOUT_READER`` that
    isn't exactly ``owner`` (empty or malformed, which :func:`resolve_reader`
    turns into the no-rights ``agent:invalid``). Nothing unknown is ever taken
    for the owner. The one predicate for the lint's widening guard
    (``scout.kb.lint_privacy``), the refusal when the privacy check can't run,
    and the privacy hooks (``scout.hooks``); the pre-commit template's
    ``scheduled()`` and hooks.json's shell checks mirror it. At commit time,
    where a run can change its environment for one ``git commit``, the hook
    also counts a commit the process table shows is a live run's
    (``scout.kb.live_run``).
    """
    return bool(env.get("SCOUT_MODE")) or resolve_reader(None, env) != "owner"
