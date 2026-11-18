"""Changelog fragments: one file per PR entry, assembled into the CHANGELOG at release time.

A PR never edits `plugin/CHANGELOG.md` or `apps/macos/CHANGELOG.md`. It adds a fragment beside the one it
belongs to, `<dir>/changelog.d/<slug>.<section>.md`, holding the entry's markdown bullets. `<slug>` is the
branch name after its last `/` (lowercase letters, digits and hyphens), so two open PRs never pick the same
file and nothing conflicts when one of them merges. `<section>` names the `### ` heading the entry goes
under (`SECTIONS`). `scripts/release.sh prepare` calls `assemble_changelogs`, which writes every fragment
into the new `## [X.Y.Z] - date` section, grouped by heading, and deletes the fragments in the release commit.

Transition: entries still written under `## [Unreleased]` by PRs opened before fragments existed are kept.
They come first in each heading, verbatim, then the fragments, so a release without fragments renders
exactly what promoting `[Unreleased]` used to.

`check` validates every fragment and, given `--base`, refuses a change to any `## [X.Y.Z]` section the base
already has: that is how a plain `git merge origin/main` after a release silently files an entry under the
release just cut. A commit trailer `Changelog-Edit-Released: <why>` between the base and HEAD allows a
deliberate fix to a released section.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Filename suffix -> `### ` heading, in the order headings appear in an assembled section.
SECTIONS: dict[str, str] = {
    "upgrade-notes": "Upgrade notes",
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "fixed": "Fixed",
    "removed": "Removed",
    "security": "Security",
    "performance": "Performance",
    "documentation": "Documentation",
}
_ORDER = list(SECTIONS.values())

# CHANGELOG -> its fragment directory, both relative to the repo root.
CHANGELOGS: dict[str, str] = {
    "plugin/CHANGELOG.md": "plugin/changelog.d",
    "apps/macos/CHANGELOG.md": "apps/macos/changelog.d",
}

FRAGMENT_NAME = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*)\.(?P<section>[a-z][a-z-]*)\.md$")
_NOT_FRAGMENTS = {"README.md"}
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_LEVEL2 = re.compile(r"(?m)^## ")
_UNRELEASED = re.compile(r"(?m)^## \[Unreleased\][^\n]*$")
_VERSION_HEADING = re.compile(r"(?m)^## \[(?P<v>\d+\.\d+\.\d+)\][^\n]*$")
TRAILER = "Changelog-Edit-Released:"


class FragmentError(ValueError):
    """One or more fragments that cannot be assembled; the message lists every problem."""


@dataclass(frozen=True)
class Fragment:
    path: Path
    section: str  # the `### ` heading, e.g. "Fixed"
    text: str  # the entry's markdown, stripped


def _fragment_problems(path: Path) -> tuple[Fragment | None, list[str]]:
    m = FRAGMENT_NAME.match(path.name)
    if not m:
        return None, [f"{path}: not a fragment name; use <branch-slug>.<section>.md, e.g. my-fix.fixed.md"]
    if m.group("section") not in SECTIONS:
        known = ", ".join(SECTIONS)
        return None, [f"{path}: unknown section {m.group('section')!r}; use one of: {known}"]
    text = path.read_text(encoding="utf-8").strip()
    problems: list[str] = []
    if not text:
        problems.append(f"{path}: empty")
    elif not text.startswith("- "):
        problems.append(f"{path}: must start with a list item ('- ')")
    for n, line in enumerate(text.splitlines(), 1):
        if _ANY_HEADING.match(line):
            problems.append(f"{path}:{n}: no headings in a fragment; its file name sets the section")
    if problems:
        return None, problems
    return Fragment(path=path, section=SECTIONS[m.group("section")], text=text), []


def read_fragments(directory: Path) -> list[Fragment]:
    """Every fragment in `directory`, sorted by file name. Raises FragmentError listing all problems."""
    if not directory.is_dir():
        return []
    fragments: list[Fragment] = []
    problems: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name in _NOT_FRAGMENTS or path.name.startswith("."):
            continue
        if not path.is_file():
            problems.append(f"{path}: not a file")
            continue
        fragment, found = _fragment_problems(path)
        problems += found
        if fragment:
            fragments.append(fragment)
    if problems:
        raise FragmentError("\n".join(problems))
    return fragments


def _split_sections(body: str) -> tuple[list[str], dict[str, list[str]]]:
    """A release body -> (lines before its first `### `, {heading: lines}). Lines are kept verbatim;
    only blank lines at either end of each part go. A heading that repeats is merged into the first."""
    chunks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in body.splitlines():
        if line.startswith("### "):
            chunks.append((line[4:].strip(), []))
        else:
            chunks[-1][1].append(line)

    def trim(lines: list[str]) -> list[str]:
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return lines

    sections: dict[str, list[str]] = {}
    for heading, lines in chunks[1:]:
        sections.setdefault(heading or "", []).extend(trim(lines))
    return trim(chunks[0][1]), sections


def render_section(unreleased: str, fragments: list[Fragment]) -> str:
    """The body of a new release section: `[Unreleased]`'s entries, then the fragments, by heading."""
    preamble, sections = _split_sections(unreleased)
    for f in fragments:
        sections.setdefault(f.section, []).extend(f.text.splitlines())
    known = [h for h in _ORDER if sections.get(h)]
    other = [h for h in sections if h not in SECTIONS.values() and sections[h]]
    parts = ["\n".join(preamble)] if preamble else []
    parts += [f"### {h}\n" + "\n".join(sections[h]) for h in known + other]
    return "\n\n".join(parts)


