"""The KB-writing release gate: score how a run wrote one project's pages, and report it as JSON.

``plugin/scripts/eval.sh kb-gate`` runs ONE consolidation session of the ``kb-front-page``
scenario on the maintainer's own Claude login and fails the release when the run broke the KB
page standard on the project's front page (checks/kb_writing.py). ``eval.sh kb-score <vault>``
scores a vault that already exists, such as a kept result dir, and starts no session.

"Before" is the vault's fixture commit (its root commit); "after" is the working tree, so a run's
uncommitted edits count too. Reading is here; the checks themselves are pure.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scout.kb.structure import Marker, load_structure_config, structure_rows

from .checks import CheckResult
from .checks.kb_writing import (
    KB_CHECKS,
    SOURCE_ROOTS,
    KbFact,
    front_page_bullets,
    front_page_growth,
    front_page_no_narration,
    kb_facts_cited,
    kb_structure,
    source_keys,
)
from .metrics import SampleMetrics, ToolCall
from .scenario import Scenario

GATE = "kb-writing"
GATE_SCENARIO = "kb-front-page"
REPORT_NAME = "kb-gate-report.json"


@dataclass(frozen=True)
class KbSpec:
    project: str
    max_growth: int
    facts: tuple[KbFact, ...]
    entry_markers: tuple[Marker, ...] = ()


def spec_for(scenario: Scenario) -> KbSpec:
    return KbSpec(
        scenario.kb_project, scenario.max_front_page_growth, scenario.expected_kb_facts, scenario.front_page_narration
    )


@dataclass(frozen=True)
class KbPages:
    front: str | None
    knowledge: dict[str, str]  # vault-relative path → text, for each note under <project>/knowledge/


class KbReadError(Exception):
    """git couldn't say what the fixture held; never read as "the fixture was empty"."""


