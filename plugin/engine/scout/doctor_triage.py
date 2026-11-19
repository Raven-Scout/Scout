"""Doctor warnings ranked by risk, and the owner's record of the ones they reviewed (UI audit R13, #505).

**Ranking.** Every doctor warning gets a risk, ``high``, ``medium`` or ``low``:

* A vault script that reads the knowledge base around ``scoutctl kb`` (the privacy drift rule
  ``script-raw-kb-read``) is ``high`` when what it reads can leave the vault (:func:`rank_vault_scripts`):
  its text mentions a way to send (chat, mail, a web or network call, ``ssh``/``rsync``/``git push``, a
  notifier or the broker, ``gh``, a Claude session, a synced folder, ``drafts/``); it is longer than Scout
  reads or can't be read; a vault script it names (a helper it imports, sources or runs) is high; or another
  vault script or runner names it, so its output may be piped on (the #453 pattern). Otherwise ``low``, a
  checker whose output stays in the vault. A false ``high`` costs a look; a false ``low`` would bury a
  leak, so every rule leans high. The doctor reports the two kinds as separate warnings
  (``privacy.script-raw-kb-read`` and ``privacy.script-raw-kb-read.internal``).
* A vault runner that doesn't mark its reads as unattended is ``high``.
* A warning about run protection itself (``PROTECTION``: the privacy audit's blocked or unchecked commits,
  the gate probe, the write sandbox, run limits, the repo-sync commit hook) is ``high`` and **can't be
  reviewed**: its text can repeat while what it reports is new (a rolling 7-day count, the same sandbox
  state on every run), so a review would hide it for good. The doctor tags these where it makes them; any
  other untagged ``privacy:``/``gate probe``/``run sandbox`` warning is treated the same way.
* Everything else is ``medium``.

**Reviewed.** The owner may mark a reviewable warning reviewed (``scoutctl bootstrap review-warning``, or
the app). The doctor then leaves it out of ``warnings``, ``warning_groups`` and the severity, and lists it
in ``warning_triage`` with ``reviewed_at``. A review holds the warning's *fingerprint*: for a file-backed
warning its files and each file's content; for any other warning, its text. When that changes, the
review no longer matches and the warning is back. Errors can't be reviewed.

Reviews live in ``~/.local/state/scout/scout-reviewed-warnings.json`` (``REVIEWED_WARNINGS_NAME``), one block
per vault, outside the vault: the write gate and the OS write sandbox protect it as an engine root, and
the CLI refuses a scheduled run (its environment, and the live-run process check), so a run can't mark its
own warnings reviewed.
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
# A warning id: a kind, a kind and a vault-relative path, ``warning.<hash>`` or ``protection.<hash>``.
_ID_RE = re.compile(r"[A-Za-z0-9][^\x00-\x1f\x7f]{0,399}")
# Ids of warnings that can't be reviewed (``PROTECTION``): set_review refuses them.
UNREVIEWABLE_PREFIX = "protection."

# What a script must mention for its output to be able to leave the vault, by the reason the doctor gives.
_OUTBOUND = (
    ("a chat or notification channel", r"\b(?:slack|telegram|discord|webhook|notify|notification|outbox|broker)\b"),
    ("mail", r"\b(?:smtp|sendmail|e-?mail|mailx?)\b"),
    (
        "a web or network call",
        r"\b(?:requests|urllib\d?|httpx|aiohttp|http\.client|curl|wget|socket|ftplib|nc|ncat|netcat)\b|https?://",
    ),
    ("a remote copy", r"\b(?:ssh|scp|sftp|rsync)\b|\bgit\s+push\b"),
    ("GitHub or Linear", r"\bgh\s+(?:issue|pr|api|release|gist)\b|\blinear\b"),
    ("a Claude session", r"\bclaude\b[^\n]{0,80}(?:\s-p\b|--print\b)|\bCLAUDE_BIN\b|\bclaude-with-retry\b"),
    ("another app", r"\b(?:osascript|pbcopy|open\s+-a)\b"),
    (
        "a synced folder",
        r"Mobile\\?\s?Documents|CloudDocs|\bicloud\b|\bdropbox\b|google ?drive|\bonedrive\b|CloudStorage",
    ),
    ("a shared surface", r"\b(?:enrichment|digest|publish|upload)\b|\bdrafts/"),
    ("a Scout sender", r"\bscoutctl\s+(?:notify|broker|send)\b"),
    # Code it builds at run time could do any of the above without naming it (#717 review).
    (
        "code it builds at run time",
        r"(?m)\b(?:exec|eval)\s*\(|\bimportlib\b|__import__|(?:\bsource|\beval|(?:^|[\s;&|(])\.)\s+\"?\$"
        r"|\b(?:ba|z)?sh\s+-c\s+\"?\$",
    ),
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
    # Vault-relative path -> sha256 of its content: any edit to one brings a review back.
    digests: Mapping[str, str] = field(default_factory=dict)
    # False for a warning about run protection itself: a review would hide it for good.
    reviewable: bool = True


# A warning about run protection itself (see the module doc): high, and never reviewable.
PROTECTION = WarningMeta(
    kind="protection",
    risk="high",
    reason="it is about privacy or run protection, so it can't be marked reviewed",
    reviewable=False,
)


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
    reviewable: bool = True

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
            "reviewable": self.reviewable,
        }


# -- ranking -----------------------------------------------------------------------------------------------------

_READ_CAP = 1_000_000
_TOO_LONG = "\0too-long"  # read_script's marker for a file longer than _READ_CAP
_BINARY = "\0binary"  # read_script's marker for a binary file


def read_script(path: Path) -> str | None:
    """A script's text to rank, or None when it can't be read. A file longer than Scout reads comes back as
    ``_TOO_LONG`` (what it does past that point is unknown, so it ranks high); a binary one as ``_BINARY``."""
    try:
        with path.open("rb") as fh:
            data = fh.read(_READ_CAP + 1)
    except OSError:
        return None
    if len(data) > _READ_CAP:
        return _TOO_LONG
    return _BINARY if b"\0" in data else data.decode("utf-8", "replace")


def script_risk(text: str | None) -> tuple[str, str]:
    """``("high", why)`` for a script whose own text could send what it reads somewhere, else ``("low", why)``."""
    if text is None:
        return "high", "Scout couldn't read it to see where its output goes"
    if text == _TOO_LONG:
        return "high", "it is too long for Scout to read whole, so where its output goes is unknown"
    if text == _BINARY:
        return "high", "it is a binary file, so where its output goes is unknown"
    for why, pattern in _OUTBOUND_RES:
        if pattern.search(text):
            return "high", f"it mentions {why}, so what it reads may leave the vault"
    return "low", "it mentions no way to send anything, so its output likely stays in the vault"


_SKIP_DIRS = frozenset({".venv", "node_modules", "__pycache__", ".git"})
# What can run another file: a caller is one of these (or a file with no suffix, like a shebang script). A
# data file under scripts/ (notes, fixtures, logs) that names a script doesn't make it high.
_SCRIPT_SUFFIXES = frozenset(
    {"", ".py", ".sh", ".bash", ".zsh", ".ksh", ".fish", ".command", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl"}
)


def _vault_files(vault: Path) -> list[str]:
    """Vault-relative paths of every file under ``scripts/`` (tooling folders left out) and each root ``run-*.sh``."""
    out = [p.name for p in sorted(vault.glob("run-*.sh")) if p.is_file()]
    scripts = vault / "scripts"
    if scripts.is_dir():
        for p in sorted(scripts.rglob("*")):
            if p.is_file() and not p.is_symlink() and not _SKIP_DIRS.intersection(p.relative_to(scripts).parts[:-1]):
                out.append(p.relative_to(vault).as_posix())
    return out


def _is_script(rel: str) -> bool:
    return Path(rel).suffix.lower() in _SCRIPT_SUFFIXES


def _forms(rel: str) -> set[str]:
    """How another file refers to *rel*: its file name, and for a module its stem (``import helper``)."""
    name = rel.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return {name} | ({stem} if name.endswith(".py") and len(stem) >= 3 else set())


def rank_vault_scripts(vault: Path, paths: Iterable[str]) -> dict[str, tuple[str, str]]:
    """``{path: (risk, why)}`` for each vault-relative *path* of a KB-reading script (see the module doc).

    A script is high when its own text could send (:func:`script_risk`), when a vault script it names is
    (followed three deep, a helper it imports, sources or runs), or when another vault script or runner names
    it. Each file is read and searched once, with one pattern for every name. Never raises: what can't be
    read ranks high."""
    paths = list(paths)
    texts: dict[str, str | None] = {}

    def text(rel: str) -> str | None:
        if rel not in texts:
            texts[rel] = read_script(vault / rel)
        return texts[rel]

    # A helper can be any text file a script names, whatever its suffix (`python3 scripts/ship.txt` runs it);
    # only a script can be a caller, so a data file that mentions a script name never makes it high.
    try:
        others = _vault_files(vault)
    except OSError:
        others = []
    others += [rel for rel in paths if rel not in others]
    by_form: dict[str, list[str]] = {}
    for rel in others:
        for form in _forms(rel):
            by_form.setdefault(form, []).append(rel)
    alternation = "|".join(re.escape(f) for f in sorted(by_form, key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w.-])(?:{alternation})(?![\w-])") if by_form else None

    helpers: dict[str, list[str]] = {}

    def named_in(rel: str) -> list[str]:
        """The vault scripts *rel*'s text names (its helpers)."""
        if rel not in helpers:
            body = text(rel)
            found: list[str] = []
            if pattern is not None and body is not None and body != _TOO_LONG:
                for form in dict.fromkeys(m.group(0) for m in pattern.finditer(body)):
                    found += [o for o in by_form[form] if o != rel and o not in found and text(o) != _BINARY]
            helpers[rel] = found
        return helpers[rel]

    callers: dict[str, str] = {}
    for o in others:  # each script once: who names whom
        if not _is_script(o):
            continue
        for helper in named_in(o):
            callers.setdefault(helper, o)

    def own_or_helper(rel: str, depth: int, seen: set[str]) -> tuple[str, str]:
        risk, why = script_risk(text(rel))
        if risk == "high" or depth == 0:
            return risk, why
        for helper in named_in(rel):
            if helper in seen:
                continue
            seen.add(helper)
            if own_or_helper(helper, depth - 1, seen)[0] == "high":
                return "high", f"it uses {helper}, which may send what it is given out of the vault"
        return risk, why

    out: dict[str, tuple[str, str]] = {}
    for rel in paths:
        risk, why = own_or_helper(rel, 3, {rel})
        if risk == "low" and rel in callers:
            risk, why = "high", f"{callers[rel]} runs or uses it, so what it prints may be passed on"
        out[rel] = (risk, why)
    return out


