"""Read-only 'is a newer plugin version available?' check.

``check`` reports installed-vs-available from the ``stable`` branch's
marketplace manifest. ``release_status`` adds the published-release gate that
``scoutctl self-update run`` (``self_update_run``) requires before it applies
anything. Nothing in this module changes the install.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

RAW_MARKETPLACE_URL = "https://raw.githubusercontent.com/Raven-Scout/Scout/stable/.claude-plugin/marketplace.json"
RELEASE_API = "https://api.github.com/repos/Raven-Scout/Scout/releases/tags/v{version}"


@dataclass(frozen=True)
class UpdateStatus:
    installed: str
    available: str
    update_available: bool


def _semver_tuple(v: str) -> tuple[int, int, int]:
    # strip pre-release (-...) and build metadata (+...) before parsing
    core = v.split("+")[0].split("-")[0]
    parts = core.split(".")[:3]
    nums = [int(p) for p in parts] + [0, 0, 0]
    return (nums[0], nums[1], nums[2])


def compare(*, installed: str, available: str) -> UpdateStatus:
    return UpdateStatus(
        installed=installed,
        available=available,
        update_available=_semver_tuple(available) > _semver_tuple(installed),
    )


def _installed_version() -> str:
    from scout import __version__

    return __version__


def _available_version() -> str:
    try:
        with urllib.request.urlopen(RAW_MARKETPLACE_URL, timeout=10) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"could not reach marketplace at {RAW_MARKETPLACE_URL}: {e}") from e
    try:
        return json.loads(raw)["plugins"][0]["version"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"could not parse marketplace.json from {RAW_MARKETPLACE_URL}: {e}") from e


def check(
    *,
    installed_fetcher: Callable[[], str] = _installed_version,
    available_fetcher: Callable[[], str] = _available_version,
) -> UpdateStatus:
    return compare(installed=installed_fetcher(), available=available_fetcher())


@dataclass(frozen=True)
class ReleaseStatus:
    """Whether release ``v<version>`` may be applied unattended (spec D8, §7.4)."""

    published: bool
    published_at: datetime | None
    reason: str  # "" when published; else "missing" | "draft" | "prerelease" | "soaking …"


def fetch_release(version: str) -> dict | None:
    """The GitHub release for tag ``v<version>``, unauthenticated; None on 404.

    Raises RuntimeError when GitHub can't be reached or answers with anything
    else, so the caller can tell "not published" from "couldn't ask".
    """
    url = RELEASE_API.format(version=version)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "scoutctl"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https URL
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub answered {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"could not reach {url}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse the release from {url}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected release payload from {url}")
    return data


def _parse_published_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def release_status(
    version: str,
    *,
    now: datetime,
    soak_hours: float,
    fetch: Callable[[str], dict | None] = fetch_release,
) -> ReleaseStatus:
    """Apply ``version`` only when its release is public and has soaked (D8).

    ``release.sh prepare`` bumps the manifest before ``finalize`` publishes, so
    a manifest that names V is not enough: the GitHub release ``vV`` must exist,
    be neither a draft nor a prerelease, and be ``soak_hours`` old.
    """
    release = fetch(version)
    if release is None:
        return ReleaseStatus(False, None, "missing")
    published_at = _parse_published_at(release.get("published_at"))
    if release.get("draft") is True or published_at is None:
        return ReleaseStatus(False, None, "draft")
    if release.get("prerelease") is True:
        return ReleaseStatus(False, published_at, "prerelease")
    if now - published_at < timedelta(hours=soak_hours):
        return ReleaseStatus(False, published_at, f"soaking until {(published_at + timedelta(hours=soak_hours)).isoformat()}")
    return ReleaseStatus(True, published_at, "")
