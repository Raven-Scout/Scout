"""Shell snippets the plugin ships run on macOS (BSD userland) and Linux (GNU) alike (#434).

Phases, commands and skills put shell commands in front of the model, and the
templates install shell scripts into the vault. A flag only one userland has
fails on the other: ``date -v-4H`` is BSD-only, so on Linux the GitHub scan's
time filter came out empty on every run; ``date -d`` is GNU-only. Use a
``python3 -c`` one-liner for date arithmetic, or chain GNU-first with a BSD
fallback on one line (``stat -c %Y f 2>/dev/null || stat -f %m f``); GNU first
matters for ``stat``, because GNU ``stat -f`` means something else and still
prints to stdout.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent
REPO_ROOT = PLUGIN_ROOT.parent

_SCANNED = [
    *sorted((PLUGIN_ROOT / "phases").rglob("*.md")),
    *sorted((PLUGIN_ROOT / "commands").rglob("*.md")),
    *sorted((PLUGIN_ROOT / "skills").rglob("*.md")),
    *sorted(p for p in (PLUGIN_ROOT / "templates").rglob("*") if p.is_file() and p.suffix in {".md", ".tmpl", ".sh"}),
    *sorted((PLUGIN_ROOT / "scripts").rglob("*.sh")),
    REPO_ROOT / "install.sh",
]

_BSD_DATE = re.compile(r"\bdate(\s+-u)?\s+-[vj]\b")
_GNU_DATE = re.compile(r"\bdate(\s+-u)?\s+(-d\b|--date\b)")
_SED_IN_PLACE = re.compile(r"\bsed\s+(-[a-zA-Z]*\s+)*-i\b")
_STAT_BSD = re.compile(r"\bstat\s+-f\b")
_STAT_GNU = re.compile(r"\bstat\s+-c\b")


def _violations(line: str) -> list[str]:
    found = []
    bsd, gnu = _BSD_DATE.search(line), _GNU_DATE.search(line)
    # One line that tries GNU, then falls back to BSD, is portable.
    if (bsd or gnu) and not (bsd and gnu and "||" in line):
        found.append("BSD-only `date -v/-j`" if bsd else "GNU-only `date -d`")
    if _SED_IN_PLACE.search(line):
        found.append("`sed -i` (BSD needs `-i ''`, GNU rejects it)")
    stat_bsd = _STAT_BSD.search(line)
    if stat_bsd:
        stat_gnu = _STAT_GNU.search(line)
        if not (stat_gnu and stat_gnu.start() < stat_bsd.start() and "||" in line):
            found.append("`stat -f` not behind a GNU-first `stat -c ... ||`")
    return found


def test_shipped_shell_is_portable_across_bsd_and_gnu():
    problems = []
    for path in _SCANNED:
        shell = path.suffix in {".sh", ".tmpl"} and not path.name.endswith(".md.tmpl")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if shell and line.lstrip().startswith("#"):
                continue  # a comment that names a flag
            for what in _violations(line):
                problems.append(f"{path.relative_to(REPO_ROOT)}:{n}: {what}")
    assert not problems, "non-portable shell:\n" + "\n".join(problems)


def test_scan_covers_the_files_that_had_the_bugs():
    rel = {p.relative_to(PLUGIN_ROOT).as_posix() for p in _SCANNED if p.is_relative_to(PLUGIN_ROOT)}
    assert {"phases/connectors/github.md", "phases/core/git-setup.md", "commands/scout-meta-review.md"} <= rel
    assert "templates/scripts/scout-lock.sh.tmpl" in rel


@pytest.mark.parametrize(
    ("line", "bad"),
    [
        ('--updated ">=$(date -v-4H +%Y-%m-%dT%H:%M:%S)"', True),
        ("date -j -f %s 0", True),
        ("TZ=x date -d 2026-01-01", True),
        ("date -u --date '-4 hours'", True),
        ("sed -i '' s/a/b/ f", True),
        ("sed -E -i s/a/b/ f", True),
        ("mod=$(stat -f '%Sm' f 2>/dev/null || stat -c '%y' f)", True),
        ("stat -c %Y f 2>/dev/null || stat -f %m f 2>/dev/null || true", False),
        ("date -u -d '-4 hours' +%s 2>/dev/null || date -u -v-4H +%s", False),
        ("TZ=\"$(scripts/scout-tz.sh)\" date '+%A %b %-d'", False),
        ("sed -n 2,35p f", False),
        ("update the item's due date -- it moved", False),
    ],
)
def test_the_lint_rules(line, bad):
    assert bool(_violations(line)) is bad


# ---- the GitHub phase's cutoffs, run for real ---------------------------------

_GITHUB = PLUGIN_ROOT / "phases" / "connectors" / "github.md"
_CUTOFF = re.compile(r"\$\(python3 -c '[^']*time\.time\(\) - (\d+) \* 3600[^']*'\)")


def _cutoffs() -> list[tuple[str, int]]:
    text = _GITHUB.read_text(encoding="utf-8")
    return [(m.group(0), int(m.group(1))) for m in _CUTOFF.finditer(text)]


def test_every_github_time_filter_uses_the_portable_cutoff():
    text = _GITHUB.read_text(encoding="utf-8")
    assert len(_cutoffs()) == 5
    assert text.count("since=$(") + text.count('">=$(') == 5
    assert not re.search(r"\bdate\s+-", text)


@pytest.mark.parametrize(("snippet", "hours"), _cutoffs())
def test_github_cutoff_is_a_utc_instant_with_no_date_binary(tmp_path, snippet, hours):
    """Runs the snippet with a `date` on PATH that refuses to run, the way GNU `date -v` fails."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "date").write_text("#!/bin/sh\necho \"date: invalid option -- 'v'\" >&2\nexit 1\n")
    (bin_dir / "date").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "TZ": "Asia/Tokyo"}

    before = datetime.now(UTC)
    out = subprocess.run(
        ["bash", "-c", f'printf %s "{snippet}"'], env=env, check=True, capture_output=True, text=True
    ).stdout
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", out), out
    # A UTC instant, even with a non-UTC local zone: the `Z` is true.
    got = datetime.strptime(out, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    expected = before - timedelta(hours=hours)
    assert abs((got - expected).total_seconds()) < 120
