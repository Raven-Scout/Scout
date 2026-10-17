#!/usr/bin/env python3
"""One-time KB log sharding (spec 2026-09-26 §3.1 Logs / §3.7).

Moves an append-only log's entries into small shard files and rewrites the
original path as a short index. Lines are moved verbatim, never edited.
Afterwards run: scoutctl kb lint --lossless-rev HEAD --lossless-path <file>
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import OrderedDict
from pathlib import Path

DATE = re.compile(r"(20\d{2})-(\d{2})-\d{2}")
Split = tuple[list[str], "OrderedDict[str, list[str]]", list[str], dict[str, list[str]]]


def _slug(text: str, limit: int = 50) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "entry"


def rows_by_month(lines: list[str]) -> Split:
    pre: list[str] = []
    rest: list[str] = []
    shards: OrderedDict[str, list[str]] = OrderedDict()
    i = 0
    while i < len(lines) and not lines[i].startswith("| "):
        pre.append(lines[i])
        i += 1
    table_header = lines[i : i + 2]
    for ln in lines[i + 2 :]:
        m = DATE.search(ln[:16]) if ln.startswith("| ") else None
        if m:
            shards.setdefault(f"{m.group(1)}-{m.group(2)}", []).append(ln)
        else:
            rest.append(ln)
    headers = {k: [f"# Session Log — {k}", "", *table_header] for k in shards}
    return pre, shards, rest, headers


ANY_HEAD = re.compile(r"^(#{1,6})\s")


def sections(lines: list[str], level: int, per_file: re.Pattern[str] | None) -> Split:
    """Split into per-unit shards, matching the unit heading (date or
    ``--pattern``) at ANY heading depth 1-6, not just ``level``.

    A source log is not guaranteed to hold its per-unit headings at one
    consistent depth — `scout-mistake-audit.md` has patterns at both ``##``
    and ``###``, and `research-queue.md` has dated run-log headings at both
    ``##`` and ``###`` too (see task-B4-report.md for the inspection that
    found this). So instead of a fixed target depth, a heading line is a new
    unit if it matches the key pattern regardless of depth; a heading that
    does *not* match but is deeper than the current unit's own heading is
    treated as a nested subsection of it (e.g. an "Instance" sub-heading
    under a pattern); anything else at or above the current depth closes
    the current unit. ``level`` is accepted for CLI compatibility but no
    longer gates matching.
    """
    pre: list[str] = []
    rest: list[str] = []
    shards: OrderedDict[str, list[str]] = OrderedDict()
    keys: dict[str, str] = {}
    current: list[str] | None = None
    current_depth = 0
    seen_unit = False
    in_fence = False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m_head = ANY_HEAD.match(ln) if not in_fence else None
        if m_head:
            depth = len(m_head.group(1))
            key = None
            if per_file is not None:
                m = per_file.search(ln)
                if m:
                    key = keys.setdefault(m.group(1), f"pattern-{m.group(1)}-{_slug(ln[m.end():])}")
            else:
                d = DATE.search(ln)
                if d:
                    key = f"{d.group(1)}-{d.group(2)}"
            if key:
                seen_unit = True
                current = shards.setdefault(key, [])
                current_depth = depth
                current.append(ln)
                continue
            if current is not None and depth > current_depth:
                current.append(ln)
                continue
            current = None
        if current is not None:
            current.append(ln)
        elif seen_unit:
            rest.append(ln)
        else:
            pre.append(ln)
    return pre, shards, rest, {}


def _write_shards(shard_dir: Path, shards: "OrderedDict[str, list[str]]", headers: dict[str, list[str]]) -> int:
    """Write each shard's body, appending to an existing file if present.

    Returns the number of incoming lines skipped as exact-match duplicates of
    lines already in the target shard (table rows or section lines alike) —
    what makes `rows-by-month --append`/`sections-by-month --append` safe to
    re-run over the same source without growing the shard. Order of the
    surviving new lines is preserved; only duplicates are dropped.
    """
    shard_dir.mkdir(parents=True, exist_ok=True)
    skipped = 0
    for name, body in shards.items():
        path = shard_dir / f"{name}.md"
        already_present = path.exists()
        existing = path.read_text(encoding="utf-8").splitlines() if already_present else headers.get(name, [])
        if already_present:
            seen = set(existing)
            new_body: list[str] = []
            for ln in body:
                if ln in seen:
                    skipped += 1
                else:
                    new_body.append(ln)
                    seen.add(ln)
            body = new_body
        path.write_text("\n".join(existing + body).rstrip("\n") + "\n", encoding="utf-8")
    return skipped


def _reindex(index_file: Path, shard_dir: Path, pre: list[str] | None = None, rest: list[str] | None = None) -> None:
    lines = index_file.read_text(encoding="utf-8").splitlines()
    if pre is None:  # refresh only the ## Shards block, keep everything around it
        start = lines.index("## Shards")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        pre, tail = lines[:start], lines[end:]
    else:
        tail = ["## Not sharded (kept verbatim)", "", *rest] if rest and any(x.strip() for x in rest) else []
    names = sorted((p.stem for p in shard_dir.glob("*.md")), reverse=True)
    try:
        link_prefix = shard_dir.resolve().relative_to(index_file.resolve().parent).as_posix()
    except ValueError:
        link_prefix = shard_dir.name  # shard_dir isn't under the index file's directory
    out = [*pre, "", "## Shards", "", *[f"- [[{link_prefix}/{n}]]" for n in names], "", *tail]
    index_file.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


def _first_free_part(p: Path) -> int:
    """The first unused `-pNN` slot for this stem.

    Re-splitting is normal: a mid-month split leaves the live file in place and
    a later run splits what has accumulated since. Numbering from 1 every time
    overwrites the shards already on disk — and because the index links shards
    by name, every link keeps resolving afterwards, so the loss is silent.
    """
    i = 1
    while p.with_name(f"{p.stem}-p{i:02d}.md").exists():
        i += 1
    return i


def split_oversized(shard_dir: Path, max_bytes: int, header_lines: int,
                    index_file: Path | None = None) -> tuple[int, list[str]]:
    """Split every over-budget shard into `-pNN` parts.

    Returns (count, orphaned_stems). Each orphaned stem is a basename that used to be a
    file and no longer is: every wikilink resolving by that basename dangles until it is
    repointed. Splitting creates that debt silently unless it is reported, so callers
    must repoint the links (and re-run dangling-links.py) before committing.
    """
    split = 0
    orphaned: list[str] = []
    for p in sorted(shard_dir.glob("*.md")):
        if p.stat().st_size <= max_bytes:
            continue
        if index_file is not None and p.resolve() == index_file.resolve():
            # The index lives inside the shard dir. Splitting it would delete the very file
            # _reindex() reads next, leaving the mutation done and the command reporting failure.
            print(f"refusing to split {p} — it is the index file passed as `file`; "
                  f"pass the real index (e.g. the folder's parent .md) instead", file=sys.stderr)
            raise SystemExit(2)
        lines = p.read_text(encoding="utf-8").splitlines()
        head, body = lines[:header_lines], lines[header_lines:]
        base = sum(len(h.encode()) + 1 for h in head)
        chunks: list[list[str]] = []
        cur: list[str] = []
        size = base
        for ln in body:
            n = len(ln.encode()) + 1
            if cur and size + n > max_bytes - 2048:
                chunks.append(cur)
                cur, size = [], base
            cur.append(ln)
            size += n
        chunks.append(cur)
        for i, c in enumerate(chunks, _first_free_part(p)):
            p.with_name(f"{p.stem}-p{i:02d}.md").write_text("\n".join(head + c) + "\n", encoding="utf-8")
        p.unlink()
        orphaned.append(p.stem)
        split += 1
    return split, orphaned


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["rows-by-month", "sections-by-month", "sections-per-file", "split-oversized"])
    ap.add_argument("file", type=Path)
    ap.add_argument("shard_dir", type=Path)
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--pattern", default=r"Pattern #(\d+)")
    ap.add_argument("--max-bytes", type=int, default=51200)
    ap.add_argument("--header-lines", type=int, default=0)
    ap.add_argument("--append", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "split-oversized":
        if not a.file.exists():
            print(f"index file {a.file} does not exist — nothing would be reindexed", file=sys.stderr)
            return 2
        n, orphaned = split_oversized(a.shard_dir, a.max_bytes, a.header_lines, index_file=a.file)
        _reindex(a.file, a.shard_dir)
        print(f"{n} oversized shard(s) split")
        if orphaned:
            print("\n⚠️  REPOINT OWED — these basenames no longer exist, so every inbound")
            print("    [[wikilink]] to them is now dangling. Repoint them in THIS change:")
            for stem in orphaned:
                print(f"      • [[{stem}]]  →  its -pNN part")
            print("    Then confirm with: python3 scripts/dangling-links.py")
            return 3
        return 0
    if a.shard_dir.exists() and any(a.shard_dir.iterdir()) and not a.append:
        print(f"{a.shard_dir} is not empty — pass --append to add to existing shards", file=sys.stderr)
        return 2
    lines = a.file.read_text(encoding="utf-8").splitlines()
    if a.mode == "rows-by-month":
        pre, shards, rest, headers = rows_by_month(lines)
    else:
        per = re.compile(a.pattern) if a.mode == "sections-per-file" else None
        pre, shards, rest, headers = sections(lines, a.level, per)
    if not shards:
        print("nothing to shard", file=sys.stderr)
        return 1
    skipped = _write_shards(a.shard_dir, shards, headers)
    _reindex(a.file, a.shard_dir, pre, rest)
    msg = f"{len(shards)} shard(s) written to {a.shard_dir}"
    if skipped:
        msg += f" ({skipped} duplicate line(s) already present — skipped)"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
