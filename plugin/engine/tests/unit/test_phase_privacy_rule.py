"""The privacy phase rule (#453, spec 2026-10-20 §6.3, §8.1).

``phases/core/privacy.md`` reaches every brain file, and no assembled phase
reads the KB raw: the vault's history comes through ``scoutctl kb delta``,
queries through ``scoutctl kb query``, never ``parser.py``, ``git diff
HEAD~1`` or a shell ``cat``/``rg``/``grep``/``ls``/``find`` into
``knowledge-base/``. Slack sends go through the outbound-draft check.

The ``scout-ask`` skill (plan Task 10, spec §6.3 row 1) is held to the same
rule, and its commands are run against the privacy fixture vault.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.bootstrap import BootstrapConfig, _assemble
from scout.scripts.phase_assembly import parse_phase_file
from tests.eval.harness.checks.privacy_canary import raw_kb_read
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
KINDS = ("SKILL", "DREAMING", "RESEARCH", "RESPOND")
PRIVACY_HEADING = "## Privacy: owner-only and shared surfaces"
_WALK_KB = re.compile(
    r"\b(?:read|open|walk|scan|go through|cat|list)\s+(?:every|each|all)\b"
    r"[^.]{0,40}?`?(?:knowledge-base|action-items/context)",
    re.IGNORECASE,
)
# "read `knowledge-base/…`": an instruction to read a KB path that doesn't say through which command.
_READ_KB_PATH = re.compile(
    r"\b(?:read|reads|reading|consult|look at|look in|check|inspect|load)\b[^.`]{0,60}`[^`\s]*"
    r"(?:knowledge-base|action-items/context)/",
    re.IGNORECASE,
)


def _all_connectors() -> set[str]:
    phases = (PLUGIN_ROOT / "phases" / "connectors").glob("*.md")
    return {s.requires for f in phases for s in parse_phase_file(f) if s.requires}


def _assembled(kind: str, connectors: set[str]) -> str:
    cfg = BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="UTC",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=connectors,
        connector_inputs={},
        skip_jobs=True,
    )
    return _assemble(cfg, kind, custom={})


def _code_spans(text: str) -> list[str]:
    """Fenced code lines and inline backtick spans: where a phase tells the run to run something."""
    spans: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            spans.append(line)
        else:
            spans += re.findall(r"`([^`\n]+)`", line)
    return spans


@pytest.mark.parametrize("connectors", [set(), None], ids=["no-connectors", "all-connectors"])
@pytest.mark.parametrize("kind", KINDS)
def test_every_brain_file_carries_the_privacy_rule(kind: str, connectors: set[str] | None) -> None:
    text = _assembled(kind, _all_connectors() if connectors is None else connectors)
    assert text.count(PRIVACY_HEADING) == 1
    rule = text.split(PRIVACY_HEADING, 1)[1]
    for needle in (
        "scoutctl kb check-outbound .scout-cache/outbound-draft.md --surface slack",
        "scoutctl kb check-query",
        "--no-verify",
        "visibility: private",
        "denied",
        "never drops the wrap",
        "scoutctl kb read <path>",  # the sanctioned read of a note the run isn't editing
        "copy its title exactly as written",  # owner-only surfaces: no paraphrase, no "(private)" tag
        # Notes shared with the reader (Agnes Shared Areas §7, ruling 10): private to the reader, cited, never copied.
        "shared_knowledge_read",
        "`<owner>'s shared note <path>`",
        "`source: shared:<owner-email>:<path>`",
        "Never copy a note whole",
        # A shared note is kept current under the hook's checked-add rule (2026-11-19), not frozen.
        "Keep a note already shared with other people",
        "leave a line with a `%%privacy:allow%%` marker as it is",
        "sets or changes `federation_scope` (narrower too)",
        "`privacy.shared_edits: refuse`",
        "can't see a paraphrase or anything from email, Slack or meetings",
        "write only what came from this note, notes shared with the same people, or sources those people were in",
    ):
        assert needle in rule, needle
    assert "Never add to a note already shared" not in rule


CONTEXT_NOTES_LIST = "scripts/scoutctl kb files --glob 'action-items/context/*.md'"


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING"])
def test_runs_that_edit_context_notes_are_told_how_to_list_them(kind: str) -> None:
    """The 2026-10-21 eval: runs reached for `ls action-items/context/` with no sanctioned listing named."""
    assert CONTEXT_NOTES_LIST in _assembled(kind, _all_connectors())


def test_consolidation_keeps_the_personal_tasks_from_session_context() -> None:
    text = _assembled("SKILL", _all_connectors())
    (line,) = [ln for ln in text.splitlines() if "`personal_tasks`" in ln]
    assert ".scout-cache/session-context.json" in line and "daily file" in line


def test_the_privacy_phase_applies_to_every_mode() -> None:
    (section,) = parse_phase_file(PLUGIN_ROOT / "phases" / "core" / "privacy.md")
    assert section.mode == [] and section.requires is None


@pytest.mark.parametrize("kind", KINDS)
def test_no_assembled_phase_reads_the_kb_raw(kind: str) -> None:
    text = _assembled(kind, _all_connectors())
    assert "parser.py" not in text
    assert "git diff HEAD~1" not in text
    raw = [span for span in _code_spans(text) if raw_kb_read(span)]
    assert raw == []
    # Prose that sends the run through the KB file by file must name the scoutctl kb command to use.
    prose = [
        line
        for line in text.splitlines()
        if (_WALK_KB.search(line) or _READ_KB_PATH.search(line)) and "scoutctl kb" not in line
    ]
    assert prose == []
    # claude-sessions reads the git history of *other* repos (`git -C <repo> log`): not the vault's.
    vault_only = _assembled(kind, _all_connectors() - {"claude_sessions"})
    assert not re.search(r"\bgit (?:-C \S+ )?log\b", vault_only), "the vault's history comes through scoutctl kb delta"


def test_connector_kb_updates_find_notes_through_scoutctl_kb() -> None:
    """#507's eval: runs that read the connector KB-update sections reached for raw Reads of people,
    topic and project notes. Each such section names the sanctioned lookups."""
    sections = [
        s
        for f in sorted((PLUGIN_ROOT / "phases" / "connectors").glob("*.md"))
        for s in parse_phase_file(f)
        if s.slot == "update"
    ]
    assert len(sections) >= 4
    for s in sections:
        for needle in ("scoutctl kb name-lookup", "kb resolve", "kb read <path>", "Read only to edit"):
            assert needle in s.body, (s.name, needle)


def test_the_raw_read_detector() -> None:
    assert raw_kb_read("cat knowledge-base/people/sam.md")
    assert raw_kb_read("cd /v && rg -n permit knowledge-base/personal")
    assert raw_kb_read("ls knowledge-base/projects/")
    assert raw_kb_read("find /v/knowledge-base -name '*.md'")
    assert raw_kb_read("grep -rn x action-items/context/")
    assert not raw_kb_read("scoutctl kb search permit --glob 'knowledge-base/**'")
    assert not raw_kb_read("scoutctl kb search x --glob 'knowledge-base/**' | grep -c .")
    assert not raw_kb_read("git add knowledge-base/people/sam.md")
    assert not raw_kb_read("cat")
    assert not raw_kb_read("scoutctl kb search head --glob knowledge-base/**")
    assert not raw_kb_read("scripts/scoutctl kb search head --glob 'knowledge-base/**'")
    assert not raw_kb_read('git commit -m "dreaming [21:00]: tidy more knowledge-base notes"')
    assert not raw_kb_read("cd /v && git add -A && git commit -m 'cat knowledge-base'")
    assert raw_kb_read("cd knowledge-base && cat people/sam.md")
    assert raw_kb_read("cd knowledge-base/personal && ls")
    assert raw_kb_read("/bin/cat knowledge-base/x.md")
    assert raw_kb_read("git show HEAD:knowledge-base/x.md")
    assert raw_kb_read("git log -p -- knowledge-base")
    assert raw_kb_read("find . -name '*.md' | xargs -0 wc -l knowledge-base/people")
    assert raw_kb_read("tree knowledge-base")
    assert not raw_kb_read("git log --oneline -- knowledge-base")  # names only: not content


def test_the_prose_read_detector() -> None:
    assert _READ_KB_PATH.search("Before building the list, read `knowledge-base/scout-mistake-audit.md`.")
    assert _READ_KB_PATH.search("read that person's entity file (`knowledge-base/people/<slug>.md`)")
    assert _READ_KB_PATH.search("Check the newest shard(s) in `knowledge-base/session-log/` for the last row")
    assert _READ_KB_PATH.search("read `/v/action-items/context/PRMT.md`")
    assert not _READ_KB_PATH.search("Open questions, people | **Project page** `knowledge-base/projects/x.md`")
    assert not _READ_KB_PATH.search("Create a per-file item `knowledge-base/research-queue/<slug>.md`")


def test_the_prose_walk_detector() -> None:
    assert _WALK_KB.search("Read every file in `knowledge-base/`. Score each file")
    assert _WALK_KB.search("Open each note under knowledge-base/people")
    assert not _WALK_KB.search("Score every file in the cache")


@pytest.mark.parametrize(
    ("kind", "heading"), [("SKILL", "## Slack Notification"), ("RESEARCH", "## Step 4d: Slack Notification")]
)
def test_slack_wrap_sections_use_the_outbound_draft_step(kind: str, heading: str) -> None:
    text = _assembled(kind, _all_connectors())
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    assert ".scout-cache/outbound-draft.md" in section
    assert "check-outbound" in section


# --- the scout-ask skill (plan Task 10) ------------------------------------------------

SCOUT_ASK = PLUGIN_ROOT / "skills" / "scout-ask" / "SKILL.md"
KB_READ_COMMANDS = {
    "resolve",
    "files",
    "read",
    "search",
    "query",
    "related",
    "traverse",
    "path",
    "paths",
    "neighbors",
    "backlinks",
    "link-gaps",
}
# Vault folders the KB read gate doesn't judge (it covers knowledge-base/ and action-items/context/).
_SHELL_VAULT_READ = re.compile(r"\b(?:ls|rg|grep|find|cat|head|tail|tree)\s+[^|;&]*\b(?:action-items|meetings)/")
SAM = "knowledge-base/people/sam.md"
# The skill's placeholders, filled so its commands reach the fixture's private person note.
_PLACEHOLDERS = {"<name>": "Sammy", "<entity>": CANARIES[SAM], "<path>": SAM}
_AGENT = "agent:person:priya@example.com"


def _scout_ask() -> tuple[str, str]:
    """(frontmatter description, body)."""
    _, fm, body = SCOUT_ASK.read_text(encoding="utf-8").split("---\n", 2)
    (description,) = [ln.split(":", 1)[1].strip() for ln in fm.splitlines() if ln.startswith("description:")]
    return description, body


_SCOUTCTL = "<scoutctl> "


def _fenced_lines(body: str) -> list[str]:
    lines: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif fenced:
            lines.append(line)
    return lines


def _locator(body: str) -> str:
    (line,) = [ln for ln in _fenced_lines(body) if "kb vault" in ln]
    return line


_ELSEWHERE = "<elsewhere>"
# What step 0 makes of `<elsewhere>`: `--reader elsewhere` outside the vault, nothing inside it.
_ELSEWHERE_IS = {True: ["--reader", "elsewhere"], False: []}


def _fenced_kb_calls(body: str, *, outside: bool) -> list[list[str]]:
    """Each fenced ``<scoutctl> kb …`` line, as ``kb`` arguments with the placeholders filled.

    Its ``--repo <vault>`` is dropped: ``_kb`` passes the fixture vault's own. ``<elsewhere>`` becomes
    ``--reader elsewhere`` when ``outside`` (``in_vault`` false), else nothing."""
    calls: list[list[str]] = []
    for line in _fenced_lines(body):
        if line.startswith(_SCOUTCTL + "kb "):
            words = shlex.split(line.split("#", 1)[0])[2:]
            i = words.index("--repo")
            assert words[i + 1] == "<vault>", line
            del words[i : i + 2]
            assert words.count(_ELSEWHERE) == 1, line
            filled: list[str] = []
            for w in words:
                filled += _ELSEWHERE_IS[outside] if w == _ELSEWHERE else [_PLACEHOLDERS.get(w, w)]
            calls.append(filled)
    return calls


def test_scout_ask_reads_the_vault_through_scoutctl_kb() -> None:
    _, body = _scout_ask()
    spans = _code_spans(body)
    assert [s for s in spans if raw_kb_read(s)] == []
    assert [s for s in spans if _SHELL_VAULT_READ.search(s)] == []
    assert not re.search(r"python3?\s+\S*parser\.py", body)
    # Prose that sends the reader to a KB path names the `kb` command for it on the same line.
    assert [ln for ln in body.splitlines() if _READ_KB_PATH.search(ln) and not re.search(r"\bkb [a-z]", ln)] == []
    used = set(re.findall(r"\bkb ([a-z-]+)", body))
    assert used <= KB_READ_COMMANDS | {"vault"}, used - KB_READ_COMMANDS
    assert {"vault", "resolve", "files", "read", "search", "query"} <= used


def test_scout_ask_never_calls_a_kb_command_that_writes() -> None:
    """Read-only from outside the vault: every `kb` subcommand the CLI has that isn't a read stays out of the
    skill (share, unshare, install-hook, lint, export, …), named in prose or run."""
    from scout.cli import kb_app

    every = {c.name for c in kb_app.registered_commands if c.name}
    others = every - KB_READ_COMMANDS - {"vault"}
    assert {"share", "unshare", "install-hook"} <= others  # the list really holds the writers
    _, body = _scout_ask()
    assert set(re.findall(r"\bkb ([a-z-]+)", body)) & others == set()
    assert "**Read-only.**" in body and "never write, edit, move or commit anything in the vault" in body


def test_scout_ask_never_runs_anything_from_the_vault_or_a_bare_scoutctl() -> None:
    """A planted vault (a repo with scout-config.yaml and scripts/scoutctl) must never get a script run: the
    skill runs only the engine `kb vault` names, by absolute path. Every fenced line is the locator or a kb read."""
    _, body = _scout_ask()
    assert "<vault>/scripts" not in body and "scripts/scoutctl" not in body
    locator = _locator(body)
    for line in _fenced_lines(body):
        if line != locator:
            assert line.startswith(_SCOUTCTL + "kb ") and "--repo <vault>" in line, line
            assert shlex.split(line.split("#", 1)[0])[2] in KB_READ_COMMANDS, line
    # No command word in the locator resolves through PATH but the shell's builtins: no bare scoutctl or sed.
    commands = [seg.strip().lstrip("{ ").split(" ", 1)[0] for seg in re.split(r"[;|&]+|\|\|", locator)]
    assert "scoutctl" not in commands and "sed" not in commands, commands
    assert locator.endswith('"$S" kb vault --json')  # kb vault's own stderr ("not a Scout vault") is never hidden
    # `${CLAUDE_PLUGIN_ROOT}` exactly: Claude Code substitutes only that spelling in a plugin skill's text;
    # a `${CLAUDE_PLUGIN_ROOT:-…}` guard would be left for bash, where the variable is unset.
    assert "${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl" in locator and "${CLAUDE_PLUGIN_ROOT:-" not in body


def test_scout_ask_names_the_vault_on_every_call() -> None:
    """From another repo, `kb`'s default `--repo` is that repo: every call names the located vault."""
    _, body = _scout_ask()
    assert _fenced_kb_calls(body, outside=True)  # each one asserted its --repo <vault> and <elsewhere>
    # Every prose `kb <read>` span ends in `<elsewhere> --repo <vault>`, or its line says each one takes them.
    each = "(each with `<elsewhere> --repo <vault>`)"
    reads = "|".join(sorted(KB_READ_COMMANDS))
    prose = [(m, ln) for ln in body.splitlines() for m in re.findall(rf"`kb (?:{reads})\b[^`]*`", ln)]
    assert {m.split()[1] for m, _ in prose} >= {"read", "files", "search", "query", "related", "traverse", "path"}
    bare = [m for m, ln in prose if not m.endswith(" <elsewhere> --repo <vault>`") and each not in ln]
    assert bare == [], bare
    for needle in (
        "tell the user `reason` and stop",
        "Never `cd` into the vault",
        "never from the folder you are in",
        "name the vault you read",
    ):
        assert needle in body, needle


