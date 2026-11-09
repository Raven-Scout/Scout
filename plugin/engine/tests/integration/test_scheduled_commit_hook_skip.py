"""End to end: a scheduled run can't skip or redirect the commit hooks (#607).

The pre-commit hook holds the scheduled-run privacy blocks, so a run that skips it
skips them all, and the hook can't see that. A real runner (``run-scout.sh`` with the
real ``scout-lock.sh``) starts a "session" (``claude-with-retry.sh``, stubbed) in the
privacy fixture vault, which has the real pre-commit hook installed. The session stages
a move out of a local-only path, which a scheduled run may not do (#562), and then
makes Bash calls the way Claude Code does: each goes through the real ``hooks.json``
PreToolUse command first, and a denied call never runs.

Every form is denied and recorded, by name, and the move never lands. The plain commit
still reaches the hook, which refuses it. What a run does every day, including the
helper and text that only mentions the forms, passes. Without the gate each form really
does land the move, which is the reason it is denied. The owner is never gated.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scout.kb.hook import install_hook
from scout.kb.ontology import clear_frontmatter_cache
from tests.integration.test_scheduled_commit_env_bypass import (
    MOVED,
    POSITIONS,
    REPO_ROOT,
    SLOT,
    TEMPLATES,
    _clean_env,
    _render,
    _scoutctl_has_kb,
    _script,
)
from tests.unit.privacy_vault_helpers import build_privacy_vault, git

pytestmark = pytest.mark.skipif(not _scoutctl_has_kb(), reason="needs the engine's scoutctl for the real hook")

MOVE = "-q -m 'move positions'"
TREE = 'c=$(git commit-tree "$(git write-tree)" -p HEAD -m "move positions")'
# Each form with the name the gate gives it. All of them land the move if nothing stops them.
FORMS = {
    "no-verify": (f"git commit --no-verify {MOVE}", "--no-verify"),
    "abbreviated": (f"git commit --no-veri {MOVE}", "--no-verify"),
    "short-n": (f"git commit -n {MOVE}", "-n"),
    "short-n-cluster": ("git commit -qnm 'move positions'", "-n"),
    "continued-line": (f"git commit \\\n  --no-verify {MOVE}", "--no-verify"),
    "dash-c": (f"git -c core.hooksPath=/dev/null commit {MOVE}", "-c core.hooksPath"),
    "dash-c-after-dash-C": (f"git -C . -c core.hookspath=/dev/null commit {MOVE}", "-c core.hooksPath"),
    "config-env": (f"E=/dev/null git --config-env=core.hooksPath=E commit {MOVE}", "--config-env core.hooksPath"),
    "alias": (f"git -c alias.c='commit --no-verify' c {MOVE}", "-c alias.*"),
    "git-config": (f"git config core.hooksPath /dev/null && git commit {MOVE}", "git config core.hooksPath"),
    "config-env-vars": (
        f"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit {MOVE}",
        "GIT_CONFIG_*",
    ),
    "commit-tree": (f'{TREE} && git update-ref HEAD "$c"', "commit-tree"),
    "write-the-hook": (f"printf '#!/bin/sh\\n' > .git/hooks/pre-commit && git commit {MOVE}", ".git/hooks"),
    "delete-the-hook": (f"rm .git/hooks/pre-commit && git commit {MOVE}", ".git/hooks"),
    "disable-the-hook": (f"chmod -x .git/hooks/pre-commit && git commit {MOVE}", ".git/hooks"),
    "cd-into-the-hooks": (f"(cd .git/hooks && rm pre-commit) && git commit {MOVE}", ".git/hooks"),
    "edit-the-config": (f"printf '[core]\\nhooksPath=/dev/null\\n' >> .git/config && git commit {MOVE}", ".git/config"),
}

# What a run does every day, or text that only mentions the forms: all allowed, all run.
EVERYDAY = [
    "git log -n 5 --oneline",
    "git show --stat HEAD",
    "git diff --stat HEAD",
    "git grep -n -e --no-verify -- notes || true",
    "cat .git/hooks/pre-commit",
    'git config user.email "alex@example.com"',
    'git config user.name "Scout Bot"',
    "git config --get core.hooksPath || true",
    "echo 'git commit --no-verify'",
    "cat <<'EOF' > .scout-cache/e2e/about.txt\ngit commit --no-verify\ngit -c core.hooksPath=/dev/null commit\nEOF",
]

# A stand-in for Claude Code: every Bash call goes through the real PreToolUse command, and a denied one never runs.
DRIVER = """import json, os, subprocess, sys
out = ".scout-cache/e2e"
hook = os.environ["E2E_HOOK"]
results = []
for command in json.load(open(f"{out}/commands.json")):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.getcwd()})
    gate = subprocess.run(["sh", "-c", hook], input=payload, capture_output=True, text=True)
    denied = gate.stdout.strip() != "" and json.loads(gate.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    row = {"command": command, "gate_rc": gate.returncode, "denied": denied, "reason": gate.stdout}
    if not denied and gate.returncode == 0:
        ran = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
        row |= {"rc": ran.returncode, "output": ran.stdout + ran.stderr}
    results.append(row)
json.dump(results, open(f"{out}/results.json", "w"))
"""

SESSION = """#!/bin/bash
cd "$SCOUT_DATA_DIR" || exit 1
mkdir -p notes
git mv {positions} {moved}
python3 scripts/e2e-driver.py
git reset -q --hard
exit 0
"""


def _hook_command() -> str:
    hooks = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    (entry,) = [h for h in hooks if h["matcher"] == "Grep|Glob|Bash"]
    return entry["hooks"][0]["command"]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    v = build_privacy_vault(tmp_path)
    with (v / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
        fh.write(".scout-logs/\n.scout-cache/\nbin/\nscripts/\nrun-*.sh\n")
    install_hook(v)  # the scoutctl next to this interpreter
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")  # the runner's own scoutctl calls
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scripts" / "scout-lock.sh", v)
    _render(TEMPLATES / "scripts" / "git-safe-commit.sh.tmpl", v / "scripts" / "git-safe-commit.sh", v)
    _script(v / "scripts" / "e2e-driver.py", DRIVER)
    _script(v / "scripts" / "claude-with-retry.sh", SESSION.format(positions=POSITIONS, moved=MOVED))
    (v / ".scout-cache" / "e2e").mkdir(parents=True)
    return v


def _session(vault: Path, commands: list[str]) -> list[dict]:
    """Run the commands in a real runner's session; what the gate and the shell did with each."""
    (vault / ".scout-cache" / "e2e" / "commands.json").write_text(json.dumps(commands), encoding="utf-8")
    script = _render(TEMPLATES / "run-scout.sh.tmpl", vault / "run-scout.sh", vault)
    env = {
        **_clean_env(),
        "SCOUT_FORCE_MODE": SLOT,
        "SCOUT_LOCK_POLL_SECS": "1",
        "SCOUT_LOCK_WAIT_SECS": "30",
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        "E2E_HOOK": _hook_command(),
    }
    proc = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    return json.loads((vault / ".scout-cache" / "e2e" / "results.json").read_text(encoding="utf-8"))


def _events(vault: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["kind"] == "kb_read_gate.hook_skip"]


def test_every_form_is_denied_and_recorded_and_the_move_never_lands(vault: Path) -> None:
    head = git(vault, "rev-parse", "HEAD")
    hook = (vault / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    results = _session(vault, [command for command, _ in FORMS.values()])
    assert len(results) == len(FORMS)
    for (name, (_, form)), row in zip(FORMS.items(), results, strict=True):
        assert row["denied"], f"{name} was not denied: {row}"
        reason = json.loads(row["reason"])["hookSpecificOutput"]["permissionDecisionReason"]
        assert form in reason, name
    assert git(vault, "rev-parse", "HEAD") == head, "no form landed anything"
    assert (vault / POSITIONS).exists() and not (vault / MOVED).exists()
    assert (vault / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8") == hook
    assert "hookspath" not in (vault / ".git" / "config").read_text(encoding="utf-8").lower()
    assert [(e["payload"]["form"], e["payload"]["mode"]) for e in _events(vault)] == [
        (form, SLOT) for _, form in FORMS.values()
    ]


def test_the_plain_commit_still_reaches_the_hook_which_refuses_it(vault: Path) -> None:
    head = git(vault, "rev-parse", "HEAD")
    (row,) = _session(vault, [f"git commit {MOVE}"])
    assert not row["denied"] and row["rc"] != 0
    assert "[privacy-widening]" in row["output"] and "local-only" in row["output"]
    assert git(vault, "rev-parse", "HEAD") == head
    assert _events(vault) == []


def test_what_a_run_does_every_day_passes_the_gate_and_runs(vault: Path) -> None:
    results = _session(vault, EVERYDAY)
    for row in results:
        assert not row["denied"] and row["gate_rc"] == 0 and row["rc"] == 0, row
    assert _events(vault) == []


def test_the_helper_and_a_message_naming_the_forms_commit_through_the_gate(vault: Path) -> None:
    """git-safe-commit.sh runs git inside it, which the gate never sees; the hook checks the commit as usual."""
    (vault / "notes").mkdir(exist_ok=True)
    (vault / "notes" / "helper.md").write_text("# Helper\n\nA plain note.\n", encoding="utf-8")
    (vault / "notes" / "direct.md").write_text("# Direct\n\nA plain note.\n", encoding="utf-8")
    results = _session(
        vault,
        [
            "scripts/git-safe-commit.sh 'kb: never --no-verify, never -n' notes/helper.md",
            "git add notes/direct.md",
            'git commit -q -m "kb: don\'t use git commit --no-verify" -- notes/direct.md',
        ],
    )
    for row in results:
        assert not row["denied"] and row["rc"] == 0, row
    log = git(vault, "log", "-2", "--format=%s", "--name-only")
    assert "notes/helper.md" in log and "notes/direct.md" in log
    assert POSITIONS not in log and MOVED not in log, "the staged move stays out of both commits"
    assert _events(vault) == []


@pytest.mark.parametrize("name", list(FORMS))
def test_without_the_gate_each_form_really_lands_the_move(vault: Path, name: str) -> None:
    """Why the gate denies them: the hook can't refuse a commit it never sees."""
    (vault / MOVED).parent.mkdir(parents=True, exist_ok=True)
    git(vault, "mv", POSITIONS, MOVED)
    head = git(vault, "rev-parse", "HEAD")
    env = {**_clean_env(), "SCOUT_MODE": SLOT, "SCOUT_READER": "autonomous", "SCOUT_DATA_DIR": str(vault)}
    proc = subprocess.run(["bash", "-c", FORMS[name][0]], cwd=vault, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert git(vault, "rev-parse", "HEAD") != head
    assert MOVED in git(vault, "show", "--name-only", "--format=", "HEAD")


def test_the_owner_is_not_gated(vault: Path) -> None:
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": FORMS["no-verify"][0]}, "cwd": str(vault)},
    )
    env = {**_clean_env(), "SCOUT_DATA_DIR": str(vault), "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT)}
    gate = subprocess.run(["sh", "-c", _hook_command()], input=payload, env=env, capture_output=True, text=True)
    assert gate.returncode == 0 and gate.stdout == "" and gate.stderr == ""
    assert _events(vault) == []
