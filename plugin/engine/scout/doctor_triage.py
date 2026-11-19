"""Doctor warnings ranked by risk, and the owner's record of the ones they reviewed (UI audit R13, #505).

**Ranking.** Every doctor warning gets a risk, ``high``, ``medium`` or ``low``:

* A vault script that reads the knowledge base around ``scoutctl kb`` (the privacy drift rule
  ``script-raw-kb-read``) is ``high`` when its text mentions a way to send what it reads anywhere
  (chat, mail, a web request, a notifier, ``gh``, a ``claude -p`` session, ``drafts/``), or can't be
  read; otherwise ``low``, a checker whose output stays in the vault. A false ``high`` costs a look; a
  false ``low`` would bury a leak, so the markers are deliberately broad. The doctor reports the two
  kinds as separate warnings (``privacy.script-raw-kb-read`` and ``privacy.script-raw-kb-read.internal``).
* A vault runner that doesn't mark its reads as unattended, any other ``privacy:`` warning, a failed
  gate probe and a run-sandbox warning are ``high``.
* Everything else is ``medium``.

**Reviewed.** The owner may mark a warning reviewed (``scoutctl bootstrap review-warning``, or the
app). The doctor then leaves it out of ``warnings``, ``warning_groups`` and the severity, and lists it
in ``warning_triage`` with ``reviewed_at``. A review holds the warning's *fingerprint*: its files, and
for a high-risk privacy file that file's content; for any other warning, its text. When that changes,
the review no longer matches and the warning is back. Errors can't be reviewed.

Reviews live in ``~/.local/state/scout/reviewed-warnings.json`` (``REVIEWED_WARNINGS_NAME``), one block
per vault, outside the vault: the write gate and the OS write sandbox protect it as an engine root, and
the CLI refuses a scheduled run (``headless_reason``), so a run can't mark its own warnings reviewed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REVIEWS_SCHEMA = 1
RISKS = ("high", "medium", "low")
_RANK = {r: i for i, r in enumerate(RISKS)}

INTERNAL_SUFFIX = ".internal"
FINGERPRINT_RE = re.compile(r"[0-9a-f]{16}")
# A warning id: a kind, a kind and a vault-relative path, or ``warning.<hash>``. No control characters.
_ID_RE = re.compile(r"[A-Za-z0-9][^\x00-\x1f\x7f]{0,399}")

# What a script must mention for its output to be able to leave the vault, by the reason the doctor gives.
_OUTBOUND = (
    ("a chat or notification channel", r"\b(?:slack|telegram|discord|webhook|notify|notification|outbox)\b"),
    ("mail", r"\b(?:smtp|sendmail|e-?mail|mailx?)\b"),
    ("a web request", r"\b(?:requests|urllib\d?|httpx|aiohttp|http\.client|curl|wget)\b|https?://"),
    ("GitHub or Linear", r"\bgh\s+(?:issue|pr|api|release|gist)\b|\blinear\b"),
    ("a Claude session", r"\bclaude\b[^\n]{0,80}(?:\s-p\b|--print\b)"),
    ("a shared surface", r"\b(?:enrichment|digest|publish|upload)\b|\bdrafts/"),
    ("a Scout sender", r"\bscoutctl\s+(?:notify|broker|send)\b"),
)
_OUTBOUND_RES = tuple((why, re.compile(pattern, re.IGNORECASE)) for why, pattern in _OUTBOUND)


@dataclass(frozen=True)
class WarningMeta:
    """What the doctor knows about one warning beyond its text (set where the warning is made)."""

    kind: str
    risk: str
    reason: str | None = None
    # A grouped warning's id is its kind; a one-file privacy warning's is ``<kind>:<path>``.
    id: str | None = None
    items: tuple[str, ...] = ()
    # Vault-relative path -> sha256 of its content, for files whose edits must bring a review back.
    digests: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TriageEntry:
    id: str
    kind: str
    risk: str
    reason: str | None
    summary: str
    items: tuple[str, ...]
    fingerprint: str
    reviewed_at: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "risk": self.risk,
            "reason": self.reason,
            "summary": self.summary,
            "items": list(self.items),
            "fingerprint": self.fingerprint,
            "reviewed_at": self.reviewed_at,
        }


# -- ranking -----------------------------------------------------------------------------------------------------


def script_risk(text: str | None) -> tuple[str, str]:
    """``("high", why)`` for a script that could send what it reads somewhere, else ``("low", why)``."""
    if text is None:
        return "high", "Scout couldn't read it to see where its output goes"
    for why, pattern in _OUTBOUND_RES:
        if pattern.search(text):
            return "high", f"it mentions {why}, so what it reads may leave the vault"
    return "low", "it mentions no way to send anything, so its output likely stays in the vault"


_READ_CAP = 1_000_000


def read_script(path: Path) -> str | None:
    """A script's text to rank (its first MB), or None when it can't be read or is binary."""
    try:
        with path.open("rb") as fh:
            data = fh.read(_READ_CAP)
    except OSError:
        return None
    return None if b"\0" in data else data.decode("utf-8", "replace")


def default_meta(text: str) -> WarningMeta:
    """A warning made without metadata: ranked by what it is about, keyed by its text."""
    lowered = text.lower()
    if lowered.startswith(("privacy:", "gate probe", "run sandbox")):
        return WarningMeta(kind="protection", risk="high", reason="about privacy or run protection")
    return WarningMeta(kind="install", risk="medium")


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def fingerprint(text: str, meta: WarningMeta) -> str:
    """What a review holds: a file-backed warning's files (and digests); any other warning's text."""
    if meta.items:
        return _hash({"kind": meta.kind, "items": sorted(meta.items), "digests": dict(sorted(meta.digests.items()))})
    return _hash({"text": text})


def warning_id(text: str, meta: WarningMeta) -> str:
    return meta.id or f"warning.{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


# -- the owner's reviews (protected) ------------------------------------------------------------------------------


def reviews_path(home: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    from scout.kb.hook_paths import REVIEWED_WARNINGS_NAME

    if home is None:
        env = os.environ if env is None else env
        home = Path(env.get("HOME") or Path.home())
    return home / ".local" / "state" / "scout" / REVIEWED_WARNINGS_NAME


def _vault_key(vault: Path) -> str:
    try:
        return str(vault.resolve())
    except OSError:
        return str(vault.absolute())


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": REVIEWS_SCHEMA, "vaults": {}}
    if not isinstance(data, dict) or data.get("schema") != REVIEWS_SCHEMA or not isinstance(data.get("vaults"), dict):
        return {"schema": REVIEWS_SCHEMA, "vaults": {}}
    return data


def load_reviews(vault: Path, *, home: Path | None = None) -> dict[str, dict[str, str]]:
    """This vault's reviews, ``{id: {"fingerprint", "reviewed_at"}}``. Never raises: unreadable means none."""
    try:
        block = _read(reviews_path(home))["vaults"].get(_vault_key(vault))
    except Exception:  # noqa: BLE001 - a broken record must never break the doctor
        return {}
    if not isinstance(block, dict):
        return {}
    return {
        k: v
        for k, v in block.items()
        if isinstance(k, str)
        and isinstance(v, dict)
        and isinstance(v.get("fingerprint"), str)
        and isinstance(v.get("reviewed_at"), str)
    }


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_review(
    vault: Path,
    warning: str,
    fp: str | None,
    *,
    home: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str] | None:
    """Record the owner's review of *warning* at fingerprint *fp*, or drop it when *fp* is None.

    Returns what is recorded now (None after a drop). Raises ValueError on a malformed id or fingerprint.
    The caller checks it is the owner (the CLI refuses a scheduled run)."""
    if not _ID_RE.fullmatch(warning):
        raise ValueError(f"{warning!r} is not a doctor warning id")
    if fp is not None and not FINGERPRINT_RE.fullmatch(fp):
        raise ValueError(f"{fp!r} is not a warning fingerprint (16 hex digits)")
    path = reviews_path(home)
    data = _read(path)
    vaults = data.setdefault("vaults", {})
    key = _vault_key(vault)
    block = vaults.get(key)
    if not isinstance(block, dict):
        block = {}
    if fp is None:
        block.pop(warning, None)
        record = None
    else:
        stamp = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        record = {"fingerprint": fp, "reviewed_at": stamp}
        block[warning] = record
    if block:
        vaults[key] = block
    else:
        vaults.pop(key, None)
    _write(path, data)
    return record


