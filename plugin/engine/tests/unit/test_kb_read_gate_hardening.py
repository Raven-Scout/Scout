"""kb-read-gate, widened after an audit found raw shell reads into knowledge-base/ getting past it,
mostly through a variable or loop holding a KB path. Only what is denied widens: every case in
test_hooks_kb_read_gate.py stands."""

from __future__ import annotations

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb import raw_read
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
    assert judged('rg x "$UNSET_DIR"') and judged('cd "$(pwd)" && rg x')
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


# ----- #734 round 2: false denies from quoted metacharacters, filters and unknown roots ----------------


@pytest.mark.parametrize(
    "command",
    [
        # a quoted | ; ( ) belongs to the pattern, not the shell
        'rg -n "TODO|FIXME" scripts/',
        'grep -rE "foo|bar" docs/',
        'git grep -n "foo|bar" -- scripts',
        'rg -n "a;b" scripts',
        'grep -rn "salary (base)" docs/',
        # a file filter keeps the search out of the KB, root or no root
        "grep -rn foo --include=*.py .",
        "grep -r x --include='*.py' .",
        "rg foo -g '*.py' .",
        "rg -t py foo",
        # roots the gate can read off the command
        'for f in scripts/*.py; do rg -n TODO "$f"; done',
        "rg TODO $(git ls-files scripts)",
        'cd "$(git rev-parse --show-toplevel)" && rg TODO scripts',
        "git diff --name-only | xargs rg TODO",
        "git ls-files scripts | xargs rg TODO",
        # both KB spellings excluded, and action-items/context too
        "rg x -g '!knowledge-base/**' -g '!action-items/context/**'",
        "du -sh knowledge-base",
        "rg x scripts 2>/dev/null",
        "cat */README.md",
        "ls -d */",
    ],
)
def test_round_two_false_denies_are_allowed(command: str) -> None:
    assert not judged(command)


@pytest.mark.parametrize(
    "command",
    [
        "rg -t md foo",
        "grep -r x --include=*.md .",
        'for d in . scripts; do rg x "$d"; done',
        "echo $(rg salary)",
        "bash -c 'rg salary'",
        "du -a knowledge-base",
        "/opt/homebrew/bin/rg Priya",
        "/usr/bin/grep -r Priya .",
        "\\rg Priya",
        "command -v cat knowledge-base/people.md",  # main denies it: keep it
        "cat know*/people.md",
        "head action-items/con*/today.md",
    ],
)
def test_round_two_denies_stand(command: str) -> None:
    assert judged(command)


@pytest.mark.parametrize(
    "command",
    [
        "/opt/homebrew/bin/rg Priya",
        "/usr/bin/grep -r Priya .",
        "/usr/bin/find . -name '*.md'",
        "/bin/ls -R",
        "\\rg Priya",
    ],
)
def test_an_absolute_path_reader_reaches_python_and_is_denied(vault, command: str) -> None:  # noqa: F811
    import json

    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", command


@pytest.mark.parametrize(
    "command",
    [
        'rg -n "TODO|FIXME" scripts/',
        'grep -rE "foo|bar" docs/',
        "rg foo -g '*.py' .",
        "git ls-files scripts | xargs rg TODO",
    ],
)
def test_a_scoped_search_is_allowed_through_the_real_cli(vault, command: str) -> None:  # noqa: F811
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0 and res.stdout == "", (command, res.stdout, res.stderr)


# ----- #734 round 3: daily-file globs, glob routing, unknown folders, filter bounds, prefixes -------

DAILY_GLOBS = [
    "ls action-items/*.md 2>/dev/null",
    "ls -la *.md 2>/dev/null",
    "head -3 docs/*.md > /tmp/heads.txt",
    "rg -l TODO docs/*.md",
    "sed -n 1,5p action-items/*.md",
]


@pytest.mark.parametrize("command", [*DAILY_GLOBS, "rg x -g 'docs/*.md'", "rg foo -g '*.py' ."])
def test_round_three_globs_over_daily_files_are_allowed(command: str) -> None:
    assert not judged(command)