def test_scout_ask_triggers_from_any_cwd_when_a_vault_is_configured() -> None:
    description, _ = _scout_ask()
    assert "ANY working directory when a Scout vault is configured" in description
    assert "no vault is configured" in description
    assert "no vault is present" not in description and "working directory is a Scout vault" not in description


def test_scout_ask_treats_another_repo_as_a_shared_place() -> None:
    _, body = _scout_ask()
    section = body.split("### Outside the vault", 1)[1].split("\n## ", 1)[0]
    for needle in (
        "When `in_vault` is false",
        "shared place, like Slack",
        "`<elsewhere>` below is `--reader elsewhere` (inside the vault, nothing)",
        "hides private, local-only and shared-in notes",
        "context for your answer",
        "Never paste KB text, people notes or client details into commits, PR or issue text,"
        " code, comments, files or messages, that project's CLAUDE.md or auto-memory, subagent prompts, or a cloud"
        " or remote session",
        # The flag is the default enforcement; the personal exception and the prose stay as its backstop.
        "Drop it only when the user explicitly asks for their own private notes about their own life (health,"
        " family, money, career), and only for the reads answering that (ideally reading the note, not searching or"
        " listing). Use only those notes, only in your reply here; skip everything else those reads return.",
    ):
        assert needle in section, needle


