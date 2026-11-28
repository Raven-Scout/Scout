"""Three-arm backend evaluation axis for ``eval.sh compare --arms`` (Phase 2).

Compares identical git checkouts, models, and connector inputs across three KB
integration strategies:

- **Arm A (``a`` / ``files``):** Today's ``scoutctl kb`` surface over ``FilesBackend``.
- **Arm B (``b`` / ``relate-verbs``):** ``RelateBackend`` plus opt-in phase guidance
  for model-facing graph verbs (``kb neighbors``, ``kb paths``, ``kb backlinks``,
  ``kb link-gaps``, ``kb search --ranked``).
- **Arm C (``c`` / ``relate-precompute``):** ``RelateBackend`` with engine-side
  ``graph_context`` precompute in ``session-context.json`` and no new phase verbs.

Three more arms separate "the model didn't use the graph" from "the graph didn't help"
(the 2026-11-28 eval plan, after the 30-item pilot tied):

- **Arm R (``r`` / ``files-ranked``):** ``FilesBackend`` plus only ``kb search --ranked``. The
  pilot's whole Arm B gain came from ranked search, which needs no ``relate-db``.
- **Arm BF (``bf`` / ``relate-verbs-forced``):** Arm B, told to call the matching graph verb
  before any other KB read: the ceiling when uptake is not the limit.
- **Arm O (``o`` / ``files-oracle``):** ``FilesBackend`` with the item's gold notes named in the
  prompt: if even this doesn't beat Arm A, retrieval is not the bottleneck.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ArmSpec:
    """Configuration and brain-overlay specification for one backend eval arm."""

    id: str
    slug: str
    description: str
    backend_kind: str
    precompute_graph_context: bool
    brain_overlay: str | None = None
    oracle: bool = False


ARM_B_BRAIN_OVERLAY = (
    "## Graph-Backed KB Lookup\n\n"
    "When answering multi-hop questions across people, projects, decisions, and sources, "
    "prefer the graph-backed `scripts/scoutctl kb` read verbs over repeated broad searches:\n\n"
    '- `scripts/scoutctl kb neighbors "<entity>" --depth 2` '
    "(`--rels works_on,owns` and `--node-types person,project`) for multi-hop typed neighbourhoods.\n"
    '- `scripts/scoutctl kb paths "<source>" "<target>" --hops 4 --k 3` '
    "for shortest typed paths between two entities.\n"
    "- `scripts/scoutctl kb backlinks <vault-path>` "
    "for visible notes whose markdown body links to `<vault-path>`, with section and line counts.\n"
    "- `scripts/scoutctl kb link-gaps --scope knowledge-base/projects/<slug>/` "
    "for body-linked entity pairs missing a typed frontmatter edge.\n"
    '- `scripts/scoutctl kb search "<terms>" --ranked` '
    '(optional `--project "<project>"`) for full-text ranked note retrieval.\n\n'
    "Use them at the step that needs them:\n\n"
    "- Meeting prep: `scripts/scoutctl kb neighbors` on each attendee (`--depth 2`) to find the "
    "projects, decisions and open threads they share.\n"
    "- Propagating a decision: `scripts/scoutctl kb backlinks` on the notes of each entity the "
    "decision touches, so every note that should change is found.\n"
    "- Missing links and orphans: `scripts/scoutctl kb link-gaps` for the scope you are checking.\n"
    "- Conflicting claims: `scripts/scoutctl kb neighbors` on the entity to list every note about "
    "it, then compare what each says and its `as_of` date.\n"
    '- "What does this affect": `scripts/scoutctl kb neighbors "<entity>" --rels '
    "depends_on,dependency_of,blocks,blocked_by` and `scripts/scoutctl kb paths` between the two ends.\n"
)

ARM_BF_BRAIN_OVERLAY = (
    ARM_B_BRAIN_OVERLAY + "\nBefore any other knowledge-base read, call the graph verb above that matches the step, "
    "and start from its results.\n"
)

ARM_R_BRAIN_OVERLAY = (
    "## Ranked KB Lookup\n\n"
    '- `scripts/scoutctl kb search "<terms>" --ranked` '
    '(optional `--project "<project>"`) returns the best-matching notes first, instead of in '
    "path order. Prefer it over plain `kb search` when looking for notes about a topic.\n"
)

ARM_C_BRAIN_OVERLAY = (
    "## Precomputed Graph Context\n\n"
    "`.scout-cache/session-context.json` has a `graph_context` key: for each active project, "
    "the people linked to it, its best-ranked notes and backlinks, and links that are missing. "
    "Read it before meeting prep, before propagating a decision, and before looking for missing "
    "links or conflicting claims, and start from the notes it lists.\n"
)

ARM_A = ArmSpec(
    id="a",
    slug="files",
    description="FilesBackend baseline (today's scoutctl kb surface)",
    backend_kind="files",
    precompute_graph_context=False,
    brain_overlay=None,
)

ARM_B = ArmSpec(
    id="b",
    slug="relate-verbs",
    description="RelateBackend + model-facing graph verbs in brain files",
    backend_kind="relate",
    precompute_graph_context=False,
    brain_overlay=ARM_B_BRAIN_OVERLAY,
)

ARM_C = ArmSpec(
    id="c",
    slug="relate-precompute",
    description="RelateBackend + engine graph_context precompute in session-context.json",
    backend_kind="relate",
    precompute_graph_context=True,
    brain_overlay=ARM_C_BRAIN_OVERLAY,
)

ARM_R = ArmSpec(
    id="r",
    slug="files-ranked",
    description="FilesBackend + kb search --ranked only (no relate-db)",
    backend_kind="files",
    precompute_graph_context=False,
    brain_overlay=ARM_R_BRAIN_OVERLAY,
)

ARM_BF = ArmSpec(
    id="bf",
    slug="relate-verbs-forced",
    description="RelateBackend + graph verbs, called before any other KB read",
    backend_kind="relate",
    precompute_graph_context=False,
    brain_overlay=ARM_BF_BRAIN_OVERLAY,
)

ARM_O = ArmSpec(
    id="o",
    slug="files-oracle",
    description="FilesBackend with the item's gold notes named in the prompt (retrieval ceiling)",
    backend_kind="files",
    precompute_graph_context=False,
    brain_overlay=None,
    oracle=True,
)

KNOWN_ARMS: dict[str, ArmSpec] = {
    "a": ARM_A,
    "files": ARM_A,
    "b": ARM_B,
    "relate-verbs": ARM_B,
    "c": ARM_C,
    "relate-precompute": ARM_C,
    "r": ARM_R,
    "files-ranked": ARM_R,
    "bf": ARM_BF,
    "relate-verbs-forced": ARM_BF,
    "o": ARM_O,
    "files-oracle": ARM_O,
}

BRAIN_FILES = ("SKILL.md", "DREAMING.md", "RESEARCH.md", "RESPOND.md")


def parse_arms(raw: str) -> tuple[ArmSpec, ...]:
    """Parse a comma-separated ``--arms`` specification (e.g. ``a,b,c``)."""
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if len(tokens) < 2:
        raise ValueError("--arms requires at least two comma-separated arms (e.g. a,b,c)")
    out: list[ArmSpec] = []
    seen_ids: set[str] = set()
    for tok in tokens:
        spec = KNOWN_ARMS.get(tok)
        if spec is None:
            allowed = ", ".join(sorted(KNOWN_ARMS))
            raise ValueError(f"unknown arm {tok!r} (valid arms: {allowed})")
        if spec.id in seen_ids:
            raise ValueError(f"duplicate arm {tok!r} (resolves to arm {spec.id!r})")
        seen_ids.add(spec.id)
        out.append(spec)
    return tuple(out)


def apply_arm(vault: Path, arm: ArmSpec) -> None:
    """Apply ``arm``'s ``kb`` config block and optional brain-file overlay to ``vault``."""
    cfg_path = vault / "scout-config.yaml"
    existing_text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    parsed = yaml.safe_load(existing_text) or {} if existing_text else {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{cfg_path}: expected a YAML mapping")

    kb_block = dict(parsed.get("kb") or {}) if isinstance(parsed.get("kb"), dict) else {}
    backend_entry: dict[str, str] = {"kind": arm.backend_kind}
    if arm.backend_kind == "relate":
        backend_entry["version"] = "1.3.x"
    kb_block["backend"] = backend_entry
    kb_block["precompute_graph_context"] = arm.precompute_graph_context

    if "kb" in parsed:
        parsed["kb"] = kb_block
        cfg_path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")
    else:
        snippet = yaml.safe_dump({"kb": kb_block}, sort_keys=False).rstrip("\n")
        sep = "\n\n" if existing_text.strip() else ""
        cfg_path.write_text(existing_text.rstrip("\n") + sep + snippet + "\n", encoding="utf-8")

    if arm.brain_overlay:
        overlay_text = arm.brain_overlay.strip() + "\n"
        for name in BRAIN_FILES:
            target = vault / name
            if target.is_file():
                current = target.read_text(encoding="utf-8").rstrip("\n")
                target.write_text(current + "\n\n" + overlay_text, encoding="utf-8")
