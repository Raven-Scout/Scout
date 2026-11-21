"""``scoutctl lab`` sub-app: thin wrappers over :mod:`scout.lab.ops`, ``experiments`` and ``summary``.

Top-level imports stay minimal (Typer, stdlib, ``scout.errors``): each command imports the lab
modules inside its body so ``scoutctl`` startup is unaffected (``tests/perf/test_no_heavy_imports.py``).
Write commands print ``ok <slug> <what>`` and never commit. ``list``/``show`` print JSON with ``--json``.
The vault is ``paths.data_dir(--vault)``: ``--vault`` > ``$SCOUT_DATA_DIR`` > the vault around the
working directory > ``~/Scout``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer

from scout.errors import LabError

app = typer.Typer(help="The Lab: research threads, questions and experiments.", no_args_is_help=True)
question_app = typer.Typer(help="Questions on a thread.", no_args_is_help=True)
understanding_app = typer.Typer(help="A thread's current understanding.", no_args_is_help=True)
evidence_app = typer.Typer(help="A thread's evidence list.", no_args_is_help=True)
timeline_app = typer.Typer(help="A thread's timeline.", no_args_is_help=True)
experiment_app = typer.Typer(help="Experiments on a thread.", no_args_is_help=True)
app.add_typer(question_app, name="question")
app.add_typer(understanding_app, name="understanding")
app.add_typer(evidence_app, name="evidence")
app.add_typer(timeline_app, name="timeline")
app.add_typer(experiment_app, name="experiment")

_HANDLE_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
_RESERVED_HANDLE = "scout"

_AuthorOpt = typer.Option(..., "--author", help="Handle of the person acting ([A-Za-z][A-Za-z0-9._-]*; not 'scout').")
_ReasonOpt = typer.Option(None, "--reason", help="Why (recorded on the timeline).")


# --- helpers ---------------------------------------------------------------------------


@app.callback()
def _group(
    ctx: typer.Context,
    vault: str | None = typer.Option(None, "--vault", help="The vault (default: SCOUT_DATA_DIR / cwd / ~/Scout)."),
) -> None:
    ctx.obj = {"vault": vault}


def _vault(ctx: typer.Context) -> Path:
    from scout import paths

    explicit = ctx.obj.get("vault") if isinstance(ctx.obj, dict) else None
    return paths.data_dir(explicit)


def _handle(author: str, *, allow_scout: bool = False) -> str:
    if not _HANDLE_RE.fullmatch(author):
        raise LabError(f"bad author {author!r}; use letters, digits, '.', '_' and '-', starting with a letter")
    if not allow_scout and author.lower() == _RESERVED_HANDLE:
        raise LabError("the handle 'scout' is reserved for Scout's replies")
    return author


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LabError(f"cannot read {path}: {exc.__class__.__name__}") from None


def _ok(slug: str, what: str) -> None:
    typer.echo(f"ok {slug} {what}")


def _dump(doc: Any) -> None:
    typer.echo(json.dumps(doc, ensure_ascii=False, indent=2))


def _labels(vault: Path, reader_flag: str | None) -> tuple[dict[str, str], bool]:
    """The reader's visibility map and whether it sees everything (as ``pre_session_data`` computes them)."""
    import os

    from scout.kb.privacy import FULL_READERS, reads_any
    from scout.kb.read import visibility_map
    from scout.kb.reader import resolve_reader

    reader = resolve_reader(reader_flag, os.environ)
    full = reader in FULL_READERS
    labels: dict[str, str] = {}
    if reads_any(reader):
        labels = visibility_map(vault, reader, ["knowledge-base/**"], who="lab")
    return labels, full


_ReaderOpt = typer.Option(None, "--reader", help="Read as this reader (can only narrow the ambient reader).")


# --- read ------------------------------------------------------------------------------