# --- step 0's engine choice, run by bash as written --------------------------------------------------------

_STUB = '#!/bin/sh\necho "{who} $*"\nexit "${{STUB_EXIT:-0}}"\n'


def _stub(path: Path, who: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_STUB.format(who=who), encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def launcher(tmp_path: Path) -> dict[str, Path]:
    """A HOME, a plugin root, and a cloned repo (the cwd) with a planted vault, scoutctl, bin/scoutctl and sed."""
    repo = tmp_path / "cloned-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scout-config.yaml").write_text("{}\n", encoding="utf-8")
    for planted in ("scoutctl", "bin/scoutctl", "scripts/scoutctl", "sed"):
        _stub(repo / planted, "PLANTED")
    home = tmp_path / "home"
    home.mkdir()
    return {"repo": repo, "home": home, "plugin": tmp_path / "plugin"}


def _run_locator(where: dict[str, Path], *, substitute: bool, stub_exit: int = 0) -> tuple[int, str, str]:
    import subprocess

    line = _locator(_scout_ask()[1])
    if substitute:  # what Claude Code does to a plugin skill's text
        line = line.replace("${CLAUDE_PLUGIN_ROOT}", str(where["plugin"]))
    env = {"HOME": str(where["home"]), "PATH": ".:./bin:./scripts:/usr/bin:/bin", "STUB_EXIT": str(stub_exit)}
    proc = subprocess.run(["/bin/bash", "-c", line], cwd=where["repo"], env=env, capture_output=True, text=True)
    assert "PLANTED" not in proc.stdout + proc.stderr
    return proc.returncode, proc.stdout, proc.stderr


