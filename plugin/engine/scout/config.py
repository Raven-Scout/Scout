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
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from scout import paths
from scout.errors import ConfigError

# The terminal fallback, used only when neither a configured override nor the
# host's own zone resolves (see resolve_timezone). The packaged defaults leave
# user.timezone empty: by default Scout follows the computer's zone.
DEFAULT_TIMEZONE = "America/New_York"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e
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


def today(data_dir: Path | None = None) -> _dt.date:
    """THE day boundary: today's civil date in the resolved zone.

    Every writer or reader that derives a daily filename, a daily cap, or a
    "today" label must come through here (or :func:`resolve_timezone`) so the
    whole system flips dates at the same instant (#207).
    """
    return now(data_dir).date()
