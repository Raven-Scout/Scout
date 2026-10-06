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
        if grab and line.startswith("## "):  # any level-2 heading ends it, not only the next version
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
    ap.add_argument("--rc", default=None, help="vX.Y.Z-rc.N: label the output as a release candidate")
    args = ap.parse_args(argv)
    plugin_path = args.repo_root / "plugin/CHANGELOG.md"
    app_path = args.repo_root / "apps/macos/CHANGELOG.md"
    if not plugin_path.exists():
        print(f"missing plugin/CHANGELOG.md under {args.repo_root}", file=sys.stderr)
        return 1
    if not app_path.exists():
        print(f"missing apps/macos/CHANGELOG.md under {args.repo_root}", file=sys.stderr)
        return 1
    plugin = extract_section(plugin_path.read_text(encoding="utf-8"), args.version)
    app = extract_section(app_path.read_text(encoding="utf-8"), args.version)
    if not plugin and not app:
        print(f"no [{args.version}] section in either changelog", file=sys.stderr)
        return 1
    body = render(args.version, app=app, plugin=plugin, prev_tag=args.prev, repo_slug=args.repo, rc=args.rc)
    args.out.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