@app.command("list")
def cli_list(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Print the lab-list JSON document."),
    reader: str | None = _ReaderOpt,
) -> None:
    """List the threads the reader may see."""
    from scout.lab import summary

    vault = _vault(ctx)
    labels, full = _labels(vault, reader)
    doc = summary.lab_list(vault, labels, full=full)
    if as_json:
        _dump(doc)
        return
    for t in doc["threads"]:
        typer.echo(f"{t['slug']}\t{t['status']}\t{t['title']}")


@app.command("show")
def cli_show(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="Print the lab-thread JSON document."),
    reader: str | None = _ReaderOpt,
) -> None:
    """Show one thread. A private thread is 'no such thread' to a reader that may not see it."""
    from scout.lab import paths, summary

    vault = _vault(ctx)
    labels, full = _labels(vault, reader)
    missing = LabError(f"no lab thread '{slug}'")
    try:
        doc = summary.lab_thread(vault, slug)
    except LabError:
        raise missing from None
    if not full:
        rel = paths.front_page(vault, slug).relative_to(vault).as_posix()
        if doc["visibility"] == "private" or rel not in labels:
            raise missing
    if as_json:
        _dump(doc)
        return
    typer.echo(f"{doc['slug']}\t{doc['status']}\t{doc['title']}")
    typer.echo(f"question: {doc['question']}")
    typer.echo(
        f"open {len(doc['open'])} · answered {len(doc['answered'])} · "
        f"experiments {len(doc['experiments'])} · unanswered comments {doc['unanswered_comments']}"
    )
    for p in doc["problems"]:
        typer.echo(f"problem (line {p['line']}): {p['message']}")


@app.command("lint")
def cli_lint(
    ctx: typer.Context,
    files: list[Path] = typer.Argument(None, metavar="[PATH]...", help="Lab files (vault-relative or absolute)."),
) -> None:
    """Check lab files against the thread format (what kb-lint's lab-format enforces at commit).

    With no PATH, every file under knowledge-base/lab/. One line per finding:
    '<path>:<line>: <check>: <message>'. Exits 1 when any finding blocks; warnings exit 0.
    """
    from scout.lab import lint, paths

    vault = _vault(ctx)
    root = paths.lab_dir(vault)
    targets: list[Path] = []
    for given in files or [root]:
        path = given if given.is_absolute() else vault / given
        targets += sorted(p for p in path.rglob("*.md")) if path.is_dir() else [path]
    blocking = False
    for path in sorted(set(targets)):
        try:
            rel = path.resolve().relative_to(vault.resolve()).as_posix()
        except ValueError:
            raise LabError(f"{path} is not inside the vault {vault}") from None
        for f in lint.lab_findings(vault, rel, _read_text(path)):
            typer.echo(f"{f.path}:{f.line or 1}: {f.check}: {f.message}")
            blocking = blocking or f.blocking
    if blocking:
        raise typer.Exit(1)


# --- owner verbs -----------------------------------------------------------------------


def _owner_verb(ctx: typer.Context, verb: str, slug: str, author: str, reason: str | None) -> None:
    from scout.lab import ops

    ops.owner_verb(_vault(ctx), slug, verb, _handle(author), reason)
    _ok(slug, verb)


@app.command("approve")
def cli_approve(
    ctx: typer.Context, slug: str = typer.Argument(...), author: str = _AuthorOpt, reason: str | None = _ReasonOpt
) -> None:
    """proposed -> active."""
    _owner_verb(ctx, "approve", slug, author, reason)


@app.command("reject")
def cli_reject(
    ctx: typer.Context, slug: str = typer.Argument(...), author: str = _AuthorOpt, reason: str | None = _ReasonOpt
) -> None:
    """proposed -> rejected."""
    _owner_verb(ctx, "reject", slug, author, reason)


@app.command("pause")
def cli_pause(
    ctx: typer.Context, slug: str = typer.Argument(...), author: str = _AuthorOpt, reason: str | None = _ReasonOpt
) -> None:
    """active -> paused."""
    _owner_verb(ctx, "pause", slug, author, reason)


