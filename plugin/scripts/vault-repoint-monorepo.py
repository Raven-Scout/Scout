#!/usr/bin/env python3
"""Re-point a maintainer vault at a ~/scout-plugin checkout that moved to the monorepo layout.

A vault whose engine is a git checkout at ~/scout-plugin (`bootstrap upgrade
--managed-by dev`) can carry hard-coded paths into that checkout:
`~/scout-plugin/engine/bin/scoutctl`, `~/scout-plugin/.venv/...`,
`~/scout-plugin/templates/scripts/`, `~/scout-plugin/phases/`. Once the checkout
is pulled into the Raven-Scout/Scout monorepo, all of those live under
`~/scout-plugin/plugin/`.

`bootstrap upgrade` re-renders the files it manages (the run-*.sh runners, the
templated scripts/*.sh, hooks/kb-pre-filter.sh, the plists, the shim). This
script covers what it doesn't manage:
- the hooks in .claude/settings.json;
- the brain and instruction files (SKILL.md, DREAMING.md, RESEARCH.md,
  CLAUDE.md);
- vault-local scripts;
- any managed file that bootstrap left alone because the vault had edited it.

History stays as written: knowledge-base/, action-items/, docs/,
dreaming-proposals*, .scout-state/. So does .claude/settings.local.json, which
holds permission rules; those are listed for the owner to update.

    python3 vault-repoint-monorepo.py [--vault ~/Scout]            # dry run: print the diff
    python3 vault-repoint-monorepo.py [--vault ~/Scout] --apply    # write it

Idempotent: an already re-pointed path (`scout-plugin/plugin/...`) never matches.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

OLD = re.compile(r"\bscout-plugin/(engine|\.venv|templates|phases|scripts|hooks|commands|skills)(?=[/\s\"'`)}]|$)")
GLOBS = (
    ".claude/settings.json",
    "SKILL.md",
    "DREAMING.md",
    "RESEARCH.md",
    "CLAUDE.md",
    "run-*.sh",
    "scripts/*",
    "hooks/*",
)
LIST_ONLY = (".claude/settings.local.json",)


def candidates(vault: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in GLOBS:
        for path in sorted(vault.glob(pattern)):
            if path.is_file() and not path.is_symlink():
                seen.setdefault(path, None)
    return list(seen)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-point a maintainer vault at a monorepo-layout ~/scout-plugin checkout."
    )
    ap.add_argument("--vault", type=Path, default=Path.home() / "Scout")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()
    vault = args.vault.expanduser()
    changed = 0
    for path in candidates(vault):
        try:
            before = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        after = OLD.sub(r"scout-plugin/plugin/\1", before)
        if after == before:
            continue
        changed += 1
        rel = path.relative_to(vault)
        sys.stdout.writelines(
            difflib.unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                f"a/{rel}",
                f"b/{rel}",
                n=0,
            )
        )
        if args.apply:
            path.write_text(after, encoding="utf-8")
    for name in LIST_ONLY:
        path = vault / name
        if path.is_file():
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD.search(line):
                    print(f"[review by hand] {name}:{n}: {line.strip()}")
    verb = "re-pointed" if args.apply else "would re-point"
    print(f"\n{verb} {changed} file(s) under {vault}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