def _pointer_to(home: Path, scoutctl: str) -> None:
    from scout.scripts.engine_pointer import EnginePointer, write_pointer

    write_pointer(EnginePointer("0.0.0", "/e", "/e/python", scoutctl, "/v", "dev", "2026-01-01T00:00:00Z"), home=home)


def test_step0_prefers_the_plugins_own_engine(launcher: dict[str, Path]) -> None:
    _stub(launcher["plugin"] / "engine" / "bin" / "scoutctl", "plugin")
    _stub(launcher["home"] / ".local" / "bin" / "scoutctl", "shim")
    assert _run_locator(launcher, substitute=True) == (0, "plugin kb vault --json\n", "")


def test_step0_never_falls_back_on_an_exit_code(launcher: dict[str, Path]) -> None:
    """A denied or no-match exit from the first engine is the answer, not a cue to try another binary."""
    _stub(launcher["plugin"] / "engine" / "bin" / "scoutctl", "plugin")
    _stub(launcher["home"] / ".local" / "bin" / "scoutctl", "shim")
    assert _run_locator(launcher, substitute=True, stub_exit=1)[:2] == (1, "plugin kb vault --json\n")


@pytest.mark.parametrize("substitute", [True, False], ids=["plugin-missing", "not-substituted"])
def test_step0_falls_back_to_the_bootstrap_shim(launcher: dict[str, Path], substitute: bool) -> None:
    _stub(launcher["home"] / ".local" / "bin" / "scoutctl", "shim")
    assert _run_locator(launcher, substitute=substitute)[:2] == (0, "shim kb vault --json\n")