# -- applying them -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Triage:
    open_warnings: list[str]
    entries: list[TriageEntry]

    @property
    def reviewed(self) -> list[TriageEntry]:
        return [e for e in self.entries if e.reviewed_at is not None]


def apply(
    vault: Path,
    warnings: Iterable[str],
    meta: Mapping[str, WarningMeta],
    *,
    home: Path | None = None,
) -> Triage:
    """Rank *warnings* and set aside the ones the owner reviewed at their current fingerprint.

    ``entries`` lists every warning, highest risk first (the doctor's order within a risk);
    ``open_warnings`` keeps the doctor's order and drops the reviewed ones."""
    reviews = load_reviews(vault, home=home)
    entries: list[TriageEntry] = []
    open_warnings: list[str] = []
    seen: dict[str, int] = {}
    for text in warnings:
        m = meta.get(text) or default_meta(text)
        wid = warning_id(text, m)
        if wid in seen:  # the same text twice: keep ids unique
            seen[wid] += 1
            wid = f"{wid}#{seen[wid]}"
        else:
            seen[wid] = 1
        fp = fingerprint(text, m)
        review = reviews.get(wid)
        reviewed_at = review["reviewed_at"] if review and review["fingerprint"] == fp else None
        risk = m.risk if m.risk in _RANK else "medium"
        entries.append(TriageEntry(wid, m.kind, risk, m.reason, text, m.items, fp, reviewed_at))
        if reviewed_at is None:
            open_warnings.append(text)
    order = {id(e): i for i, e in enumerate(entries)}
    entries.sort(key=lambda e: (_RANK[e.risk], order[id(e)]))
    return Triage(open_warnings=open_warnings, entries=entries)
