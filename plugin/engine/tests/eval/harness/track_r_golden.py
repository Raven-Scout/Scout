"""Local-only Track R golden-set schema, public-repo guard, and vault candidate miner (Phase 2).

Real-vault golden sets contain personal and client data, so :func:`write_golden_set`
enforces owner-only permissions (``0700`` directory, ``0600`` JSONL file) and
refuses any destination inside a public repository checkout (``Raven-Scout/Scout``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scout import safe_write
from scout.kb.backend import FilesBackend

from .checks.track_r import TRACK_R_CATEGORIES

PLUGIN_REPO_ROOT = Path(__file__).resolve().parents[5]
PUBLIC_REPO_MARKERS = ("raven-scout/scout",)

_ACTION_DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(?:\[#([A-Za-z0-9_-]+)\]\s*)?(.+?)\s*$")
_HEADING_NUMBERED_RE = re.compile(r"^#{2,4}\s+(?:Pattern\s+|Rule\s+|#)?(\d+)[.:)—-]\s*(.+?)\s*$", re.IGNORECASE)
_RESOLVED_BULLET_RE = re.compile(r"^\s*-\s*\[[xX]\]\s+(.+?)\s*$")


@dataclass(frozen=True)
class GoldenItem:
    """One Track R evaluation question pinned to ``as_of_sha``."""

    id: str
    category: str
    question: str
    gold_answer: str
    gold_paths: tuple[str, ...]
    as_of_sha: str
    project: str = ""
    source_kind: str = "manual"
    exclude_paths: tuple[str, ...] = ()
    expected_tokens: tuple[str, ...] = ()
    audited: bool = False

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.id.strip():
            problems.append("missing id")
        if self.category not in TRACK_R_CATEGORIES:
            problems.append(f"unknown category {self.category!r}")
        if not self.question.strip():
            problems.append(f"{self.id}: empty question")
        if self.category != "abstention" and not self.gold_answer.strip():
            problems.append(f"{self.id}: empty gold_answer")
        if self.category != "abstention" and not self.gold_paths:
            problems.append(f"{self.id}: non-abstention item needs at least one gold_path")
        if not self.as_of_sha.strip():
            problems.append(f"{self.id}: missing as_of_sha")
        if "knowledge-base/scout-mistake-audit.md" in self.gold_paths:
            problems.append(f"{self.id}: mistake-audit file must be in exclude_paths, never gold_paths")
        return problems

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GoldenItem:
        return cls(
            id=str(raw["id"]),
            category=str(raw["category"]),
            question=str(raw["question"]),
            gold_answer=str(raw.get("gold_answer") or ""),
            gold_paths=tuple(str(p) for p in raw.get("gold_paths") or ()),
            as_of_sha=str(raw["as_of_sha"]),
            project=str(raw.get("project") or ""),
            source_kind=str(raw.get("source_kind") or "manual"),
            exclude_paths=tuple(str(p) for p in raw.get("exclude_paths") or ()),
            expected_tokens=tuple(str(t) for t in raw.get("expected_tokens") or ()),
            audited=bool(raw.get("audited", False)),
        )


def refuse_public_repo_destination(dest: Path) -> None:
    """Refuse writing real-vault Track R data inside the plugin repo or any Raven-Scout/Scout remote."""
    resolved = dest.expanduser().resolve()
    plugin_root = PLUGIN_REPO_ROOT.resolve()
    if resolved == plugin_root or resolved.is_relative_to(plugin_root):
        raise PermissionError(
            f"Refusing to write Track R golden data inside the plugin repository ({plugin_root}); "
            "use an owner-only local directory outside the repo."
        )
    probe_dir = resolved if resolved.is_dir() else resolved.parent
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    proc = subprocess.run(
        ["git", "-C", str(probe_dir), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        remote = proc.stdout.strip().lower()
        if any(marker in remote for marker in PUBLIC_REPO_MARKERS):
            raise PermissionError(f"Refusing to write Track R golden data inside public repo remote {remote!r}")


def write_golden_set(dest: Path, items: Iterable[GoldenItem]) -> Path:
    """Atomically write ``items`` as JSONL into an owner-only (``0700``/``0600``) local file."""
    refuse_public_repo_destination(dest)
    target = dest.expanduser().resolve()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    rows = list(items)
    for item in rows:
        errs = item.validate()
        if errs:
            raise ValueError("; ".join(errs))
    payload = "".join(json.dumps(item.to_json(), ensure_ascii=False) + "\n" for item in rows)
    safe_write.atomic_write_text(target, payload, mode=0o600)
    return target


def load_golden_set(src: Path) -> list[GoldenItem]:
    """Load and validate a JSONL Track R golden set."""
    items: list[GoldenItem] = []
    for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = GoldenItem.from_dict(json.loads(line))
        errs = item.validate()
        if errs:
            raise ValueError(f"{src}:{lineno}: {'; '.join(errs)}")
        items.append(item)
    return items


def summarize_golden_set(items: Sequence[GoldenItem]) -> dict[str, Any]:
    """Summary counts by category, source_kind, and audit status against Gate G1 power targets."""
    by_cat: dict[str, int] = dict.fromkeys(TRACK_R_CATEGORIES, 0)
    by_source: dict[str, int] = {}
    audited = 0
    for item in items:
        by_cat[item.category] = by_cat.get(item.category, 0) + 1
        by_source[item.source_kind] = by_source.get(item.source_kind, 0) + 1
        audited += int(item.audited)
    return {
        "total": len(items),
        "audited": audited,
        "by_category": by_cat,
        "by_source_kind": dict(sorted(by_source.items())),
        "meets_g1_targets": len(items) >= 200 and by_cat.get("multi_hop", 0) >= 70,
    }


def _vault_head_sha(vault: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else "working-tree"


def seed_candidates_from_vault(vault: Path, *, max_items: int = 220) -> list[GoldenItem]:
    """Mine deterministic candidate Track R items from a Scout vault's own artifacts and graph.

    Stratifies across all 7 pre-registered categories (allocating >=35% to ``multi_hop`` so a
    220+ item run hits the >=70 ``multi_hop`` Gate G1 power target alongside every other stratum).
    """
    root = vault.expanduser().resolve()
    sha = _vault_head_sha(root)
    fb = FilesBackend(root, "owner")
    buckets: dict[str, list[GoldenItem]] = {cat: [] for cat in TRACK_R_CATEGORIES}
    seq = 0

    def next_id(prefix: str) -> str:
        nonlocal seq
        seq += 1
        return f"{prefix}-{seq:04d}"

    # 1. Project status, multi-hop chains, and project/source citations
    all_entities = {str(e["name"]): e for e in fb.entities() if e.get("name") and (e.get("_label") or {}).get("path")}
    projects = {name: ent for name, ent in all_entities.items() if ent.get("type") == "project"}
    for proj_name, proj_ent in sorted(projects.items()):
        proj_path = str((proj_ent.get("_label") or {}).get("path") or "")
        proj_slug = Path(proj_path).parent.name if proj_path else proj_name
        status_val = str(proj_ent.get("status") or "").strip()
        if status_val and proj_path:
            buckets["status"].append(
                GoldenItem(
                    id=next_id("status"),
                    category="status",
                    question=f"What is the current recorded status of project '{proj_name}'?",
                    gold_answer=status_val,
                    gold_paths=(proj_path,),
                    as_of_sha=sha,
                    project=proj_slug,
                    source_kind="project_frontmatter",
                    expected_tokens=(status_val.split()[0],),
                )
            )

        backlinks = fb.backlinks(proj_path) if proj_path else []
        nbs = fb.neighbors(proj_name, depth=2, limit=16)
        # Group 2-hop endpoints by (mid_rel, end_rel) so questions do not leak mid_node
        hop_groups: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for nb in nbs:
            steps = nb.get("path") or []
            nb_path = str((nb.get("_label") or {}).get("path") or "")
            if len(steps) >= 2 and nb_path and proj_path:
                mid_rel, mid_node = str(steps[0][0]), str(steps[0][1])
                end_rel, end_node = str(steps[-1][0]), str(steps[-1][1])
                if end_node == proj_name:
                    continue
                mid_ent = all_entities.get(mid_node) or {}
                mid_path = str((mid_ent.get("_label") or {}).get("path") or "")
                hop_groups.setdefault((mid_rel, end_rel), []).append((mid_path, end_node, nb_path))

        for (mid_rel, end_rel), targets in sorted(hop_groups.items()):
            seen_ends: dict[str, tuple[str, str]] = {}
            for mid_path, end_node, nb_path in targets:
                seen_ends.setdefault(end_node, (mid_path, nb_path))
            if len(seen_ends) == 1:
                end_node, (mid_path, nb_path) = next(iter(seen_ends.items()))
                gpaths = tuple(dict.fromkeys(p for p in (proj_path, mid_path, nb_path) if p))
                mid_phrase = mid_rel.replace("_", " ")
                end_phrase = end_rel.replace("_", " ")
                buckets["multi_hop"].append(
                    GoldenItem(
                        id=next_id("mh"),
                        category="multi_hop",
                        question=(
                            f"Starting from project '{proj_name}', follow its '{mid_phrase}' relationship "
                            f"and identify which entity that neighbor connects to via '{end_phrase}'."
                        ),
                        gold_answer=end_node,
                        gold_paths=gpaths,
                        as_of_sha=sha,
                        project=proj_slug,
                        source_kind="multi_hop_graph",
                        expected_tokens=(end_node,),
                    )
                )

        for bl in backlinks[:4]:
            bsrc = str(bl.get("source") or "")
            if bsrc.startswith("knowledge-base/sources/") and proj_path:
                q_text = f"Which source note in knowledge-base/sources/ directly links to project '{proj_name}'?"
                buckets["citation"].append(
                    GoldenItem(
                        id=next_id("cite"),
                        category="citation",
                        question=q_text,
                        gold_answer=bsrc,
                        gold_paths=(bsrc, proj_path),
                        as_of_sha=sha,
                        project=proj_slug,
                        source_kind="source_backlink",
                        expected_tokens=(Path(bsrc).stem,),
                    )
                )

        # Mine deep-dive knowledge/ notes under each project folder for citation grounding
        kn_dir = root / "knowledge-base" / "projects" / proj_slug / "knowledge"
        if kn_dir.is_dir() and proj_path:
            for kn_file in sorted(kn_dir.glob("*.md"))[:2]:
                kn_rel = kn_file.relative_to(root).as_posix()
                kn_lines = kn_file.read_text(encoding="utf-8", errors="replace").splitlines()
                title = next((ln.lstrip("# ").strip() for ln in kn_lines if ln.startswith("# ")), kn_file.stem)
                if title:
                    buckets["citation"].append(
                        GoldenItem(
                            id=next_id("cite-kn"),
                            category="citation",
                            question=(
                                f"Which knowledge deep-dive note under project '{proj_name}' "
                                f"documents '{title}', and cite its vault path?"
                            ),
                            gold_answer=kn_rel,
                            gold_paths=(kn_rel, proj_path),
                            as_of_sha=sha,
                            project=proj_slug,
                            source_kind="project_knowledge_citation",
                            expected_tokens=(kn_file.stem,),
                        )
                    )

    # 2. Single-hop & entity disambiguation (aliases)
    for ent in sorted(fb.entities(), key=lambda e: str(e.get("name") or "")):
        ename = str(ent.get("name") or "")
        epath = str((ent.get("_label") or {}).get("path") or "")
        if not ename or not epath:
            continue
        aliases = [str(a).strip() for a in (ent.get("aliases") or []) if str(a).strip()]
        if aliases:
            buckets["disambiguation"].append(
                GoldenItem(
                    id=next_id("disamb"),
                    category="disambiguation",
                    question=f"Which canonical KB entity corresponds to the alias '{aliases[0]}'?",
                    gold_answer=ename,
                    gold_paths=(epath,),
                    as_of_sha=sha,
                    source_kind="entity_alias",
                    expected_tokens=(ename,),
                )
            )
        role = str(ent.get("role") or "").strip()
        if ent.get("type") == "person" and role:
            buckets["single_hop"].append(
                GoldenItem(
                    id=next_id("sh"),
                    category="single_hop",
                    question=f"What role is recorded on {ename}'s person note?",
                    gold_answer=role,
                    gold_paths=(epath,),
                    as_of_sha=sha,
                    source_kind="person_role",
                    expected_tokens=(role,),
                )
            )

    # 3. Resolved review-queue items & mistake-audit regressions
    rq_path = root / "knowledge-base" / "review-queue.md"
    if rq_path.is_file():
        for line in rq_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RESOLVED_BULLET_RE.match(line)
            if m:
                summary = m.group(1).strip()[:180]
                buckets["abstention"].append(
                    GoldenItem(
                        id=next_id("rq"),
                        category="abstention",
                        question=f"Before resolution, did the KB confirm: {summary}?",
                        gold_answer="",
                        gold_paths=(),
                        as_of_sha=sha,
                        source_kind="review_queue",
                    )
                )

    audit_rel = "knowledge-base/scout-mistake-audit.md"
    audit_path = root / audit_rel
    if audit_path.is_file():
        for line in audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
            hm = _HEADING_NUMBERED_RE.match(line)
            if hm:
                num, title = hm.group(1), hm.group(2).strip()
                buckets["abstention"].append(
                    GoldenItem(
                        id=next_id(f"audit-{num}"),
                        category="abstention",
                        question=f"Regression check #{num}: verify unconfirmed claim handling for '{title}'.",
                        gold_answer="",
                        gold_paths=(),
                        as_of_sha=sha,
                        source_kind="mistake_audit",
                        exclude_paths=(audit_rel,),
                    )
                )

    # 4. Completed action items with date evidence (temporal)
    ai_dir = root / "action-items"
    if ai_dir.is_dir():
        for daily in sorted(ai_dir.glob("action-items-*.md"), reverse=True)[:15]:
            rel_daily = daily.relative_to(root).as_posix()
            day_str = daily.stem.removeprefix("action-items-")
            for line in daily.read_text(encoding="utf-8", errors="replace").splitlines():
                am = _ACTION_DONE_RE.match(line)
                if am and am.group(1):
                    tag, body = am.group(1), am.group(2)[:120]
                    buckets["temporal"].append(
                        GoldenItem(
                            id=next_id(f"temp-{tag.lower()}"),
                            category="temporal",
                            question=f"On which daily action-items date was item [#{tag}] ({body}) marked complete?",
                            gold_answer=day_str,
                            gold_paths=(rel_daily,),
                            as_of_sha=sha,
                            source_kind="action_item",
                            expected_tokens=(day_str,),
                        )
                    )

    quotas = {
        "multi_hop": max(1, int(max_items * 0.40)),
        "status": max(1, int(max_items * 0.12)),
        "citation": max(1, int(max_items * 0.12)),
        "temporal": max(1, int(max_items * 0.10)),
        "single_hop": max(1, int(max_items * 0.10)),
        "disambiguation": max(1, int(max_items * 0.08)),
        "abstention": max(1, int(max_items * 0.08)),
    }
    selected: list[GoldenItem] = []
    leftover: list[GoldenItem] = []
    for cat in TRACK_R_CATEGORIES:
        cap = quotas[cat]
        selected.extend(buckets[cat][:cap])
        leftover.extend(buckets[cat][cap:])
    if len(selected) < max_items:
        selected.extend(leftover[: max_items - len(selected)])
    return selected[:max_items]