def default_meta(text: str) -> WarningMeta:
    """A warning made without metadata: ranked by what it is about, keyed by its text."""
    if text.lower().startswith(
        ("privacy:", "gate probe", "run sandbox", "repo sync:", "the last scheduled run ran without the write sandbox")
    ):
        return PROTECTION
    return WarningMeta(kind="install", risk="medium")


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def fingerprint(text: str, meta: WarningMeta) -> str:
    """What a review holds: a file-backed warning's files and their digests; any other warning's text."""
    if meta.items:
        return _hash({"kind": meta.kind, "items": sorted(meta.items), "digests": dict(sorted(meta.digests.items()))})
    return _hash({"text": text})


def warning_id(text: str, meta: WarningMeta) -> str:
    if meta.id:
        return meta.id
    prefix = "warning." if meta.reviewable else UNREVIEWABLE_PREFIX
    return f"{prefix}{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


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
    if fp is not None and warning.startswith(UNREVIEWABLE_PREFIX):
        raise ValueError(f"{warning} is about run protection, so it can't be marked reviewed")
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
        # A protection warning's review never holds, whatever the record says (a hand-edited file, an old id).
        held = m.reviewable and not wid.startswith(UNREVIEWABLE_PREFIX)
        reviewed_at = review["reviewed_at"] if held and review and review["fingerprint"] == fp else None
        risk = m.risk if m.risk in _RANK else "medium"
        entries.append(TriageEntry(wid, m.kind, risk, m.reason, text, m.items, fp, reviewed_at, held))
        if reviewed_at is None:
            open_warnings.append(text)
    order = {id(e): i for i, e in enumerate(entries)}
    entries.sort(key=lambda e: (_RANK[e.risk], order[id(e)]))
    return Triage(open_warnings=open_warnings, entries=entries)
