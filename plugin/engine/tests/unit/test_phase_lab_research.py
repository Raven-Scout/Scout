"""The research run works the Lab (spec §6, §6.4, §7, §11): comments, experiments, threads, proposals.

``research-targets.md`` chooses the run's work from the lab block, and ``lab-experiments.md``
runs and plans experiments. These pin the phrases a run must follow, the order of work, and
that every ``scoutctl lab`` command the text names is a real command.
"""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from scout.lab.cli import app as lab_app
from scout.scripts.phase_assembly import parse_phase_file

PLUGIN = Path(__file__).resolve().parents[3]
TARGETS = PLUGIN / "phases" / "research" / "research-targets.md"
EXPERIMENTS = PLUGIN / "phases" / "research" / "lab-experiments.md"
DEEP = PLUGIN / "phases" / "research" / "deep-research.md"
INTEGRATION = PLUGIN / "phases" / "research" / "knowledge-integration.md"
COMMIT = PLUGIN / "phases" / "research" / "commit-notify.md"
INBOX = PLUGIN / "phases" / "core" / "inbox-processing.md"
RUNNER = PLUGIN / "templates" / "run-research.sh.tmpl"
SUB_GROUPS = {"question", "understanding", "evidence", "timeline", "experiment"}  # ruling R1
# `scoutctl lab <word>( <word>)?`, and a backticked short form (`lab reply …`) the text uses after naming scoutctl.
_LAB_COMMAND = re.compile(r"(?:scoutctl |`)lab ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """The text with line breaks folded into spaces, so a pinned phrase may wrap."""
    return re.sub(r"\s+", " ", _text(path))


def test_targets_phase_pins_the_run_order() -> None:
    text = _flat(TARGETS)
    for phrase in (
        "# PHASE 1: THE LAB — CHOOSE THIS RUN'S WORK",
        ".scout-cache/session-context.json",
        "scripts/scoutctl lab list --json",
        "scripts/scoutctl lab show <slug> --json",
        "Owner comments come first",
        "declined, with the reason",
        "A paused thread's comments are answered; the thread is not researched",
        "--owner-comment <n>",
        "Work 1–2 threads in depth",
        "scripts/coverage-mandates.py",
        "do not extend the dates",
        "No research outside threads",
        "`lab.max_proposals_per_run`",
        "`lab.max_open_proposals`",
        "`lab.target_threads`",
        "`lab.stale_runs`",
        "--visibility private",
        "kb query --visibility normal",
        "never edit a lab file by hand to get around a refusal",
    ):
        assert phrase in text, phrase
    assert text.index("Owner comments come first") < text.index("Work 1–2 threads in depth")
    # Steps run in the order the spec gives: comments, experiments, threads, scope, proposals, refusals.
    steps = [text.index(f"## Step 1{c}") for c in "abcdefg"]
    assert steps == sorted(steps)


def test_targets_phase_drops_the_queue() -> None:
    text = _text(TARGETS)
    for gone in ("research-queue/", "research_queue", "priority: urgent", "graph-completeness"):
        assert gone not in text, gone


def test_targets_phase_keeps_its_frontmatter() -> None:
    (section,) = parse_phase_file(TARGETS)
    assert section.slot == "target-selection" and section.requires is None


def test_an_unactionable_owner_comment_still_gets_a_declining_reply() -> None:
    """Review Focus 2: out of scope, ambiguous or forbidden, the comment is answered, never skipped."""
    text = _flat(TARGETS)
    step = text.split("## Step 1b", 1)[1].split("## Step 1c", 1)[0]
    assert "scripts/scoutctl lab reply <slug>" in step
    assert "declined, with the reason" in step
    assert "never skipped because it is hard or out of scope" in step
    assert "1-based position" in step


def test_an_empty_lab_proposes_from_entity_scoring(tmp_path: Path) -> None:
    """Review Focus 3: no `knowledge-base/lab/` at all is an empty thread list, and the run proposes."""
    step = _flat(TARGETS).split("## Step 1f", 1)[1].split("## Step 1g", 1)[0]
    assert "empty lab" in step and "entity scoring" in step
    assert "scripts/scoutctl kb query --visibility normal" in step
    assert "7 days" in step
    # The text's premise: a vault without a lab lists no threads, rather than failing.
    result = CliRunner().invoke(lab_app, ["--vault", str(tmp_path), "list", "--json"])
    assert result.exit_code == 0 and '"threads": []' in result.output


def test_a_refused_lab_write_stops_work_on_that_thread() -> None:
    """Review Focus 4: the owner pauses a thread, or breaks its front page, mid-run."""
    step = _flat(TARGETS).split("## Step 1g", 1)[1]
    assert "stop working that thread" in step
    assert "Still reply to its comments" in step
    assert "wrap" in step
    assert "never edit a lab file by hand to get around a refusal" in step


def test_experiments_phase_pins_the_rules() -> None:
    text = _flat(EXPERIMENTS)
    for phrase in (
        "# PHASE 2E: EXPERIMENTS",
        "--timeout 540",
        "600000",
        "experiment ran: exit <n>",
        "experiment refused:",
        "experiment not run:",
        "--detach",
        "experiment started: detached",
        "experiment running:",
        "scoutctl lab experiment collect",
        "$SCOUT_LAB_WEIGHTS_DIR",
        "checkpoints/",
        "No experiments in private threads",
        "Never edit a planned experiment's note or scripts after committing them",
        "$SCOUT_LAB_SOURCE_DIR",
        "is a result",
        "`lab.experiment_notice_hours`",
        "--timeout-hours",
        "--model",
        "--disk-budget",
        "scripts/scoutctl lab experiment call --prompt-file",
        "--result-file",
        "--means-file",
        "--one-line",
        "--tag experiment",
    ):
        assert phrase in text, phrase


def test_experiments_phase_reads_each_status_line_the_right_way() -> None:
    text = _flat(EXPERIMENTS)
    collect = text.split("Collect detached experiments first", 1)[1].split("**Before running", 1)[0]
    # A collected `not run` is the experiment's final outcome, except a survivor of the sweep.
    assert "abandon" in collect and "survived the sweep" in collect
    run = text.split("**Read the last stderr line", 1)[1].split("**Finish", 1)[0]
    refused = run.split("experiment refused:", 1)[1].split("experiment not run:", 1)[0]
    assert "abandon" in refused and "new EID" in refused
    not_run = run.split("experiment not run:", 1)[1].lower()
    assert "leave it `running`" in not_run and "wrap" in not_run
    assert "no broker to run it" in not_run
    # Experiments off still means planning them.
    assert "experiments are off" in text and "still plan" in text
    # Scout never stops an experiment: that verb is the owner's.
    assert "Never call `scripts/scoutctl lab experiment stop`: stopping a running experiment is the owner's." in text
    # A run meets 2E before PHASE 1 in the assembled brain (files sort by name).
    assert text.index("# PHASE 2E: EXPERIMENTS") < text.index("You reach this phase from Step 1c (PHASE 1).")
    assert "retry `running` experiments not marked `detached`" in text


def _refused(text: str) -> str:
    run = text.split("**Read the last stderr line", 1)[1].split("**Finish", 1)[0]
    return run.split("experiment refused:", 1)[1].split("experiment not run:", 1)[0]


def test_a_used_allowance_is_not_a_refusal_of_the_experiment() -> None:
    """Exit 5 says `experiment refused: this run has run its …`: abandoning on it re-plans every second experiment."""
    text = _flat(EXPERIMENTS)
    assert "`lab.experiments_per_run` (default 1)" in text
    refused = _refused(text)
    allowance = refused.split("exit 5", 1)[1]
    assert "this run has run its" in allowance
    assert "is not a refusal of the experiment" in allowance
    assert "stop running experiments this run and leave that one as it is" in allowance
    # The allowance rule comes before the abandon rule, so a run meets it first.
    assert refused.index("exit 5") < refused.index("abandon")


def test_a_refusal_never_replans_a_veto() -> None:
    refused = _refused(_flat(EXPERIMENTS))
    veto = refused.split("a veto (", 1)[1].split("anything else", 1)[0]
    # The broker's veto wordings, and the two it uses when it can't prove there is none.
    assert "`vetoed`" in veto and "`a veto can't be ruled out`" in veto
    assert "never re-plan it" in veto and "unless an owner comment invites it" in veto and "Vetoes" in veto
    # The re-plan rule is only for what is left.
    assert refused.index("a veto (") < refused.index("new EID")


def test_a_held_experiment_runs_only_if_its_comment_doesnt_object() -> None:
    """The run that reads a held experiment's comment decides; a later retry checks only what it can see."""
    text = _flat(EXPERIMENTS)
    hold = _refused(text).split("`held:`", 1)[1].split("a veto (", 1)[0]
    assert (
        "answer it per Step 1b now; if the comment objects, `lab experiment abandon` it with the reason now;"
        " otherwise leave it `running` for the next run." in hold
    )
    assert "Next time" not in hold  # a later run can't see the refusal, so it can't make the call
    retry = text.split("retry `running` experiments not marked `detached`", 1)[1].split("3. **Then", 1)[0]
    assert (
        "Before retrying a `running` experiment, read the owner comments on its line and on its questions in"
        " `lab show`; if one objects, abandon it with the reason." in retry
    )
    assert "refused `held:`" not in retry  # nothing records an earlier refusal


def test_collect_keeps_broker_side_answers_running() -> None:
    """Ruling P2-R12: a collect `not run` is final only when it is the experiment's own outcome."""
    collect = _flat(EXPERIMENTS).split("Collect detached experiments first", 1)[1].split("**Before running", 1)[0]
    keep = collect.split("Some reasons are not final", 1)[1]
    for reason in (
        "this run's broker didn't answer",
        "collecting a detached experiment needs the run broker",
        "survived the sweep",
    ):
        assert reason in keep, reason
    assert "leave it `running`" in keep and "wrap" in keep


def test_detached_experiments_are_collected_in_paused_threads_too() -> None:
    collect = _flat(EXPERIMENTS).split("Collect detached experiments first", 1)[1].split("**Before running", 1)[0]
    assert "in `active` and `paused` threads" in collect
    assert "In a paused thread, only report the result in the wrap" in collect
    assert "`finish` and `abandon` wait until the thread resumes" in collect


def test_experiments_phase_never_commits_checkpoints() -> None:
    text = _flat(EXPERIMENTS)
    assert "by explicit path" in text and "never `checkpoints/`" in text
    assert "10 MiB" in text


def test_both_phases_are_research_only() -> None:
    for path in (TARGETS, EXPERIMENTS):
        for section in parse_phase_file(path):
            assert section.mode == ["research"], path.name
    (experiments,) = parse_phase_file(EXPERIMENTS)
    assert experiments.slot == "experiments" and experiments.name == "Lab Experiments"
    assert experiments.requires is None


def _named_commands() -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for path in (TARGETS, EXPERIMENTS, DEEP, INTEGRATION, COMMIT, INBOX):
        for first, second in _LAB_COMMAND.findall(_text(path)):
            found.add((first, second) if first in SUB_GROUPS and second else (first,))
    return found


def test_every_lab_command_named_exists() -> None:
    found = _named_commands()
    assert {("experiment", "collect"), ("experiment", "stop"), ("reply",), ("propose",)} <= found
    # Step 3d's thread updates, the wrap's gather and the inbox route.
    assert {
        ("understanding", "set"),
        ("question", "answer"),
        ("evidence", "add"),
        ("timeline", "add"),
        ("list",),
        ("comment",),
    } <= found
    runner = CliRunner()
    for words in sorted(found):
        result = runner.invoke(lab_app, [*words, "--help"])
        assert result.exit_code == 0, (words, result.output)


# --- Task 11: deep research, integration, commit and wrap -----------------------------------------------


def _section(path: Path, heading: str) -> str:
    """The body of the one section in ``path`` whose text holds ``heading``."""
    (section,) = [s for s in parse_phase_file(path) if heading in s.body]
    return re.sub(r"\s+", " ", section.body)


def test_deep_research_targets_a_threads_open_question() -> None:
    text = _flat(DEEP)
    assert "A research target is a thread's open question" in text.split("## Step 2a", 1)[0]
    step = text.split("## Step 2a", 1)[1].split("## Step 2b", 1)[0]
    assert "front page" in step and "Evidence" in step
    assert "scripts/scoutctl kb read" in step


def test_integration_and_wrap_pin_the_thread_updates() -> None:
    integration = _flat(INTEGRATION)
    assert '--tag "research run"' in integration
    assert "a sentence a cold reader understands" in integration
    slack = _section(COMMIT, "## Step 4d: Slack Notification")
    telegram = _section(COMMIT, "## Step 4d: Telegram Notification")
    for wrap in (slack, telegram):
        assert "comments still waiting" in wrap
        assert "awaiting your approval" in wrap
    assert "only normal-visibility threads" in slack


def test_step_3d_updates_the_thread_after_the_topic_notes() -> None:
    text = _flat(INTEGRATION)
    step = text.split("## Step 3d: Update the thread", 1)[1]
    for phrase in (
        "lab understanding set <slug> --file",
        "under 2 KB",
        "lab question answer",
        "drop",
        "add",
        "lab evidence add <slug> --target",
        "--why",
        'lab timeline add <slug> --tag "research run" --did',
        "--found",
        "--questions",
        "[--steers",
        "per touched thread",
        "never a commit subject or a file list",
    ):
        assert phrase in step, phrase
    assert text.index("## Step 3a") < text.index("## Step 3d")
    step_3c = text.split("## Step 3c", 1)[1].split("## Step 3d", 1)[0]
    assert "Do NOT create action items" in step_3c
    assert "research→Scout findings path" in step_3c


def test_commit_names_threads_and_lists_lab_files() -> None:
    commit = _section(COMMIT, "## Step 4a: Commit")
    step_4a = commit.split("## Step 4a", 1)[1].split("## Step 4b", 1)[0]
    assert '"research [HH:MM]: <threads worked and what moved>"' in step_4a
    assert "knowledge-base/lab/" in step_4a
    step_4b = commit.split("## Step 4b", 1)[1].split("## Step 4c", 1)[0]
    assert "threads worked" in step_4b and "entities updated" not in step_4b


def test_both_wraps_gather_the_lab_at_wrap_time() -> None:
    for heading in ("## Step 4d: Slack Notification", "## Step 4d: Telegram Notification"):
        wrap = _section(COMMIT, heading)
        for phrase in (
            "scripts/scoutctl lab list --json",
            "one line per touched thread",
            "`unanswered_comments`",
            "`active` and `paused`",
            "`not run`",
            "still running",
            "elapsed",
            "`checkpoint_bytes`",
            "`needs_you.proposals`",
        ):
            assert phrase in wrap, (heading, phrase)


def test_slack_wrap_never_names_a_private_thread_and_telegram_may() -> None:
    """Review Focus 5: the Slack wrap never names a private thread, though the Telegram wrap may."""
    slack = _section(COMMIT, "## Step 4d: Slack Notification")
    assert "private threads also moved" in slack
    telegram = _section(COMMIT, "## Step 4d: Telegram Notification")
    assert "may name `[private]` items and private threads by their exact title" in telegram


def test_research_runner_prompt_follows_the_lab_phases() -> None:
    """Ruling P2-R13: the run prompt no longer sends a run to the retired research queue."""
    text = _text(RUNNER)
    prompt = text[text.index('PROMPT="') : text.index('"\n', text.index('PROMPT="') + 8)]
    assert "research-queue" not in prompt and "score entities" not in prompt
    steps = [
        "Step 2: Execute Phase 1 (The Lab)",
        "owner comments first",
        "PHASE 2E",
        "Step 3: Execute Phase 2 (Deep Research)",
        "Step 4: Execute Phase 3 (Knowledge Integration)",
        "Step 3d",
        "Step 5: Commit",
        "Step 6: Send the wrap notification",
    ]
    at = [prompt.index(s) for s in steps]
    assert at == sorted(at), "the runner's steps are out of order"


def test_inbox_research_entries_go_to_the_lab() -> None:
    """Ruling P2-R14: an inbox idea joins a matching thread as an owner comment, else waits for a proposal."""
    text = _flat(INBOX)
    assert "research-queue" not in text
    route = text.split("**Research/idea**", 1)[1].split("4. **Project-specific**", 1)[0]
    for phrase in (
        "`active` or `paused`",
        'scripts/scoutctl lab comment <slug> --text "Q: <entry>" --author',
        "{{USER_NAME}}",
        "[for research]",
    ):
        assert phrase in route, phrase
    proposals = _flat(TARGETS).split("## Step 1f", 1)[1].split("## Step 1g", 1)[0]
    assert "`[for research]`" in proposals and "inbox.md" in proposals
    assert "once its proposal is made" in proposals


def test_run_sandbox_allows_lab_writes(tmp_path: Path) -> None:
    """Every `scoutctl lab` write a run makes lands in the lock file and the thread folder."""
    import os
    import subprocess

    from scout.kb import hook_paths
    from scout.sandbox import launch, profile

    root = tmp_path.resolve()
    home, vault = root / "home", root / "vault"
    (home / ".claude").mkdir(parents=True)
    vault.mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "SCOUT_SECRETS_DIR", "GIT_DIR")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    env.pop("GIT_CONFIG_GLOBAL", None)
    env.pop(launch.ENV_SWITCH, None)
    hook_paths._ROOTS_CACHE.clear()
    rules = profile.build_rules(vault, env)
    for path in (vault / ".scout-state" / "lab.lock", vault / "knowledge-base" / "lab" / "t" / "t.md"):
        assert profile.decide(rules, str(path)) == "allow", path
