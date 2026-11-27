"""Part 2 of layout v2: the space review (spec 2026-11-26 §5).

The layout move files most knowledge in the default space by rule. :func:`space_candidates` lists the notes
that sit there only by that rule and show a signal against it; an interactive session judges them with the
user, and each approved row runs :func:`move_to_space` (``scoutctl vault move``). Nothing here runs a model.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout import config as scout_config
from scout import layout, safe_write
from scout.layout_migrate import split_frontmatter
from scout.layout_rewrite import Ref, rewrite

KB = "knowledge-base"
_FAMILY_TYPES = frozenset({"family_of", "partner_of"})
_WIKI = re.compile(r"\[\[([^\]|#\\]+)")


class SpaceMoveError(RuntimeError):
    pass


@dataclass
class Candidate:
    path: str
    space: str  # where it is now
    signals: list[str] = field(default_factory=list)
    suggested: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "space": self.space, "signals": list(self.signals), "suggested": self.suggested}


def _front(path: Path) -> dict[str, Any]:
    try:
        fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        data = yaml.safe_load(fm) if fm else None
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _space_of_link(target: str, keys: set[str]) -> str | None:
    first = target.strip().split("/", 1)[0]
    return first if first in keys else None


def space_candidates(vault: Path) -> list[Candidate]:
    """Notes in the default space with a signal against it (§5 step 1): ``domain:`` naming another space,
    links that mostly point into one other space, a family relationship, a meeting series whose attendees have
    no role note in the default space. Read-only; names, spaces and signal kinds only, never note text."""
    if layout.layout_version(vault) != 2:
        return []
    spaces = scout_config.load_spaces(vault)
    default = scout_config.default_space(spaces).key
    keys = {s.key for s in spaces}
    root = vault / layout.space_dir(default)
    out: list[Candidate] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        fm = _front(path)
        cand = Candidate(rel, default)
        domain = fm.get("domain")
        if isinstance(domain, str) and domain.strip().lower() in keys - {default}:
            cand.signals.append(f"domain:{domain.strip().lower()}")
            cand.suggested = domain.strip().lower()
        rels = fm.get("relationships")
        if isinstance(rels, list) and any(isinstance(r, dict) and r.get("type") in _FAMILY_TYPES for r in rels):
            cand.signals.append("family-relationship")
            cand.suggested = cand.suggested or ("personal" if "personal" in keys else None)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        counts = Counter(s for t in _WIKI.findall(text) if (s := _space_of_link(t, keys)))
        if counts:
            top, n = counts.most_common(1)[0]
            if top != default and n > sum(counts.values()) / 2:
                cand.signals.append(f"links-mostly:{top}")
                cand.suggested = cand.suggested or top
        if cand.signals:
            out.append(cand)
    return out


def _git(vault: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SpaceMoveError(f"git {args[0]}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def _target(rel: str, default_or_from: str, to: str) -> str:
    prefix = f"{KB}/{default_or_from}/"
    if not rel.startswith(prefix):
        raise SpaceMoveError(f"{rel} is not in the {default_or_from} space")
    rest = rel[len(prefix) :]
    parts = rest.split("/")
    # A role note is renamed for its new space: people/<n>-<from>.md -> people/<n>-<to>.md
    if len(parts) == 2 and parts[0] in ("people", "organizations") and parts[1].endswith(f"-{default_or_from}.md"):
        parts[1] = parts[1][: -len(f"-{default_or_from}.md")] + f"-{to}.md"
    return f"{KB}/{to}/" + "/".join(parts)


def move_to_space(vault: Path, rel: str, space: str, *, commit: bool = True) -> dict[str, Any]:
    """Move one note or meeting series to ``space`` (§5 step 3): ``git mv``, the path rewrite with a one-entry
    move list over current files, the role note's ``space:`` and its identity's ``spaces:``, and the meetings
    registry's Space cell for a series. Returns what moved and what was rewritten."""
    if layout.layout_version(vault) != 2:
        raise SpaceMoveError("this vault isn't on layout v2 yet; run `scoutctl bootstrap upgrade` first")
    spaces = scout_config.load_spaces(vault)
    keys = {s.key for s in spaces}
    if space not in keys:
        raise SpaceMoveError(f"{space!r} is not a registered space ({', '.join(sorted(keys))})")
    rel = rel.strip("/")
    parts = rel.split("/")
    if len(parts) < 3 or parts[0] != KB or parts[1] not in keys:
        raise SpaceMoveError(f"{rel} is not inside a space folder (knowledge-base/<space>/...)")
    src_space = parts[1]
    if src_space == space:
        raise SpaceMoveError(f"{rel} is already in {space}")
    if not (vault / rel).exists():
        raise SpaceMoveError(f"{rel} doesn't exist")
    dst = _target(rel, src_space, space)
    if (vault / dst).exists():
        raise SpaceMoveError(f"{dst} already exists")
    (vault / dst).parent.mkdir(parents=True, exist_ok=True)
    _git(vault, "mv", rel, dst)
    is_dir = (vault / dst).is_dir()
    refs = [Ref(rel, dst, is_dir)]
    if not is_dir and rel.endswith(".md"):
        refs = [Ref(rel, dst, False)]
    rewritten: list[str] = []
    from types import SimpleNamespace

    from scout.layout_migrate import _is_text_file, rewrite_targets  # noqa: PLC0415 - shared target list

    today = scout_config.today(vault).isoformat()
    view = SimpleNamespace(spaces=tuple(spaces))  # rewrite_targets reads only the plan's spaces
    for target_rel, kind in rewrite_targets(vault, view, today):  # type: ignore[arg-type]
        p = vault / target_rel
        text = _is_text_file(p)
        if text is None:
            continue
        new = rewrite(text, refs, kind=kind, space=space, spaces=sorted(keys))
        if new != text:
            safe_write.atomic_write_text(p, new)
            rewritten.append(target_rel)
    # A role note: its space, and its identity's spaces.
    if not is_dir and dst.split("/")[2] in ("people", "organizations"):
        text = (vault / dst).read_text(encoding="utf-8")
        new = re.sub(r"^space:\s*\S+\s*$", f"space: {space}", text, count=1, flags=re.M)
        if new != text:
            safe_write.atomic_write_text(vault / dst, new)
        fm = _front(vault / dst)
        role_of = str(fm.get("role_of") or "")
        m = _WIKI.search(role_of)
        if m:
            identity = vault / KB / f"{m.group(1).strip()}.md"
            if identity.is_file():
                itext = identity.read_text(encoding="utf-8")
                ifm = _front(identity)
                raw_spaces = ifm.get("spaces")
                have: list[Any] = raw_spaces if isinstance(raw_spaces, list) else []
                want = sorted({str(x) for x in have if x != src_space} | {space})
                inew = re.sub(r"^spaces:.*$", f"spaces: [{', '.join(want)}]", itext, count=1, flags=re.M)
                if inew != itext:
                    safe_write.atomic_write_text(identity, inew)
                    rewritten.append(identity.relative_to(vault).as_posix())
    # A meeting series: its registry row's Space cell.
    if is_dir and dst.split("/")[2] == "meetings":
        reg = vault / layout.V2["meetings_registry"]
        slug = dst.rstrip("/").split("/")[-1]
        if reg.is_file():
            text = reg.read_text(encoding="utf-8")
            # The link was rewritten above; the row's Space cell follows it.
            new = re.sub(
                rf"(\[\[{re.escape(space)}/meetings/{re.escape(slug)}/[^\]]*\]\] \| ){re.escape(src_space)}( \|)",
                rf"\g<1>{space}\g<2>",
                text,
            )
            if new != text:
                safe_write.atomic_write_text(reg, new)
                rewritten.append(reg.relative_to(vault).as_posix())
    sha = ""
    if commit:
        # Only what this move touched: an edit the app, Obsidian or a session made meanwhile stays out of the commit.
        paths = sorted({dst, *rewritten})
        _git(vault, "add", "-A", "--", *paths)
        msg = f"scout: move {rel} to the {space} space"
        _git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg, "--", rel, *paths)
        sha = _git(vault, "rev-parse", "HEAD").strip()
    (vault / layout.REVIEW_PENDING_REL).unlink(missing_ok=True)  # a review has run
    return {"from": rel, "to": dst, "rewritten": sorted(set(rewritten)), "commit": sha}
