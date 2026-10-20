"""Reader resolution for the privacy commands (#453, spec §4.1).

A reader says whose eyes a command's output is for: ``owner`` and
``autonomous`` see every file (labelled); ``agent:<principal>`` sees nothing in
v1. ``<principal>`` is ``person:<email>`` or ``group:<id>`` (spec §12.2).

Readers are canonicalised (scheme case-insensitive, person email lower-cased).

Resolution: ``SCOUT_READER`` if set; else ``autonomous`` if ``SCOUT_MODE`` is
set (a scheduled run); else ``owner``. ``--reader`` may only *narrow* that
(``owner`` > ``autonomous`` > ``agent:*``). An agent reader can't be swapped
for another agent principal either: that is lateral today, but the principals
get different rights in Stage 2, so it's refused now.

A malformed ``SCOUT_READER`` fails closed: it resolves to
:data:`FAIL_CLOSED_READER`, an ``agent:`` reader with no rights, which nothing
can widen. The ``indexer`` reader isn't a CLI reader in v1.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import typer

FAIL_CLOSED_READER = "agent:invalid"

_PERSON_RE = re.compile(r"^person:[^\s@,\[\]]+@[^\s@,\[\]]+$", re.IGNORECASE)
_GROUP_RE = re.compile(r"^group:[A-Za-z0-9_-]{1,64}$")
_AGENT_PREFIX = "agent:"


def _canonical(reader: str) -> str | None:
    """The canonical spelling of a valid reader, else None.

    The ``agent:`` and ``person:`` schemes are case-insensitive and a person's
    email is lower-cased (spec §12.2, like ``_parse_principal``); a ``group:``
    id is opaque and kept as written. ``owner``/``autonomous`` are exact.
    """
    if reader in ("owner", "autonomous"):
        return reader
    if reader[: len(_AGENT_PREFIX)].lower() != _AGENT_PREFIX:
        return None
    principal = reader[len(_AGENT_PREFIX) :]
    if _PERSON_RE.match(principal):
        return _AGENT_PREFIX + principal.lower()
    if _GROUP_RE.match(principal):
        return _AGENT_PREFIX + principal[:5].lower() + principal[5:]
    return None


def _rank(reader: str) -> int:
    return {"owner": 2, "autonomous": 1}.get(reader, 0)


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
    canon = _canonical(flag)
    if canon is None:
        raise typer.BadParameter(
            f"unknown reader {flag!r}: use owner, autonomous, agent:person:<email> or agent:group:<id>",
            param_hint="--reader",
        )
    if _rank(canon) > _rank(base) or (_rank(base) == 0 and canon != base):
        raise typer.BadParameter(
            f"--reader {canon} would widen the ambient reader {base}; --reader can only narrow",
            param_hint="--reader",
        )
    return canon
