"""The run brokers against the real kernel (macOS; spec 2026-11-16 §9.3–9.5).

A scratch vault and HOME, the 1c profile from :func:`scout.sandbox.launch.prepare`, a broker process outside the
sandbox, and the profile it tightens. Properties: in-session git can't change the vault's repository, and the
helper still commits through the broker with its SHA back; read-only git keeps working; the secrets, the Telegram
files included, can't be read, while `test -s` still says whether Telegram is set up; the broker's folder can't be
changed; the model agrees with the kernel. Then the wrapper end to end, and a real `claude -p` against the local
fake endpoint (no spend). Nothing here touches the real HOME, a live vault or launchd.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.kb import hook_paths
from scout.sandbox import broker, launch
from scout.sandbox import profile as sbx
from scout.scripts.phase_assembly import render_template

pytestmark = pytest.mark.skipif(not sbx.available(), reason="macOS sandbox-exec only")

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
GIT_ID = {
    "GIT_AUTHOR_NAME": "Scout Bot",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "Scout Bot",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class World:
    def __init__(self, root: Path, broker_dir: Path, *, helper: bool = True) -> None:
        self.root = root
        self.home = root / "home"
        self.vault = root / "vault"
        self.broker_dir = broker_dir
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text("{}\n")
        (self.home / ".claude.json").write_text("{}\n")
        (self.home / ".gitconfig").write_text("[user]\n\tname = Scout Bot\n\temail = alex@example.com\n")
        self.secrets = self.home / ".scout-secrets"
        self.secrets.mkdir()
        for name, value in (("telegram-bot-token", "123:abc"), ("telegram-chat-id", "4242"), ("linear-api-key", "k")):
            (self.secrets / name).write_text(value + "\n")
            (self.secrets / name).chmod(0o600)
        for d in ("notes", ".scout-logs", ".scout-state", "scripts"):
            (self.vault / d).mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SCOUT_", "GIT_", "CLAUDE_"))}
        env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"), **GIT_ID)
        env.update(SCOUT_DATA_DIR=str(self.vault), SCOUT_MODE="consolidation")
        self.env = env
        self.git("init", "-q", "-b", "main")
        hook = self.vault / ".git" / "hooks" / "pre-commit"
        hook.write_text('#!/bin/sh\necho "ran mode=$SCOUT_MODE" >> .scout-logs/pre-commit.log\n')
        hook.chmod(0o755)
        template = (TEMPLATES / "scripts" / "git-safe-commit.sh.tmpl").read_text()
        if not helper:  # a vault whose helper predates the broker
            template = template.replace("SCOUT_RUN_BROKER", "SCOUT_NO_SUCH_VARIABLE")
        helper_path = self.vault / "scripts" / "git-safe-commit.sh"
        helper_path.write_text(render_template(template, {"SCOUT_DIR": str(self.vault)}))
        helper_path.chmod(0o755)
        self.scoutctl = self.vault / "scripts" / "scoutctl"
        self.scoutctl.write_text(f'#!/bin/sh\nexec "{sys.executable}" -P -m scout "$@"\n')
        self.scoutctl.chmod(0o755)
        (self.vault / ".gitignore").write_text(".scout-cache/\n.scout-logs/\n.scout-state/\n")
        (self.vault / "notes" / "seed.md").write_text("seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed", "--no-verify")
        hook_paths._ROOTS_CACHE.clear()
        result = launch.prepare(self.vault, env)
        assert result.status == "enforced", result.reason
        self.base = result.profile
        self.profile = ""

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.vault), *args], env=self.env, check=True, capture_output=True, text=True
        ).stdout

    def repo_state(self) -> dict[str, object]:
        gitdir = self.vault / ".git"
        return {
            "head": self.git("rev-parse", "HEAD").strip(),
            "refs": self.git("for-each-ref").strip(),
            "index": (gitdir / "index").read_bytes(),
            "objects": sorted(str(p.relative_to(gitdir)) for p in (gitdir / "objects").rglob("*") if p.is_file()),
            "stash": (gitdir / "refs" / "stash").exists(),
        }

    def sh(self, command: str, *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        env = {**self.env, "SCOUT_RUN_BROKER": str(self.broker_dir / broker.SOCKET_NAME), "GIT_OPTIONAL_LOCKS": "0"}
        return subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", self.profile, "/bin/bash", "-c", command],
            cwd=self.vault, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )  # fmt: skip

    def serve(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(self.scoutctl), "broker", "serve", "--vault", str(self.vault), "--dir", str(self.broker_dir)],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip


@pytest.fixture
def broker_dir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="scout-broker.", dir="/tmp")).resolve()
    d.chmod(0o700)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def world(tmp_path: Path, broker_dir: Path) -> Iterator[World]:
    w = World(tmp_path.resolve(), broker_dir)
    proc = w.serve()
    try:
        w.profile = broker.build_profile(w.vault, broker_dir, w.base, w.env)
        yield w
    finally:
        proc.terminate()
        proc.wait(timeout=30)


# -- in-session git ------------------------------------------------------------------------------------------------

REPO_WRITES = {
    "add": "echo x > notes/a.md && git add notes/a.md",
    "commit": "echo x > notes/a.md && git commit -qam x --no-verify",
    "commit with hooks off": "echo x > notes/seed.md && git -c core.hooksPath=/dev/null commit -qam x",
    "update-ref": 'git update-ref refs/heads/side "$(git rev-parse HEAD)"',
    "stash": "echo x >> notes/seed.md && git stash -q",
    "tag": "git tag v-test",
    "object write": "echo x | git hash-object -w --stdin",
}


@pytest.mark.parametrize("name", sorted(REPO_WRITES))
def test_in_session_git_leaves_the_repository_unchanged(world: World, name: str) -> None:
    before = world.repo_state()
    res = world.sh(REPO_WRITES[name])
    assert res.returncode != 0, name
    assert world.repo_state() == before, name


def test_the_helper_commits_through_the_broker_with_its_sha_back(world: World) -> None:
    res = world.sh('echo kb > notes/kb.md && scripts/git-safe-commit.sh "notes: kb" notes/kb.md')
    assert res.returncode == 0, res.stderr
    head = world.git("rev-parse", "HEAD").strip()
    assert f"committed {head[:7]}" in res.stderr
    assert world.git("log", "-1", "--format=%s").strip() == "notes: kb"
    assert "ran mode=consolidation" in (world.vault / ".scout-logs" / "pre-commit.log").read_text()


def test_read_only_git_and_the_documented_replacements_keep_working(world: World) -> None:
    script = """
