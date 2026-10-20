"""Privacy snapshot: JSON projection of ``scout/defaults/privacy.yaml`` (#453, spec §5.1).

The Agnes indexer loads this file instead of parsing YAML or running Scout
code. It is generated, never hand-edited::

    python -m scout.scripts.privacy_snapshot            # write the canonical file
    python -m scout.scripts.privacy_snapshot --check    # exit 1 on drift

Snapshot file format (v1)::

    {
      "engine_version": "<scout.__version__ at generation>",
      "policy": {"local_only": {...}, "shared_files": [...],
                 "surfaces": {...}, "visibility_defaults": [...]},
      "schema_version": 1
    }

``policy`` is ``load_policy(None).to_json()`` without ``warnings``: the engine
default in ``privacy.yaml``'s shape, surface ids sorted.

Determinism: ``json.dumps(indent=2, sort_keys=True)`` plus one trailing
newline; list order (first-match-wins) is preserved. ``--check`` compares
everything except ``engine_version``: a release bumps the version without
regenerating, and that alone isn't policy drift.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

import scout
from scout.kb.privacy import SCHEMA_VERSION, load_policy


def canonical_snapshot_path() -> Path:
    """``engine/scout/privacy.snapshot.json``, the committed file."""
    return Path(__file__).resolve().parents[1] / "privacy.snapshot.json"


def build_snapshot(*, engine_version: str | None = None) -> dict[str, Any]:
    policy = load_policy(None).to_json()
    policy.pop("warnings")
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": engine_version or scout.__version__,
        "policy": policy,
    }


def serialize(snapshot: dict[str, Any]) -> str:
    """Stable serialization; the writer and ``--check`` both go through it."""
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def write_snapshot(target: Path, *, engine_version: str | None = None) -> str:
    text = serialize(build_snapshot(engine_version=engine_version))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return text


def check_snapshot(target: Path) -> tuple[bool, str]:
    """``(matches, diff)``: does ``target`` hold what ``write_snapshot`` would write?

    ``engine_version`` is taken from the file itself, so only policy or schema
    drift fails.
    """
    if not target.exists():
        return False, f"snapshot target does not exist: {target}\n"
    actual = target.read_text(encoding="utf-8")
    try:
        on_disk = json.loads(actual)
        version = on_disk.get("engine_version") if isinstance(on_disk, dict) else None
    except json.JSONDecodeError:
        version = None
    expected = serialize(build_snapshot(engine_version=version if isinstance(version, str) else None))
    if actual == expected:
        return True, ""
    diff = difflib.unified_diff(
        actual.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{target} (on disk)",
        tofile=f"{target} (would write)",
    )
    return False, "".join(diff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="privacy-snapshot",
        description="Write or verify privacy.snapshot.json from defaults/privacy.yaml.",
    )
    parser.add_argument(
        "--target",
        "-t",
        type=Path,
        default=canonical_snapshot_path(),
        help="Where to write the snapshot. Defaults to engine/scout/privacy.snapshot.json.",
    )
    parser.add_argument("--check", action="store_true", help="Exit 1 if the file differs from what would be written.")
    args = parser.parse_args(argv)

    if args.check:
        ok, diff = check_snapshot(args.target)
        if ok:
            print(f"privacy snapshot OK: {args.target}")
            return 0
        sys.stderr.write(diff)
        sys.stderr.write(
            f"\nDrift detected: regenerate with `python -m scout.scripts.privacy_snapshot --target {args.target}`.\n"
        )
        return 1

    write_snapshot(args.target)
    print(f"Wrote: {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
