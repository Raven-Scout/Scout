"""A diagnostics report the owner can attach to a bug report (app audit card C11).

``scoutctl diagnostics --json`` gathers what the engine already reports about
itself (the doctor, the write sandbox's status, the agent runtime check, the
run ledger) and keeps only fields that describe *how Scout is set up and
running*, never *what is in the vault*. Every string that leaves this module
goes through :func:`scrub`.

What is never in the report:

* note text, note names, folder names, or any vault path outside Scout's own
  hidden folders (``.scout-state``, ``.scout-logs``, …) and the engine files it
  installs;
* secrets or tokens (known credential shapes, long opaque strings, emails);
* session transcripts and run logs (a failed run's own error line, which the
  doctor quotes verbatim, is dropped: only ``has_detail`` says one exists);
* the owner's name and email, delegate ids and custom connector names.

The app adds its own versions and logs on top and shows the owner the whole
text before anything is saved.
"""

from __future__ import annotations

import json
import platform
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout import __version__
from scout.redact import redact_secrets

SCHEMA_VERSION = 1
RECENT_RUNS = 20
MAX_STRING = 400

# Vault-relative paths that are Scout's own, not the owner's notes: hidden
# folders (first path component starts with "."), plus the engine files it
# installs at the top of the vault. Anything else under the vault is content.
_STRUCTURAL_FILES = frozenset(
    {
        "scout-config.yaml",
        "connectors.custom.yaml",
        "CLAUDE.md",
        "SKILL.md",
        "DREAMING.md",
        "RESEARCH.md",
        "scripts",
        "hooks",
        "knowledge-base/ontology/parser.py",
        "action-items/render.py",
        "scripts/heartbeat.sh",
        "hooks/kb-pre-filter.sh",
    }
)
_STRUCTURAL_TOP_DIRS = frozenset({"scripts", "hooks"})
# Scout's own folder names: never hidden as names, though what is inside them is walked.
_KEEP_WORDS = frozenset(
    {
        "knowledge-base",
        "action-items",
        "ontology",
        "wishlist",
        "research",
        "proposals",
        "archive",
        "scripts",
        "hooks",
        "Scout",
    }
)
_STRUCTURAL_MD = frozenset({"CLAUDE.md", "SKILL.md", "DREAMING.md", "RESEARCH.md", "README.md"})

# Folders whose contents are never the owner's notes when walking for names.
_SKIP_WALK = frozenset({".git", "node_modules", "__pycache__"})
# A name this short is too likely to be an ordinary word to replace everywhere.
_MIN_NAME = 3
# Walk at most this many entries: a huge vault still gets a report, scrubbed by the generic rules.
_MAX_WALK = 50_000

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ANTHROPIC_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
# Any long opaque run of token characters: API keys, session ids, hashes.
_OPAQUE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
_WIKILINK = re.compile(r"\[\[[^\]\n]*\]\]")
# A note-like file named without spaces: foo.md, dir/foo.md. Names with spaces are caught by the walk.
_MD_TOKEN = re.compile(r"(?<![\w/.<>-])[^\s'\"`()<>\[\]]*\.md\b")
_URL = re.compile(r"https?://[^\s'\"`<>)]+")
_PROJECT_URL_PREFIXES = (
    "https://github.com/Raven-Scout/",
    "https://raven-scout.github.io/",
    "https://docs.anthropic.com/",
    "https://code.claude.com/",
)
_VAULT_PATH = re.compile(r"<vault>/([^\s'\"`,;:()]+)")


@dataclass
class ScrubContext:
    """What :func:`scrub` replaces: the vault and home paths, and the names to hide."""

    vault: Path | None = None
    home: Path | None = None
    # Exact strings to hide (note paths and names, folder names, people), each with its placeholder.
    names: dict[str, str] = field(default_factory=dict)
    _pattern: re.Pattern[str] | None = None

    def add(self, value: str, placeholder: str) -> None:
        value = value.strip()
        if len(value) >= _MIN_NAME and value not in _KEEP_WORDS:
            self.names.setdefault(value, placeholder)

    def finish(self) -> ScrubContext:
        """Compile the names into one whole-word pattern, longest first (a path before its file name)."""
        if self.names:
            alternatives = sorted(self.names, key=len, reverse=True)
            self._pattern = re.compile(r"(?<![\w])(?:" + "|".join(map(re.escape, alternatives)) + r")(?![\w])")
        return self

    def replace_names(self, text: str) -> str:
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self.names[m.group(0)], text)


