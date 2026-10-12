"""Allowlist scrub for the eval fixture (spec 2026-10-12 §5.3).

Every identifying token in the fixture's data files must appear in
``tests/eval/allowlist.txt``:

- capitalised words (``word``), so a real person or product name can't slip in;
- e-mail domains (``email``) and URL hosts (``host``);
- ``owner/repo#N`` and ``github.com/owner/repo`` owners (``repo``);
- tracker id prefixes such as ``PROJ`` in ``PROJ-1234`` (``id``);
- ``@handles`` (``handle``).

An allowlist inverts the usual denylist, which would itself have to name the
real things it protects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRS = ("vault", "scenarios", "stubs", "golden")
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".jsonl", ".txt", ".tmpl"}

_EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_URL = re.compile(r"\bhttps?://([\w.-]+)[^\s)\]>`'\"]*")
_GITHUB_REPO = re.compile(r"github\.com/([\w.-]+)/[\w.-]+")
_REPO_REF = re.compile(r"(?<![\w/])([a-z0-9][\w.-]*)/[\w.-]+#\d+")
_TRACKER_ID = re.compile(r"\b([A-Z]{2,})-\d+\b")
_HANDLE = re.compile(r"(?<![\w.@/])@([a-z0-9][\w.-]*[a-z0-9]|[a-z0-9])", re.IGNORECASE)
_WORD = re.compile(r"\b[A-Z][a-zA-Z]+\b")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    token: str
    kind: str


def load_allowlist(path: Path) -> set[str]:
    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        token = raw.split("#", 1)[0].strip()
        if token:
            out.add(token)
    return out


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _scan_line(line: str, allow: set[str]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []

    def check(kind: str, token: str) -> None:
        if token not in allow and (token, kind) not in hits:
            hits.append((token, kind))

    for m in _GITHUB_REPO.finditer(line):
        check("repo", m.group(1))
    for m in _URL.finditer(line):
        check("host", m.group(1).lower())
    rest = _blank(line, _URL)
    for m in _EMAIL.finditer(rest):
        check("email", m.group(1).lower())
    rest = _blank(rest, _EMAIL)
    for m in _REPO_REF.finditer(rest):
        check("repo", m.group(1))
    for m in _TRACKER_ID.finditer(rest):
        check("id", m.group(1))
    rest = _blank(_blank(rest, _REPO_REF), _TRACKER_ID)
    for m in _HANDLE.finditer(rest):
        check("handle", m.group(1).lower())
    rest = _blank(rest, _HANDLE)
    for m in _WORD.finditer(rest):
        check("word", m.group(0))
    return hits


def scan(root: Path, allow: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.suffix not in TEXT_SUFFIXES and path.name != "gh":
            continue
        rel = path.relative_to(root).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            findings += [Finding(rel, n, tok, kind) for tok, kind in _scan_line(line, allow)]
    return findings


def scan_fixture(eval_root: Path = EVAL_ROOT) -> list[Finding]:
    allow = load_allowlist(eval_root / "allowlist.txt")
    findings: list[Finding] = []
    for name in DATA_DIRS:
        d = eval_root / name
        if d.is_dir():
            findings += [Finding(f"{name}/{f.path}", f.line, f.token, f.kind) for f in scan(d, allow)]
    return findings