def test_step0_then_the_pointers_absolute_scoutctl(launcher: dict[str, Path], tmp_path: Path) -> None:
    _pointer_to(launcher["home"], str(_stub(tmp_path / "venv" / "bin" / "scoutctl", "pointer")))
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert _run_locator(launcher, substitute=True)[:2] == (0, "pointer kb vault --json\n")


def test_step0_trusts_the_pointers_scoutctl_only_inside_a_venv(launcher: dict[str, Path], tmp_path: Path) -> None:
    """Like the launcher (#637): a pointer naming some other executable, outside a venv, is ignored."""
    _pointer_to(launcher["home"], str(_stub(tmp_path / "not-a-venv" / "bin" / "scoutctl", "pointer")))
    code, out, err = _run_locator(launcher, substitute=True)
    assert (code, out) == (127, "") and "no Scout engine found" in err


def test_step0_tells_an_old_engine_from_no_vault() -> None:
    _, body = _scout_ask()
    assert "Exit 2: the engine is too old for this skill; say so and stop" in body


@pytest.mark.parametrize("pointed", [None, "bin/scoutctl", "scoutctl"], ids=["no-pointer", "relative", "bare"])
def test_step0_without_an_engine_stops_and_runs_nothing_from_the_repo(
    launcher: dict[str, Path], pointed: str | None
) -> None:
    if pointed is not None:
        _pointer_to(launcher["home"], pointed)
    code, out, err = _run_locator(launcher, substitute=True)
    assert (code, out) == (127, "") and "no Scout engine found" in err


