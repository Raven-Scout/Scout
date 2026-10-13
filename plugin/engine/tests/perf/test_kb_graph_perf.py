"""Budget test for KnowledgeGraph.load() on a mature-vault-sized KB (#77).

CI asserts the work each load does, not how long it takes:
- a cold load parses every file;
- a warm load (same process, any instance) parses none;
- after a handful of edits, only those files are parsed again.

The graph a warm load builds must equal the cold one. The wall-clock ceilings are
generous and only catch a gross slowdown on a slow runner. Measured on an M-series
Mac with this fixture: cold ~1.0 s, warm ~0.03 s (before #77 every load was ~1.0 s).

The fixture is generated stand-in content shaped like a mature vault's KB: 3,000
notes across a few folders, most with frontmatter and 1–4 wikilink relationships,
one in seven without frontmatter, bodies of 1–6 KB.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any

import pytest

from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache

pytestmark = pytest.mark.perf

FILES = 3000
EDITED = 5
COLD_CEILING_S = 30.0
WARM_CEILING_S = 5.0
SCHEMA = (
    "entity_types:\n"
    "  person: {properties: {required: [name, type]}}\n"
    "  project: {properties: {required: [name, type]}}\n"
    "  task: {properties: {required: [name, type]}}\n"
    "relationship_types:\n"
    "  works_with: {inverse: works_with}\n"
)


def _write_kb(root: Path, n: int) -> list[Path]:
    rnd = random.Random(77)
    types = ["person", "project", "task"]
    folders = ["people", "projects", "tasks", "notes", "work/areas", "personal"]
    settled = time.time() - 86_400  # KB files are mostly old; fresh ones skip the cache by design
    files: list[Path] = []
    for i in range(n):
        folder = root / folders[i % len(folders)]
        folder.mkdir(parents=True, exist_ok=True)
        rels = "".join(
            f'  - type: works_with\n    target: "[[Entity {rnd.randrange(n)}]]"\n' for _ in range(rnd.randint(1, 4))
        )
        fm = (
            ""
            if i % 7 == 0
            else f"---\nname: Entity {i}\ntype: {types[i % 3]}\nstatus: open\npriority: p{i % 3}\n"
            f"domain: work\nrelationships:\n{rels}---\n"
        )
        body = "Stand-in text for a knowledge-base note. " * rnd.randint(25, 150)
        path = folder / f"entity-{i:05d}.md"
        path.write_text(f"{fm}# Entity {i}\n\n{body}\n", encoding="utf-8")
        os.utime(path, (settled, settled))
        files.append(path)
    return files


def test_kb_graph_load_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_frontmatter_cache()
    schema = tmp_path / "schema.yaml"
    schema.write_text(SCHEMA, encoding="utf-8")
    root = tmp_path / "knowledge-base"
    files = _write_kb(root, FILES)

    parsed: list[Path] = []
    real = KnowledgeGraph._extract_frontmatter

    def counting(self: KnowledgeGraph, path: Path) -> dict[str, Any] | None:
        parsed.append(path)
        return real(self, path)

    monkeypatch.setattr(KnowledgeGraph, "_extract_frontmatter", counting)

    def load() -> tuple[KnowledgeGraph, float]:
        parsed.clear()
        start = time.perf_counter()
        graph = KnowledgeGraph(str(schema), str(root)).load()
        return graph, time.perf_counter() - start

    cold, cold_s = load()
    assert len(parsed) == FILES
    assert len(cold.entities) == FILES - len(range(0, FILES, 7))

    warm, warm_s = load()
    assert parsed == []
    assert warm.export_json() == cold.export_json()

    later = time.time() - 3_600
    for path in files[:EDITED]:
        path.write_text(path.read_text(encoding="utf-8") + "Edited.\n", encoding="utf-8")
        os.utime(path, (later, later))
    _, edited_s = load()
    assert sorted(parsed) == sorted(files[:EDITED])

    print(f"\nKB graph load, {FILES} files: cold {cold_s:.3f}s, warm {warm_s:.3f}s, {EDITED} edited {edited_s:.3f}s")
    assert cold_s < COLD_CEILING_S
    assert warm_s < WARM_CEILING_S
    assert edited_s < WARM_CEILING_S
