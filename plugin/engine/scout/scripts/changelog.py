"""Changelog fragments: one file per PR entry, assembled into the CHANGELOG at release time.

A PR never edits `plugin/CHANGELOG.md` or `apps/macos/CHANGELOG.md`. It adds a fragment beside the one it
belongs to, `<dir>/changelog.d/<slug>.<section>.md`, holding the entry's markdown bullets. `<slug>` is the
branch name after its last `/` (lowercase letters, digits and hyphens), so two open PRs never pick the same
file and nothing conflicts when one of them merges. `<section>` names the `### ` heading the entry goes
under (`SECTIONS`). `scripts/release.sh prepare` calls `assemble_changelogs`, which writes every fragment
into the new `## [X.Y.Z] - date` section, grouped by heading, and deletes the fragments in the release commit.

There is no `## [Unreleased]` section any more: it existed only for PRs opened before fragments, and
`check` (on every run) and `assemble_changelogs` refuse a CHANGELOG that has one again.

`check` validates every fragment and, given `--base`, refuses a change to any `## [X.Y.Z]` section the base
already has: that is how a plain `git merge origin/main` after a release silently files an entry under the
release just cut. A real commit trailer `Changelog-Edit-Released: X.Y.Z <why>` between the base and HEAD
allows a deliberate fix to that one version's section. A PR into `release/vX.Y.Z` (`--target-branch`) may
edit `[X.Y.Z]`, which prepare wrote and finalize has not published, and may not add a fragment, which would
never reach X.Y.Z. `check` also refuses a PR that changes a fragment the base already has: two branches
whose names end the same, or a reused branch name. These base checks gate pull requests and the merge queue
only (`--event`): on a push to main the PR was already checked, and a squash merge's message keeps only
the Co-authored-by block as trailers, so a waiver that passed the PR would turn main red.
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


def render_section(fragments: list[Fragment]) -> str:
    """The body of a new release section: the fragments under their `### ` headings, in `SECTIONS` order."""
    sections: dict[str, list[str]] = {}
    for f in fragments:
        sections.setdefault(f.section, []).extend(f.text.splitlines())
    return "\n\n".join(f"### {h}\n" + "\n".join(sections[h]) for h in _ORDER if h in sections)


def assemble(text: str, fragments: list[Fragment], *, version: str, date: str) -> str:
    """CHANGELOG text with a new `## [version] - date` section holding the fragments, above the first
    `## ` heading (the previous release), below the file's header prose."""
    nxt = _LEVEL2.search(text)
    end = nxt.start() if nxt else len(text)
    before, after = text[:end].rstrip("\n") + "\n\n", text[end:]
    body = render_section(fragments)
    section = f"## [{version}] - {date}\n" + (f"\n{body}\n" if body else "")
    return before + section + ("\n" + after if after else "")


def unreleased_problems(repo_root: Path) -> list[str]:
    """A CHANGELOG that has a `## [Unreleased]` heading again: its entries would never be released."""
    return [
        f"{rel}: has a '## [Unreleased]' heading. Move its entries to fragments under {frag_dir}/ "
        "(see plugin/changelog.d/README.md) and delete the heading."
        for rel, frag_dir in CHANGELOGS.items()
        if (repo_root / rel).exists() and _UNRELEASED.search((repo_root / rel).read_text(encoding="utf-8"))
    ]


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
    problems += unreleased_problems(repo_root)
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


_RELEASE_BRANCH = re.compile(r"^(?:refs/heads/)?release/v(?P<v>\d+\.\d+\.\d+)$")
_TRAILER_VERSION = re.compile(r"^\[?(?P<v>\d+\.\d+\.\d+)\]?(?:\s|$)")


def release_branch_version(branch: str | None) -> str | None:
    """X.Y.Z when `branch` is `release/vX.Y.Z` (or `refs/heads/release/vX.Y.Z`), else None."""
    m = _RELEASE_BRANCH.match(branch or "")
    return m.group("v") if m else None


def released_section_edits(repo_root: Path, base: str, *, exempt: str | None = None) -> dict[str, list[str]]:
    """{version: problems} for every `## [X.Y.Z]` section `base` has that the working tree changed or dropped.

    `exempt` is the version a PR into `release/vX.Y.Z` may still edit: prepare wrote it, finalize has not
    published it, and a fix for that release belongs in its section."""
    problems: dict[str, list[str]] = {}
    for rel in CHANGELOGS:
        shown = _git(repo_root, "show", f"{base}:{rel}")
        if shown.returncode != 0:
            continue  # not in the base: nothing released to protect
        path = repo_root / rel
        now = versioned_sections(path.read_text(encoding="utf-8")) if path.exists() else {}
        for version, section in versioned_sections(shown.stdout).items():
            if version == exempt:
                continue
            if version not in now:
                problems.setdefault(version, []).append(f"{rel}: the released [{version}] section is gone")
            elif now[version] != section:
                problems.setdefault(version, []).append(f"{rel}: the released [{version}] section changed")
    return problems


