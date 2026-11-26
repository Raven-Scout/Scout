"""Three-arm backend evaluation axis for ``eval.sh compare --arms`` (Phase 2).

Compares identical git checkouts, models, and connector inputs across three KB
integration strategies:

- **Arm A (``a`` / ``files``):** Today's ``scoutctl kb`` surface over ``FilesBackend``.
- **Arm B (``b`` / ``relate-verbs``):** ``RelateBackend`` plus opt-in phase guidance
  for model-facing graph verbs (``kb neighbors``, ``kb paths``, ``kb backlinks``,
  ``kb link-gaps``, ``kb search --ranked``).
- **Arm C (``c`` / ``relate-precompute``):** ``RelateBackend`` with engine-side
  ``graph_context`` precompute in ``session-context.json`` and no new phase verbs.
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
    '(optional `--project "<project>"`) for full-text ranked note retrieval.\n'
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
    brain_overlay=None,
)

KNOWN_ARMS: dict[str, ArmSpec] = {
    "a": ARM_A,
    "files": ARM_A,
    "b": ARM_B,
    "relate-verbs": ARM_B,
    "c": ARM_C,
    "relate-precompute": ARM_C,
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
