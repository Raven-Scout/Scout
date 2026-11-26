"""A diagnostics report the owner can attach to a bug report (app audit card C11).

``scoutctl diagnostics --json`` gathers what the engine already reports about
itself (the doctor, the write sandbox's status, the agent runtime and its last
gate probe, the run ledger) and keeps only fields that describe *how Scout is
set up and running*, never *what is in the vault*. It writes nothing to the
vault, runs no gate probe and starts no agent CLI.

How it keeps vault content out:

* **Allowlisted fields.** Run-ledger rows and schedule slots are rebuilt field
  by field. Enum-like fields (mode, outcome, failure class, sandbox,
  integrity) keep only known values; anything else becomes ``<other>``. Slot
  keys that aren't in the default schedule become ``custom-1``, ``custom-2``, …
  everywhere in the report. A failed run's quoted error line is dropped
  (``has_detail`` says one exists); a section that fails names only the
  exception type.
* **Scrubbed text.** Every string in the finished report goes through
  :func:`scrub` once more, recursively: vault and home paths, every note and
  folder name under the vault (dot-folders other than Scout's own included),
  the owner's name and email, the login name, delegate ids, custom connector
  names, quoted spans, emails, credential shapes and long opaque strings.

The app adds its own versions and logs, scrubs again with the same rules
(``DiagnosticsScrubber.swift``), and shows the owner the whole text before
anything is saved.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from scout import __version__
from scout.redact import redact_secrets

SCHEMA_VERSION = 1
RECENT_RUNS = 20
MAX_STRING = 400
OTHER = "<other>"

# Scout's own hidden folders: the only dot-folders whose paths are kept whole. Every other
# dot-folder (.trash, .obsidian, .claude, …) is the owner's and is walked for names like any folder.
_SCOUT_DOT_DIRS = frozenset({".scout-state", ".scout-logs", ".scout-cache", ".git"})
# Engine files and folders Scout installs at the top of the vault.
_STRUCTURAL_FILES = frozenset(
    {
        "scout-config.yaml",
        "connectors.custom.yaml",
        "CLAUDE.md",
        "SKILL.md",
        "DREAMING.md",
        "RESEARCH.md",
        "RESPOND.md",
        "knowledge-base/ontology/parser.py",
        "action-items/render.py",
    }
)
_STRUCTURAL_TOP_DIRS = frozenset({"scripts", "hooks"})
# Inside .git only these files are Scout's business (lock and HEAD errors); refs and the rest can name the owner's work.
_GIT_FILES = frozenset(
    {
        ".git",
        ".git/index.lock",
        ".git/HEAD.lock",
        ".git/HEAD",
        ".git/config",
        ".git/index",
        ".git/ORIG_HEAD",
        ".git/FETCH_HEAD",
        ".git/MERGE_HEAD",
    }
)
_STRUCTURAL_MD = frozenset({"CLAUDE.md", "SKILL.md", "DREAMING.md", "RESEARCH.md", "RESPOND.md", "README.md"})
# Scout's own folder names: never hidden as names, though what is inside them is walked.
_KEEP_WORDS = frozenset(
    {"knowledge-base", "action-items", "ontology", "wishlist", "research", "proposals", "archive", "scripts", "hooks"}
)
# Paths under the home folder that are Scout's or the toolchain's, kept up to the first space after the prefix.
# Scout's and the toolchain's own folders. Only the first path segment after one is kept; anything deeper is <path>.
_HOME_KEEP_PREFIXES = (
    ".local/share/scout/",
    ".local/state/scout/",
    "Library/Application Support/Scout/",
    "Library/Caches/com.scout.",
    "Library/Caches/Scout/",
    "Library/LaunchAgents/com.scout.",
    "Library/Logs/Scout/",
    ".local/bin/",
    ".claude/plugins/",
    ".scout-secrets/",
    ".scout/",
)

# Known values of the enum-like run-ledger fields.
_OUTCOMES = frozenset(
    {
        "ok",
        "failed",
        "skipped_lock",
        "skipped_budget",
        "skipped_redundant",
        "skipped_lease",
        "skipped_gates",
        "skipped_sandbox",
        "skipped_runtime",
    }
)
_FAILURE_CLASSES = frozenset(
    {
        "",
        "ok",
        "unknown",
        "timeout",
        "budget",
        "oauth_expired",
        "auth_race",
        "network",
        "git_lock",
        "rate_limited",
        "lock",
        "redundant",
        "lease_held",
        "lease_unavailable",
        "gates",
        "gates_unsafe_endpoint",
        "gates_unsafe_launcher",
        "sandbox",
        "enforced",
        "unavailable",
        "off",
        "conflict",
        "failed",
        "version_unverified",
        "tier_too_low",
        "restrict_tools_unenforceable",
        "provider_not_consented",
        "local_only_unenforced",
        "token_ceiling",
        "check_failed",
    }
)
_SANDBOX = frozenset({"enforced", "unavailable", "off", "conflict", "failed"})
_INTEGRITY = frozenset({"clean", "restored", "unrestored", "error", "unchecked"})
_SLOT_TYPES = frozenset({"briefing", "consolidation", "dreaming", "research", "manual"})
# Words a doctor message may quote that are Scout's own vocabulary, kept when quoted ("must be one of ['mon', …]").
_KEYWORDS = frozenset(
    {
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
        "fire",
        "skip",
        "collapse",
        "true",
        "false",
        "yes",
        "no",
        "on",
        "off",
        "inbound",
        "outbound",
        "lookup",
        "owner",
        "autonomous",
        "claude-code",
        "anthropic",
    }
)
_DEFAULT_SLOTS = frozenset(
    {
        "morning-briefing",
        "weekend-briefing",
        "morning-consolidation",
        "midday-consolidation",
        "afternoon-consolidation",
        "evening-consolidation",
        "dreaming-evening",
        "dreaming-nightly",
        "dreaming-weekend-morning",
        "research",
        "consolidation",
        "dreaming",
    }
)

_KEYWORDS = _KEYWORDS | _SLOT_TYPES | _DEFAULT_SLOTS | _OUTCOMES | (_FAILURE_CLASSES - {""}) | _SANDBOX | _INTEGRITY
_GENERIC_HOME_NAMES = frozenset({"", "home", "Users", "root", "var", "tmp"})
# Names this short are hidden only where they sit next to a "/" (a path segment).
_MIN_NAME = 3
# Odd ledger values shorter than this are left alone in free text (see _ledger_free_text).
_EXACT_MIN = 8
_ANYWHERE_LEN = 5
_SKIP_WALK = frozenset({".git", "node_modules", "__pycache__"})
_PEOPLE_DIR = "knowledge-base/people/"
_MAX_WALK = 50_000

_DELIM = "'\"`<>()\\[\\]\n"
_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_KEYVAL_SECRET = re.compile(
    r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|client[_-]?secret)"
    r"\s*[=:]\s*)[^\s&'\"`,;]+"
)
_OPAQUE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
_SCP_REMOTE = re.compile(r"\b[\w.-]+@([\w.-]+):[^\s'\"`]+")
_FILE_URL = re.compile(r"file://([^\s'\"`<>]+)")
_URL = re.compile(r"https?://[^\s'\"`<>)]+")
_PROJECT_URL_PREFIXES = (
    "https://github.com/Raven-Scout/",
    "https://raven-scout.github.io/",
    "https://docs.anthropic.com/",
    "https://code.claude.com/",
)
_VAULT_PATH = re.compile(rf"<vault>/([^{_DELIM}]*)")
_HOME_PATH = re.compile(rf"(?<![\w/])~/([^{_DELIM}]*)")
_USERS_PATH = re.compile(rf"(?<![\w])(/Users|/home)/(?!<user>)[^/\s{_DELIM}]+((?:/[^{_DELIM}]*)?)")
# Other places an owner keeps files: external volumes and temporary folders. Everything after the root is <path>.
_OTHER_ROOTS = re.compile(rf"(?<![\w/~>])(/Volumes|/private|/tmp|/var)/(?!<path>)[^{_DELIM}]*")
_ENCODED_USERS = re.compile(r"-(Users|home)-(?!<user>)[A-Za-z0-9._]+(-|$)")
_WIKILINK = re.compile(r"\[\[[^\]\n]*\]\]")
# A quoted span is usually a value from the vault (a title, a field). (pattern, quote character)
_QUOTED = (
    (re.compile(r"(?<![\w])'([^'\n]{1,300})'(?![\w])"), "'"),
    (re.compile(r"(?<![\w\\])\"([^\"\n]{1,300})\"(?![\w])"), '"'),
)
# A note-like file named without spaces (foo.md, dir/foo.md, <name>/foo.md); names with spaces are caught by the walk.
_MD_TOKEN = re.compile(r"(?<![\w/.-])[^\s'\"`()\[\]]*\.md\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _is_wide(c: str) -> bool:
    """A character of a script written without spaces between words (CJK and the like)."""
    return unicodedata.east_asian_width(c) in ("W", "F") and unicodedata.category(c).startswith("L")


@dataclass
class ScrubContext:
    """What :func:`scrub` replaces: path prefixes, and names with their placeholders."""

    # Path prefixes (absolute, any spelling) → placeholder, longest first. Matched case-insensitively.
    paths: list[tuple[str, str]] = field(default_factory=list)
    # Names hidden as whole words anywhere.
    names: dict[str, str] = field(default_factory=dict)
    # Short names hidden only as a path segment (next to a "/").
    segments: dict[str, str] = field(default_factory=dict)
    # Raw text Scout's own ledgers hold, replaced verbatim before any other rule rewrites it.
    exact: dict[str, str] = field(default_factory=dict)
    # Names hidden anywhere, even inside a word: scripts written without spaces between words (CJK).
    loose: dict[str, str] = field(default_factory=dict)
    _path_pattern: re.Pattern[str] | None = None
    _exact_pattern: re.Pattern[str] | None = None
    _loose_pattern: re.Pattern[str] | None = None
    _name_pattern: re.Pattern[str] | None = None
    _segment_pattern: re.Pattern[str] | None = None

    def add_path(self, path: Path | str, placeholder: str) -> None:
        text = str(path).rstrip("/")
        if len(text) > 1:
            self.paths.append((_nfc(text), placeholder))

    def add(self, value: str, placeholder: str, *, anywhere: bool = False) -> None:
        """Hide ``value``. Long or distinctive names go anywhere; short plain words only as path segments."""
        value = _nfc(value.strip())
        if not value or value in _KEEP_WORDS:
            return
        if any(_is_wide(c) for c in value):
            # No spaces between words in these scripts, so no word boundary to wait for.
            self.loose.setdefault(value, placeholder)
            return
        if len(value) < 2:
            return
        distinctive = any(not c.isalpha() for c in value) or any(c.isupper() for c in value[1:])
        non_ascii = any(ord(c) > 127 for c in value)
        if anywhere or non_ascii or (len(value) >= _ANYWHERE_LEN and (distinctive or len(value) >= 8)) or " " in value:
            self.names.setdefault(value, placeholder)
        elif len(value) >= _MIN_NAME:
            self.segments.setdefault(value, placeholder)

    def finish(self) -> ScrubContext:
        """Compile the paths and names into whole-word patterns, longest first."""
        if self.paths:
            unique = {p: ph for p, ph in sorted(self.paths, key=lambda x: len(x[0]), reverse=True)}
            self.paths = list(unique.items())
            alts = "|".join(re.escape(p) for p in unique)
            self._path_pattern = re.compile(rf"(?:{alts})(?![^/\s{_DELIM}])", re.IGNORECASE)
        if self.exact:
            alts = "|".join(map(re.escape, sorted(self.exact, key=len, reverse=True)))
            self._exact_pattern = re.compile(rf"(?<![\w])(?:{alts})(?![\w])")
        if self.loose:
            alts = "|".join(map(re.escape, sorted(self.loose, key=len, reverse=True)))
            self._loose_pattern = re.compile(rf"(?:{alts})")
        if self.names:
            alts = "|".join(map(re.escape, sorted(self.names, key=len, reverse=True)))
            self._name_pattern = re.compile(rf"(?<![\w])(?:{alts})(?![\w])")
        if self.segments:
            alts = "|".join(map(re.escape, sorted(self.segments, key=len, reverse=True)))
            self._segment_pattern = re.compile(rf"(?:(?<=/)(?:{alts})(?![\w])|(?<![\w])(?:{alts})(?=/))")
        return self

    def replace_paths(self, text: str) -> str:
        if self._path_pattern is None:
            return text
        lookup = {p.casefold(): ph for p, ph in self.paths}
        return self._path_pattern.sub(lambda m: lookup.get(m.group(0).casefold(), "<path>"), text)

    def replace_names(self, text: str) -> str:
        if self._loose_pattern is not None:
            text = self._loose_pattern.sub(lambda m: self.loose[m.group(0)], text)
        if self._name_pattern is not None:
            text = self._name_pattern.sub(lambda m: self.names[m.group(0)], text)
        if self._segment_pattern is not None:
            text = self._segment_pattern.sub(lambda m: self.segments[m.group(0)], text)
        return text


def _is_structural(rel: str) -> bool:
    rel = rel.rstrip("/").rstrip(":,;.")
    first = rel.split("/", 1)[0]
    if first == ".git":
        return rel in _GIT_FILES
    return first in _SCOUT_DOT_DIRS or first in _STRUCTURAL_TOP_DIRS or rel in _STRUCTURAL_FILES


def _vault_path(match: re.Match[str]) -> str:
    rest = match.group(1)
    if not rest or rest.startswith("<path>"):
        return match.group(0)
    token, sep, remainder = rest.partition(" ")
    if _is_structural(token):
        return f"<vault>/{token}{sep}{remainder}"
    # Not Scout's: the path runs to the next quote, bracket or end of line, spaces and all.
    return "<vault>/<path>"


def _home_rest(rest: str) -> str:
    """A path under a home folder: a kept prefix and its first segment stay, anything deeper is ``<path>``.

    Text after the first segment that doesn't continue the path (a space, then words) is ordinary text.
    """
    for prefix in _HOME_KEEP_PREFIXES:
        if rest.startswith(prefix):
            tail = rest[len(prefix) :]
            segment = re.match(r"[^/\s]*", tail).group(0)  # type: ignore[union-attr]
            after = tail[len(segment) :]
            return f"{prefix}{segment}/<path>" if after.startswith("/") else f"{prefix}{segment}{after}"
    return "<path>"


def _home_path(match: re.Match[str]) -> str:
    rest = match.group(1)
    if not rest or rest.startswith("<path>"):
        return match.group(0)
    return "~/" + _home_rest(rest)


def _users_path(match: re.Match[str]) -> str:
    rest = match.group(2)
    if not rest or rest == "/":
        return f"{match.group(1)}/<user>{rest}"
    return f"{match.group(1)}/<user>/" + _home_rest(rest[1:])


def _url(match: re.Match[str]) -> str:
    url = match.group(0)
    base = re.split(r"[?#]", url, maxsplit=1)[0]  # query strings and fragments can carry tokens
    return base if url.startswith(_PROJECT_URL_PREFIXES) else "<url>"


def _quoted_replacer(quote: str) -> Callable[[re.Match[str]], str]:
    def replace(m: re.Match[str]) -> str:
        inner = m.group(1)
        if inner.startswith("<") or inner in _KEYWORDS:
            return m.group(0)
        return f"{quote}<quoted>{quote}"

    return replace


def _secrets(text: str) -> str:
    text = redact_secrets(text)
    text = _ANTHROPIC_KEY.sub("<redacted>", text)
    return _GITHUB_TOKEN.sub("<redacted>", text)


def scrub(text: str, ctx: ScrubContext) -> str:
    """``text`` with vault content, personal names and secrets replaced by placeholders."""
    if not text:
        return text
    text = _nfc(text).replace("\\/", "/")
    if ctx._exact_pattern is not None:
        text = ctx._exact_pattern.sub(lambda m: ctx.exact[m.group(0)], text)
    text = _secrets(text)
    text = _FILE_URL.sub(lambda m: unquote(m.group(1)), text)
    if "%" in text:
        text = _secrets(unquote(text))
    text = _SCP_REMOTE.sub(lambda m: f"git@{m.group(1)}:<repo>", text)
    text = _URL.sub(_url, text)
    text = _KEYVAL_SECRET.sub(r"\1<redacted>", text)
    text = ctx.replace_paths(text)
    # Path rules before names: a path runs to the next quote, bracket or end of line, so a
    # name with spaces in it can't split it; names then catch what is left in free text.
    text = _VAULT_PATH.sub(_vault_path, text)
    text = _HOME_PATH.sub(_home_path, text)
    text = _USERS_PATH.sub(_users_path, text)
    text = _OTHER_ROOTS.sub(lambda m: f"{m.group(1)}/<path>", text)
    text = _ENCODED_USERS.sub(lambda m: f"-{m.group(1)}-<user>{m.group(2)}", text)
    text = ctx.replace_names(text)
    text = _WIKILINK.sub("[[<note>]]", text)
    for pattern, quote in _QUOTED:
        text = pattern.sub(_quoted_replacer(quote), text)
    text = _MD_TOKEN.sub(lambda m: m.group(0) if m.group(0).rsplit("/", 1)[-1] in _STRUCTURAL_MD else "<note>.md", text)
    text = _EMAIL.sub("<email>", text)
    text = _OPAQUE.sub("<redacted>", text)
    if len(text) > MAX_STRING:
        text = text[:MAX_STRING] + "…"
    return text


# Fields whose values are built from allowlists (or aliases) above, so scrubbing can only corrupt them.
STRUCTURED_KEYS = frozenset(
    {
        "id",
        "severity",
        "source",
        "outcome",
        "failure_class",
        "sandbox",
        "integrity",
        "mode",
        "slot",
        "key",
        "type",
        "status",
        "runtime",
        "provider",
    }
)
# Every key the report uses. Any other key (none today) is replaced with <key-N>, in case one ever carries content.
REPORT_KEYS = frozenset(
    {
        "schema",
        "generated_at",
        "engine",
        "version",
        "python",
        "platform",
        "vault",
        "exists",
        "schedule",
        "config",
        "git",
        "custom_slots",
        "doctor",
        "severity",
        "errors",
        "warnings",
        "notes",
        "run_health",
        "evaluated_at",
        "last_ok_at",
        "findings",
        "id",
        "since",
        "source",
        "until",
        "summary",
        "fix",
        "has_detail",
        "slots",
        "key",
        "type",
        "last_fire",
        "last_outcome",
        "outcome",
        "finished_at",
        "failure_class",
        "sandbox",
        "status",
        "reason",
        "runtime",
        "provider",
        "gate_probe",
        "ok",
        "checked_at",
        "claude_version",
        "recent_runs",
        "slot",
        "mode",
        "started_at",
        "exit_code",
        "integrity",
        "commits",
        "redaction",
        "unavailable",
    }
)


def scrub_tree(value: Any, ctx: ScrubContext, key: str | None = None) -> Any:
    """``value`` with every string in it scrubbed, except allowlisted fields; unknown keys are replaced."""
    if isinstance(value, str):
        return value if key in STRUCTURED_KEYS else scrub(value, ctx)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items(), 1):
            out[k if k in REPORT_KEYS else f"<key-{i}>"] = scrub_tree(v, ctx, k)
        return out
    if isinstance(value, list | tuple):
        return [scrub_tree(v, ctx, key) for v in value]
    return value


# ---------------------------------------------------------------------------
# What to hide
# ---------------------------------------------------------------------------


def _walk_names(vault: Path, ctx: ScrubContext) -> None:
    """Hide every file and folder under ``vault`` that isn't Scout's: relative path, name, stem."""
    count = 0
    stack = [vault]
    while stack and count < _MAX_WALK:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            count += 1
            if entry.name in _SKIP_WALK:
                continue
            rel = entry.relative_to(vault).as_posix()
            if _is_structural(rel):
                continue
            ctx.add(rel, "<path>", anywhere=True)
            # A person's name is short and plain ("Priya"), and the most sensitive name there is: hide it anywhere.
            person = rel.startswith(_PEOPLE_DIR)
            ctx.add(entry.name, "<name>", anywhere=person)
            if entry.suffix:
                ctx.add(entry.stem, "<name>", anywhere=person)
            try:
                if entry.is_dir() and not entry.is_symlink():
                    stack.append(entry)
            except OSError:
                continue


def _yaml(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _identity_names(vault: Path, ctx: ScrubContext) -> None:
    """The owner's name and email, delegate ids and custom connector names."""
    cfg = _yaml(vault / "scout-config.yaml")
    user = cfg.get("user") if isinstance(cfg, dict) else None
    if isinstance(user, dict):
        name, email = user.get("name"), user.get("email")
        if isinstance(name, str) and name.strip():
            ctx.add(name, "<owner>", anywhere=True)
            for part in name.split():
                ctx.add(part, "<owner>", anywhere=True)
        if isinstance(email, str) and email.strip():
            ctx.add(email, "<owner>", anywhere=True)
            # The local part's pieces, never the domain's ("com", "org" are ordinary words).
            for part in re.split(r"[._+-]+", email.split("@", 1)[0]):
                if len(part) >= 3:
                    ctx.add(part, "<owner>", anywhere=True)
    raw = _yaml(vault / "connectors.custom.yaml")
    if isinstance(raw, dict):
        entries = raw.get("connectors", raw)
        if isinstance(entries, dict):
            for key in entries:
                if isinstance(key, str) and key != "schema_version":
                    ctx.add(key, "<custom-connector>", anywhere=True)
    for rel in (".scout-state/delegate-mirrors.json", ".scout-state/privacy-policy.json"):
        try:
            data = json.loads((vault / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in _delegate_keys(data):
            ctx.add(key, "<delegate>", anywhere=True)


def _delegate_keys(data: object) -> Iterable[str]:
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    for container in (data.get("links"), data.get("delegates"), body.get("links") if body else None):
        if isinstance(container, dict):
            out.extend(k for k in container if isinstance(k, str))
    privacy = data.get("privacy")
    if isinstance(privacy, dict) and isinstance(privacy.get("delegates"), dict):
        out.extend(k for k in privacy["delegates"] if isinstance(k, str))
    return out


def _homes(home: Path | None) -> list[Path]:
    """The home folder by every route: the argument, $HOME, and the password database."""
    out: list[Path] = []
    candidates: list[Path | None] = [home, Path.home()]
    try:
        import pwd

        candidates.append(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except (ImportError, KeyError, OSError):
        pass
    for c in candidates:
        if c is not None and c not in out:
            out.append(c)
    return out


def _login_names() -> list[str]:
    names: list[str] = []
    try:
        names.append(getpass.getuser())
    except (OSError, KeyError):
        pass
    try:
        import pwd

        entry = pwd.getpwuid(os.getuid())
        names.append(entry.pw_name)
        names.extend(p for p in re.split(r"[\s,]+", entry.pw_gecos or "") if len(p) >= 2)
    except (ImportError, KeyError, OSError):
        pass
    return names


def _encoded(path: Path) -> str:
    """Claude Code's folder name for a cwd: every non-alphanumeric becomes "-"."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _ledger_free_text(vault: Path) -> tuple[list[str], list[str]]:
    """Free text the doctor quotes from Scout's own ledgers: (each self-update row's ``detail``, every
    run-ledger value outside its allowlist, which a run-health summary can name)."""
    from scout.health import read_outcomes
    from scout.update_outcomes import read

    details: list[str] = []
    out: list[str] = []
    try:
        details = [row.detail for row in read(vault) if row.detail]
    except Exception:  # noqa: BLE001 - an unreadable ledger quotes nothing
        pass
    try:
        for r in read_outcomes(vault):
            for value, allowed in (
                (r.failure_class, _FAILURE_CLASSES),
                (r.outcome, _OUTCOMES),
                (r.sandbox, _SANDBOX),
                (r.integrity, _INTEGRITY),
            ):
                if value and value not in allowed:
                    out.append(value)
    except Exception:  # noqa: BLE001
        pass
    return details, out


def build_context(
    vault: Path | None, home: Path | None = None, *, slot_aliases: dict[str, str] | None = None
) -> ScrubContext:
    ctx = ScrubContext()
    if vault is not None:
        for variant in {vault, vault.resolve() if vault.exists() else vault}:
            ctx.add_path(variant, "<vault>")
            ctx.add(_encoded(variant), "<vault>", anywhere=True)
        if vault.name and vault.name not in _KEEP_WORDS and vault.name != "Scout":
            ctx.add(vault.name, "<vault-name>", anywhere=True)
        parts = vault.parts
        if len(parts) > 2 and parts[1] == "Volumes":
            ctx.add(parts[2], "<volume>", anywhere=True)
    for h in _homes(home):
        ctx.add_path(h, "~")
        if h.name not in _GENERIC_HOME_NAMES:
            ctx.add(h.name, "<user>", anywhere=True)
    for name in _login_names():
        ctx.add(name, "<user>", anywhere=True)
    for key, alias in (slot_aliases or {}).items():
        ctx.add(key, alias, anywhere=True)
    if vault is not None and vault.is_dir():
        _identity_names(vault, ctx)
        _walk_names(vault, ctx)
        details, values = _ledger_free_text(vault)
        for text in details:
            if text.strip():
                ctx.exact.setdefault(_nfc(text), OTHER)
        # An odd ledger value short enough to be an ordinary word ("auth", "crash") would rewrite other words.
        for text in values:
            if len(text) >= _EXACT_MIN:
                ctx.exact.setdefault(_nfc(text), OTHER)
    return ctx.finish()


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def slot_aliases(vault: Path, extra_keys: Iterable[str] = ()) -> dict[str, str]:
    """``custom-1``, ``custom-2``, … for every slot key that isn't one of Scout's defaults, in sorted order."""
    keys: set[str] = set(extra_keys)
    data = _yaml(vault / ".scout-state" / "schedule.yaml")
    slots = data.get("slots") if isinstance(data, dict) else None
    if isinstance(slots, dict):
        keys.update(k for k in slots if isinstance(k, str))
    custom = sorted(k for k in keys if k and k not in _DEFAULT_SLOTS)
    return {k: f"custom-{i}" for i, k in enumerate(custom, 1)}


def _slot(key: str, aliases: dict[str, str]) -> str:
    if key in _DEFAULT_SLOTS:
        return key
    return aliases.get(key, OTHER)


def _mode(mode: str, aliases: dict[str, str]) -> str:
    if mode in _DEFAULT_SLOTS or mode in _SLOT_TYPES:
        return mode
    return aliases.get(mode, OTHER)


def _finding_id(fid: str, aliases: dict[str, str]) -> str:
    """A run-health finding id, with the slot or lane it names (``slot.stale:<key>``) aliased like any slot."""
    kind, sep, name = fid.partition(":")
    if not sep:
        return fid if re.fullmatch(r"[a-z_.]+", fid) else OTHER
    if kind.startswith("lane."):
        return f"{kind}:{name if name in _SLOT_TYPES else OTHER}"
    return f"{kind}:{_slot(name, aliases)}"


def _known(value: str | None, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    return value if value in allowed else OTHER


def _doctor_section(
    vault: Path, aliases: dict[str, str], *, home: Path | None, now: datetime | None, check_jobs: bool
) -> dict[str, Any]:
    from scout.scripts.bootstrap_doctor import run_doctor

    report = run_doctor(vault=vault, check_jobs=check_jobs, home=home, now=now)
    run_health = None
    if report.run_health is not None:
        rh = report.run_health.to_dict()
        slots = []
        for s in rh["slots"]:
            last = s.get("last_outcome")
            slots.append(
                {
                    "key": _slot(str(s.get("key")), aliases),
                    "type": _known(s.get("type"), _SLOT_TYPES),
                    "last_fire": s.get("last_fire"),
                    "last_outcome": None
                    if not isinstance(last, dict)
                    else {
                        "outcome": _known(last.get("outcome"), _OUTCOMES),
                        "finished_at": last.get("finished_at"),
                        "failure_class": _known(last.get("failure_class"), _FAILURE_CLASSES),
                    },
                }
            )
        run_health = {
            "severity": rh["severity"],
            "evaluated_at": rh["evaluated_at"],
            "last_ok_at": rh["last_ok_at"],
            "findings": [
                {
                    "id": _finding_id(f["id"], aliases),
                    "severity": f["severity"],
                    "since": f["since"],
                    "source": f["source"],
                    "until": f["until"],
                    "summary": f["summary"],
                    "fix": f["fix"],
                    # The failing run's own error line is quoted from its log: never included.
                    "has_detail": bool(f["detail"]),
                }
                for f in rh["findings"]
            ],
            "slots": slots,
        }
    return {
        "severity": report.severity.value,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "notes": list(report.notes),
        "run_health": run_health,
    }


def _sandbox_section(vault: Path) -> dict[str, Any]:
    from scout.sandbox import launch

    result = launch.prepare(vault)
    return {"status": _known(result.status, _SANDBOX), "reason": result.reason or ""}


def _runtime_section(vault: Path) -> dict[str, Any]:
    """The runtime scheduled runs resolve to, and its last gate probe from the cache. Starts nothing."""
    from scout.agent_runtimes import registry
    from scout.agent_runtimes.gate_probe import read_cache
    from scout.agent_runtimes.resolve import resolve_runtime

    rid = resolve_runtime(vault, None).runtime
    adapter = registry.get(rid)
    probe = read_cache(vault)
    return {
        "runtime": rid,
        "provider": getattr(adapter, "provider", None),
        "gate_probe": None
        if probe is None
        else {
            "ok": probe.ok,
            "checked_at": probe.checked_at,
            "claude_version": probe.claude_version,
            "reason": probe.reason,
        },
    }


def _recent_runs(vault: Path, aliases: dict[str, str], limit: int = RECENT_RUNS) -> list[dict[str, Any]]:
    from scout.health import iso_z, read_outcomes

    rows = read_outcomes(vault)[-limit:]
    return [
        {
            "slot": _slot(r.slot, aliases),
            "mode": _mode(r.mode, aliases),
            "started_at": iso_z(r.started_at),
            "finished_at": iso_z(r.finished_at),
            "exit_code": r.exit_code,
            "outcome": _known(r.outcome, _OUTCOMES),
            "failure_class": _known(r.failure_class, _FAILURE_CLASSES),
            "sandbox": _known(r.sandbox, _SANDBOX),
            "integrity": _known(r.integrity, _INTEGRITY),
            "commits": r.commits_in_window,
        }
        for r in reversed(rows)
    ]


def _guard(fn: Callable[[], Any]) -> Any:
    """A section that fails says which exception, never its message (which can quote anything)."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - one broken section must not lose the rest
        return {"unavailable": type(e).__name__}


def build_report(
    vault: Path,
    *,
    home: Path | None = None,
    now: datetime | None = None,
    check_jobs: bool = True,
    sandbox: Callable[[Path], dict[str, Any]] | None = None,
    runtime: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The diagnostics report for ``vault``. ``sandbox``/``runtime`` are injectable for tests.

    It writes nothing to the vault and starts no agent CLI. The sandbox check builds its profile and
    self-tests it in a temporary folder of its own, as the doctor's check does.

    Sections return raw values; the whole report is scrubbed once at the end, so a section can't skip it.
    """
    from scout.health import read_outcomes

    exists = vault.is_dir()
    ledger_slots: list[str] = []
    if exists:
        try:
            ledger_slots = [k for r in read_outcomes(vault) for k in (r.slot, r.mode)]
        except Exception:  # noqa: BLE001 - an unreadable ledger hides nothing extra
            ledger_slots = []
    aliases = slot_aliases(vault, ledger_slots) if exists else {}
    ctx = build_context(vault, home, slot_aliases=aliases)
    sandbox_fn = sandbox or _sandbox_section
    runtime_fn = runtime or _runtime_section
    report: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "generated_at": (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
        },
        "vault": {
            "exists": exists,
            "schedule": exists and (vault / ".scout-state" / "schedule.yaml").is_file(),
            "config": exists and (vault / "scout-config.yaml").is_file(),
            "git": exists and (vault / ".git").exists(),
            "custom_slots": len(aliases),
        },
        "doctor": _guard(lambda: _doctor_section(vault, aliases, home=home, now=now, check_jobs=check_jobs)),
        "sandbox": _guard(lambda: sandbox_fn(vault)) if exists else None,
        "runtime": _guard(lambda: runtime_fn(vault)) if exists else None,
        "recent_runs": _guard(lambda: _recent_runs(vault, aliases)) if exists else [],
        "redaction": [
            "no note text, note or folder names, or vault paths outside Scout's own folders",
            "no secrets, tokens, emails, transcripts or run logs",
            "a failed run's quoted error line is left out (has_detail says one exists)",
            "the owner's name, login name, delegate ids and custom connector names are replaced",
            "slots not in the default schedule are named custom-1, custom-2, …",
        ],
    }
    return scrub_tree(report, ctx)


def main(vault: Path, *, as_json: bool = True, check_jobs: bool = True) -> str:
    report = build_report(vault, check_jobs=check_jobs)
    if as_json:
        return json.dumps(report, indent=2, sort_keys=True)
    lines = [f"scout engine {report['engine']['version']} · python {report['engine']['python']}"]
    doctor = report["doctor"]
    if isinstance(doctor, dict) and "severity" in doctor:
        lines.append(f"doctor: {doctor['severity']}")
        lines += [f"  error: {e}" for e in doctor["errors"]]
        lines += [f"  warning: {w}" for w in doctor["warnings"]]
    if isinstance(report["sandbox"], dict):
        lines.append(f"sandbox: {report['sandbox'].get('status', 'unavailable')}")
    if isinstance(report["runtime"], dict):
        lines.append(f"runtime: {report['runtime'].get('runtime', 'unavailable')}")
    lines.append(f"recent runs: {len(report['recent_runs']) if isinstance(report['recent_runs'], list) else 0}")
    return "\n".join(lines)