@pytest.mark.parametrize(
    "command",
    [
        # after a cd the gate can't follow
        'cd "$X" && rg x action-items',
        "cd - && rg x action-items",
        "cd && rg x Scout",
        'cd "$X" && rg x ./action-items',
        # a substitution that prints where the vault is, or names a KB folder
        'rg x "$(pwd)"',
        "rg x $(echo knowledge-base)",
        "rg x \"$(printf 'know%s' ledge-base)\"",
        'rg x "$(pwd)/action-items"',
        # filters that can still match a KB note
        "rg x -g '*people*'",
        "rg x -g '*.m?'",
        "rg x -g '*.{md,txt}'",
        "rg x --iglob '*.MD'",
        "grep -r x --include='*.m*' .",
        "rg --type-add 'k:*.md' -t k x",
        # prefixes by absolute path
        "/usr/bin/env rg x",
        "/usr/bin/timeout 10 rg x",
        "scripts/scoutctl kb files | /usr/bin/xargs wc -c",
    ],
)
def test_round_three_denies(command: str) -> None:
    assert judged(command)


def test_the_documented_residual_stays_allowed() -> None:
    # xargs fed by a command that prints no KB path: main never denied it (raw_kb_read's docstring).
    assert not judged("ls | xargs rg x")


@pytest.mark.parametrize(
    "command", ["cat know*/people.md", "head action-items/con*/today.md", "cat knowledge-bas?/x.md"]
)
def test_a_kb_spelling_glob_reaches_python_and_is_denied(vault, command: str) -> None:  # noqa: F811
    import json

    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", command


@pytest.mark.parametrize("command", DAILY_GLOBS)
def test_daily_file_globs_are_allowed_through_the_real_cli(vault, command: str) -> None:  # noqa: F811
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault)
    assert res.returncode == 0 and res.stdout == "", (command, res.stdout, res.stderr)


# ----- the parsing helpers, one edge each ----------------------------------------------


def test_glob_regex_reads_ripgrep_globs() -> None:
    from scout.kb.raw_read import _glob_regex

    assert _glob_regex("**/people/**").fullmatch("knowledge-base/people/priya.md")
    assert _glob_regex("[ab]c").fullmatch("ac") and not _glob_regex("[!a]c").fullmatch("ac")
    assert _glob_regex("[!a]c").fullmatch("bc")
    assert _glob_regex("[x").fullmatch("[x")  # an unclosed bracket is a literal
    assert _glob_regex("{a,b").fullmatch("b")  # an unclosed brace still closes
    assert _glob_regex("[z-a]").pattern == ".*"  # a glob the gate can't read bounds nothing


def test_flag_and_exclusion_helpers() -> None:
    from scout.kb.raw_read import _exclusions, _flag_value, _int

    assert _int("two") is None and _int(None) is None and _int("2") == 2
    assert _flag_value(["--max-depth=2"], {"--max-depth"}) == "2"
    assert _flag_value(["-L", "3"], {"-L"}) == "3"
    assert _exclusions(["--exclude-dir=knowledge-base", "--glob=!action-items/**", "-g", "*.py"]) == (
        "knowledge-base",
        "action-items/**",
    )


def test_read_names_skips_a_flags_value() -> None:
    from scout.kb.raw_read import _read_names

    assert _read_names("read", ["-r", "-p", "prompt", "f"]) == {"f"}
    assert _read_names("mapfile", ["-t", "-n", "5", "lines"]) == {"lines"}


def test_git_with_only_global_options_has_no_subcommand() -> None:
    from scout.kb.raw_read import _git

    assert _git(["--no-pager", "-C", "docs"]) == (None, [], ["docs"])


def test_find_without_tests_takes_every_word_as_a_root() -> None:
    from scout.kb.raw_read import _recursive

    rec = _recursive("find", ["docs", "scripts"])
    assert rec is not None and rec.roots == ["docs", "scripts"] and not rec.content