@app.command("resume")
def cli_resume(
    ctx: typer.Context, slug: str = typer.Argument(...), author: str = _AuthorOpt, reason: str | None = _ReasonOpt
) -> None:
    """paused -> active."""
    _owner_verb(ctx, "resume", slug, author, reason)


@app.command("close")
def cli_close(
    ctx: typer.Context, slug: str = typer.Argument(...), author: str = _AuthorOpt, reason: str | None = _ReasonOpt
) -> None:
    """active or paused -> closed."""
    _owner_verb(ctx, "close", slug, author, reason)


@app.command("reopen")
def cli_reopen(
    ctx: typer.Context, slug: str = typer.Argument(...), author: str = _AuthorOpt, reason: str | None = _ReasonOpt
) -> None:
    """closed or rejected -> active."""
    _owner_verb(ctx, "reopen", slug, author, reason)


@app.command("comment")
def cli_comment(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    on: str = typer.Option("thread", "--on", help="'thread' (default), a question (Q3) or an experiment (E2)."),
    author: str = _AuthorOpt,
    text: str = typer.Option(..., "--text"),
) -> None:
    """Add an owner comment."""
    from scout.lab import ops

    ops.comment(_vault(ctx), slug, on, _handle(author), text)
    _ok(slug, f"comment {on}")


@app.command("reply")
def cli_reply(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    on: str = typer.Option("thread", "--on", help="'thread' (default), a question (Q3) or an experiment (E2)."),
    to: int = typer.Option(..., "--to", help="Which top-level comment (1-based) Scout replies to."),
    text: str = typer.Option(..., "--text"),
) -> None:
    """Scout's reply to an owner comment."""
    from scout.lab import ops

    ops.reply(_vault(ctx), slug, on, to, text)
    _ok(slug, f"reply {on}")


@app.command("veto")
def cli_veto(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    eid: str = typer.Argument(..., metavar="EID"),
    author: str = _AuthorOpt,
) -> None:
    """Veto a planned experiment."""
    from scout.lab import experiments

    experiments.veto(_vault(ctx), slug, eid, _handle(author))
    _ok(slug, f"veto {eid}")


# --- propose ---------------------------------------------------------------------------


