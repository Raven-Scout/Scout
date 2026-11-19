"""kb-read-gate, widened after the KB usage audit of 2026-11-19: 131 of 302 raw shell reads into
knowledge-base/ got past it (sed 35, wc 26, grep 25, tail 19, ls 12), mostly through a variable or
loop holding a KB path. Only what is denied widens: every case in test_hooks_kb_read_gate.py stands."""

from __future__ import annotations

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.raw_read import is_kb_path, raw_kb_read
from tests.unit.test_hooks_kb_read_gate import RUN, _decision, _denied, _payload, _sh, vault  # noqa: F401

VAULT = "/home/alex/Scout"

# ----- the forms that got through -------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # through a variable holding a KB path (the audit's main form)
        "f=knowledge-base/projects/widgets/widgets.md; wc -c $f",
        "F=knowledge-base/projects/widgets/widgets.md; tail -c 600 $F 2>/dev/null",
        'P=knowledge-base/projects/widgets && sed -n 1,20p "$P/widgets.md"',
        "P=knowledge-base/projects/widgets; wc -c $P/widgets.md $P/knowledge/*.md",
        "export B=knowledge-base/projects; ls -d $B/*/ 2>/dev/null",
        "D=$P/knowledge; P=knowledge-base/projects/widgets; ls $D",
        "f=knowledge-base/people.md && head -7 ${f}",
        'f=knowledge-base/people.md; grep -n Priya "$f"',
        "f=knowledge-base/people.md; awk 'NR<5' $f",
        "f=knowledge-base/people.md; cat $f",
        "f=knowledge-base/people.md; less $f",
        # loops
        'for f in knowledge-base/projects/*/*.md; do wc -c "$f"; done',
        'for f in $(scripts/scoutctl kb files --glob "projects/**"); do tail -n 3 "$f"; done',
        'scripts/scoutctl kb files --glob "projects/**" | while read -r f; do head -7 "$f"; done',
        'mapfile -t xs < <(scripts/scoutctl kb files); wc -c "${xs[@]}"',
        # xargs fed with KB paths
        "scripts/scoutctl kb files --glob 'projects/*' | xargs wc -c",
        "scripts/scoutctl kb files | xargs -n 1 wc -c",
        "scripts/scoutctl kb search Priya | cut -d: -f1 | sort -u | xargs grep -c Priya",
        # shell keywords before the command
        "if true; then cat knowledge-base/people.md; fi",
        "while true; do sed -n 1p knowledge-base/people.md; break; done",
        # redirections
        "wc -c <knowledge-base/people.md",
        "while read l; do echo $l; done < knowledge-base/people.md",
        "echo $(<knowledge-base/people.md)",
        # quoting, braces, prefixes
        'cat know"ledge-base"/people.md',
        "cat {action-items/x.md,knowledge-base/people.md}",
        "timeout 10 cat knowledge-base/people.md",
        "nice -n 10 tail knowledge-base/people.md",
        "sudo -u alex cat knowledge-base/people.md",
        "bash -c 'cat knowledge-base/people.md'",
        "bash -c 'cd knowledge-base && ls'",
        'eval "sed -n 1,5p knowledge-base/people.md"',
        "pushd knowledge-base && ls",
        # more readers
        "stat knowledge-base/people.md",
        "du -a knowledge-base",
        "sort knowledge-base/people.md | uniq -c",
        "diff knowledge-base/people.md /tmp/old.md",
        "git grep Priya -- knowledge-base",
        "git blame knowledge-base/people.md",
    ],
)
def test_the_forms_that_got_through_are_flagged(command: str) -> None:
    assert raw_kb_read(command)


@pytest.mark.parametrize(
    "command",
    ["rg Priya", "grep -rn Priya .", "grep -r Priya", "grep -R --include=*.md Priya", "find . -name '*.md'",
     "find -name '*.md'", "ls -R", "tree", "du -a", "rg -l Priya --glob '**/*.md'", "cd docs && grep -r x .."],
)  # fmt: skip
def test_a_recursive_read_from_the_vault_root_is_flagged(command: str) -> None:
    assert raw_kb_read(command, cwd=VAULT, vault=VAULT)
    assert raw_kb_read(command, cwd="/home/alex", vault=VAULT)  # a folder above the vault, too