def test_reaches_kb_inside_the_kb_needs_a_depth() -> None:
    from scout.kb.raw_read import _reaches_kb, _Recursive

    inside = f"{VAULT}/knowledge-base/projects"
    assert _reaches_kb(inside, VAULT, _Recursive([], True, None))
    assert _reaches_kb(inside, VAULT, _Recursive([], True, 1))
    assert not _reaches_kb(inside, VAULT, _Recursive([], True, 0))


def test_expand_a_tilde_without_home_is_unknown() -> None:
    from scout.kb.raw_read import _UNKNOWN, _expand

    assert _expand("~/x", {}, {}) == _UNKNOWN
    assert _expand("~/x", {"HOME": "/home/alex"}, {}) == "/home/alex/x"


def test_shell_split_and_substitution_edges() -> None:
    from scout.kb.raw_read import _shell_split, _shell_words, _substitution_end

    statements, _ = _shell_split("sleep 1 & cat docs/x.md")
    assert len(statements) == 2
    assert _substitution_end("`ls` docs", 0) == 4
    assert _substitution_end("`ls docs", 0) == len("`ls docs")
    assert _substitution_end("$(a $(b) (c)) d", 0) == len("$(a $(b) (c))")
    assert _shell_words('echo "unclosed docs') == ["echo", '"unclosed', "docs"]


@pytest.mark.parametrize(
    "command",
    [
        "scripts/scoutctl kb files | while read -r -d '' f; do cat \"$f\"; done",
        "2>/dev/null; rg -n TODO .",
    ],
)
def test_edge_forms_into_the_kb_are_flagged(command: str) -> None:
    assert raw_kb_read(command, cwd=VAULT, vault=VAULT, env={"HOME": "/home/alex"})


# ----- #541: a vault-rooted recursive search filtered to a data format the KB holds -----------------

# The four forms the issue names.
ISSUE_541_FORMS = [
    "grep -r Priya .",
    "rg Priya",
    "find . -name '*.yaml'",
    "grep -r Priya . --include='*.yaml'",
]


@pytest.mark.parametrize("command", ISSUE_541_FORMS)
def test_issue_541_forms_are_flagged_from_the_vault_root_and_above(command: str) -> None:
    assert raw_kb_read(command, cwd=VAULT, vault=VAULT)
    assert raw_kb_read(command, cwd="/home/alex", vault=VAULT)


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn --include=*.yaml Priya",
        "grep -r --include '*.yml' Priya .",
        "grep -rl Priya --include=*.csv .",
        "rg Priya -g '*.yaml'",
        "rg Priya --glob='*.{yaml,yml}' .",
        "rg -l Priya --iglob '*.JSON'",
        "rg Priya -g '**/*.csv'",
        "rg -t yaml Priya",
        "rg --type=json Priya .",
        "rg -tcsv Priya",
        "rg Priya -g '*.txt'",
    ],
)
def test_a_data_file_filter_does_not_bound_a_vault_rooted_search(command: str) -> None:
    assert judged(command)
    assert judged(command, cwd="/home/alex")


@pytest.mark.parametrize(
    "command",
    [
        # rooted outside the KB
        "grep -r Priya scripts --include='*.yaml'",
        "rg Priya -g '*.yaml' .scout-logs",
        "rg -t json Priya .scout-cache",
        "find scripts -name '*.yaml'",
        "cd /tmp && grep -r Priya . --include='*.yaml'",
        "rg Priya .scout-logs/",
        "rg -n Priya scripts/ docs/",
        # filtered to a folder that isn't the KB
        "rg Priya -g 'scripts/*.yaml'",
        "rg Priya -g '.scout-logs/**/*.json'",
        "rg Priya -g 'action-items/*.md'",
        # filtered to code, or to a format the KB keeps no facts in
        "grep -r Priya . --include='*.py'",
        "rg Priya -g '*.sh'",
        "rg -t py Priya",
        "rg -tpy Priya",
        "rg Priya -g '*.jsonl'",
        "rg Priya -g '*.log'",
        # the KB excluded (`--exclude-dir=knowledge-base` names a KB path, which main already denies)
        "rg Priya -g '*.yaml' -g '!knowledge-base/**' -g '!action-items/context/**'",
    ],
)
def test_a_data_file_search_kept_out_of_the_kb_is_allowed(command: str) -> None:
    assert not judged(command)


