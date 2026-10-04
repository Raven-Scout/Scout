"""Scout's day boundary follows the computer's timezone.

The scheduler fires slots at the host's local wall-clock times, so every date
and timestamp Scout renders must come from that same zone. A zone kept by
hand in scout-config.yaml drifts the moment the user travels.

Resolution order — scripts/scout-tz.sh and scout.config.resolve_timezone
must give the same answer for every input:

  1. SCOUT_USER_TIMEZONE (env). The runners pin each run's zone here.
  2. scout-config.yaml ``user.timezone``, then the top-level ``timezone:``
     that bootstrap writes. An optional override, never required.
  3. The host's zone, read off the /etc/localtime symlink.
  4. America/New_York, only when nothing above resolves.

An override that does not name a real zone falls through to the host, not to
the default.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout import config

_PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent  # repo root
_TEMPLATE = _PLUGIN_ROOT / "templates" / "scripts" / "scout-tz.sh.tmpl"

ZONE_EAST = "Pacific/Kiritimati"  # UTC+14
ZONE_WEST = "Etc/GMT+12"  # UTC-12: never shares a civil date with ZONE_EAST


def _fake_localtime(tmp_path: Path, zone: str, *, layout: str = "var/db/timezone/zoneinfo") -> Path:
    """A /etc/localtime-style symlink into a tz-database layout naming ``zone``."""
    target = tmp_path / "tzdb" / layout / zone
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    link = tmp_path / f"localtime-{zone.replace('/', '-')}"
    link.symlink_to(target)
    return link


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set the fake host's zone: ``host("Europe/Prague")``."""

    def set_host(zone: str) -> Path:
        link = _fake_localtime(tmp_path, zone)
        monkeypatch.setenv("SCOUT_LOCALTIME", str(link))
        return link

    return set_host


# ----- reading the host's zone ----------------------------------------------


@pytest.mark.parametrize(
    "layout",
    [
        "var/db/timezone/zoneinfo",  # macOS, the link /etc/localtime points at
        "var/db/timezone/tz/2026a.1.0/zoneinfo",  # macOS, once that link resolves
        "usr/share/zoneinfo.default",  # macOS before tzd has run
        "usr/share/zoneinfo",  # Linux
    ],
)
def test_host_zone_is_read_off_the_localtime_symlink(tmp_path: Path, layout: str) -> None:
    link = _fake_localtime(tmp_path, "Europe/Prague", layout=layout)
    assert config.host_timezone_name(link) == "Europe/Prague"


def test_host_zone_honours_scout_localtime(host) -> None:
    host("Asia/Tokyo")
    assert config.host_timezone_name() == "Asia/Tokyo"


def test_host_zone_is_none_when_localtime_is_missing(tmp_path: Path) -> None:
    assert config.host_timezone_name(tmp_path / "absent") is None


def test_host_zone_is_none_when_localtime_is_a_plain_file(tmp_path: Path) -> None:
    plain = tmp_path / "localtime"
    plain.write_bytes(b"")
    assert config.host_timezone_name(plain) is None


def test_host_zone_is_none_without_a_zoneinfo_component(tmp_path: Path) -> None:
    target = tmp_path / "somewhere" / "else"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    link = tmp_path / "localtime"
    link.symlink_to(target)
    assert config.host_timezone_name(link) is None


def test_host_zone_is_none_for_an_unloadable_zone(tmp_path: Path) -> None:
    assert config.host_timezone_name(_fake_localtime(tmp_path, "Mars/Olympus_Mons")) is None


def test_host_zone_ignores_tz(monkeypatch: pytest.MonkeyPatch, host) -> None:
    """$TZ is a per-process override: the trading runner pins
    America/New_York for US market hours. Honouring it would give that one
    process a different "today" from the rest of the vault."""
    host("Europe/Prague")
    monkeypatch.setenv("TZ", "America/New_York")
    assert config.host_timezone_name() == "Europe/Prague"


# ----- resolution order ------------------------------------------------------


def _write_config(vault: Path, text: str) -> None:
    (vault / "scout-config.yaml").write_text(text, encoding="utf-8")


def test_packaged_defaults_do_not_pin_a_zone() -> None:
    assert not config._read_packaged_defaults()["user"]["timezone"]


@pytest.mark.parametrize("zone", [ZONE_EAST, ZONE_WEST])
def test_an_unconfigured_vault_follows_the_host(zone: str, fake_data_dir: Path, host) -> None:
    host(zone)
    assert config.resolve_timezone(fake_data_dir).key == zone
    before = dt.datetime.now(ZoneInfo(zone)).date()
    today = config.today(fake_data_dir)
    after = dt.datetime.now(ZoneInfo(zone)).date()
    assert today in {before, after}