@pytest.mark.parametrize(
    "command",
    [
        # piping scoutctl kb's labelled output is fine; the deny message points at --lines / stat for it
        "scripts/scoutctl kb read knowledge-base/people.md | sed -n 45,75p",
        "scripts/scoutctl kb read knowledge-base/people.md | wc -l",
        "scripts/scoutctl kb read knowledge-base/people.md 2>&1 | head -40",
        "scripts/scoutctl kb read knowledge-base/people.md | grep -n Priya | tail -5",
        "scripts/scoutctl kb search Priya | grep -v archive | cut -c1-200",
        's=$(scripts/scoutctl kb read knowledge-base/people.md); echo "$s" | wc -c',
        "scripts/scoutctl kb read knowledge-base/people.md | while read -r line; do echo $line; done",
        "scripts/scoutctl kb files | xargs -I{} scripts/scoutctl kb read {}",
        # variables and loops over other folders
        "F=action-items/action-items-2026-11-10.md; grep -nE PROJ-1234 $F | cut -c1-600",
        "for k in PROJ-1 PROJ-2; do echo $k; awk -v k=$k 'x' $F; done",
        # commits, and reads elsewhere
        'scripts/git-safe-commit.sh "kb: people" knowledge-base/people.md 2>&1 | tail -5',
        "head -c 800 .scout-cache/run-health.json",
        "find action-items/meeting-prep -name '*.md' -delete",
        "bash scripts/check.sh",
        "ls -la",
    ],
)
def test_ordinary_run_commands_stay_allowed(command: str) -> None:
    assert not raw_kb_read(command)
    assert not raw_kb_read(command, cwd=VAULT, vault=VAULT)


@pytest.mark.parametrize(
    "command",
    ["grep -l Priya -r docs/wishlist", "rg Priya docs/", "rg -o x --glob 'action-items/*.md'",
     "grep -r --include=*.py x", "ls -la", "find docs -name x", "du -sh ."],
)  # fmt: skip
def test_a_recursive_read_bounded_away_from_the_kb_is_allowed(command: str) -> None:
    assert not raw_kb_read(command, cwd=VAULT, vault=VAULT)


def test_a_recursive_read_below_the_vault_root_is_allowed() -> None:
    assert not raw_kb_read("rg Priya", cwd=f"{VAULT}/docs", vault=VAULT)
    assert raw_kb_read("cd .. && rg Priya", cwd=f"{VAULT}/docs", vault=VAULT)


def test_without_the_vault_the_classifier_judges_as_before() -> None:
    # The eval harness calls it with the command alone.
    assert not raw_kb_read("rg Priya")


def test_a_brace_or_redirect_spelling_is_a_kb_path() -> None:
    assert is_kb_path("{a,knowledge-base}/x.md") and is_kb_path("<knowledge-base/x.md")


# ----- through the gate --------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "f=knowledge-base/people.md; wc -c $f",
        "scripts/scoutctl kb files | xargs wc -c",
        "rg Priya",
        "grep -rn Priya .",
    ],
)
def test_the_gate_denies_them_in_a_run(vault, command: str) -> None:  # noqa: F811
    reason = _denied(_decision(vault, "Bash", {"command": command}))
    assert "--lines A:B" in reason and "kb stat" in reason


def test_a_recursive_read_from_a_folder_below_the_vault_is_allowed(vault) -> None:  # noqa: F811
    (vault / "docs").mkdir()
    assert _decision(vault, "Bash", {"command": "rg Priya"}, cwd=vault / "docs") is None


def test_the_deny_message_keeps_its_old_words_and_names_the_new_options(vault) -> None:  # noqa: F811
    reason = _denied(_decision(vault, "Bash", {"command": "cat knowledge-base/people.md"}))
    assert reason.startswith(f"{gate.MARK} Scheduled runs don't read knowledge-base/")
    assert "kb read <path>" in reason and "kb read <path> --lines A:B" in reason
    assert "kb stat <path>" in reason and "wc" in reason


def test_the_owner_is_still_never_gated(vault) -> None:  # noqa: F811
    assert _decision(vault, "Bash", {"command": "f=knowledge-base/people.md; wc -c $f"}, env={}) is None


# ----- hooks.json: the shell sends the new forms to Python -----------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "scripts/scoutctl kb files | xargs wc -c",
        'for f in $(scripts/scoutctl kb files); do wc -c "$f"; done',
        "rg Priya",
        "grep -rn Priya .",
        "find . -name x",
        "ls -R",
    ],
)
def test_the_shell_sends_them_to_python(vault, tmp_path, command: str) -> None:  # noqa: F811
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2  # Python was needed (and, missing here, fails closed)