def test_scout_ask_locator_finds_the_vault_from_another_repo(privacy_vault: Path, tmp_path: Path) -> None:
    """`kb vault`, run from a repo that isn't the vault, prints the configured vault and an engine to call."""
    import json
    import os

    other = tmp_path / "some-repo"
    other.mkdir()
    old = Path.cwd()
    os.chdir(other)
    try:
        result = CliRunner().invoke(app, ["kb", "vault", "--json"], env={"SCOUT_DATA_DIR": str(privacy_vault)})
    finally:
        os.chdir(old)
    doc = json.loads(result.stdout)
    assert (result.exit_code, doc["vault"], doc["in_vault"]) == (0, str(privacy_vault.resolve()), False)
    assert doc["scoutctl"] and Path(doc["scoutctl"]).is_absolute() and os.access(doc["scoutctl"], os.X_OK)


def test_scout_ask_keeps_the_session_reader_inside_and_narrows_to_elsewhere_outside() -> None:
    """`--reader "${SCOUT_READER:-owner}"` (the plan's wording) widens a SCOUT_MODE-only run, which
    fails, so inside the vault the skill's commands pass no reader. Outside it every one passes exactly
    `--reader elsewhere` (Jordan, 2026-11-19: hide private notes by default outside the vault). The label,
    denial and refusal rules are spelt out."""
    _, body = _scout_ask()
    assert "${SCOUT_READER" not in body
    inside, outside = _fenced_kb_calls(body, outside=False), _fenced_kb_calls(body, outside=True)
    assert len(inside) == len(outside) >= 4
    assert all("--reader" not in arg for call in inside for arg in call)
    for call in outside:
        assert call.count("--reader") == 1 and call[call.index("--reader") + 1] == "elsewhere", call
    # The only `--reader` the skill ever names is `--reader elsewhere`.
    assert set(re.findall(r"--reader \S+", body)) == {"--reader elsewhere`"}
    for needle in (
        "Pass no `--reader` but `<elsewhere>`",
        "`[private]`",
        "owner",
        "`denied` (exit 3)",
        "never read around it",
        # Exit 2 is a refused --reader, but also a usage error or an engine without `elsewhere`: never a cue to drop it.
        "**Exit 2** (refused `--reader`, bad argument, older engine): relay the engine's error and stop;"
        " never retry without `<elsewhere>`",
    ):
        assert needle in body, needle