set -euo pipefail
echo changed > notes/seed.md
git status --porcelain | grep -q seed
git diff --stat > /dev/null
git log --oneline -n 1 > /dev/null
git show HEAD:notes/seed.md > notes/seed.md
test "$(cat notes/seed.md)" = seed
git grep -q seed
mv notes/seed.md notes/moved.md
scripts/git-safe-commit.sh "notes: move" notes/seed.md notes/moved.md 2>/dev/null
echo ok
"""
    res = world.sh(script)
    assert res.returncode == 0 and res.stdout.strip().endswith("ok"), res.stderr
    assert world.git("ls-files", "notes").split() == ["notes/moved.md"]


# -- secrets and the broker's folder -------------------------------------------------------------------------------


def test_no_secret_can_be_read_but_setup_checks_still_work(world: World) -> None:
    for name in ("telegram-bot-token", "telegram-chat-id", "linear-api-key"):
        assert world.sh(f'cat "{world.secrets / name}" > /dev/null').returncode != 0, name
    assert world.sh(f'ls "{world.secrets}"').returncode != 0
    assert world.sh(f'test -s "{world.secrets / "telegram-bot-token"}"').returncode == 0


def test_the_broker_folder_cannot_be_changed(world: World) -> None:
    sock = world.broker_dir / broker.SOCKET_NAME
    res = world.sh(f'rm -f "{sock}"; echo x > "{world.broker_dir}/other"; true')
    assert sock.exists() and not (world.broker_dir / "other").exists(), res.stderr
    assert world.sh('scripts/git-safe-commit.sh "notes: still" notes/seed.md').returncode == 4  # still answers


def test_the_model_agrees_with_the_kernel(world: World) -> None:
    env = world.env
    rules = sbx.build_rules(world.vault, env) + sbx.broker_rules(world.vault, world.broker_dir, env)
    git = world.vault / ".git"
    targets = [
        git / "index",
        git / "objects" / "ab" / "x",
        git / "refs" / "heads" / "x",
        world.vault / "notes" / "n.md",
    ]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        kernel = "allow" if world.sh(f'printf x >> "{target}"').returncode == 0 else "deny"
        assert kernel == sbx.decide(rules, str(target)), target
    for name in ("telegram-bot-token", "linear-api-key"):
        target = world.secrets / name
        kernel = "allow" if world.sh(f'cat "{target}" > /dev/null').returncode == 0 else "deny"
        assert kernel == sbx.decide(rules, str(target), "read") == "deny"


# -- the wrapper ---------------------------------------------------------------------------------------------------


def _wrapper(w: World) -> Path:
    text = (TEMPLATES / "scripts" / "claude-with-retry.sh.tmpl").read_text()
    text = render_template(text, {"SCOUT_DIR": str(w.vault), "INSTANCE_NAME": "Scout", "INSTANCE_NAME_LOWER": "scout"})
    path = w.root / "bin" / "claude-with-retry.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def _wrapper_env(w: World) -> dict[str, str]:
    return {
        **w.env,
        "SCOUT_SANDBOX_PROFILE": w.base,
        "SCOUT_SANDBOX_STATUS": "enforced",
        "SCOUTCTL_BIN": str(w.scoutctl),
        "SCOUT_RETRY_DISABLE": "1",
        "SCOUT_NO_CAFFEINATE": "1",
        "TMPDIR": "/tmp",
    }


def _stub_claude(w: World) -> Path:
    path = w.root / "bin" / "claude"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/bash\n"
        'echo "broker=${SCOUT_RUN_BROKER:+set}" > notes/ran.txt\n'
        'echo wrap > notes/w.md && scripts/git-safe-commit.sh "notes: w" notes/w.md 2>> notes/ran.txt; '
        'echo "helper=$?" >> notes/ran.txt\n'
        'echo d > notes/d.md && git add notes/d.md 2>/dev/null; echo "direct=$?" >> notes/ran.txt\n'
        'cat "$HOME/.scout-secrets/telegram-bot-token" > /dev/null 2>&1; echo "token=$?" >> notes/ran.txt\n'
        "exit 0\n"
    )
    path.chmod(0o755)
    return path


def _broker_processes(w: World) -> list[str]:
    out = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if "broker serve" in line and str(w.vault) in line]


def test_the_wrapper_runs_the_session_with_its_broker_and_cleans_up(tmp_path: Path, broker_dir: Path) -> None:
    w = World(tmp_path.resolve(), broker_dir)
    log = w.vault / ".scout-logs" / "run.log"
    folders_before = set(Path("/tmp").glob("scout-broker.*"))
    res = subprocess.run(
        [str(_wrapper(w)), str(log), str(_stub_claude(w)), "-p", "x"],
        cwd=w.vault, env=_wrapper_env(w), capture_output=True, text=True, timeout=180,
    )  # fmt: skip
    assert res.returncode == 0, log.read_text()
    ran = (w.vault / "notes" / "ran.txt").read_text()
    assert "broker=set" in ran and "helper=0" in ran and "committed " in ran, ran
    assert "direct=0" not in ran and "token=0" not in ran, ran
    assert w.git("log", "-1", "--format=%s").strip() == "notes: w"
    assert json.loads((w.vault / ".scout-logs" / broker.STATUS_FILE).read_text())["status"] == "on"
    assert "run brokers on" in log.read_text()
    deadline = time.monotonic() + 10
    while _broker_processes(w) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert _broker_processes(w) == []
    assert set(Path("/tmp").glob("scout-broker.*")) <= folders_before  # the wrapper removed its broker folder


def test_a_vault_with_an_older_helper_keeps_the_1c_sandbox(tmp_path: Path, broker_dir: Path) -> None:
    w = World(tmp_path.resolve(), broker_dir, helper=False)
    log = w.vault / ".scout-logs" / "run.log"
    res = subprocess.run(
        [str(_wrapper(w)), str(log), str(_stub_claude(w)), "-p", "x"],
        cwd=w.vault, env=_wrapper_env(w), capture_output=True, text=True, timeout=180,
    )  # fmt: skip
    assert res.returncode == 0, log.read_text()
    ran = (w.vault / "notes" / "ran.txt").read_text()
    assert "broker=\n" in ran and "helper=0" in ran, ran  # commits as under 1c
    status = json.loads((w.vault / ".scout-logs" / broker.STATUS_FILE).read_text())
    assert status["status"] == "unavailable" and "predates" in status["reason"]


def test_a_commit_queued_mid_session_lands_when_the_wrapper_exits(tmp_path: Path, broker_dir: Path) -> None:
    w = World(tmp_path.resolve(), broker_dir)
    claude = w.root / "bin" / "claude"
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(
        "#!/bin/bash\n"
        # The broker stops answering this client (as if it died): the client queues, the wrapper's drain lands it.
        'echo q > notes/q.md && SCOUT_RUN_BROKER=/tmp/scout-no-broker.sock scripts/git-safe-commit.sh "notes: q" '
        'notes/q.md; echo "helper=$?" > notes/ran.txt\n'
        "exit 0\n"
    )
    claude.chmod(0o755)
    log = w.vault / ".scout-logs" / "run.log"
    res = subprocess.run(
        [str(_wrapper(w)), str(log), str(claude), "-p", "x"],
        cwd=w.vault, env=_wrapper_env(w), capture_output=True, text=True, timeout=180,
    )  # fmt: skip
    assert res.returncode == 0, log.read_text()
    assert "helper=6" in (w.vault / "notes" / "ran.txt").read_text()
    assert w.git("log", "-1", "--format=%s").strip() == "notes: q", log.read_text()
    assert broker.pending(w.vault) == 0


# -- real claude, no spend (FakeAnthropic, a dummy key, a scratch HOME) ------------------------------------------


@pytest.mark.skipif(shutil.which("claude") is None, reason="no claude on PATH")
def test_real_claude_commits_and_sends_through_the_brokers(tmp_path: Path, broker_dir: Path) -> None:
    from scout.agent_runtimes.fake_anthropic import FakeAnthropic, ScriptedTurn
    from scout.agent_runtimes.gate_probe import NO_MCP_FLAGS, probe_env

    w = World(tmp_path.resolve(), broker_dir)
    notify = f'"{sys.executable}" -P -m scout notify telegram --dry-run --body "wrap: 1 commit"'
    script = [
        ScriptedTurn("Write", {"file_path": str(w.vault / "notes" / "by-claude.md"), "content": "hi\n"}),
        ScriptedTurn("Bash", {"command": 'scripts/git-safe-commit.sh "notes: by claude" notes/by-claude.md'}),
        ScriptedTurn("Bash", {"command": f"{notify} && echo SENT-dry-run"}),
        ScriptedTurn("Bash", {"command": "git commit -qam direct --no-verify || echo REFUSED-direct-commit"}),
        ScriptedTurn("Bash", {"command": 'cat "$HOME/.scout-secrets/telegram-bot-token" || echo REFUSED-token'}),
    ]
    fake = FakeAnthropic(script)
    base_url = fake.start()
    try:
        # probe_env last: no credential or endpoint from this shell survives; only the fake and a dummy key.
        env = probe_env(base_url=base_url, vault=w.vault, base=_wrapper_env(w))
        env.update(SCOUT_MODE="consolidation", SCOUT_READER="autonomous")
        assert env["ANTHROPIC_BASE_URL"] == base_url and "CLAUDE_CODE_OAUTH_TOKEN" not in env
        log = w.vault / ".scout-logs" / "run.log"
        res = subprocess.run(
            [str(_wrapper(w)), str(log), shutil.which("claude") or "claude",
             "--permission-mode", "bypassPermissions", *NO_MCP_FLAGS, "-p", "broker check"],
            cwd=w.vault, env=env, capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL,
        )  # fmt: skip
    finally:
        fake.stop()
    assert res.returncode == 0, log.read_text()[-1500:]
    texts = [FakeAnthropic.result_text(r) for r in fake.tool_results]
    assert len(texts) == len(script), texts
    head = w.git("rev-parse", "HEAD").strip()
    assert f"committed {head[:7]}" in texts[1], texts[1]
    assert w.git("log", "-1", "--format=%s").strip() == "notes: by claude"
    assert "SENT-dry-run" in texts[2] and '"chat_id": "4242"' in texts[2], texts[2]
    assert "REFUSED-direct-commit" in texts[3]
    assert "REFUSED-token" in texts[4] and "123:abc" not in texts[4]
    assert json.loads((w.vault / ".scout-logs" / broker.STATUS_FILE).read_text())["status"] == "on"
    assert any((w.home / ".claude" / "projects").rglob("*.jsonl"))  # claude wrote its transcript