@pytest.mark.parametrize("command", ["ls notes", "ls -la", "git status", "cat notes/a.md"])
def test_ordinary_commands_still_exit_in_the_shell(vault, tmp_path, command: str) -> None:  # noqa: F811
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_a_variable_read_is_denied_through_the_real_cli(vault) -> None:  # noqa: F811
    import json

    payload = _payload("Bash", {"command": "f=knowledge-base/people.md; wc -c $f"}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ----- #734 review: what the first round missed ------------------------------------------------

ENV = {"HOME": "/home/alex", "SCOUT_DATA_DIR": VAULT}


def judged(command: str, cwd: str = VAULT) -> bool:
    return raw_kb_read(command, cwd=cwd, vault=VAULT, env=ENV)


@pytest.mark.parametrize(
    "command",
    [
        # git's own options before the subcommand (main denied the `-C ''` forms; keep it so)
        "git -C '' show HEAD:knowledge-base/people.md",
        'git -C "" log -p -- knowledge-base',
        "git --no-pager show HEAD:knowledge-base/people.md",
        "git -P diff HEAD~1 -- knowledge-base",
        "git -c core.pager=cat show HEAD:knowledge-base/people.md",
        "git grep salary",
        "git -C . grep -n salary",
        # command substitution that runs a path-printing scoutctl kb
        "cat $(scripts/scoutctl kb files)",
        "wc -c $(scripts/scoutctl kb files --glob 'projects/**')",
        "head `scripts/scoutctl kb files`",
        "files=$(scripts/scoutctl kb files); wc -c $files",
        # recursive reads that reach a KB note, as the Grep tool's rule has it
        "rg salary action-items",
        "grep -r x action-items",
        "grep --recursive salary .",
        "rg x ~/Scout",
        'rg x "$SCOUT_DATA_DIR"',
        "cd ~/Scout && rg x",
        "rg -m 5 x",
        "rg --max-depth 9 x",
        "grep -r --max-count 5 x",
        "find action-items -name '*.md' -exec cat {} +",
        "rg x -g '!knowledge-base/**'",  # action-items/context is still in reach
        # prefixes
        "timeout -s KILL 10 cat knowledge-base/people.md",
        "env -C knowledge-base cat people.md",
        "env --chdir=knowledge-base cat people.md",
    ],
)
def test_the_review_forms_are_flagged(command: str) -> None:
    assert judged(command)


@pytest.mark.parametrize(
    "command",
    [
        "rg --version",
        "command -v rg",
        "find --version",
        "rg --files scripts/",
        "find . -maxdepth 1 -name x",
        "n=$(scripts/scoutctl kb files | wc -l); head -n $n scripts/list.txt",
        "git add knowledge-base/people.md; ls scripts | xargs wc -l",
        "git grep salary -- scripts/",
        "git log --oneline -5",
        "ls -R action-items",  # names only, from a KB folder's parent
        "find action-items -name x",
        "tree -L 1",
        "d=scripts; rg x $d",
        "x=$(scripts/scoutctl kb search foo | head -1); echo $x",
    ],
)
def test_the_review_false_denies_are_allowed(command: str) -> None:
    assert not judged(command)


def test_an_exclusion_main_already_denied_stays_denied() -> None:
    # main matches `=knowledge-base` as a KB path: the gate only widens, so this keeps its deny.
    assert raw_kb_read("grep -r --exclude-dir=knowledge-base x docs")


def test_a_content_search_the_gate_cant_place_is_denied() -> None:
    assert judged('rg x "$(git rev-parse --show-toplevel)"')
    assert not judged('ls -R "$UNSET_DIR"')  # a name listing it can't place stays allowed


def test_the_eval_counts_a_recursive_read_from_the_vault() -> None:
    from tests.eval.harness.checks.privacy_canary import leading_indicators
    from tests.eval.harness.metrics import ToolCall

    call = ToolCall(seq=0, name="Bash", input={"command": "rg salary"}, is_error=False, position=0.0)
    assert leading_indicators([call], vaults=[VAULT])["raw_kb_read"] == 1
    assert leading_indicators([call])["raw_kb_read"] == 0


@pytest.mark.parametrize(
    "command",
    [
        "cat know'ledge-base'/people.md",
        "cat know\\ledge-base/people.md",
        "files=$(scripts/scoutctl kb files); wc -c $files",
        "cat $(scripts/scoutctl kb files)",
        "head `scripts/scoutctl kb files`",
        "grep --recursive salary .",
        "git grep salary",
        "git -C '' show HEAD:knowledge-base/people.md",
    ],
)
def test_the_shell_and_python_deny_them_together(vault, command: str) -> None:  # noqa: F811
    import json

    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", command


def test_the_deny_message_says_how_to_search_elsewhere(vault) -> None:  # noqa: F811
    reason = _denied(_decision(vault, "Bash", {"command": "rg salary"}))
    assert "name one outside the KB" in reason and "rg x scripts/" in reason
