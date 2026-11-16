"""The run brokers end to end with real git (Phase 1d, spec 2026-11-16 §9.2). Any OS; no sandbox needed.

A broker process serves a scratch vault that has the real git-safe-commit.sh and a pre-commit hook. The run side
is the helper and `scoutctl notify telegram` with SCOUT_RUN_BROKER set. Properties: a commit made through the
broker lands with the hook run and its SHA back in session; a refused path leaves HEAD where it was; with the
broker gone, the request is queued and the drain lands it; the Telegram send goes through the broker.
"""

from __future__ import annotations

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
from scout.sandbox import broker
from scout.scripts.phase_assembly import render_template

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
GIT_ID = {
    "GIT_AUTHOR_NAME": "Scout Bot",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "Scout Bot",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "vault"
        self.home = Path(os.environ["HOME"])
        for d in ("notes", "scripts", ".scout-logs", ".scout-state"):
            (self.path / d).mkdir(parents=True)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SCOUT_"))},
            **GIT_ID,
            "SCOUT_DATA_DIR": str(self.path),
            "SCOUT_MODE": "consolidation",
        }
        (self.home / ".gitconfig").write_text("[user]\n\tname = Scout Bot\n\temail = alex@example.com\n")
        self.git("init", "-q", "-b", "main")
        helper = self.path / "scripts" / "git-safe-commit.sh"
        template = (TEMPLATES / "scripts" / "git-safe-commit.sh.tmpl").read_text()
        helper.write_text(render_template(template, {"SCOUT_DIR": str(self.path)}))
        helper.chmod(0o755)
        launcher = self.path / "scripts" / "scoutctl"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -P -m scout "$@"\n')
        launcher.chmod(0o755)
        (self.path / ".gitignore").write_text(".scout-cache/\n.scout-logs/\n.scout-state/\n")
        hook = self.path / ".git" / "hooks" / "pre-commit"
        hook.write_text('#!/bin/sh\necho "ran mode=$SCOUT_MODE" >> .scout-logs/pre-commit.log\n')
        hook.chmod(0o755)
        (self.path / "notes" / "seed.md").write_text("seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed", "--no-verify")
        hook_paths._ROOTS_CACHE.clear()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args], env=self.env, check=True, capture_output=True, text=True
        ).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def run(self, command: str, sock: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, "SCOUT_RUN_BROKER": sock}
        return subprocess.run(
            ["/bin/bash", "-c", command], cwd=self.path, env=env, capture_output=True, text=True, timeout=120
        )


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault(tmp_path.resolve())


@pytest.fixture
def broker_dir() -> Iterator[Path]:
    # A unix socket path has a ~100-byte limit; pytest's tmp_path can be longer than that on macOS.
    d = Path(tempfile.mkdtemp(prefix="scout-broker.", dir="/tmp")).resolve()
    d.chmod(0o700)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def served(vault: Vault, broker_dir: Path) -> Iterator[str]:
    proc = subprocess.Popen(
        [sys.executable, "-P", "-m", "scout", "broker", "serve", "--vault", str(vault.path), "--dir", str(broker_dir)],
        env=vault.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    sock = broker_dir / broker.SOCKET_NAME
    try:
        assert broker.wait_for(sock, timeout=30), "the broker never answered"
        yield str(sock)
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def test_a_commit_through_the_broker_lands_with_its_sha_and_the_hook_runs(vault: Vault, served: str) -> None:
    before = vault.head()
    res = vault.run('echo note > notes/a.md && scripts/git-safe-commit.sh "notes: a" notes/a.md', served)
    assert res.returncode == 0, res.stderr
    head = vault.head()
    assert head != before
    assert f"committed {head[:7]}" in res.stderr  # the SHA comes back during the session
    assert vault.git("log", "-1", "--format=%s").strip() == "notes: a"
    assert "ran mode=consolidation" in (vault.path / ".scout-logs" / "pre-commit.log").read_text()


def test_moves_and_deletions_commit_through_the_helper(vault: Vault, served: str) -> None:
    move = 'mv notes/seed.md notes/moved.md && scripts/git-safe-commit.sh "notes: move" notes/seed.md notes/moved.md'
    res = vault.run(move, served)
    assert res.returncode == 0, res.stderr
    assert vault.git("ls-files", "notes").split() == ["notes/moved.md"]


def test_nothing_to_commit_keeps_its_exit_code(vault: Vault, served: str) -> None:
    res = vault.run('scripts/git-safe-commit.sh "notes: none" notes/seed.md', served)
    assert res.returncode == 4


def test_a_commit_the_hook_refuses_leaves_nothing_staged(vault: Vault, served: str) -> None:
    hook = vault.path / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        '#!/bin/sh\ngit diff --cached --name-only | grep -q blocked && { echo "kb-lint: no" >&2; exit 1; }\nexit 0\n'
    )
    before = vault.head()
    res = vault.run('echo x > notes/blocked.md && scripts/git-safe-commit.sh "notes: blocked" notes/blocked.md', served)
    assert res.returncode == 5, res.stderr
    assert vault.head() == before
    assert vault.git("diff", "--cached", "--name-only").strip() == ""  # the owner's next commit won't carry it