def _git(vault: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise KbReadError(f"git {' '.join(args)}: exit {done.returncode}: {done.stderr.strip()[:200]}")
    return done.stdout


def _folder(vault: Path, project: str) -> str:
    return f"{load_structure_config(vault).projects_dir}/{project}"


def pages_at_fixture(vault: Path, project: str) -> KbPages:
    roots = _git(vault, "rev-list", "--max-parents=0", "HEAD").split()
    if not roots:
        raise KbReadError(f"{vault}: no fixture commit")
    folder = _folder(vault, project)
    listed = _git(vault, "ls-tree", "-r", "--name-only", roots[0], "--", folder).splitlines()
    front_rel = f"{folder}/{project}.md"
    front = _git(vault, "show", f"{roots[0]}:{front_rel}") if front_rel in listed else None
    notes = {
        rel: _git(vault, "show", f"{roots[0]}:{rel}")
        for rel in listed
        if rel.startswith(f"{folder}/knowledge/") and rel.endswith(".md")
    }
    return KbPages(front, notes)


def pages_now(vault: Path, project: str) -> KbPages:
    folder = vault / _folder(vault, project)
    front = folder / f"{project}.md"
    knowledge = folder / "knowledge"
    notes = (
        {p.relative_to(vault).as_posix(): p.read_text(encoding="utf-8") for p in sorted(knowledge.rglob("*.md"))}
        if knowledge.is_dir()
        else {}
    )
    return KbPages(front.read_text(encoding="utf-8") if front.is_file() else None, notes)


def source_notes(vault: Path) -> frozenset[str]:
    """Vault-relative paths of the notes a citation may link (``SOURCE_ROOTS``), as they are now."""
    roots = [vault / root for root in SOURCE_ROOTS if (vault / root).is_dir()]
    return frozenset(p.relative_to(vault).as_posix() for root in roots for p in root.rglob("*.md"))


def structure_row(vault: Path, project: str) -> dict[str, Any] | None:
    return next((r for r in structure_rows(vault) if r["project"] == project), None)


def score_kb(vault: Path, spec: KbSpec, names: Sequence[str] = KB_CHECKS) -> tuple[CheckResult, ...]:
    """The KB checks named in ``names``, in that order. A fixture git can't read makes every
    check unevaluated (``passed`` None), never a pass."""
    try:
        before = pages_at_fixture(vault, spec.project)
    except KbReadError as exc:
        return tuple(CheckResult(n, None, (f"fixture unreadable: {exc}",)) for n in names)
    after = pages_now(vault, spec.project)
    cfg = load_structure_config(vault)
    run = {
        "kb_structure": lambda: kb_structure(structure_row(vault, spec.project), spec.project),
        "front_page_growth": lambda: front_page_growth(before.front, after.front, spec.max_growth),
        "kb_facts_cited": lambda: kb_facts_cited(
            before.knowledge,
            after.knowledge,
            spec.facts,
            source_keys(source_notes(vault)),
            front_before=before.front,
            front_after=after.front,
        ),
        "front_page_no_narration": lambda: front_page_no_narration(
            before.front, after.front, cfg.narration_markers, spec.entry_markers
        ),
        "front_page_bullets": lambda: front_page_bullets(
            before.front, after.front, cfg.max_bullet_chars, cfg.bullet_sections
        ),
    }
    return tuple(run[n]() for n in names)


def checkout_state(root: Path) -> dict[str, Any]:
    """The code a gate run tested: HEAD, its branch, and whether the tree had changes git doesn't
    have (``dirty``), so a pasted report says which commit it cleared. ``None`` values when git can't say."""

    def ask(*args: str) -> str | None:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else None

    status = ask("status", "--porcelain")
    return {
        "commit": ask("rev-parse", "HEAD"),
        "branch": ask("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": None if status is None else bool(status),
    }


def _bytes(text: str | None) -> int | None:
    return None if text is None else len(text.encode("utf-8"))


WRITE_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
DENIED_INPUT_CHARS = 300


def commits_since_fixture(vault: Path) -> int | None:
    """Commits on HEAD past the vault's fixture (root) commit; None when git can't say."""
    try:
        roots = _git(vault, "rev-list", "--max-parents=0", "HEAD").split()
        return int(_git(vault, "rev-list", "--count", f"{roots[0]}..HEAD").strip()) if roots else None
    except (KbReadError, ValueError):
        return None


def _denied(call: ToolCall) -> dict[str, str]:
    shown = call.input.get("command") or call.input.get("file_path") or json.dumps(call.input, sort_keys=True)
    return {"tool": call.name, "input": str(shown)[:DENIED_INPUT_CHARS]}


def void_run(metrics: SampleMetrics | None, commits: int | None, pages_changed: bool | None) -> dict[str, Any] | None:
    """Why a session that finished did nothing, so the gate tested no KB writing; None when it did something.

    Nothing means all three: no commit past the fixture, no Edit/Write that went through, and the project's
    pages as the fixture left them. A run that tried and wrote badly is a ``fail``, never void. Anything
    unmeasured (no transcript, git can't say) is not void: the checks decide. The denied calls are listed,
    since one the session took to mean "the run can't go on" is the usual cause (v0.18.19, v0.18.21).
    """
    if metrics is None or commits != 0 or pages_changed is not False:
        return None
    if any(c.name in WRITE_TOOLS and not c.is_error for c in metrics.calls):
        return None
    denied = [_denied(c) for c in metrics.calls if c.denied]
    listed = {d["tool"] for d in denied}
    # An older transcript whose denials carry no tool_use_id: name the tool at least.
    denied += [{"tool": name, "input": ""} for name in metrics.denials if name not in listed]
    return {
        "reason": "the session finished but did nothing: no commit, no file written, the project's pages unchanged",
        "denials": denied,
        "duration_s": metrics.duration_s,
        "turns": metrics.turns,
        "tool_calls": metrics.tool_call_count,
        "result": metrics.result_subtype,
    }


def pages_changed(vault: Path, project: str) -> bool | None:
    """Whether the project's front page or a knowledge/ note differs from the fixture; None when git can't say."""
    try:
        return pages_at_fixture(vault, project) != pages_now(vault, project)
    except KbReadError:
        return None


def gate_report(
    vault: Path,
    spec: KbSpec,
    checks: Sequence[CheckResult],
    *,
    scenario: str,
    sample: dict[str, Any] | None = None,
    void: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The gate's JSON report. ``verdict``: ``pass`` (every check passed), ``fail`` (one failed),
    ``unevaluated`` (none failed, but one couldn't be scored), ``aborted`` (the session didn't
    finish, so nothing was scored) or ``void`` (it finished but did nothing, :func:`void_run`: the
    run tested no KB writing, so it is neither a pass nor a fail; ``void`` says why and lists the
    denied calls; the checks are still reported)."""
    aborted = (sample or {}).get("aborted")
    if aborted:
        verdict = "aborted"
    elif void:
        verdict = "void"
    elif any(c.passed is False for c in checks):
        verdict = "fail"
    elif checks and all(c.passed is True for c in checks):
        verdict = "pass"
    else:
        verdict = "unevaluated"
    front: dict[str, Any] = {"path": f"{_folder(vault, spec.project)}/{spec.project}.md"}
    try:
        before = _bytes(pages_at_fixture(vault, spec.project).front)
    except KbReadError:
        before = None
    after = _bytes(pages_now(vault, spec.project).front) if vault.is_dir() else None
    front.update(bytes_before=before, bytes_after=after,
                 growth=None if before is None or after is None else after - before)  # fmt: skip
    return {
        "gate": GATE,
        "verdict": verdict,
        "passed": verdict == "pass",
        "scenario": scenario,
        "project": spec.project,
        "max_front_page_growth": spec.max_growth,
        "facts": [f.id for f in spec.facts],
        "front_page": front,
        "checks": [asdict(c) for c in checks],
        "void": void,
        "sample": sample,
    }


EXIT_VOID = 3


def exit_code(report: dict[str, Any]) -> int:
    """0 pass, 1 fail; 2 when the gate couldn't decide (an aborted session, an unscored check);
    3 for a void run (it finished but did nothing): rerun it, it says nothing about the release."""
    return {"pass": 0, "fail": 1, "void": EXIT_VOID}.get(report["verdict"], 2)