def test_scout_ask_stays_cheap() -> None:
    """#84: the description is in every session's context, the body in every scout-ask turn."""
    description, body = _scout_ask()
    assert len(description) <= 600
    # Was 1250; +75 for step 0's engine choice and the outside-the-vault rule (#721 review). `<elsewhere>`
    # took no more: the prose the flag made redundant came out.
    assert len(body.split()) <= 1325


@pytest.fixture(scope="module")
def privacy_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("scout-ask"))


def _kb(vault: Path, args: list[str], env: dict[str, str]) -> tuple[int, str]:
    """``scoutctl kb …`` with ``env`` on top of the hermetic one (no SCOUT_* set: the owner)."""
    result = CliRunner().invoke(app, ["kb", *args, "--repo", str(vault)], env=env)
    return result.exit_code, result.output


@pytest.mark.parametrize(
    "env", [{}, {"SCOUT_MODE": "briefing"}, {"SCOUT_READER": "autonomous"}], ids=["owner", "run", "autonomous"]
)
def test_scout_ask_commands_label_a_private_note(privacy_vault: Path, env: dict[str, str]) -> None:
    """Inside the vault each fenced command runs as written, and those that reach the private person note label it."""
    calls = _fenced_kb_calls(_scout_ask()[1], outside=False)
    assert len(calls) >= 4
    labelled = 0
    for args in calls:
        code, out = _kb(privacy_vault, args, env)
        assert code in (0, 1), (args, out)  # 1 = no match (resolve, search); never a usage error
        if SAM in out:
            assert all(ln.startswith("[private] ") for ln in out.splitlines() if SAM in ln), (args, out)
            labelled += 1
    assert labelled >= 3  # resolve by alias, read, the entity-folder search


@pytest.mark.parametrize(
    "env", [{}, {"SCOUT_MODE": "briefing"}, {"SCOUT_READER": "autonomous"}], ids=["owner", "run", "autonomous"]
)
def test_scout_ask_commands_outside_the_vault_never_reach_a_private_note(
    privacy_vault: Path, env: dict[str, str]
) -> None:
    """Outside the vault the same commands, with `--reader elsewhere`, never show the private person note:
    resolve and search find nothing, and read of it is denied (exit 3: the skill stops)."""
    calls = _fenced_kb_calls(_scout_ask()[1], outside=True)
    assert len(calls) >= 4
    for args in calls:
        code, out = _kb(privacy_vault, args, env)
        assert CANARIES[SAM] not in out and SAM not in out, (args, out)
        assert code == 3 if args[0] == "read" else code in (0, 1), (args, out)


def test_scout_ask_commands_give_an_agent_reader_nothing(privacy_vault: Path) -> None:
    calls = _fenced_kb_calls(_scout_ask()[1], outside=False)
    assert len(calls) >= 4
    for args in calls:
        code, out = _kb(privacy_vault, args, {"SCOUT_READER": _AGENT})
        assert CANARIES[SAM] not in out and SAM not in out, (args, out)
        if args[0] == "read":
            assert code == 3 and '"denied": true' in out
        else:
            assert code in (0, 1), (args, out)


@pytest.mark.parametrize("reader", [_AGENT, "delegate:phone"], ids=["agent", "delegate"])
def test_scout_ask_outside_the_vault_under_an_agent_or_delegate_is_refused(privacy_vault: Path, reader: str) -> None:
    """`--reader elsewhere` can't narrow an agent (it would widen it) or swap for a delegate (another rank-1 reader
    with other rights): every outside call exits 2, shows nothing, and the skill relays the error and stops."""
    for args in _fenced_kb_calls(_scout_ask()[1], outside=True):
        code, out = _kb(privacy_vault, args, {"SCOUT_READER": reader})
        assert code == 2 and "--reader" in out, (args, out)
        assert CANARIES[SAM] not in out and SAM not in out, (args, out)
