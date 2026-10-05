"""Render the GitHub release body for a Scout release: App + Plugin and engine sections
taken from the two changelogs, a compare link, and install steps
(spec: docs/superpowers/specs/2026-10-05-unified-release-design.md §3)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_INSTALL = """## Install

1. Download `Scout-{dmg_version}.dmg` below.
2. Open it and drag **Scout.app** into **Applications**.
3. Open Scout. Onboarding sets up everything else: the engine, Claude Code's plugin, and your vault.

Terminal only? `curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash`"""


def extract_section(text: str, version: str) -> str:
    head = f"## [{version}]"
    out: list[str] = []
    grab = False
    for line in text.splitlines():
        if line.startswith(head):
            grab = True
            continue
        if grab and line.startswith("## ["):
            break
        if grab:
            out.append(line)
    return "\n".join(out).strip()


def render(version: str, *, app: str, plugin: str, prev_tag: str | None, repo_slug: str, rc: str | None = None) -> str:
    parts: list[str] = []
    if rc:
        parts += [f"> **Release candidate {rc}.** For testing; not for general use.", ""]
    parts += ["## App", "", app or "_No app changes._", ""]
    parts += ["## Plugin and engine", "", plugin or "_No plugin changes._", ""]
    if prev_tag:
        parts += [f"**Full changelog**: https://github.com/{repo_slug}/compare/{prev_tag}...v{version}", ""]
    parts += ["---", "", _INSTALL.format(dmg_version=rc.removeprefix("v") if rc else version)]
    return "\n".join(parts).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a Scout GitHub release body from both changelogs.")
    ap.add_argument("--repo-root", type=Path, required=True)
    ap.add_argument("version")
    ap.add_argument("--repo", required=True, help="owner/repo for the compare link")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--prev", default=None, help="previous release tag, for the compare link")
    ap.add_argument("--rc", default=None, help="vX.Y.Z-rc.N: render the [Unreleased] sections as a candidate")
    args = ap.parse_args(argv)
    section = "Unreleased" if args.rc else args.version
    plugin = extract_section((args.repo_root / "plugin/CHANGELOG.md").read_text(encoding="utf-8"), section)
    app = extract_section((args.repo_root / "apps/macos/CHANGELOG.md").read_text(encoding="utf-8"), section)
    if not args.rc and not plugin and not app:
        print(f"no [{args.version}] section in either changelog", file=sys.stderr)
        return 1
    body = render(args.version, app=app, plugin=plugin, prev_tag=args.prev, repo_slug=args.repo, rc=args.rc)
    args.out.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