def trailer_versions(repo_root: Path, base: str) -> set[str]:
    """Versions named by real `Changelog-Edit-Released: X.Y.Z <why>` trailers on the commits in base..HEAD.

    git parses the trailers (`%(trailers)`), so only a commit message's closing trailer block counts, not a
    sentence that happens to start with the key."""
    key = TRAILER.rstrip(":")
    log = _git(repo_root, "log", f"--format=%(trailers:key={key},valueonly,separator=%x0A)", f"{base}..HEAD")
    if log.returncode != 0:
        return set()
    return {m.group("v") for line in log.stdout.splitlines() if (m := _TRAILER_VERSION.match(line.strip()))}


def _changed_fragments(repo_root: Path, base: str, diff_filter: str) -> list[str]:
    """Fragment paths (not READMEs) that the working tree has with status `diff_filter` against `base`."""
    out = _git(repo_root, "diff", "--name-only", f"--diff-filter={diff_filter}", base, "--", *CHANGELOGS.values())
    return [p for p in out.stdout.splitlines() if Path(p).name not in _NOT_FRAGMENTS]


# The events whose runs compare against a base. A push already merged a PR that one of these checked.
GATED_EVENTS = ("pull_request", "merge_group")


def check(
    repo_root: Path, base: str | None = None, target_branch: str | None = None, event: str | None = None
) -> list[str]:
    """Problems with the fragments and, given `base`, with what changed since it.

    `event` is the CI event (`github.event_name`). Outside `GATED_EVENTS` (a push) only the fragments and the
    `[Unreleased]` heading are checked: a squash merge drops the PR's trailers, so the base checks would fail
    a waiver the PR passed.

    `target_branch` is the branch the PR merges into (`github.base_ref`). For `release/vX.Y.Z`, that
    version's section may change, and no fragment may be added: fragments there are never assembled into
    X.Y.Z, so the fix would ship in X.Y.Z but be listed under the next release."""
    problems: list[str] = []
    for frag_dir in CHANGELOGS.values():
        try:
            read_fragments(repo_root / frag_dir)
        except FragmentError as e:
            problems += str(e).splitlines()
    problems += unreleased_problems(repo_root)
    if event and event not in GATED_EVENTS:
        return problems
    if not base or set(base) == {"0"}:  # all zeros: a push that created the branch, which has no base
        return problems
    if _git(repo_root, "rev-parse", "-q", "--verify", f"{base}^{{commit}}").returncode != 0:
        return [*problems, f"--base {base} is not a commit in this clone (a shallow checkout?)"]
    release = release_branch_version(target_branch)
    edits = released_section_edits(repo_root, base, exempt=release)
    allowed = trailer_versions(repo_root, base)
    unexcused = [p for version, found in edits.items() if version not in allowed for p in found]
    if unexcused:
        problems += unexcused
        problems.append(
            "A PR adds a fragment under changelog.d/ instead. If an entry moved into a released section, your "
            "merge of main put it there: move it to a fragment. To fix a released entry on purpose, end a "
            f"commit message with the trailer '{TRAILER} X.Y.Z <why>' naming that version."
        )
    for path in _changed_fragments(repo_root, base, "M"):
        problems.append(
            f"{path}: changes a fragment main already has (another PR's, or yours from a merged branch whose "
            "name you reused). Add a new fragment with a different slug instead."
        )
    if release:
        for path in _changed_fragments(repo_root, base, "A"):
            problems.append(
                f"{path}: a fragment on release/v{release} is never assembled into {release}. If you "
                f"didn't write it, this branch contains main's unreleased work: branch from release/v{release} "
                f"instead. If you did, edit the ## [{release}] section in the CHANGELOG instead."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Changelog fragments: validate them, and guard released sections.")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[4])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="every fragment parses; with --base, no released section changed")
    c.add_argument("--base", default=None, help="git ref to compare released sections against")
    c.add_argument("--target-branch", default=None, help="the branch the PR merges into (github.base_ref)")
    c.add_argument(
        "--event", default=None, help="github.event_name; base checks run only for pull_request and merge_group"
    )
    args = ap.parse_args(argv)
    problems = check(args.repo_root.resolve(), args.base or None, args.target_branch or None, args.event or None)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print("changelog: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