def test_a_refused_commit_leaves_what_the_owner_staged_as_it_was(vault: Vault, served: str) -> None:
    (vault.path / "notes" / "seed.md").write_text("owner's staged version\n")
    vault.git("add", "notes/seed.md")
    staged = vault.git("ls-files", "-s", "notes/seed.md")
    hook = vault.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    res = vault.run('echo "run edit" > notes/seed.md && scripts/git-safe-commit.sh "notes: s" notes/seed.md', served)
    assert res.returncode == 5, res.stderr
    assert vault.git("ls-files", "-s", "notes/seed.md") == staged


def test_a_stopped_drain_stops_the_commit_it_was_running(vault: Vault) -> None:
    import signal

    hook = vault.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho $$ > .scout-logs/hook.pid\nsleep 120\n")
    broker.enqueue(vault.path, "commit", {"message": "notes: slow", "paths": ["notes/slow.md"]})
    (vault.path / "notes" / "slow.md").write_text("x\n")
    proc = subprocess.Popen(
        [sys.executable, "-P", "-m", "scout", "broker", "drain", "--vault", str(vault.path)],
        env=vault.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    pid_file = vault.path / ".scout-logs" / "hook.pid"
    deadline = time.monotonic() + 60
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    hook_pid = int(pid_file.read_text().strip())
    proc.send_signal(signal.SIGTERM)  # what the wrapper's watchdog does
    proc.wait(timeout=20)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(hook_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        pytest.fail("the commit's hook outlived the stopped drain")
    assert proc.returncode != 0


def test_a_refused_path_leaves_head_where_it_was(vault: Vault, served: str) -> None:
    before = vault.head()
    res = vault.run(
        "echo x >> scripts/claude-with-retry.sh; echo y > notes/b.md; "
        'scripts/git-safe-commit.sh "sneak" notes/b.md scripts/claude-with-retry.sh',
        served,
    )
    assert res.returncode == broker.EXIT_REFUSED
    assert "protected" in res.stderr
    assert vault.head() == before


def test_with_the_broker_gone_the_commit_is_queued_and_the_drain_lands_it(vault: Vault, broker_dir: Path) -> None:
    before = vault.head()
    command = 'echo later > notes/c.md && scripts/git-safe-commit.sh "notes: c" notes/c.md'
    res = vault.run(command, str(broker_dir / "b.sock"))
    assert res.returncode == broker.EXIT_QUEUED, res.stderr
    assert "no SHA yet" in res.stderr
    assert vault.head() == before
    drained = subprocess.run(
        [sys.executable, "-P", "-m", "scout", "broker", "drain", "--vault", str(vault.path)],
        env={k: v for k, v in vault.env.items() if k != "SCOUT_RUN_BROKER"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert drained.returncode == 0, drained.stderr
    assert "1 done" in drained.stdout
    assert vault.git("log", "-1", "--format=%s").strip() == "notes: c"
    assert broker.pending(vault.path) == 0


def test_a_telegram_dry_run_goes_through_the_broker_to_the_owners_chat(vault: Vault, served: str) -> None:
    secrets = vault.home / ".scout-secrets"
    secrets.mkdir(exist_ok=True)
    for name, value in (("telegram-bot-token", "123:abc"), ("telegram-chat-id", "4242")):
        (secrets / name).write_text(value + "\n")
        (secrets / name).chmod(0o600)
    res = vault.run(f'"{sys.executable}" -P -m scout notify telegram --dry-run --body "wrap"', served)
    assert res.returncode == 0, res.stderr
    assert res.stderr.startswith("id=")
    assert '"chat_id": "4242"' in res.stderr and "<REDACTED>" in res.stderr
    assert '"dry_run": true' in res.stdout


def test_the_broker_stops_with_its_parent(vault: Vault, broker_dir: Path) -> None:
    parent = subprocess.Popen(["/bin/sleep", "30"])
    proc = subprocess.Popen(
        [sys.executable, "-P", "-m", "scout", "broker", "serve", "--vault", str(vault.path), "--dir", str(broker_dir),
         "--parent", str(parent.pid)],
        env=vault.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        assert broker.wait_for(broker_dir / broker.SOCKET_NAME, timeout=30)
        parent.kill()
        parent.wait()
        deadline = time.monotonic() + 15
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert proc.poll() == 0
        assert not (broker_dir / broker.SOCKET_NAME).exists()
    finally:
        if proc.poll() is None:
            proc.kill()