def assemble(text: str, fragments: list[Fragment], *, version: str, date: str) -> str:
    """CHANGELOG text with a new `## [version] - date` section: the `[Unreleased]` body plus the fragments.

    `## [Unreleased]` stays, empty, above it while PRs from before fragments may still add to it. Without
    that heading the new section goes above the first `## ` heading."""
    head = _UNRELEASED.search(text)
    if head:
        start = head.end()
        nxt = _LEVEL2.search(text, start)
        end = nxt.start() if nxt else len(text)
        before, unreleased, after = text[:start] + "\n\n", text[start:end], text[end:]
    else:
        nxt = _LEVEL2.search(text)
        end = nxt.start() if nxt else len(text)
        before, unreleased, after = text[:end].rstrip("\n") + "\n\n", "", text[end:]
    body = render_section(unreleased, fragments)
    section = f"## [{version}] - {date}\n" + (f"\n{body}\n" if body else "")
    return before + section + ("\n" + after if after else "")


def assemble_changelogs(repo_root: Path, *, version: str, date: str) -> list[Path]:
    """Write both changelogs' new section and delete the fragments. Validates every fragment of both
    first, so a bad one changes nothing. Returns the deleted fragment paths."""
    problems: list[str] = []
    plan: list[tuple[Path, list[Fragment]]] = []
    for rel, frag_dir in CHANGELOGS.items():
        try:
            plan.append((repo_root / rel, read_fragments(repo_root / frag_dir)))
        except FragmentError as e:
            problems.append(str(e))
    if problems:
        raise FragmentError("\n".join(problems))
    for path, _ in plan:
        if re.search(rf"(?m)^## \[{re.escape(version)}\]", path.read_text(encoding="utf-8")):
            raise ValueError(f"{path} already has a [{version}] section")
    deleted: list[Path] = []
    for path, fragments in plan:
        text = path.read_text(encoding="utf-8")
        path.write_text(assemble(text, fragments, version=version, date=date), encoding="utf-8")
        for f in fragments:
            f.path.unlink()
            deleted.append(f.path)
    return deleted


def versioned_sections(text: str) -> dict[str, str]:
    """{version: its section, heading line included, trailing blank lines dropped}."""
    heads = list(_VERSION_HEADING.finditer(text))
    out: dict[str, str] = {}
    for m in heads:
        nxt = _LEVEL2.search(text, m.end())
        out.setdefault(m.group("v"), text[m.start() : nxt.start() if nxt else len(text)].rstrip())
    return out


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)


def released_section_edits(repo_root: Path, base: str) -> list[str]:
    """Every `## [X.Y.Z]` section that `base` has and the working tree changed or dropped."""
    problems: list[str] = []
    for rel in CHANGELOGS:
        shown = _git(repo_root, "show", f"{base}:{rel}")
        if shown.returncode != 0:
            continue  # not in the base: nothing released to protect
        path = repo_root / rel
        now = versioned_sections(path.read_text(encoding="utf-8")) if path.exists() else {}
        for version, section in versioned_sections(shown.stdout).items():
            if version not in now:
                problems.append(f"{rel}: the released [{version}] section is gone")
            elif now[version] != section:
                problems.append(f"{rel}: the released [{version}] section changed")
    return problems


def _allowed_by_trailer(repo_root: Path, base: str) -> bool:
    log = _git(repo_root, "log", "--format=%B", f"{base}..HEAD")
    return log.returncode == 0 and any(line.startswith(TRAILER) for line in log.stdout.splitlines())


def check(repo_root: Path, base: str | None = None) -> list[str]:
    """Problems with the fragments, and with released sections changed since `base` (if given)."""
    problems: list[str] = []
    for frag_dir in CHANGELOGS.values():
        try:
            read_fragments(repo_root / frag_dir)
        except FragmentError as e:
            problems += str(e).splitlines()
    if base and set(base) != {"0"}:  # all zeros: a push that created the branch, which has no base
        if _git(repo_root, "rev-parse", "-q", "--verify", f"{base}^{{commit}}").returncode != 0:
            return [*problems, f"--base {base} is not a commit in this clone (a shallow checkout?)"]
        edits = released_section_edits(repo_root, base)
        if edits and not _allowed_by_trailer(repo_root, base):
            problems += edits
            problems.append(
                "A PR adds a fragment under changelog.d/ instead. If an entry moved into a released section, "
                "your merge of main put it there: move it to a fragment. To fix a released entry on purpose, "
                f"add a '{TRAILER} <why>' trailer to a commit."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Changelog fragments: validate them, and guard released sections.")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[4])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="every fragment parses; with --base, no released section changed")
    c.add_argument("--base", default=None, help="git ref to compare released sections against")
    args = ap.parse_args(argv)
    problems = check(args.repo_root.resolve(), args.base or None)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print("changelog: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