@pytest.mark.parametrize(
    "command",
    [
        # ripgrep ignores -t when an include glob is given: these search the glob's files
        "rg x -g '*.md' -tpy",
        "rg -t py -g '*.md' x",
        "rg -tpy -g '*.yaml' x",
        "rg x --type=py --glob='*.json'",
        "rg x --type py --iglob '*.MD'",
        # an html page the KB keeps
        "rg x -g '*.html'",
        "rg -t html x",
    ],
)
def test_an_rg_type_does_not_bound_an_include_glob_that_reaches_the_kb(command: str) -> None:
    assert judged(command)
    assert judged(command, cwd="/home/alex")


@pytest.mark.parametrize(
    "command",
    [
        "rg -tpy x",
        "rg -t py x -g '!knowledge-base/**'",  # a negated glob is no include: the type still bounds it
        "rg -t py -g '*.py' x",
    ],
)
def test_an_rg_type_bounds_a_search_with_no_include_glob(command: str) -> None:
    assert not judged(command)


@pytest.mark.parametrize(
    "command",
    [
        "ag Priya knowledge-base",
        "ack Priya knowledge-base/people.md",
        "fd people knowledge-base",
        "ugrep -r x knowledge-base",
    ],
)
def test_other_search_tools_naming_the_kb_are_flagged(command: str) -> None:
    assert raw_kb_read(command)


def test_a_data_file_search_below_the_vault_root_is_allowed() -> None:
    assert not judged("grep -r Priya . --include='*.yaml'", cwd=f"{VAULT}/scripts")
    assert judged("grep -r Priya . --include='*.yaml'", cwd=f"{VAULT}/knowledge-base/ontology")


def test_a_data_file_search_from_the_vault_root_is_denied_by_the_hook(vault) -> None:  # noqa: F811
    assert _denied(_decision(vault, "Bash", {"command": "grep -r Priya . --include='*.yaml'"}))
    assert _decision(vault, "Bash", {"command": "grep -r Priya scripts --include='*.yaml'"}) is None


# ----- ag, ack, fd, ugrep from the vault root ------------------------------------------------

OTHER_TOOL_ROOT_FORMS = [
    "ag Priya",
    "ag Priya .",
    "ag -l Priya",
    "ag -g people",
    "ack Priya",
    "ack -f",
    "fd people",
    "fd",
    "fd . -X cat",
    "fd -e yaml -x cat {}",
    "fd -e md people",
    "ugrep -r Priya .",
    "ugrep -r Priya . --include='*.yaml'",
    "ugrep -rn Priya -g '*.json'",
]


@pytest.mark.parametrize("command", OTHER_TOOL_ROOT_FORMS)
def test_other_search_tools_from_the_vault_root_are_flagged(command: str) -> None:
    assert judged(command)
    assert judged(command, cwd="/home/alex")


@pytest.mark.parametrize(
    "command",
    [
        "ag Priya scripts",
        "ack Priya scripts/",
        "fd people scripts",
        "fd people --search-path scripts",
        "fd -e py",
        "fd -e sh -X cat",
        "ugrep -r Priya scripts",
        "ugrep -r Priya . --include='*.py'",
        "ugrep Priya notes.txt",
    ],
)
def test_other_search_tools_kept_out_of_the_kb_are_allowed(command: str) -> None:
    assert not judged(command)


def test_other_search_tools_below_the_vault_root_are_allowed() -> None:
    assert not judged("ag Priya", cwd=f"{VAULT}/scripts")
    assert not judged("fd . -X cat", cwd=f"{VAULT}/scripts")
    assert judged("fd . -X cat", cwd=f"{VAULT}/knowledge-base")


@pytest.mark.parametrize("command", ["ag Priya", "ack Priya", "fd people", "ugrep -r Priya ."])
def test_the_shell_sends_other_search_tools_to_python(vault, tmp_path, command: str) -> None:  # noqa: F811
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2  # Python was needed (and, missing here, fails closed)