def test_a_configured_zone_overrides_the_host(fake_data_dir: Path, host) -> None:
    host(ZONE_EAST)
    _write_config(fake_data_dir, "timezone: Europe/Prague\n")
    assert config.resolve_timezone(fake_data_dir).key == "Europe/Prague"


def test_an_invalid_configured_zone_falls_through_to_the_host(fake_data_dir: Path, host) -> None:
    host(ZONE_EAST)
    _write_config(fake_data_dir, "timezone: Mars/Olympus_Mons\n")
    assert config.resolve_timezone(fake_data_dir).key == ZONE_EAST


def test_the_env_override_beats_config_and_host(fake_data_dir: Path, host, monkeypatch: pytest.MonkeyPatch) -> None:
    host(ZONE_EAST)
    _write_config(fake_data_dir, "timezone: Europe/Prague\n")
    monkeypatch.setenv("SCOUT_USER_TIMEZONE", ZONE_WEST)
    assert config.resolve_timezone(fake_data_dir).key == ZONE_WEST


def test_the_default_applies_only_when_nothing_resolves(fake_data_dir: Path) -> None:
    # conftest hides the host's /etc/localtime; nothing is configured.
    assert config.resolve_timezone(fake_data_dir).key == config.DEFAULT_TIMEZONE


def test_timezone_or_default_falls_back_to_the_host(host) -> None:
    """An explicit name that is empty or bogus (bootstrap's TODAY_DATE, a
    --timezone flag) lands on the host's zone before the packaged default."""
    host(ZONE_WEST)
    assert config.timezone_or_default("").key == ZONE_WEST
    assert config.timezone_or_default("Not/AZone").key == ZONE_WEST
    assert config.timezone_or_default("Asia/Tokyo").key == "Asia/Tokyo"


# ----- shell / Python parity -------------------------------------------------


def _install_resolver(vault: Path) -> Path:
    script = vault / "scripts" / "scout-tz.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_TEMPLATE.read_text(encoding="utf-8").replace("{{INSTANCE_NAME}}", "TestScout"), encoding="utf-8")
    script.chmod(0o755)
    return script


def _shell_zone(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=dict(os.environ), timeout=30)


PARITY_CASES = {
    # name: (scout-config.yaml text, SCOUT_USER_TIMEZONE, host zone, expected)
    "no config: the host": ("", None, ZONE_EAST, ZONE_EAST),
    "no key: the host": ("platform: macos\n", None, ZONE_EAST, ZONE_EAST),
    "empty key: the host": ("timezone:\n", None, ZONE_EAST, ZONE_EAST),
    "null key: the host": ("timezone: null\n", None, ZONE_EAST, ZONE_EAST),
    "top-level override": ("timezone: Europe/Prague\n", None, ZONE_EAST, "Europe/Prague"),
    "quoted, commented override": ('timezone: "Europe/London"  # traveling\n', None, ZONE_EAST, "Europe/London"),
    "user.timezone beats top-level": (
        "user:\n  name: Alex\n  timezone: Asia/Tokyo\ntimezone: Europe/Prague\n",
        None,
        ZONE_EAST,
        "Asia/Tokyo",
    ),
    "nested key of another block is not user.timezone": (
        "user:\n  name: Alex\nschedule:\n  timezone: Asia/Tokyo\n",
        None,
        ZONE_EAST,
        ZONE_EAST,
    ),
    "invalid override: the host": ("timezone: Mars/Olympus_Mons\n", None, ZONE_EAST, ZONE_EAST),
    "invalid override, no host: the default": (
        "timezone: Mars/Olympus_Mons\n",
        None,
        None,
        config.DEFAULT_TIMEZONE,
    ),
    "nothing at all: the default": ("", None, None, config.DEFAULT_TIMEZONE),
    "env beats config and host": ("timezone: Europe/Prague\n", ZONE_WEST, ZONE_EAST, ZONE_WEST),
    "invalid env: the host": ("timezone: Europe/Prague\n", "Not/AZone", ZONE_EAST, ZONE_EAST),
}


@pytest.mark.parametrize("case", PARITY_CASES, ids=list(PARITY_CASES))
def test_shell_and_python_resolve_the_same_zone(
    case: str, fake_data_dir: Path, host, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_text, env_zone, host_zone, expected = PARITY_CASES[case]
    if config_text:
        _write_config(fake_data_dir, config_text)
    if env_zone:
        monkeypatch.setenv("SCOUT_USER_TIMEZONE", env_zone)
    if host_zone:
        host(host_zone)
    script = _install_resolver(fake_data_dir)

    shell = _shell_zone(script)
    assert shell.returncode == 0, shell.stderr
    assert shell.stdout.strip() == expected, f"scout-tz.sh: {shell.stderr}"
    assert config.resolve_timezone(fake_data_dir).key == expected
