"""Layered configuration loader for Scout.

Precedence (low → high, later overrides earlier):
  1. Engine defaults (scout/defaults/scout-config.yaml, shipped with package)
  2. The vault's scout-config.yaml ($SCOUT_DATA_DIR/scout-config.yaml — the
     file /scout-setup and bootstrap write; NO dot, see #207/#202)
  3. SCOUT_* environment variables (whitelisted keys)

The vault file doubles as bootstrap state (version stamps, connectors,
schedule, plan) and predates the canonical schema, so layer 2 is read
tolerantly: legacy key shapes are normalized on read (never rewritten on
disk), unreadable YAML degrades to defaults with a stderr warning, and a
scalar where the defaults define a mapping is ignored with a warning instead
of clobbering the subtree. This layer was silently dead for every existing
vault until #207 — tolerance keeps switching it on from turning a stale or
hand-mangled file into a crash.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from scout import paths
from scout.errors import ConfigError
from scout.yaml_safe import load_yaml, load_yaml_key, yaml_problem

# The terminal fallback, used only when neither a configured override nor the
# host's own zone resolves (see resolve_timezone). The packaged defaults leave
# user.timezone empty: by default Scout follows the computer's zone.
DEFAULT_TIMEZONE = "America/New_York"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = load_yaml(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {yaml_problem(e, text)}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. `override` wins on conflicts."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_overrides() -> dict[str, Any]:
    """Whitelisted SCOUT_* env vars → config overrides."""
    out: dict[str, Any] = {}
    if v := os.environ.get("SCOUT_USER_EMAIL"):
        out.setdefault("user", {})["email"] = v
    if v := os.environ.get("SCOUT_USER_TIMEZONE"):
        out.setdefault("user", {})["timezone"] = v
    return out


def _read_packaged_defaults() -> dict[str, Any]:
    """Read the shipped scout-config.yaml via importlib.resources.

    Resolving through importlib.resources keeps load_config() working
    when the package is installed from a wheel — Path(__file__).parent
    navigation breaks because the defaults sit under scout/defaults/
    in the installed tree, not relative to a sibling 'engine/' dir.
    """
    resource = files("scout") / "defaults" / "scout-config.yaml"
    with as_file(resource) as path:
        return _read_yaml(path)


def _warn(msg: str) -> None:
    """One-line stderr warning. The silent-defaults fallback is what kept
    #202 invisible for so long — degradation must be loud enough to spot in
    --verbose output and run logs, but must never raise."""
    print(f"scout-config: {msg}", file=sys.stderr)


def _normalize_legacy_keys(overrides: dict[str, Any]) -> dict[str, Any]:
    """Map the key shapes bootstrap actually writes onto the canonical schema
    (#207 §2). Read-side only — the vault file is never rewritten, so
    existing vaults need no migration. Explicit canonical ``user.*`` keys
    always win; legacy keys only fill gaps.

      timezone (top level)               → user.timezone
      connectors.inputs.github_username  → user.github_username
      connectors.inputs.user_slack_id    → user.slack_user_id
    """
    out = dict(overrides)
    raw_user = out.get("user")
    user: dict[str, Any] = dict(raw_user) if isinstance(raw_user, dict) else {}

    def fill(canonical: str, value: object) -> None:
        if isinstance(value, str) and value and not user.get(canonical):
            user[canonical] = value

    fill("timezone", out.get("timezone"))
    connectors = out.get("connectors")
    inputs = connectors.get("inputs") if isinstance(connectors, dict) else None
    if isinstance(inputs, dict):
        fill("github_username", inputs.get("github_username"))
        fill("slack_user_id", inputs.get("user_slack_id"))

    if user:
        out["user"] = user
    return out


def _merge_user_layer(defaults: dict[str, Any], overrides: dict[str, Any], _path: str = "") -> dict[str, Any]:
    """Deep merge with a guard: where the DEFAULTS define a mapping, a
    non-mapping override is ignored with a warning instead of replacing the
    subtree (a stale ``user: oops`` must not take user.timezone down with
    it). Keys unknown to the defaults — bootstrap state like ``plugin`` or
    ``schedule`` — merge through silently."""
    result = dict(defaults)
    for key, value in overrides.items():
        where = f"{_path}{key}"
        if key in result and isinstance(result[key], dict):
            if isinstance(value, dict):
                result[key] = _merge_user_layer(result[key], value, f"{where}.")
            else:
                _warn(f"ignoring '{where}': expected a mapping, got {type(value).__name__} — using defaults")
        else:
            result[key] = value
    return result


def load_config(data_dir: Path | None = None) -> dict[str, Any]:
    """Load the three-layer merged config.

    Never raises on a bad VAULT file — the packaged defaults must scream
    (a broken wheel is a bug), but the user layer warns and degrades so a
    stale or hand-mangled scout-config.yaml cannot block a run.
    """
    defaults = _read_packaged_defaults()
    user_path = paths.config_path(data_dir)
    try:
        user_overrides = _read_yaml(user_path)
    except (ConfigError, OSError, UnicodeDecodeError) as e:
        # OSError: permissions/races; UnicodeDecodeError: binary corruption.
        _warn(f"ignoring unreadable {user_path.name}: {e} — running on packaged defaults")
        user_overrides = {}
    env_overrides = _env_overrides()

    merged = _merge_user_layer(defaults, _normalize_legacy_keys(user_overrides))
    merged = _deep_merge(merged, env_overrides)
    return merged


def config_load_problem(data_dir: Path | None = None) -> str | None:
    """Why the vault's scout-config.yaml doesn't load, or None when it does (or isn't there).

    The cause and line only, never the file's text: this reaches the doctor and
    run-health pushes, and YAML errors can quote what they choked on.
    """
    try:
        text = paths.config_path(data_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        return type(e).__name__
    try:
        data = load_yaml(text)
    except yaml.YAMLError as e:
        return yaml_problem(e, text)
    return None if data is None or isinstance(data, dict) else "not a mapping"


def user_value(key: str, data_dir: Path | None = None) -> Any:
    """``key`` as the vault's own file writes it, before any merging; None when
    absent or unreadable (``load_config`` already warns about an unreadable file).
    For telling a person what they wrote, which the merge can hide."""
    try:
        return _read_yaml(paths.config_path(data_dir)).get(key)
    except (ConfigError, OSError, UnicodeDecodeError):
        return None


# ----- day boundary ---------------------------------------------------------
#
# Scout's "today" (daily action-items filename, trigger daily caps, freshness
# math, rendered timestamps) is a civil date in ONE zone. Before #207 the
# codebase had multiple authorities — the configured zone, bare host-clock
# date.today(), and hardcoded America/New_York — which agreed only while the
# config read was broken. Everything below is the single Python-side
# authority; the shell-side twin is templates/scripts/scout-tz.sh, and the two
# must resolve the same zone for every input (tests/unit/test_host_timezone.py):
#
#   1. SCOUT_USER_TIMEZONE (env) — the runners pin each run's zone here
#   2. scout-config.yaml user.timezone, then the top-level timezone: bootstrap
#      writes — an optional override, never required
#   3. the host's zone (host_timezone_name) — the scheduler fires slots at the
#      host's wall-clock times, so by default the dates follow the same clock
#   4. DEFAULT_TIMEZONE, only when nothing above resolves
#
# A configured name that is not a real zone falls through to the host.

# Where the operating system records its zone: a symlink into the tz database.
HOST_LOCALTIME = Path("/etc/localtime")
_ZONEINFO_DIR_RE = re.compile(r"/zoneinfo[^/]*/")


def zone_from_tzdb_path(path: str) -> str | None:
    """The IANA name in a tz-database file path, or None.

    The name is whatever follows the deepest ``zoneinfo*`` directory: macOS
    resolves /etc/localtime through layouts like /var/db/timezone/tz/<ver>/
    zoneinfo/ or /usr/share/zoneinfo.default/ depending on whether tzd has
    run, so a literal ``zoneinfo/`` match is not enough.
    """
    matches = list(_ZONEINFO_DIR_RE.finditer(path))
    if not matches:
        return None
    return path[matches[-1].end() :] or None


def host_timezone_name(localtime: Path | None = None) -> str | None:
    """The computer's IANA zone, read off the /etc/localtime symlink, or None.

    Deliberately not ``$TZ``: that is a per-process override (the trading
    runner pins America/New_York for US market hours), and honouring it would
    give that one process a different "today" from the rest of the vault.
    ``SCOUT_LOCALTIME`` names another link to read instead (tests, odd hosts).
    """
    link = localtime or Path(os.environ.get("SCOUT_LOCALTIME") or HOST_LOCALTIME)
    try:
        if not link.is_symlink():
            return None
        name = zone_from_tzdb_path(str(link.resolve()))
    except OSError:
        return None
    if not name:
        return None
    try:
        ZoneInfo(name)
    except Exception:
        return None
    return name


def timezone_or_default(tz_name: object) -> ZoneInfo:
    """ZoneInfo for ``tz_name``; else the host's zone; else :data:`DEFAULT_TIMEZONE`.

    The fallback lives INSIDE the resolver on purpose (#207): a missing,
    malformed, or unknown zone shifts every consumer to the same zone
    together, instead of each call site inventing its own fallback and
    splitting the day boundary between surfaces.
    """
    if isinstance(tz_name, str) and tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    if host := host_timezone_name():
        return ZoneInfo(host)
    return ZoneInfo(DEFAULT_TIMEZONE)


def resolve_timezone(data_dir: Path | None = None) -> ZoneInfo:
    """The day-boundary zone for ``data_dir``'s vault: the configured
    override if any, else the host's zone.

    Never raises — config problems degrade to the host's zone, then to
    :data:`DEFAULT_TIMEZONE`, so a bad edit can never make a run
    timezone-blind (mirrors scout-tz.sh).
    """
    try:
        user = load_config(data_dir).get("user")
        tz_name = user.get("timezone") if isinstance(user, dict) else None
    except Exception:
        tz_name = None
    return timezone_or_default(tz_name)


def now(data_dir: Path | None = None) -> _dt.datetime:
    """Wall-clock now in the resolved zone (tz-aware)."""
    return _dt.datetime.now(resolve_timezone(data_dir))


# EVAL-ONLY: never set this on a real vault — every daily file would carry that day's date.
# Set only by the eval harness (tests/eval/harness/runner_env.py): the day a scenario
# was rendered for. Unset in every real run, where the clock decides.
EVAL_TODAY_ENV = "SCOUT_EVAL_TODAY"


def today(data_dir: Path | None = None) -> _dt.date:
    """THE day boundary: today's civil date in the resolved zone.

    Every writer or reader that derives a daily filename, a daily cap, or a
    "today" label must come through here (or :func:`resolve_timezone`) so the
    whole system flips dates at the same instant (#207).

    An eval run pins the day in ``SCOUT_EVAL_TODAY`` (``YYYY-MM-DD``), so the
    runner's steps and the session's own ``scoutctl`` calls name the same daily
    file as the scenario's fixtures. Unset or empty, the clock decides.
    """
    return pinned_eval_day() or now(data_dir).date()


def pinned_eval_day() -> _dt.date | None:
    """The day an eval run pinned in ``SCOUT_EVAL_TODAY``; None when unset or empty.

    Raises ConfigError for a value that isn't ``YYYY-MM-DD``: an eval that fell
    back to the clock would score the wrong day's file.
    """
    pinned = os.environ.get(EVAL_TODAY_ENV)  # EVAL-ONLY (see EVAL_TODAY_ENV); unset on real vaults
    if not pinned:
        return None
    try:
        return _dt.date.fromisoformat(pinned)
    except ValueError as e:
        raise ConfigError(f"{EVAL_TODAY_ENV}: not a YYYY-MM-DD date: {pinned!r}") from e


# ----- space registry (vault layout v2, spec 2026-11-26 §2 Q11, R1) ----------
#
# ``spaces:`` in scout-config.yaml is a list of ``{key, default, visibility}``.
# It names the knowledge-base/<key>/ folders and each one's default note
# visibility. Routing (delivery channels, connector maps) is Feature 1's.

SPACE_KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Scout-wide KB folders and v1 folder names: a space key may equal none of them (spec §3.1).
RESERVED_SPACE_KEYS = frozenset(
    {
        "people",
        "organizations",
        "ontology",
        "decisions-log",
        "review-queue",
        "research-queue",
        "recurring-tasks",
        "from-agents",
        "lab",
        "projects",
        "topics",
        "sources",
        "meetings",
        "inbox",
    }
)
SPACE_VISIBILITIES = ("normal", "private")


@dataclass(frozen=True)
class Space:
    """One registered space."""

    key: str
    default: bool = False
    visibility: str = "normal"

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "default": self.default, "visibility": self.visibility}


# What a vault without a ``spaces:`` key has, and what the layout move writes there (spec §4.4).
DEFAULT_SPACES: tuple[Space, ...] = (Space("work", True, "normal"), Space("personal", False, "private"))


def parse_spaces(raw: Any) -> tuple[Space, ...]:
    """The registry from a ``spaces:`` value. ``ConfigError`` naming the first problem.

    ``None`` (no key) gives :data:`DEFAULT_SPACES`. Each entry is a mapping with
    ``key``; ``default`` (bool, exactly one true) and ``visibility``
    (``normal``/``private``, default ``normal``) are optional.
    """
    if raw is None:
        return DEFAULT_SPACES
    if not isinstance(raw, list) or not raw:
        raise ConfigError("spaces: must be a non-empty list of {key, default, visibility}")
    out: list[Space] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"spaces[{i}]: must be a mapping with a key")
        key = entry.get("key")
        if not isinstance(key, str) or not SPACE_KEY_RE.match(key):
            raise ConfigError(f"spaces[{i}].key: must match [a-z][a-z0-9-]*")
        if key in RESERVED_SPACE_KEYS:
            raise ConfigError(f"spaces[{i}].key: {key!r} is a Scout-wide knowledge-base folder")
        if key in seen:
            raise ConfigError(f"spaces[{i}].key: {key!r} is listed twice")
        seen.add(key)
        default = entry.get("default", False)
        if not isinstance(default, bool):
            raise ConfigError(f"spaces[{i}].default: must be true or false")
        visibility = entry.get("visibility", "normal")
        if visibility not in SPACE_VISIBILITIES:
            raise ConfigError(f"spaces[{i}].visibility: must be normal or private")
        out.append(Space(key, default, visibility))
    defaults = [s.key for s in out if s.default]
    if len(defaults) != 1:
        raise ConfigError(f"spaces: exactly one space must be the default (found {len(defaults)})")
    return tuple(out)


def spaces_problem(data_dir: Path | None = None) -> str | None:
    """Why the vault's ``spaces:`` is invalid, or None when it is valid or absent."""
    try:
        parse_spaces(user_value("spaces", data_dir))
    except ConfigError as e:
        return str(e)
    return None


def load_spaces(data_dir: Path | None = None) -> tuple[Space, ...]:
    """The vault's space registry; :data:`DEFAULT_SPACES` when absent.

    An invalid registry warns and falls back to the defaults, like the rest of
    the user layer: a hand-mangled file must not stop a run. The layout move
    and ``vault move`` check :func:`spaces_problem` and refuse instead.
    """
    try:
        return parse_spaces(user_value("spaces", data_dir))
    except ConfigError as e:
        _warn(f"ignoring scout-config.yaml spaces: {e}; using work (default) and personal")
        return DEFAULT_SPACES


def spaces_or_none(data_dir: Path | None = None) -> tuple[Space, ...] | None:
    """The space registry, or None when the config file is there but its ``spaces:`` can't be read.

    For the readers that must fail closed (privacy, the pre-filter's private-space skip): an unreadable file
    or registry must not turn a custom private space into a normal folder, as :func:`load_spaces` would.
    """
    config = paths.config_path(data_dir)
    if not config.exists():
        return DEFAULT_SPACES
    try:
        raw = load_yaml_key(config.read_text(encoding="utf-8"), "spaces")
        return parse_spaces(raw)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ConfigError):
        return None


def default_space(spaces: tuple[Space, ...]) -> Space:
    return next(s for s in spaces if s.default)


def render_spaces_yaml(spaces: tuple[Space, ...]) -> str:
    """The ``spaces:`` block as the layout move and setup write it."""
    lines = [
        "# Spaces: one knowledge-base/<key>/ folder each. Exactly one is the default, where a note",
        "# goes when nothing says otherwise. visibility is each space's default note visibility.",
        "spaces:",
    ]
    for s in spaces:
        lines.append(f"  - key: {s.key}")
        if s.default:
            lines.append("    default: true")
        lines.append(f"    visibility: {s.visibility}")
    return "\n".join(lines) + "\n"