def test_an_ag_search_from_the_vault_root_is_denied_by_the_hook(vault) -> None:  # noqa: F811
    assert _denied(_decision(vault, "Bash", {"command": "ag Priya"}))
    assert _decision(vault, "Bash", {"command": "ag Priya scripts"}) is None


# ----- a value flag before the pattern (#788 review) ------------------------------------------
# Each row: a flag whose value, read as the pattern, used to make the real pattern look like a
# root outside the KB. From the vault root these all read notes.
VALUE_FLAG_THEN_PATTERN = {
    "ack": [
        "ack -t markdown TODO",
        "ack -T python TODO",
        "ack --and foo TODO",
        "ack --or foo TODO",
        "ack --not foo TODO",
        "ack --range-start foo TODO",
        "ack --range-end foo TODO",
        "ack --files-from list TODO",
    ],
    "ugrep": [
        "ugrep -r -t markdown TODO",
        "ugrep -r -t python,markdown TODO",
        "ugrep -rO md TODO",
        "ugrep -r -O md TODO",
        "ugrep -r -M magic TODO",
        "ugrep -r -N foo TODO",
        "ugrep -r --and foo TODO",
        "ugrep -r --andnot foo TODO",
        "ugrep -r --not foo TODO",
        "ugrep -3 x .",
        "ugrep --depth=2 x .",
        "ugrep -r x -g '*.py,*.md'",
    ],
    "fd": [
        "fd -C . people",
        "fd --newer 1d people",
        "fd --older 1d people",
        "fd --changed-after 1d people",
        "fd --change-newer-than 1d people",
        "fd --change-older-than 1d people",
        "fd --ignore-contain x people",
        "fd --batch-size 10 people -X cat",
        "fd x scripts -X cat ';' .",
        "fd x scripts -x cat '{}' ';' -e md .",
    ],
    "ag": [
        "ag -W 80 TODO",
        "ag --width 80 TODO",
        "ag --color-path 31 TODO",
        "ag --color-match 31 TODO",
        "ag --color-line-number 31 TODO",
        "ag -A TODO",
    ],
    "aliases": [
        "ug -r TODO .",
        "ug+ -r TODO .",
        "ugrep+ -r TODO .",
        "fdfind people",
        "ack-grep TODO",
    ],
}


@pytest.mark.parametrize("command", [c for rows in VALUE_FLAG_THEN_PATTERN.values() for c in rows])
def test_a_value_flag_before_the_pattern_does_not_hide_a_vault_root_search(command: str) -> None:
    assert judged(command)
    assert judged(command, cwd="/home/alex")


def test_fd_base_directory_puts_the_search_in_the_vault() -> None:
    assert judged("fd -C ~/Scout -e md", cwd="/tmp")
    assert judged("fd --base-directory ~/Scout people", cwd="/tmp")
    assert not judged("fd -C ~/Scout/scripts people", cwd="/tmp")
    assert not judged("fd -C ~/Scout people scripts", cwd="/tmp")


@pytest.mark.parametrize(
    "command",
    [
        # the same flags, with a root outside the KB or a filter to code
        "ack -t markdown TODO scripts",
        "ack -t python TODO",
        "ack --and foo TODO scripts",
        "ugrep -r -t markdown TODO scripts",
        "ugrep -r -t python TODO",
        "ugrep -rO py TODO",
        "ugrep -r -O py,sh TODO",
        "ugrep -r x -g '*.py,*.sh'",
        "ugrep -3 x scripts",
        "fd -C scripts people",
        "fd --newer 1d people scripts",
        "fd x scripts -X cat ';'",
        "fd x scripts -x cat '{}' ';' -e py",
        "ag -W 80 TODO scripts",
        "ag -A 3 TODO scripts",
        "ug -r TODO scripts",
        "fdfind people scripts",
        "ack-grep TODO scripts",
        # over-denies the review found: none of these reads a file
        "ag -V",
        "fd -V",
        "fd -h",
        "ag --list-file-types",
        "ack --help-types",
        "fd -epy x",
        "ag --context 3 x",  # `--context` takes its value only attached: `3` is the pattern, `x` the root
    ],
)
def test_value_flags_kept_out_of_the_kb_are_allowed(command: str) -> None:
    assert not judged(command)