def _is_structural(rel: str) -> bool:
    rel = rel.rstrip("/")
    first = rel.split("/", 1)[0]
    return first.startswith(".") or rel in _STRUCTURAL_FILES or first in _STRUCTURAL_TOP_DIRS


def _path_variants(path: Path) -> list[str]:
    out = [str(path)]
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    if resolved not in out:
        out.append(resolved)
    return sorted(out, key=len, reverse=True)


def scrub(text: str, ctx: ScrubContext) -> str:
    """``text`` with vault content, personal names and secrets replaced by placeholders."""
    if not text:
        return text
    text = redact_secrets(text)
    text = _ANTHROPIC_KEY.sub("<redacted>", text)
    text = _GITHUB_TOKEN.sub("<redacted>", text)
    # URLs before paths: a URL's path can look like a vault path. Keep the project's own links.
    text = _URL.sub(lambda m: m.group(0) if m.group(0).startswith(_PROJECT_URL_PREFIXES) else "<url>", text)
    if ctx.vault is not None:
        for variant in _path_variants(ctx.vault):
            text = text.replace(variant, "<vault>")
    if ctx.home is not None:
        for variant in _path_variants(ctx.home):
            text = text.replace(variant, "~")
    # Names first: a note or folder name can hold spaces, which end the generic path rule's match.
    text = ctx.replace_names(text)
    text = _VAULT_PATH.sub(lambda m: m.group(0) if _is_structural(m.group(1)) else "<vault>/<path>", text)
    text = _WIKILINK.sub("[[<note>]]", text)
    text = _MD_TOKEN.sub(lambda m: m.group(0) if m.group(0).rsplit("/", 1)[-1] in _STRUCTURAL_MD else "<note>.md", text)
    text = _EMAIL.sub("<email>", text)
    text = _OPAQUE.sub("<redacted>", text)
    if len(text) > MAX_STRING:
        text = text[:MAX_STRING] + "…"
    return text


def _walk_names(vault: Path, ctx: ScrubContext) -> None:
    """Add every non-structural file and folder under ``vault`` to ``ctx`` (paths, names, stems)."""
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
            name = entry.name
            if name in _SKIP_WALK:
                continue
            rel = entry.relative_to(vault).as_posix()
            if _is_structural(rel):
                continue
            ctx.add(rel, "<path>")
            ctx.add(name, "<name>")
            if entry.suffix:
                ctx.add(entry.stem, "<name>")
            try:
                if entry.is_dir() and not entry.is_symlink():
                    stack.append(entry)
            except OSError:
                continue