def _evidence_pairs(items: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in items:
        target, sep, why = item.partition("=")
        if not sep:
            raise LabError(f"--evidence {item!r} must look like TARGET=WHY")
        pairs.append((target, why))
    return pairs


@app.command("propose")
def cli_propose(
    ctx: typer.Context,
    slug: str = typer.Option(..., "--slug"),
    title: str = typer.Option(..., "--title"),
    question: str = typer.Option(..., "--question"),
    open_questions: list[str] = typer.Option(..., "--open", help="An opening question (repeatable)."),
    evidence: list[str] = typer.Option([], "--evidence", help="TARGET=WHY (repeatable)."),
    visibility: str = typer.Option("normal", "--visibility", help="normal or private."),
) -> None:
    """Propose a new thread (status proposed)."""
    from scout.lab import ops

    ops.propose(_vault(ctx), slug, title, question, list(open_questions), _evidence_pairs(list(evidence)), visibility)
    _ok(slug, "propose")


# --- questions, understanding, evidence, timeline --------------------------------------


@question_app.command("add")
def cli_question_add(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    text: str = typer.Option(..., "--text"),
    by: str | None = typer.Option(None, "--by", help="Who raised it."),
) -> None:
    """Raise a question. Prints 'ok <slug> question <QID>', then the new QID alone on the last line."""
    from scout.lab import ops

    who = _handle(by, allow_scout=True) if by is not None else None
    qid = ops.question_add(_vault(ctx), slug, text, who)
    _ok(slug, f"question {qid}")
    typer.echo(qid)


@question_app.command("answer")
def cli_question_answer(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    qid: str = typer.Argument(..., metavar="QID"),
    note: str = typer.Option(..., "--note"),
) -> None:
    """Answer a question."""
    from scout.lab import ops

    ops.question_answer(_vault(ctx), slug, qid, note)
    _ok(slug, f"answer {qid}")


@question_app.command("drop")
def cli_question_drop(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    qid: str = typer.Argument(..., metavar="QID"),
    reason: str = typer.Option(..., "--reason"),
    owner_comment: int | None = typer.Option(
        None, "--owner-comment", help="The owner comment (1-based) behind the drop."
    ),
) -> None:
    """Drop a question."""
    from scout.lab import ops

    ops.question_drop(_vault(ctx), slug, qid, reason, owner_comment)
    _ok(slug, f"drop {qid}")


@understanding_app.command("set")
def cli_understanding_set(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    file: Path = typer.Option(..., "--file", help="UTF-8 text file with the new prose."),
) -> None:
    """Replace '## Current understanding'."""
    from scout.lab import ops

    ops.understanding_set(_vault(ctx), slug, _read_text(file))
    _ok(slug, "understanding")


@evidence_app.command("add")
def cli_evidence_add(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    target: str = typer.Option(..., "--target"),
    why: str = typer.Option(..., "--why"),
) -> None:
    """List a KB note as evidence."""
    from scout.lab import ops

    ops.evidence_add(_vault(ctx), slug, target, why)
    _ok(slug, "evidence")


@timeline_app.command("add")
def cli_timeline_add(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    tag: str = typer.Option(..., "--tag", help="research run, experiment, owner or proposal."),
    did: str = typer.Option(..., "--did"),
    found: str = typer.Option(..., "--found"),
    questions: str = typer.Option(..., "--questions"),
    steers: str | None = typer.Option(None, "--steers"),
    evidence: str | None = typer.Option(None, "--evidence"),
) -> None:
    """Prepend a timeline entry."""
    from scout.lab import ops

    ops.timeline_add(_vault(ctx), slug, tag, did, found, questions, steers, evidence)
    _ok(slug, "timeline")


# --- experiments -----------------------------------------------------------------------


@experiment_app.command("plan")
def cli_experiment_plan(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    short: str = typer.Option(..., "--short"),
    title: str = typer.Option(..., "--title"),
    question: list[str] = typer.Option(..., "--question", help="A question ID this answers (repeatable)."),
    file: Path = typer.Option(..., "--file", help="UTF-8 markdown body (## Hypothesis, ## Method, ## Data)."),
    cap: float | None = typer.Option(None, "--cap", help="Cost cap in USD."),
) -> None:
    """Plan an experiment; prints 'ok <slug> plan <EID>'."""
    from scout.lab import experiments

    exp = experiments.plan(_vault(ctx), slug, short, title, list(question), _read_text(file), cap)
    _ok(slug, f"plan {exp.eid}")


@experiment_app.command("start")
def cli_experiment_start(
    ctx: typer.Context, slug: str = typer.Argument(...), eid: str = typer.Argument(..., metavar="EID")
) -> None:
    """planned -> running."""
    from scout.lab import experiments

    experiments.start(_vault(ctx), slug, eid)
    _ok(slug, f"start {eid}")


@experiment_app.command("abandon")
def cli_experiment_abandon(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    eid: str = typer.Argument(..., metavar="EID"),
    reason: str | None = _ReasonOpt,
) -> None:
    """planned or running -> abandoned."""
    from scout.lab import experiments

    experiments.abandon(_vault(ctx), slug, eid, reason or "abandoned")
    _ok(slug, f"abandon {eid}")


@experiment_app.command("finish")
def cli_experiment_finish(
    ctx: typer.Context,
    slug: str = typer.Argument(...),
    eid: str = typer.Argument(..., metavar="EID"),
    result_file: Path = typer.Option(..., "--result-file"),
    means_file: Path = typer.Option(..., "--means-file"),
    one_line: str = typer.Option(..., "--one-line"),
) -> None:
    """running -> done."""
    from scout.lab import experiments

    experiments.finish(_vault(ctx), slug, eid, _read_text(result_file), _read_text(means_file), one_line)
    _ok(slug, f"finish {eid}")