def test_options_reads_getopt_clusters() -> None:
    assert raw_read._options("ugrep", ["-rO", "md", "x"]) == [("-r", None), ("-O", "md")]
    assert raw_read._options("fd", ["-epy", "--type=f", "--and", "y"]) == [
        ("-e", "py"),
        ("--type", "f"),
        ("--and", "y"),
    ]
    assert raw_read._options("ag", ["-A", "x", "-C", "2", "--context", "3"]) == [
        ("-A", None),
        ("-C", "2"),
        ("--context", None),
    ]
    assert raw_read._options("grep", ["-i.bak", "--", "-e"]) == [("-i", None)]
    assert raw_read._operands("ugrep", ["-rO", "md", "x", "."]) == ["-rO", "x", "."]
    assert raw_read._split_list("*.{md,txt},!*.py") == ["*.{md,txt}", "!*.py"]


@pytest.mark.parametrize("command", ["ug -r x .", "fdfind people", "ack-grep x"])
def test_the_shell_sends_search_tool_aliases_to_python(vault, tmp_path, command: str) -> None:  # noqa: F811
    payload = _payload("Bash", {"command": command}, vault).getvalue()
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, root=tmp_path / "missing")
    assert res.returncode == 2


# ----- #791 review: holes in the new ugrep/ack/fd parsing, and grep clusters -----------------------
REVIEW_MUST_DENY = {
    # 1. ugrep: a `!` or `^` entry excludes, so it widens the search; a type is matched by its first letters
    "ugrep exclusions": [
        "ugrep -r -g '^*.py' TODO",
        "ugrep -r -g '!*.py' TODO",
        "ugrep -r -O '^py' TODO",
        "ugrep -r -O '!py' TODO",
        "ugrep -r -t '^python' TODO",
        "ugrep -r -t mark TODO",
        "ugrep -r -t py,mark TODO",
    ],
    # 2. ack: `no` + a type excludes it
    "ack no-type": ["ack --type=nopython TODO", "ack -t nopython TODO", "ack --type nopython TODO"],
    # 3. ugrep value flags
    "ugrep values": ["ugrep -r -K 1 TODO", "ugrep -r -J 4 TODO", "ugrep --depth 5 TODO", "ugrep -r --range 1 TODO"],
    # 5. grep's recursion inside a cluster (on main too)
    "grep clusters": ["grep -rnA3 TODO", "grep -rC2 TODO", "grep -r3 TODO .", "egrep -inr TODO", "grep -nRe TODO"],
}


@pytest.mark.parametrize("command", [c for rows in REVIEW_MUST_DENY.values() for c in rows])
def test_review_791_holes_are_denied(command: str) -> None:
    assert judged(command)
    assert judged(command, cwd="/home/alex")


# 4. fd's exec flag in a cluster or attached (clap): from action-items/ the command reads context/
@pytest.mark.parametrize("command", ["fd -Hx cat", "fd -xcat", "fd -x=cat", "fd -HX cat", "fd . -IHx cat {}"])
def test_fd_exec_in_a_cluster_is_a_content_read(command: str) -> None:
    assert judged(command, cwd=f"{VAULT}/action-items")


@pytest.mark.parametrize(
    "command",
    [
        "ugrep -r -t python TODO",  # a type filter to code still bounds the search
        "ugrep -r -O py,'^sh' TODO",  # an include beside an exclude: the include bounds it
        "ugrep -r -g '*.py,!*.md' TODO",
        "ack -t python TODO",
        "ugrep -r -K 1 TODO scripts",
        "ugrep --depth 5 TODO scripts",
        "grep -rnA3 TODO scripts",
        "grep -ne -r docs/a.md",  # `-e` takes `-r` as the pattern: not recursive
        "fd -ex people scripts",  # `-e x`, not exec
    ],
)
def test_review_791_neighbours_are_allowed(command: str) -> None:
    assert not judged(command)
    assert not judged("fd -Hex cat scripts", cwd=f"{VAULT}/action-items")