def _identity_names(vault: Path, ctx: ScrubContext) -> None:
    """The owner's name and email, delegate ids and custom connector names."""
    import yaml

    try:
        cfg = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        cfg = {}
    user = cfg.get("user") if isinstance(cfg, dict) else None
    if isinstance(user, dict):
        for key in ("name", "email"):
            value = user.get(key)
            if isinstance(value, str):
                ctx.add(value, "<owner>")
                for part in value.split():
                    if len(part) >= 4:
                        ctx.add(part, "<owner>")
    try:
        raw = yaml.safe_load((vault / "connectors.custom.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raw = None
    if isinstance(raw, dict):
        entries = raw.get("connectors", raw)
        if isinstance(entries, dict):
            for key in entries:
                if isinstance(key, str) and key != "schema_version":
                    ctx.add(key, "<custom-connector>")
    for rel in (".scout-state/delegate-mirrors.json", ".scout-state/privacy-policy.json"):
        try:
            data = json.loads((vault / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in _delegate_keys(data):
            ctx.add(key, "<delegate>")


def _delegate_keys(data: object) -> Iterable[str]:
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for container in (
        data.get("links"),
        data.get("delegates"),
        (data.get("body") or {}).get("links") if isinstance(data.get("body"), dict) else None,
    ):
        if isinstance(container, dict):
            out.extend(k for k in container if isinstance(k, str))
    privacy = data.get("privacy")
    if isinstance(privacy, dict) and isinstance(privacy.get("delegates"), dict):
        out.extend(k for k in privacy["delegates"] if isinstance(k, str))
    return out


def build_context(vault: Path | None, home: Path | None = None) -> ScrubContext:
    ctx = ScrubContext(vault=vault, home=home or Path.home())
    if vault is not None and vault.is_dir():
        _identity_names(vault, ctx)
        _walk_names(vault, ctx)
    return ctx.finish()


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _doctor_section(
    vault: Path, ctx: ScrubContext, *, home: Path | None, now: datetime | None, check_jobs: bool
) -> dict[str, Any]:
    from scout.scripts.bootstrap_doctor import run_doctor

    report = run_doctor(vault=vault, check_jobs=check_jobs, home=home, now=now)
    run_health = None
    if report.run_health is not None:
        rh = report.run_health.to_dict()
        run_health = {
            "severity": rh["severity"],
            "evaluated_at": rh["evaluated_at"],
            "last_ok_at": rh["last_ok_at"],
            "findings": [
                {
                    "id": f["id"],
                    "severity": f["severity"],
                    "since": f["since"],
                    "source": f["source"],
                    "until": f["until"],
                    "summary": scrub(f["summary"], ctx),
                    "fix": scrub(f["fix"], ctx),
                    # The failing run's own error line is quoted from its log: never included.
                    "has_detail": bool(f["detail"]),
                }
                for f in rh["findings"]
            ],
            "slots": rh["slots"],
        }
    return {
        "severity": report.severity.value,
        "errors": [scrub(e, ctx) for e in report.errors],
        "warnings": [scrub(w, ctx) for w in report.warnings],
        "notes": [scrub(n, ctx) for n in report.notes],
        "run_health": run_health,
    }


def _sandbox_section(vault: Path, ctx: ScrubContext) -> dict[str, Any]:
    from scout.sandbox import launch

    result = launch.prepare(vault)
    return {"status": result.status, "reason": scrub(result.reason or "", ctx)}


def _runtime_section(vault: Path, ctx: ScrubContext) -> dict[str, Any]:
    from scout.agent_runtimes import check as _check
    from scout.agent_runtimes import registry
    from scout.agent_runtimes.resolve import resolve_runtime

    rid = resolve_runtime(vault, None).runtime
    report = _check.evaluate(vault, registry.get(rid), reader="autonomous", mode=None, interactive=False)
    refusal = report.refusal
    return {
        "runtime": report.runtime,
        "provider": report.provider,
        "version": scrub(report.version or "", ctx) or None,
        "tier": report.tier,
        "binary": scrub(str(report.binary), ctx) if report.binary else None,
        "allowed": refusal is None,
        "code": refusal.code if refusal else None,
        "reason": scrub(refusal.reason, ctx) if refusal else None,
    }


def _recent_runs(vault: Path, limit: int = RECENT_RUNS) -> list[dict[str, Any]]:
    from scout.health import iso_z, read_outcomes

    rows = read_outcomes(vault)[-limit:]
    return [
        {
            "slot": r.slot,
            "mode": r.mode,
            "started_at": iso_z(r.started_at),
            "finished_at": iso_z(r.finished_at),
            "exit_code": r.exit_code,
            "outcome": r.outcome,
            "failure_class": r.failure_class,
            "sandbox": r.sandbox,
            "integrity": r.integrity,
            "commits": r.commits_in_window,
        }
        for r in reversed(rows)
    ]


def _guard(fn: Callable[[], Any], ctx: ScrubContext) -> Any:
    """A section that fails says so (scrubbed) instead of failing the whole report."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - one broken section must not lose the rest
        return {"unavailable": scrub(f"{type(e).__name__}: {e}", ctx)}


def build_report(
    vault: Path,
    *,
    home: Path | None = None,
    now: datetime | None = None,
    check_jobs: bool = True,
    sandbox: Callable[[Path, ScrubContext], dict[str, Any]] | None = None,
    runtime: Callable[[Path, ScrubContext], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The diagnostics report for ``vault``. Pure read; ``sandbox``/``runtime`` are injectable for tests."""
    ctx = build_context(vault, home)
    exists = vault.is_dir()
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
        },
        "doctor": _guard(lambda: _doctor_section(vault, ctx, home=home, now=now, check_jobs=check_jobs), ctx),
        "sandbox": _guard(lambda: sandbox_fn(vault, ctx), ctx) if exists else None,
        "runtime": _guard(lambda: runtime_fn(vault, ctx), ctx) if exists else None,
        "recent_runs": _guard(lambda: _recent_runs(vault), ctx) if exists else [],
        "redaction": [
            "no note text, note or folder names, or vault paths outside Scout's own folders",
            "no secrets, tokens, emails, transcripts or run logs",
            "a failed run's quoted error line is left out (has_detail says one exists)",
            "the owner's name, delegate ids and custom connector names are replaced",
        ],
    }
    return report


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
