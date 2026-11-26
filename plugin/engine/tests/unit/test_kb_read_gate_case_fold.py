"""kb-read-gate compares program names folded to lower case: on a case-insensitive disk (macOS's default) the shell
finds a program in any letter case, so ``FIND``, ``RG``, ``NOHUP``, ``XARGS`` and ``GIT`` run find, rg, nohup, xargs
and git. A builtin or keyword the shell knows only as spelled (``cd``, ``exec``, ``eval``) keeps its spelling:
``CD x`` runs /usr/bin/cd in a child and moves nothing, ``EXEC x`` is "command not found". ``command`` and ``time``
fold, as /usr/bin/command and /usr/bin/time run the word after them. Only the program's name folds, never a path or
an argument.

Synthetic vault only; the shell pre-filter's side is in test_hook_prefilter_differential.py.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import live_run
from scout.kb.raw_read import SHELL_ONLY, fold, program_name, raw_kb_read
from tests.unit.test_hooks_kb_read_gate import RUN, _denied, _payload


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "vault"
    for rel in ("knowledge-base/a.md", "knowledge-base/people/priya.md", "action-items/context/x.md", "docs/a.md"):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n")
    (v / "Find.md").write_text("x\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v.resolve()


def _run(vault: Path, command: str, cwd: Path | None = None):
    return gate.run(stdin=_payload("Bash", {"command": command}, cwd or vault), env={**RUN, "HOME": str(vault.parent)})


# ----- the name rule ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "program"),
    [
        ("FIND", "find"),
        ("Rg", "rg"),
        ("nOhUp", "nohup"),
        ("PYTHON3", "python3"),
        ("COMMAND", "command"),
        ("TIME", "time"),
        ("CD", "CD"),
        ("EXEC", "EXEC"),
        ("Eval", "Eval"),
        ("cd", "cd"),
        # APFS folds the Unicode way: the ﬁ ligature, the long s and ß
        ("ﬁnd", "find"),
        ("xargſ", "xargs"),
        ("ſh", "sh"),
        ("ßh", "ssh"),
        ("oſaſcript", "osascript"),
        ("ＦＩＮＤ", "find"),  # fullwidth: over-denied, never let through
        ("ſet", "ſet"),  # folds to a builtin, so it is not the builtin
    ],
)
def test_a_program_name_folds_and_a_builtin_keeps_its_spelling(word: str, program: str) -> None:
    assert program_name(word) == program


@pytest.mark.parametrize("word", ["café", "ßh", "xärgs", "Ångström", "ﬁnd"])
def test_an_nfd_spelling_folds_as_the_nfc_one(word: str) -> None:
    """APFS compares names normalisation-insensitively: an NFD spelling is the same file."""
    assert fold(unicodedata.normalize("NFD", word)) == fold(unicodedata.normalize("NFC", word))


def test_command_and_time_are_programs_too() -> None:
    assert not {"command", "time"} & SHELL_ONLY


# ----- what a run is denied in any letter case ------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "NOHUP sleep 100",
        "SETSID sleep 100",
        "Nohup sleep 100",
        "/usr/bin/NOHUP sleep 1",
        "/usr/bin/nice NOHUP sleep 1",
        "NICE NOHUP sleep 1",
        "SUDO NOHUP sleep 1",
        "COMMAND NOHUP sleep 1",
        "'NOHUP' sleep 1",
        "\\NOHUP sleep 1",
        "NO''HUP sleep 1",
        "BASH -c 'NOHUP sleep 1'",
        "OSASCRIPT -e beep",
        "Launchctl list",
    ],
)
def test_a_detach_word_in_any_case_is_denied(vault: Path, command: str) -> None:
    assert "foreground" in _denied(_run(vault, command))


@pytest.mark.parametrize(
    "command",
    [
        "FIND . -name a.md",
        "RG foo .",
        "Rg foo",
        "COMMAND FIND . -name a.md",
        "command FIND . -name a.md",
        "exec RG foo .",
        "TIME RG foo .",
        "'FIND' . -name a.md",
        "F''IND . -name a.md",
        "\\RG foo .",
        "SH -c 'rg foo .'",
        "GREP -r foo .",
        "UGREP -r foo .",
        "UG -r foo .",
        "Fdfind -e md . .",
        "ACK-GREP foo",
        "LS -R .",
        "Tree .",
        "GIT grep foo",
        "git GREP foo",
    ],
)
def test_a_recursive_read_from_the_vault_root_in_any_case_is_denied(vault: Path, command: str) -> None:
    reason = _denied(_run(vault, command))
    assert "skips the privacy labels" in reason or "uses git only in a few read forms" in reason  # grep: off the list


@pytest.mark.parametrize(
    "command",
    [
        "echo knowledge-base/a.md | XARGS cat",
        "echo knowledge-base/a.md | Xargs -I{} head -1 {}",
        "SCOUTCTL kb files | XARGS cat",
        "scripts/Scoutctl kb files | xargs cat",
        "echo knowledge-base/a.md | PARALLEL cat",
    ],
)
def test_xargs_fed_kb_paths_in_any_case_is_denied(vault: Path, command: str) -> None:
    assert _denied(_run(vault, command))


@pytest.mark.parametrize(
    "command",
    [
        "CAT knowledge-base/a.md",
        "Head -3 knowledge-base/a.md",
        "GIT show HEAD:knowledge-base/a.md",
        "git SHOW HEAD:knowledge-base/a.md",
    ],
)
def test_a_kb_read_by_a_program_in_any_case_is_denied(vault: Path, command: str) -> None:
    assert _denied(_run(vault, command))


@pytest.mark.parametrize(
    "command",
    [
        "GIT -c core.hooksPath=/dev/null commit -m x",
        "Git -c core.hooksPath=/dev/null commit -m x",
        "/usr/bin/GIT -c core.hooksPath=/dev/null commit -m x",
        "Git commit --no-verify -m x",
        "git COMMIT --no-verify -m x",
        "TIMEOUT 5 GIT commit --no-verify -m x",
        "GIT commit-tree abc",
    ],
)
def test_a_git_hook_skip_in_any_case_is_denied(vault: Path, command: str) -> None:
    assert "commit hooks" in _denied(_run(vault, command))


def test_env_in_any_case_that_clears_the_run_is_denied(vault: Path) -> None:
    assert "SCOUT_MODE" in _denied(_run(vault, "Env -i X=1 sh"))
    assert "SCOUT_MODE" in _denied(_run(vault, "ENV -u SCOUT_MODE ls"))


def test_an_interpreter_in_any_case_that_writes_agent_settings_is_denied(vault: Path) -> None:
    assert _denied(_run(vault, 'PYTHON3 -c \'open(".claude/settings.json", "w").write("{}")\''))
    assert _denied(_run(vault, "Perl -e 'open(F, \">.claude/settings.json\")'"))


def test_a_writer_in_any_case_into_git_hooks_is_denied(vault: Path) -> None:
    assert _denied(_run(vault, "RM .git/hooks/pre-commit"))
    assert _denied(_run(vault, "Cp x .git/hooks/pre-commit"))


# ----- what stays allowed: an argument, a file name, a builtin that isn't one ------------------------


@pytest.mark.parametrize(
    "command",
    [
        "echo FIND",
        "echo NOHUP",
        "scripts/git-safe-commit.sh 'NOHUP note' docs/a.md",
        "scripts/git-safe-commit.sh 'Run FIND and RG' docs/a.md",
        "printf '%s\\n' RG",
        "cat Find.md",
        "cat docs/a.md",
        "ls docs",
        "git status",
        "Echo hello",
        "EXEC ls docs",  # "command not found": no exec builtin runs it
    ],
)
def test_an_argument_or_file_in_another_case_is_allowed(vault: Path, command: str) -> None:
    assert _run(vault, command) is None


def test_cd_in_another_case_moves_nothing(vault: Path) -> None:
    """``CD knowledge-base`` runs /usr/bin/cd in a child shell: the next command still runs from the vault."""
    assert _denied(_run(vault, "cd knowledge-base && cat a.md"))
    assert not raw_kb_read("CD knowledge-base && cat a.md", cwd=str(vault), vault=str(vault))


def test_the_escape_check_folds_the_program() -> None:
    assert live_run.run_escape("NOHUP sleep 1") == "detach"
    assert live_run.run_escape("/usr/bin/nice NOHUP sleep 1") == "detach"
    assert live_run.run_escape("GIT -c core.hooksPath=x commit") == "-c core.hooksPath"
    assert live_run.run_escape("echo NOHUP") is None


# ----- the Unicode way: APFS folds the ﬁ ligature (U+FB01), the long s (U+017F) and ß ------------------


@pytest.mark.parametrize(
    "command",
    [
        "ﬁnd . -type f -exec cat {} +",
        "ﬁnd . -name a.md",
        "scoutctl kb files | xargſ cat",
        "echo knowledge-base/a.md | xargſ cat",
        "echo x | base64 -d | zſh",
        "ßh host true",
        "oſaſcript -e beep",
        "ſcreen -dm x",
        "ſh -c 'cat knowledge-base/a.md'",
        "/bin/ſh -c 'cat knowledge-base/a.md'",
        "baſh -c 'cat knowledge-base/a.md'",
        "ſed -n p knowledge-base/a.md",
        "rſync x .git/hooks/pre-commit",
        "ſcript -q out ls",
        "GIT -c core.hooksPath=x commit -m x",
        unicodedata.normalize("NFD", "ﬁnd . -name a.md"),
    ],
)
def test_a_program_folded_the_unicode_way_is_denied(vault: Path, command: str) -> None:
    assert _denied(_run(vault, command))


@pytest.mark.parametrize(
    "command",
    ["echo 'naïve café'", "scripts/git-safe-commit.sh 'Fix the — dash' docs/a.md", "cat docs/a.md # ä", "ls docs"],
)
def test_a_non_ascii_argument_is_allowed(vault: Path, command: str) -> None:
    assert _run(vault, command) is None
