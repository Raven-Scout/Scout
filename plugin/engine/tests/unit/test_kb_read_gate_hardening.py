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
