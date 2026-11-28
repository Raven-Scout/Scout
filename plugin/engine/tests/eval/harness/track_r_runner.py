"""Track R per-question 3-arm retrieval and QA evaluation runner (Phase 2).

Runs read-only Claude Code Q&A sessions (via :mod:`tests.eval.harness.claude_shim`)
interleaved across Arms A (``files``), B (``relate-verbs``), and C (``relate-precompute``)
against detached worktrees of a Scout vault pinned to each :class:`GoldenItem.as_of_sha`.

**Privacy & isolation guarantees:**

- Refuses any ``results_root`` or ``report`` destination inside ``Raven-Scout/Scout``
  (:func:`refuse_public_repo_destination`) and writes all output dirs/files with
  owner-only permissions (``0700`` / ``0600``).
- Zero MCP servers (``{"mcpServers": {}}`` under ``--strict-mcp-config``); ``guard_init``
  aborts immediately if any live ``claude.ai`` connector leaks in.
- Read-only session sandbox: ``Edit`` and ``Write`` are omitted from ``--allowedTools``,
  and the OS Seatbelt sandbox restricts writes to the sample's result dir plus the
  worktree's ``.scout-cache`` / ``.scout-logs`` directories, keeping all vault notes
  mechanically read-only.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from scout import safe_write
from scout.kb.privacy import FULL_READERS
from scout.kb.read import load_graph, visibility_map
from scout.scripts import session_context_kb

from .arms import BRAIN_FILES, KNOWN_ARMS, ArmSpec, apply_arm
from .checks import CheckResult
from .checks.track_r import (
    TRACK_R_CATEGORIES,
    abstention_check,
    calls_to_first_gold_hit,
    citation_faithfulness,
    dolphin_removal_check,
    evaluate_gate_g1,
    exact_answer_match,
    precision_at_k,
    recall_at_k,
    strip_backend_revealing_text,
)
from .claude_shim import SHIM
from .metrics import (
    SampleMetrics,
    graph_context_reads,
    graph_verb_breakdown,
    graph_verb_calls,
    parse_stream,
    verb_uptake_rate,
)
from .runner import LoginRequired, _kill_group, _stop_runner
from .runner_env import ENGINE, build_env
from .track_r_golden import GoldenItem, load_golden_set, refuse_public_repo_destination

DEFAULT_TRACK_R_RESULTS = Path.home() / ".scout-worktrees" / "relatedb-eval-data" / "runs"
DEFAULT_TRACK_R_BUDGET = "0.35"
DEFAULT_TRACK_R_TIMEOUT_S = 180
TRACK_R_REPORT_NAME = "track-r-report.json"

_READ_ONLY_BASH = (
    "scoutctl",
    "git",
    "python3",
    "python",
    "jq",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "rg",
    "find",
    "sort",
    "uniq",
    "cut",
    "tr",
    "sed",
    "awk",
    "date",
    "basename",
    "dirname",
    "true",
    "test",
)
_VAULT_MD_RE = re.compile(r"(?:knowledge-base|action-items)/[A-Za-z0-9_./-]+\.md\b")


def parse_track_r_arms(raw: str) -> tuple[ArmSpec, ...]:
    """Parse 1 or more comma-separated Track R arm identifiers (e.g. ``a,b,c`` or ``b``)."""
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not tokens:
        raise ValueError("--arms requires at least one arm (e.g. a,b,c)")
    out: list[ArmSpec] = []
    seen: set[str] = set()
    for tok in tokens:
        spec = KNOWN_ARMS.get(tok)
        if spec is None:
            allowed = ", ".join(sorted(KNOWN_ARMS))
            raise ValueError(f"unknown arm {tok!r} (valid arms: {allowed})")
        if spec.id in seen:
            raise ValueError(f"duplicate arm {tok!r} (resolves to arm {spec.id!r})")
        seen.add(spec.id)
        out.append(spec)
    return tuple(out)


def track_r_allowed_tools(vault: Path) -> list[str]:
    """Read-only tool allowlist for Track R Q&A sessions (no ``Edit``, ``Write``, or MCP tools)."""
    roots = list(dict.fromkeys([f"/{vault.resolve()}", f"/{vault}"]))
    tools = [*(f"Read({r}/**)" for r in roots), "Glob", "Grep"]
    tools += [f"Bash({t} *)" for t in _READ_ONLY_BASH]
    for vpath in dict.fromkeys([vault.resolve(), vault]):
        tools += [f"Bash({vpath}/scripts/*)", f"Bash(bash {vpath}/scripts/*)"]
    tools += [
        "Bash(scripts/*)",
        "Bash(bash scripts/*)",
        f"Bash({ENGINE / 'bin' / 'scoutctl'} *)",
        f"Bash({ENGINE / '.venv' / 'bin' / 'scoutctl'} *)",
    ]
    return tools


@contextlib.contextmanager
def _with_extra_sys_path(extra_pythonpath: str | None) -> Iterator[None]:
    if not extra_pythonpath:
        yield
        return
    added = [p for p in extra_pythonpath.split(os.pathsep) if p and p not in sys.path]
    for p in reversed(added):
        sys.path.insert(0, p)
    try:
        yield
    finally:
        for p in added:
            with contextlib.suppress(ValueError):
                sys.path.remove(p)


def _strip_vault_prefixes(text: str, vault: Path) -> str:
    out = text
    for prefix in dict.fromkeys([str(vault.resolve()) + "/", str(vault) + "/"]):
        out = out.replace(prefix, "")
    return out


def extract_retrieved_by_call(stream_lines: Sequence[str], vault: Path) -> list[list[str]]:
    """Extract ordered vault-relative ``.md`` paths retrieved by each non-error tool call."""
    uses: list[tuple[str, str, dict[str, Any]]] = []
    results_by_id: dict[str, tuple[bool, str]] = {}
    for line in stream_lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "assistant":
            for blk in (ev.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    uses.append((str(blk.get("id") or ""), str(blk.get("name") or ""), blk.get("input") or {}))
        elif kind == "user":
            for blk in (ev.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    tid = str(blk.get("tool_use_id") or "")
                    content = blk.get("content")
                    if isinstance(content, str):
                        txt = content
                    elif isinstance(content, list):
                        txt = "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
                    else:
                        txt = ""
                    results_by_id[tid] = (bool(blk.get("is_error")), txt)

    by_call: list[list[str]] = []
    for tid, name, inp in uses:
        is_err, res_txt = results_by_id.get(tid, (False, ""))
        if is_err:
            continue
        file_arg = _strip_vault_prefixes(str(inp.get("file_path") or ""), vault)
        if name == "Read" and file_arg.endswith("session-context.json"):
            continue
        combined = _strip_vault_prefixes(json.dumps(inp, ensure_ascii=False) + "\n" + res_txt, vault)
        found: list[str] = []
        seen: set[str] = set()
        for m in _VAULT_MD_RE.finditer(combined):
            rel = m.group(0)
            if rel not in seen and (vault / rel).is_file():
                seen.add(rel)
                found.append(rel)
        if found:
            by_call.append(found)
    return by_call


def extract_cited_paths(answer: str, vault: Path) -> list[str]:
    """Extract unique vault-relative ``.md`` paths cited in ``answer`` (preserving hallucinated ones)."""
    cleaned = _strip_vault_prefixes(answer, vault)
    out: list[str] = []
    seen: set[str] = set()
    for m in _VAULT_MD_RE.finditer(cleaned):
        rel = m.group(0)
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def oracle_hint(gold_paths: Sequence[str]) -> str:
    """The Arm O prompt block: the notes that hold the answer, named up front (a retrieval ceiling)."""
    if not gold_paths:
        return ""
    listed = "\n".join(f"- `{p}`" for p in gold_paths)
    return (
        "\n\nThe notes that hold what you need are listed below. Read them with "
        f"`scripts/scoutctl kb read <path>` first:\n{listed}"
    )


def build_track_r_prompt(item: GoldenItem, arm: ArmSpec) -> str:
    """Construct the read-only Track R Q&A prompt for ``item`` under ``arm``."""
    base_kb = (
        'Standard KB commands: `scripts/scoutctl kb search "<words>"`, `scripts/scoutctl kb read <path>`, '
        '`scripts/scoutctl kb files [dir]`, `scripts/scoutctl kb entity "<name>"`, '
        '`scripts/scoutctl kb related "<name>"`, `scripts/scoutctl kb resolve "<name>"`, '
        '`scripts/scoutctl kb name-lookup "<name>"`.'
    )
    overlay = f"\n\n{arm.brain_overlay.strip()}" if arm.brain_overlay else ""
    overlay += oracle_hint(item.gold_paths) if arm.oracle else ""
    return (
        "You are answering a read-only Track R evaluation question from the Scout vault in your current "
        "working directory.\n"
        "- Ground every claim strictly in this vault's notes and daily files; never guess from outside knowledge.\n"
        "- Do not modify any file in the vault.\n"
        "- Read the knowledge base via `scripts/scoutctl kb` (or `Read` on specific files) and inspect "
        "`.scout-cache/session-context.json` if precomputed project/people context is helpful.\n"
        "- If the vault does not confirm the answer, or marks the claim `[unverified]` / open in "
        "`knowledge-base/review-queue.md`, explicitly state that it is unverified / cannot be confirmed from "
        "the KB and should route to review-queue.\n\n"
        f"{base_kb}{overlay}\n\n"
        f"Question ({item.id}):\n{item.question}\n\n"
        "Respond concisely using these two sections:\n"
        "Answer: <direct factual answer, or explicit abstention if unconfirmed>\n"
        "Citations: <comma-separated vault-relative .md paths supporting your answer, or none>\n"
    )


def score_track_r_item(
    item: GoldenItem,
    *,
    vault: Path,
    answer: str,
    retrieved_by_call: Sequence[Sequence[str]],
    cited_paths: Sequence[str],
) -> dict[str, Any]:
    """Grade one Track R ``answer`` and retrieval trace with pure ``checks/track_r.py`` functions."""
    flat_retrieved = [p for group in retrieved_by_call for p in group]
    should_abstain = item.category == "abstention"
    abs_res = abstention_check(answer, should_abstain=should_abstain)
    checks: list[CheckResult] = [abs_res]

    if should_abstain:
        if item.expected_tokens:
            checks.append(exact_answer_match(answer, item.expected_tokens))
        passed = all(bool(c.passed) for c in checks)
        return {
            "passed": passed,
            "checks": [dataclasses.asdict(c) for c in checks],
            "recall_at_5": None,
            "precision_at_5": None,
            "calls_to_first_hit": None,
            "cited_paths": list(cited_paths),
            "retrieved_paths": list(dict.fromkeys(flat_retrieved)),
        }

    tokens = item.expected_tokens or (item.gold_answer,)
    exact_res = exact_answer_match(answer, tokens)
    checks = [exact_res] if exact_res.passed else [abs_res, exact_res]

    note_texts = {
        rel: (vault / rel).read_text(encoding="utf-8", errors="replace")
        for rel in set(cited_paths) | set(item.gold_paths)
        if (vault / rel).is_file()
    }
    if item.category == "citation":
        cite_res = citation_faithfulness(cited_paths, note_texts, tokens)
        checks.append(cite_res)
    elif cited_paths:
        # Even outside the citation stratum, hallucinated citations fail citation faithfulness
        cite_res = citation_faithfulness(cited_paths, note_texts, ())
        checks.append(cite_res)

    passed = all(bool(c.passed) for c in checks)
    return {
        "passed": passed,
        "checks": [dataclasses.asdict(c) for c in checks],
        "recall_at_5": round(recall_at_k(flat_retrieved, item.gold_paths, k=5), 4),
        "precision_at_5": round(precision_at_k(flat_retrieved, item.gold_paths, k=5), 4),
        "calls_to_first_hit": calls_to_first_gold_hit(retrieved_by_call, item.gold_paths),
        "cited_paths": list(cited_paths),
        "retrieved_paths": list(dict.fromkeys(flat_retrieved)),
    }


@dataclasses.dataclass
class _PreparedWorktree:
    path: Path
    is_git_worktree: bool
    orig_files: dict[str, str | None]
    base_session_ctx: str
    arm_c_session_ctx: str


def _install_worktree_scoutctl(wt: Path) -> None:
    scripts_dir = wt / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    wrapper = scripts_dir / "scoutctl"
    wrapper.write_text(
        "#!/bin/sh\n"
        'VAULT="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'export SCOUT_DATA_DIR="${SCOUT_DATA_DIR:-$VAULT}"\n'
        'exec "${SCOUT_EVAL_ENGINE}/bin/scoutctl" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)


def _build_offline_session_contexts(
    wt: Path,
    *,
    reader: str,
    need_relate: bool,
    need_arm_c: bool,
    as_of_sha: str,
    query_hint: str | None = None,
) -> tuple[str, str]:
    full = reader in FULL_READERS
    labels = visibility_map(wt, reader, ["knowledge-base/**"], who="track-r-runner")
    graph = load_graph(wt, reader)
    today = dt.date.today()
    items_txt = session_context_kb.items_text_for(wt, today)

    if need_relate:
        from scout.kb.relate_backend import RelateBackend

        RelateBackend(wt, reader).build(snapshot_sha=as_of_sha)

    common: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_type": "research",
        "project_knowledge": session_context_kb.project_knowledge(wt, labels, full=full),
        "people_context": session_context_kb.people_context(graph, today, items_text=items_txt),
        "research_queue": session_context_kb.research_queue(wt, labels, full=full),
        "graph_context": {},
    }
    base_json = json.dumps(session_context_kb.fit_budget(common), indent=2, ensure_ascii=False) + "\n"
    if not need_arm_c:
        return base_json, base_json

    from scout.kb.backend import open_backend

    rel_backend = open_backend(wt, reader, kind="relate", graph=graph)
    g_ctx = session_context_kb.graph_context(
        wt,
        labels,
        full=full,
        reader=reader,
        query_hint=(query_hint or items_txt)[:400],
        backend=rel_backend,
        graph=graph,
    )
    arm_c_payload = session_context_kb.fit_budget({**common, "graph_context": g_ctx})
    arm_c_json = json.dumps(arm_c_payload, indent=2, ensure_ascii=False) + "\n"
    return base_json, arm_c_json


def _prepare_worktree(
    vault_repo: Path,
    dest: Path,
    *,
    as_of_sha: str,
    exclude_paths: Sequence[str],
    reader: str,
    need_relate: bool,
    need_arm_c: bool,
    mutate: Callable[[Path], None] | None = None,
    query_hint: str | None = None,
) -> _PreparedWorktree:
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    is_git_wt = False
    if (vault_repo / ".git").exists() and as_of_sha and as_of_sha != "working-tree":
        added = subprocess.run(
            ["git", "-C", str(vault_repo), "worktree", "add", "--detach", str(dest), as_of_sha],
            capture_output=True,
            text=True,
            check=False,
        )
        if added.returncode != 0:
            raise RuntimeError(f"git worktree add {as_of_sha} failed: {added.stderr.strip()[:240]}")
        is_git_wt = True
    else:
        shutil.copytree(
            vault_repo,
            dest,
            ignore=shutil.ignore_patterns(".git", ".scout-cache", ".scout-logs", ".eval-results"),
        )
    os.chmod(dest, 0o700)

    for rel_excl in exclude_paths:
        cand = (dest / rel_excl).resolve()
        if cand.is_relative_to(dest.resolve()) and cand.is_file():
            cand.unlink()
    if mutate is not None:
        # Applied before any snapshot or session context is built, so every arm sees the same vault.
        mutate(dest)

    _install_worktree_scoutctl(dest)
    (dest / ".scout-cache").mkdir(mode=0o700, exist_ok=True)
    (dest / ".scout-logs").mkdir(mode=0o700, exist_ok=True)

    tracked_names = ("scout-config.yaml", *BRAIN_FILES)
    orig_files = {
        name: (dest / name).read_text(encoding="utf-8") if (dest / name).is_file() else None for name in tracked_names
    }
    base_ctx, arm_c_ctx = _build_offline_session_contexts(
        dest,
        reader=reader,
        need_relate=need_relate,
        need_arm_c=need_arm_c,
        as_of_sha=as_of_sha,
        query_hint=query_hint,
    )
    return _PreparedWorktree(
        path=dest,
        is_git_worktree=is_git_wt,
        orig_files=orig_files,
        base_session_ctx=base_ctx,
        arm_c_session_ctx=arm_c_ctx,
    )


def _stage_arm_on_worktree(prep: _PreparedWorktree, arm: ArmSpec) -> None:
    wt = prep.path
    for name, content in prep.orig_files.items():
        target = wt / name
        if content is None:
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
        else:
            target.write_text(content, encoding="utf-8")
    apply_arm(wt, arm)
    ctx_text = prep.arm_c_session_ctx if arm.precompute_graph_context else prep.base_session_ctx
    safe_write.atomic_write_text(wt / ".scout-cache" / "session-context.json", ctx_text, mode=0o600)
    with contextlib.suppress(FileNotFoundError):
        (wt / ".scout-logs" / "kb-reads.jsonl").unlink()


def _cleanup_worktree(vault_repo: Path, prep: _PreparedWorktree) -> None:
    if prep.is_git_worktree:
        subprocess.run(
            ["git", "-C", str(vault_repo), "worktree", "remove", "--force", str(prep.path)],
            capture_output=True,
            text=True,
            check=False,
        )
    shutil.rmtree(prep.path, ignore_errors=True)


def _run_one_qa_session(
    item: GoldenItem,
    arm: ArmSpec,
    *,
    prep: _PreparedWorktree,
    sample_dir: Path,
    real_claude: str,
    model: str,
    budget: str,
    timeout_s: int,
    reader: str,
    extra_pythonpath: str | None,
) -> tuple[str, list[str], SampleMetrics | None, dict[str, Any] | None]:
    return run_eval_session(
        f"track-r-{item.id}-{arm.id}",
        build_track_r_prompt(item, arm),
        arm,
        prep=prep,
        sample_dir=sample_dir,
        real_claude=real_claude,
        model=model,
        budget=budget,
        timeout_s=timeout_s,
        reader=reader,
        extra_pythonpath=extra_pythonpath,
    )


def run_eval_session(
    name: str,
    prompt: str,
    arm: ArmSpec,
    *,
    prep: _PreparedWorktree,
    sample_dir: Path,
    real_claude: str,
    model: str,
    budget: str,
    timeout_s: int,
    reader: str,
    extra_pythonpath: str | None,
) -> tuple[str, list[str], SampleMetrics | None, dict[str, Any] | None]:
    _stage_arm_on_worktree(prep, arm)
    sample_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(sample_dir, 0o700)
    mcp_path = sample_dir / "mcp.json"
    safe_write.atomic_write_text(mcp_path, json.dumps({"mcpServers": {}}) + "\n", mode=0o600)

    wt = prep.path
    env = build_env(
        base=os.environ,
        mode="research",
        result_dir=sample_dir,
        mcp_config=mcp_path,
        allowed_servers=(),
        allowed_tools=track_r_allowed_tools(wt),
        model=model,
        real_claude=real_claude,
        script=sample_dir,
        today=dt.date.today().isoformat(),
        timeout_s=timeout_s,
        pythonpath=ENGINE,
        extra_pythonpath=extra_pythonpath,
    )
    py_bin_dir = Path(sys.executable).parent
    if (py_bin_dir.parent / "pyvenv.cfg").is_file():
        cur_parts = env["PATH"].split(":")
        env["PATH"] = ":".join(dict.fromkeys([cur_parts[0], str(ENGINE / "bin"), str(py_bin_dir), *cur_parts[1:]]))
    env.update(
        SCOUT_MODE="research",
        SCOUT_READER=reader,
        SCOUT_DATA_DIR=str(wt),
        SCOUT_KB_BACKEND=arm.backend_kind,
        SCOUT_KB_GRAPH_CONTEXT="1" if arm.precompute_graph_context else "0",
        SCOUT_EVAL_EXTRA_ALLOW_WRITE=os.pathsep.join([str(wt / ".scout-cache"), str(wt / ".scout-logs")]),
    )
    safe_write.atomic_write_text(sample_dir / "prompt.txt", prompt, mode=0o600)

    timed_out = False
    with (sample_dir / "runner.log").open("w", encoding="utf-8") as log:
        os.chmod(sample_dir / "runner.log", 0o600)
        proc = subprocess.Popen(
            [
                str(SHIM),
                "--name",
                name,
                "--max-budget-usd",
                budget,
                "-p",
                prompt,
            ],
            cwd=wt,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_s + 30)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop_runner(proc)
        except BaseException:
            _stop_runner(proc)
            raise
    _kill_group(proc.pid)
    pgid = sample_dir / "claude.pgid"
    if pgid.exists():
        _kill_group(int(pgid.read_text(encoding="utf-8")))

    stream_file = sample_dir / "stream.jsonl"
    stream_lines = stream_file.read_text(encoding="utf-8").splitlines() if stream_file.exists() else []
    metrics = parse_stream(stream_lines) if stream_lines else None
    stderr = sample_dir / "claude.stderr"
    login_text = (stderr.read_text(encoding="utf-8") if stderr.exists() else "") + "\n".join(stream_lines)
    if "Not logged in" in login_text or "Please run /login" in login_text:
        raise LoginRequired(f"Claude Code is not logged in (see {stderr}); run `claude auth login`, then retry.")

    aborted_file = sample_dir / "aborted.json"
    aborted = json.loads(aborted_file.read_text(encoding="utf-8")) if aborted_file.exists() else None
    if aborted is None and timed_out:
        aborted = {"reason": "runner_timeout", "seconds": timeout_s + 30}
    if aborted is None and (metrics is None or (metrics.model is None and metrics.result_subtype is None)):
        aborted = {"reason": "no_session", "detail": "no init or result event"}
    if aborted is None and metrics is not None and metrics.result_subtype not in (None, "success"):
        aborted = {"reason": metrics.result_subtype, "budget_usd": budget}
    if aborted is None and metrics is not None and metrics.api_error is not None:
        aborted = {"reason": "api_error", "detail": metrics.api_error[:200], "api_retries": metrics.api_retries}

    answer = ""
    for line in reversed(stream_lines):
        with contextlib.suppress(json.JSONDecodeError):
            ev = json.loads(line)
            if isinstance(ev, dict) and ev.get("type") == "result":
                answer = str(ev.get("result") or "")
                break
    return answer, stream_lines, metrics, aborted


def _summarize_arm(arm: ArmSpec, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("aborted") is None]
    aborted = [r for r in rows if r.get("aborted") is not None]
    by_cat: dict[str, dict[str, Any]] = {}
    for cat in TRACK_R_CATEGORIES:
        cat_rows = [r for r in scored if r["category"] == cat]
        if cat_rows:
            by_cat[cat] = {
                "n": len(cat_rows),
                "passed": sum(1 for r in cat_rows if r["passed"]),
                "accuracy": round(sum(1 for r in cat_rows if r["passed"]) / len(cat_rows), 4),
            }

    recalls = [float(r["recall_at_5"]) for r in scored if r.get("recall_at_5") is not None]
    precs = [float(r["precision_at_5"]) for r in scored if r.get("precision_at_5") is not None]
    hits = [int(r["calls_to_first_hit"]) for r in scored if r.get("calls_to_first_hit") is not None]
    toks = [int(r["total_tokens"]) for r in scored]
    calls = [int(r["tool_calls"]) for r in scored]
    costs = [float(r["cost_usd"]) for r in scored if r.get("cost_usd") is not None]
    verb_used = sum(1 for r in scored if int(r.get("graph_verb_calls") or 0) > 0)

    return {
        "arm": arm.id,
        "slug": arm.slug,
        "backend_kind": arm.backend_kind,
        "n_total": len(rows),
        "n_scored": len(scored),
        "n_aborted": len(aborted),
        "passed": sum(1 for r in scored if r["passed"]),
        "accuracy": round(sum(1 for r in scored if r["passed"]) / len(scored), 4) if scored else 0.0,
        "recall_at_5_mean": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "precision_at_5_mean": round(sum(precs) / len(precs), 4) if precs else None,
        "calls_to_first_hit_median": statistics.median(hits) if hits else None,
        "median_total_tokens": float(statistics.median(toks)) if toks else 0.0,
        "median_tool_calls": float(statistics.median(calls)) if calls else 0.0,
        "verb_uptake_rate": round(verb_used / len(scored), 4) if scored else 0.0,
        "total_cost_usd": round(sum(costs), 4),
        "by_category": by_cat,
    }


def run_track_r(
    golden_path: Path,
    *,
    vault_repo: Path,
    arm_specs: Sequence[ArmSpec],
    results_root: Path = DEFAULT_TRACK_R_RESULTS,
    real_claude: str,
    model: str = "sonnet",
    budget: str = DEFAULT_TRACK_R_BUDGET,
    timeout_s: int = DEFAULT_TRACK_R_TIMEOUT_S,
    reader: str = "autonomous",
    limit: int | None = None,
    categories: Sequence[str] | None = None,
    only_audited: bool = False,
    dolphin_check: bool = False,
    extra_pythonpath: str | None = None,
    cli_version: str = "",
    progress: bool = True,
) -> dict[str, Any]:
    """Execute Track R across ``arm_specs`` interleaved per item and write an owner-only report."""
    refuse_public_repo_destination(results_root)
    vault_resolved = vault_repo.expanduser().resolve()
    results_resolved = results_root.expanduser().resolve()
    results_resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(results_resolved, 0o700)

    items = load_golden_set(golden_path.expanduser().resolve())
    if only_audited:
        items = [it for it in items if it.audited]
    if categories:
        allowed_cats = set(categories)
        items = [it for it in items if it.category in allowed_cats]
    if limit is not None:
        items = items[: max(0, limit)]
    if not items:
        raise ValueError("no Track R items matched the requested filters")

    run_stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = results_resolved / f"track-r-{run_stamp}"
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(run_dir, 0o700)

    need_relate = any(a.backend_kind == "relate" for a in arm_specs)
    need_arm_c = any(a.precompute_graph_context for a in arm_specs)
    worktrees: dict[tuple[str, tuple[str, ...]], _PreparedWorktree] = {}
    rows_by_arm: dict[str, list[dict[str, Any]]] = {a.id: [] for a in arm_specs}
    dolphin_results: list[dict[str, Any]] = []

    with _with_extra_sys_path(extra_pythonpath):
        try:
            for wt_idx, key in enumerate(dict.fromkeys((it.as_of_sha, it.exclude_paths) for it in items)):
                sha, excl = key
                wt_dest = run_dir / "_worktrees" / f"wt-{wt_idx:03d}-{sha[:12]}"
                worktrees[key] = _prepare_worktree(
                    vault_resolved,
                    wt_dest,
                    as_of_sha=sha,
                    exclude_paths=excl,
                    reader=reader,
                    need_relate=need_relate,
                    need_arm_c=need_arm_c,
                )

            for idx, item in enumerate(items, start=1):
                prep = worktrees[(item.as_of_sha, item.exclude_paths)]
                for arm in arm_specs:
                    if progress:
                        print(
                            f"… [{idx}/{len(items)}] {item.id} ({item.category}) arm-{arm.id} ({arm.slug})",
                            flush=True,
                        )
                    sdir = run_dir / "samples" / f"{idx:03d}-{item.id}-arm-{arm.id}"
                    ans, s_lines, metrics, aborted = _run_one_qa_session(
                        item,
                        arm,
                        prep=prep,
                        sample_dir=sdir,
                        real_claude=real_claude,
                        model=model,
                        budget=budget,
                        timeout_s=timeout_s,
                        reader=reader,
                        extra_pythonpath=extra_pythonpath,
                    )
                    calls = metrics.calls if metrics is not None else ()
                    ret_by_call = extract_retrieved_by_call(s_lines, prep.path) if aborted is None else []
                    cited = extract_cited_paths(ans, prep.path) if aborted is None else []
                    grade = (
                        score_track_r_item(
                            item,
                            vault=prep.path,
                            answer=ans,
                            retrieved_by_call=ret_by_call,
                            cited_paths=cited,
                        )
                        if aborted is None
                        else {
                            "passed": False,
                            "checks": [],
                            "recall_at_5": None,
                            "precision_at_5": None,
                            "calls_to_first_hit": None,
                            "cited_paths": [],
                            "retrieved_paths": [],
                        }
                    )
                    total_tokens = (
                        metrics.input_tokens
                        + metrics.output_tokens
                        + metrics.cache_read_tokens
                        + (metrics.cache_creation_tokens or 0)
                        if metrics is not None
                        else 0
                    )
                    rec: dict[str, Any] = {
                        "id": item.id,
                        "category": item.category,
                        "project": item.project,
                        "arm": arm.id,
                        "slug": arm.slug,
                        "aborted": aborted,
                        **grade,
                        "tool_calls": metrics.tool_call_count if metrics is not None else 0,
                        "graph_verb_calls": graph_verb_calls(calls),
                        "graph_verb_breakdown": graph_verb_breakdown(calls),
                        "graph_context_reads": graph_context_reads(calls),
                        "verb_uptake": verb_uptake_rate([calls]) if calls else 0.0,
                        "input_tokens": metrics.input_tokens if metrics is not None else 0,
                        "output_tokens": metrics.output_tokens if metrics is not None else 0,
                        "total_tokens": total_tokens,
                        "cost_usd": metrics.cost_usd if metrics is not None else None,
                        "duration_s": metrics.duration_s if metrics is not None else None,
                        "blinded_answer": strip_backend_revealing_text(ans),
                        "sample_dir": str(sdir),
                    }
                    safe_write.atomic_write_text(
                        sdir / "item-result.json",
                        json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
                        mode=0o600,
                    )
                    rows_by_arm[arm.id].append(rec)

                # Optional DolphinBench gold-removal check on the reference arm
                if dolphin_check and item.category != "abstention" and item.gold_paths:
                    ref_arm = arm_specs[0]
                    ref_row = rows_by_arm[ref_arm.id][-1]
                    if ref_row["aborted"] is None and ref_row["passed"]:
                        d_excl = tuple(dict.fromkeys((*item.exclude_paths, *item.gold_paths)))
                        d_wt_dir = run_dir / "_worktrees" / f"dolphin-{idx:03d}-{item.id}"
                        d_prep = _prepare_worktree(
                            vault_resolved,
                            d_wt_dir,
                            as_of_sha=item.as_of_sha,
                            exclude_paths=d_excl,
                            reader=reader,
                            need_relate=(ref_arm.backend_kind == "relate"),
                            need_arm_c=ref_arm.precompute_graph_context,
                        )
                        try:
                            d_sdir = run_dir / "samples" / f"{idx:03d}-{item.id}-dolphin"
                            d_ans, d_lines, _dm, d_aborted = _run_one_qa_session(
                                item,
                                ref_arm,
                                prep=d_prep,
                                sample_dir=d_sdir,
                                real_claude=real_claude,
                                model=model,
                                budget=budget,
                                timeout_s=timeout_s,
                                reader=reader,
                                extra_pythonpath=extra_pythonpath,
                            )
                            d_grade = (
                                score_track_r_item(
                                    item,
                                    vault=d_prep.path,
                                    answer=d_ans,
                                    retrieved_by_call=extract_retrieved_by_call(d_lines, d_prep.path),
                                    cited_paths=extract_cited_paths(d_ans, d_prep.path),
                                )
                                if d_aborted is None
                                else {"passed": False}
                            )
                            d_chk = dolphin_removal_check(
                                passed_with_gold=True,
                                passed_without_gold=bool(d_grade["passed"]),
                            )
                            dolphin_results.append(
                                {
                                    "id": item.id,
                                    "passed": bool(d_chk.passed),
                                    "passed_without_gold": bool(d_grade["passed"]),
                                    "details": list(d_chk.details),
                                }
                            )
                        finally:
                            _cleanup_worktree(vault_resolved, d_prep)
        finally:
            for prep in worktrees.values():
                _cleanup_worktree(vault_resolved, prep)

    arm_summaries = {a.id: _summarize_arm(a, rows_by_arm[a.id]) for a in arm_specs}
    comparisons: dict[str, Any] = {}
    pairs: list[tuple[ArmSpec, ArmSpec]] = []
    if len(arm_specs) >= 2:
        ref = arm_specs[0]
        for cand in arm_specs[1:]:
            pairs.append((ref, cand))
    if len(arm_specs) >= 3:
        pairs.append((arm_specs[1], arm_specs[2]))

    for ref_spec, cand_spec in pairs:
        ref_map = {r["id"]: r for r in rows_by_arm[ref_spec.id] if r["aborted"] is None}
        cand_map = {r["id"]: r for r in rows_by_arm[cand_spec.id] if r["aborted"] is None}
        shared_ids = [it.id for it in items if it.id in ref_map and it.id in cand_map]
        paired_records = [
            {
                "id": iid,
                "project": ref_map[iid]["project"],
                "category": ref_map[iid]["category"],
                "ref_pass": int(ref_map[iid]["passed"]),
                "cand_pass": int(cand_map[iid]["passed"]),
            }
            for iid in shared_ids
        ]
        comparisons[f"{cand_spec.id}_vs_{ref_spec.id}"] = evaluate_gate_g1(
            paired_records,
            baseline_tokens_median=arm_summaries[ref_spec.id]["median_total_tokens"],
            candidate_tokens_median=arm_summaries[cand_spec.id]["median_total_tokens"],
            baseline_calls_median=arm_summaries[ref_spec.id]["median_tool_calls"],
            candidate_calls_median=arm_summaries[cand_spec.id]["median_tool_calls"],
        )

    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_dir": str(run_dir),
        "golden_path": str(golden_path.expanduser().resolve()),
        "vault_repo": str(vault_resolved),
        "model": model,
        "cli_version": cli_version,
        "reader": reader,
        "n_items": len(items),
        "arms": arm_summaries,
        "gate_g1": comparisons,
        "dolphin": {
            "checked": len(dolphin_results),
            "valid": sum(1 for d in dolphin_results if d["passed"]),
            "items": dolphin_results,
        }
        if dolphin_check
        else None,
        "items_by_arm": rows_by_arm,
    }
    report_path = run_dir / TRACK_R_REPORT_NAME
    safe_write.atomic_write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n", mode=0o600)
    return report


def format_track_r_summary(report: Mapping[str, Any]) -> str:
    """Format a concise console summary of a completed Track R run and its Gate G1 verdicts."""
    lines = [
        f"== Track R report ({report['n_items']} items, model={report['model']}) ==",
        f"Run dir: {report['run_dir']}",
        "",
        "Arm summary:",
    ]
    for arm_id, s in (report.get("arms") or {}).items():
        r5 = f"{s['recall_at_5_mean']:.2f}" if s.get("recall_at_5_mean") is not None else "n/a"
        p5 = f"{s['precision_at_5_mean']:.2f}" if s.get("precision_at_5_mean") is not None else "n/a"
        lines.append(
            f"  arm-{arm_id} ({s['slug']}): acc={s['accuracy'] * 100:.1f}% ({s['passed']}/{s['n_scored']}), "
            f"R@5={r5}, P@5={p5}, med_calls={s['median_tool_calls']:.1f}, "
            f"med_tokens={s['median_total_tokens']:.0f}, verb_uptake={s['verb_uptake_rate'] * 100:.0f}%"
        )
    for pair_key, g1 in (report.get("gate_g1") or {}).items():
        verdict = "PASS" if g1.get("passed") else "FAIL"
        ov = g1.get("overall") or {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0}
        mc_p = (g1.get("mcnemar") or {}).get("p_value", 1.0)
        lines.append(
            f"\nGate G1 ({pair_key}): {verdict} | diff={ov['mean_diff']:+.3f} "
            f"[{ov['ci_low']:+.3f}, {ov['ci_high']:+.3f}], McNemar p={mc_p:.4f}"
        )
        for reason in g1.get("reasons") or ():
            lines.append(f"  - {reason}")
    return "\n".join(lines)
