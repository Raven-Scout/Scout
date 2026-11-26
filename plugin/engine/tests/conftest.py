"""Shared pytest fixtures."""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from _pytest.runner import runtestprotocol

# ---- quarantine: bounded reruns for known-flaky tests ------------------------
#
# pytest-rerunfailures isn't a dependency, so known flakes are retried here.
# ``@pytest.mark.flaky(reruns=2, issue="https://github.com/Raven-Scout/Scout/issues/N")``
# reruns a test whose *call* phase failed — a setup or teardown error is never
# retried — with fresh function-scoped fixtures, up to ``reruns`` more times.
# Only the last attempt is reported, so a test that fails every attempt still
# fails the run. Every earlier failure is listed under "quarantined flaky
# reruns" in the terminal summary with its issue, so a quarantined test that
# starts failing more often stays visible. Remove the marker when the issue is
# fixed; never add one without an issue.

# ---- SCOUT_TEST_NO_KERNEL: the ubuntu legs' view of the macOS sandbox, on a Mac --------
#
# The kernel tests (the lab's and the run sandbox's) are gated on
# ``scout.sandbox.profile.available()``, which is False off macOS; the gate
# probe's sandbox tests on ``sys.platform == "darwin"`` and
# ``gate_probe.SANDBOX_EXEC`` existing. With SCOUT_TEST_NO_KERNEL=1 both say no
# here too, before any test module is imported, so a Mac runs the suite as the
# ubuntu coverage leg does: every sandbox-exec test skips, and the code under
# test sees no sandbox-exec. Read once at import; the per-test SCOUT_* scrub
# below doesn't undo it. Not covered: tests gated on other macOS facts (a
# case-insensitive disk, /dev/ttys*, launchd's branch, Homebrew git), which still
# run on a Mac.
if os.environ.get("SCOUT_TEST_NO_KERNEL") == "1":
    from pathlib import Path as _Path

    from scout.agent_runtimes import gate_probe as _gate_probe
    from scout.sandbox import profile as _sandbox_profile

    _sandbox_profile.available = lambda: False
    _gate_probe.SANDBOX_EXEC = _Path("/nonexistent/sandbox-exec")

_FLAKY_RERUN_PROPERTY = "quarantined-flaky-rerun"
# This session's rerun lines. pytest_runtest_logreport gets no config to stash
# them on, so they're module state, reset per session so an in-process pytest
# run (pytester) doesn't inherit an earlier session's lines.
_FLAKY_RERUN_LINES: list[str] = []


def pytest_configure(config: pytest.Config) -> None:
    _FLAKY_RERUN_LINES.clear()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> bool | None:
    marker = item.get_closest_marker("flaky")
    if marker is None:
        return None
    issue = marker.kwargs.get("issue")
    if not issue:
        raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.flaky needs issue=<tracking issue URL>")
    reruns = max(0, int(marker.kwargs.get("reruns", 2)))

    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    reports = runtestprotocol(item, nextitem=nextitem, log=False)
    rerun_lines = []
    for attempt in range(1, reruns + 1):
        call = next((r for r in reports if r.when == "call"), None)
        if call is None or not call.failed:
            break
        crash = getattr(call.longrepr, "reprcrash", None)
        why = crash.message.splitlines()[0] if crash and crash.message else "failed"
        rerun_lines.append(f"{item.nodeid} attempt {attempt}/{reruns + 1}: {why} ({issue})")
        reports = runtestprotocol(item, nextitem=nextitem, log=False)
    # The lines ride on the reports, not straight into the stash: under
    # pytest-xdist this runs in a worker, whose stash the controller never
    # prints, while user_properties are serialized back with the report.
    for report in reports:
        if report.when == "call":
            report.user_properties.extend((_FLAKY_RERUN_PROPERTY, line) for line in rerun_lines)
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Fires in the process that prints the summary (the xdist controller, or the
    # only process without xdist) and in each worker; only the former's lines
    # are ever printed.
    for name, value in report.user_properties:
        if name == _FLAKY_RERUN_PROPERTY:
            _FLAKY_RERUN_LINES.append(str(value))


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    reruns = _FLAKY_RERUN_LINES
    if reruns:
        terminalreporter.section("quarantined flaky reruns", yellow=True)
        for line in reruns:
            terminalreporter.line(f"RERUN {line}")


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's live vault.

    paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
    unset, so a live ~/Scout makes non-fake_data_dir tests read real user
    data (and fail — the schedule CLI tests picked up the live overlay's
    extra slot). Point HOME at an empty per-test tmp dir and scrub SCOUT_*
    vars. Tests that need a data dir keep using fake_data_dir, which sets
    SCOUT_DATA_DIR after this fixture runs.

    The host's timezone is hidden the same way: SCOUT_LOCALTIME points host
    detection (scout.config.host_timezone_name, scripts/scout-tz.sh) at a link
    that does not exist, so an unconfigured vault resolves to the packaged
    default on every developer machine and CI host alike. Tests that exercise
    host detection point it at a fixture symlink of their own.
    """
    home = tmp_path_factory.mktemp("hermetic-home")
    monkeypatch.setenv("HOME", str(home))
    for key in list(os.environ):
        if key.startswith("SCOUT_"):
            monkeypatch.delenv(key, raising=False)
    # Claude Code's own config dir overrides HOME for ~/.claude.json and settings.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("SCOUT_LOCALTIME", str(home / "no-localtime"))
    # The runners' OS write sandbox (spec 2026-11-15) is off by default in tests: most runner tests stand in a
    # no-op engine (/usr/bin/true) for scoutctl, which can't print a profile. The sandbox's own tests
    # (test_run_sandbox_*) delete this to turn it on.
    monkeypatch.setenv("SCOUT_RUN_SANDBOX", "off")


@pytest.fixture(autouse=True)
def _owner_store_follows_home(monkeypatch: pytest.MonkeyPatch) -> Callable[[], Path]:
    """The changes map's owner store lives under the account's home from the account database, never ``$HOME``
    (a run sets its own environment), and the run sandbox protects every per-user path under both
    (hook_paths.homes). Tests keep the account's home at the per-test HOME, read when it is used, so a test that
    re-points HOME moves the store with it and no protected list names the real home. Returns the real lookup
    (hook_paths.account_home) for the tests that exercise it."""
    from scout.kb import hook_paths

    real = hook_paths.account_home
    monkeypatch.setattr(hook_paths, "account_home", lambda: Path(os.environ["HOME"]))
    return real


# ---- protected roots: the machine's git config, asked once per process ---------
#
# Every gate decision builds the protected roots (scout.kb.hook_paths), and
# git_config_files asks every git on PATH for its system and global config, from
# no cwd and again from the vault: 8 git runs on a Mac with Homebrew's git and
# Apple's /usr/bin/git (an xcrun shim). A hook is one process, so production
# builds the list once per call; in tests every test has its own HOME and PATH,
# so hook_paths' per-process cache never hits, and each in-process decision paid
# ~0.4 s on macOS CI (test_hooks_write_gate: 295 tests, median 0.42 s vs 0.04 s
# on Ubuntu). Only HOME and the vault differ between tests, and a test's HOME
# and vault are empty temp dirs, so the answer is the machine's system configs.

_SESSION_PATH = os.environ.get("PATH", "")
_MACHINE_GIT_CONFIG: list[tuple[str, bool]] = []
_MACHINE_GIT_CONFIG_READ = False


def _machine_git_config(real: Callable[..., list[tuple[str, bool]]]) -> list[tuple[str, bool]]:
    """What the real ``git_config_files`` names for an empty HOME and no vault: each git's system config."""
    global _MACHINE_GIT_CONFIG_READ
    if not _MACHINE_GIT_CONFIG_READ:
        with tempfile.TemporaryDirectory() as home:
            found = real({"PATH": _SESSION_PATH, "HOME": home})
            roots = {home, os.path.realpath(home)}
            _MACHINE_GIT_CONFIG[:] = [
                (p, tree) for p, tree in found if not any(p == r or p.startswith(r + os.sep) for r in roots)
            ]
        _MACHINE_GIT_CONFIG_READ = True
    return _MACHINE_GIT_CONFIG


@pytest.fixture(autouse=True)
def _machine_git_config_once(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """In-process gate decisions get the machine's git config list asked once per process, not 8 gits per test.

    ``$GIT_CONFIG_SYSTEM`` is still honoured (the real function names that file in place of the system ones).
    What this leaves out: ``include.path`` / ``includeIf`` entries a test writes into its own ``~/.gitconfig``,
    a fake ``git`` a test puts on PATH, and the deadline. A test about any of those is marked
    ``@pytest.mark.real_git_config`` and gets the real function. Hooks run as subprocesses always do.
    """
    if request.node.get_closest_marker("real_git_config"):
        return
    from scout.kb import hook_paths

    real = hook_paths.git_config_files

    def git_config_files(
        env: Mapping[str, str], *, deadline: float | None = None, vault: Path | None = None
    ) -> list[tuple[str, bool]]:
        if env.get("GIT_CONFIG_SYSTEM"):
            return [(env["GIT_CONFIG_SYSTEM"], False)]
        return list(_machine_git_config(real))

    monkeypatch.setattr(hook_paths, "git_config_files", git_config_files)


@pytest.fixture
def eval_runner_git_off_xcrun(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval harness's runner gets this machine's own git first on its system PATH, on macOS.

    A sample runs the real runner with ``PATH`` ending in ``/usr/bin`` (tests/eval/harness/runner_env.py), and
    on macOS ``/usr/bin/git`` is an xcrun shim that finds the Command Line Tools' git on every call; a sample
    runs git ~90 times. When the test session's PATH has another git (Homebrew's, on CI), a folder holding
    just that one goes ahead of ``/usr/bin``; nothing else on the runner's PATH changes. Elsewhere: no-op.
    """
    import shutil
    import sys

    git = shutil.which("git", path=_SESSION_PATH)
    if sys.platform != "darwin" or not git or os.path.realpath(git) == os.path.realpath("/usr/bin/git"):
        return
    import tests.eval.harness.runner_env as runner_env

    bin_dir = tmp_path_factory.mktemp("eval-git")
    (bin_dir / "git").symlink_to(git)
    monkeypatch.setattr(runner_env, "SYSTEM_PATH", [str(bin_dir), *runner_env.SYSTEM_PATH])


_REFUSING_LAUNCHCTL = """#!/bin/sh
echo "launchctl is blocked in tests (tests/conftest.py): launchd's gui/<uid> domain is global" >&2
echo "to the user, so a test reaching it replaces the real scheduler jobs. Mock subprocess instead." >&2
exit 99
"""


@pytest.fixture(autouse=True)
def _block_real_launchctl(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may run the real launchctl, in this process or in a child.

    A temp HOME doesn't isolate launchd: a `bootstrap install` without --no-jobs boots out and
    replaces the user's live com.scout.* jobs (it happened once, during an eval-harness test run).
    The engine always calls `launchctl` through PATH, so a refusing one goes first. Tests that
    exercise launchctl calls mock subprocess, which never reaches PATH.
    """
    bin_dir = tmp_path_factory.mktemp("no-launchctl")
    script = bin_dir / "launchctl"
    script.write_text(_REFUSING_LAUNCHCTL)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


_RECORDING_NOTIFIER = """#!/bin/sh
# A stand-in desktop notifier (tests/conftest.py): records its argv, never reaches the desktop.
{{ printf '%s' "$(basename "$0")"; printf ' %s' "$@"; printf '\\n'; }} >> "{record}"
cat > /dev/null
exit 0
"""


@pytest.fixture(autouse=True)
def desktop_notifications(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test may raise a real desktop notification from a shell script, in this process or a child.

    The shell helpers (run-outcome.sh's engine-unreachable floor, claude-with-retry.sh's auth
    alert, session-lane-liveness.py) call `osascript` / `notify-send` through PATH. A test that
    forgot its own fake once popped a real "Scout engine is broken" banner on the maintainer's
    Mac on every run. So a recording `osascript` and `notify-send` go first on PATH for every
    test; each call appends one line, `<name> <argv…>`, to the returned file (absent until the
    first call). A test that puts its own fake first on PATH still wins.
    """
    bin_dir = tmp_path_factory.mktemp("no-desktop-notify")
    record = bin_dir / "desktop-notifications.log"
    for name in ("osascript", "notify-send"):
        script = bin_dir / name
        script.write_text(_RECORDING_NOTIFIER.format(record=record))
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return record


_RECORDING_POWER = """#!/bin/sh
# A stand-in power assertion (tests/conftest.py): records its argv, then runs the
# wrapped command without asserting anything on the host.
{{ printf '%s' "$(basename "$0")"; printf ' %s' "$@"; printf '\\n'; }} >> "{record}"
while [ "$#" -gt 0 ]; do
    case "$1" in -*) shift ;; *) break ;; esac
done
[ "$#" -gt 0 ] || exit 0
exec "$@"
"""


@pytest.fixture(autouse=True)
def power_assertions(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test may hold a real idle-sleep assertion from a shell script (#210).

    claude-with-retry.sh wraps each attempt and backoff in ``caffeinate`` (macOS)
    or ``systemd-inhibit`` (Linux), found through PATH. Recording stand-ins go
    first on PATH for every test: each call appends ``<name> <argv…>`` to the
    returned file (absent until the first call) and runs the wrapped command.
    """
    bin_dir = tmp_path_factory.mktemp("no-power-assertion")
    record = bin_dir / "power-assertions.log"
    for name in ("caffeinate", "systemd-inhibit"):
        script = bin_dir / name
        script.write_text(_RECORDING_POWER.format(record=record))
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return record


@pytest.fixture(autouse=True)
def _block_real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the real ``gh`` (Agent Sessions spec §7).

    ``gh`` reads the developer's auth and hits the network. Every test sees gh as
    absent, and the default runner fails loudly if something calls it anyway. Tests
    that need gh behaviour inject a fake through ``BuildOptions`` or their own
    monkeypatch, which runs after this fixture and so wins.
    """
    import scout.sessions.github as gh

    def _real_gh_blocked(argv: list[str]) -> str | None:
        raise AssertionError("real gh called from a test")

    monkeypatch.setattr(gh, "gh_available", lambda: False)
    monkeypatch.setattr(gh, "default_runner", _real_gh_blocked)


class _GuardedModule:
    """A stand-in for a module in ONE importer's namespace: every attribute is the
    real module's, except the named entry points, which fail the test."""

    def __init__(self, real: object, blocked: dict[str, str]) -> None:
        self._real = real
        for name, what in blocked.items():
            setattr(self, name, self._blocker(what))

    @staticmethod
    def _blocker(what: str):
        def blocked(*_a: object, **_k: object) -> None:
            # pytest.fail raises a BaseException, so the notifier's own
            # ``except Exception`` (fan-out, notify) cannot swallow it.
            pytest.fail(f"{what} reached from a test; patch it")

        return blocked

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@pytest.fixture(autouse=True)
def _block_real_pushes(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may raise a real desktop notification or send a real Telegram message.

    A test once sent a real Telegram push to the developer's phone. The desktop
    sender's ``subprocess.run`` (osascript / notify-send) and the Telegram
    client's ``requests.post`` fail the test unless the test patches them, which
    it does after this fixture runs and so wins. Only those two importers'
    references are replaced; the real ``subprocess`` and ``requests`` modules are
    untouched.
    """
    import scout.notify_desktop as desktop
    import scout.scripts.notify_telegram as telegram

    monkeypatch.setattr(
        desktop, "subprocess", _GuardedModule(desktop.subprocess, {"run": "real osascript/notify-send"})
    )
    monkeypatch.setattr(telegram, "requests", _GuardedModule(telegram.requests, {"post": "real Telegram send"}))


_FAKE_SECURITY = r"""#!/bin/bash
# A stand-in macOS `security` (tests/conftest.py). It keeps Scout's one generic-password
# item in a temp dir, as <service>@<account> files, and refuses everything else. The real
# `security` reads and writes the user's login Keychain, which a test must never touch.
store="{store}"
refuse() {{
    echo "security is blocked in tests (tests/conftest.py): $*" >&2
    echo "Only Scout's own generic-password item is faked; anything else would reach the real Keychain." >&2
    exit 99
}}
one() {{
    local cmd="$1"; shift
    local svc="" acct="" pw="" want_pw=0 update=0
    while [ $# -gt 0 ]; do
        case "$1" in
            -s) svc="$2"; shift 2 ;;
            -a) acct="$2"; shift 2 ;;
            -U) update=1; shift ;;
            -w) if [ "$cmd" = find-generic-password ]; then want_pw=1; shift; else pw="$2"; shift 2; fi ;;
            *) refuse "$cmd: option $1" ;;
        esac
    done
    [ "$svc" = scout-claude-oauth-token ] || refuse "$cmd for service '$svc'"
    local item="$store/$svc@$acct"
    case "$cmd" in
        add-generic-password)
            [ -n "${{FAKE_KEYCHAIN_LOCKED:-}}" ] && return 36
            if [ -e "$item" ] && [ "$update" = 0 ]; then
                echo "The specified item already exists in the keychain." >&2
                return 45
            fi
            printf '%s' "$pw" > "$item" ;;
        find-generic-password)
            [ -e "$item" ] || {{ echo "The specified item could not be found in the keychain." >&2; return 44; }}
            if [ "$want_pw" = 1 ]; then
                [ -n "${{FAKE_KEYCHAIN_LOCKED:-}}" ] && {{ echo "User interaction is not allowed." >&2; return 36; }}
                cat "$item"; echo
            fi ;;
        delete-generic-password)
            [ -e "$item" ] || return 44
            rm -f "$item" ;;
        *) refuse "$cmd" ;;
    esac
}}
echo "$*" | sed -E 's/-w [^ ]+/-w <redacted>/' >> "$store/.calls"
if [ "${{1:-}}" = -i ]; then
    while read -r -a words; do
        [ "${{#words[@]}}" -gt 0 ] || continue
        echo "-i ${{words[*]}}" | sed -E 's/-w [^ ]+/-w <redacted>/' >> "$store/.calls"
        one "${{words[@]}}" || true
    done
    exit 0  # like the real one, -i doesn't report a failed command in its exit status
fi
one "$@"
"""


@pytest.fixture(autouse=True)
def fake_keychain(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test may reach the real macOS ``security`` (and with it the login Keychain).

    A fake ``security`` goes first on PATH, in this process and in every child. It keeps
    Scout's ``scout-claude-oauth-token`` item as a file in the returned directory and
    exits 99 on any other subcommand or service: no write, delete or secret read can reach
    the real Keychain. ``FAKE_KEYCHAIN_LOCKED=1`` makes reads of the secret fail the way a
    locked Keychain does. ``<dir>/.calls`` records each call, with ``-w`` values redacted.
    Scout calls ``security`` through PATH only; a test pins that.
    """
    bin_dir = tmp_path_factory.mktemp("fake-security")
    store = tmp_path_factory.mktemp("fake-keychain")
    script = bin_dir / "security"
    script.write_text(_FAKE_SECURITY.format(store=store))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("FAKE_KEYCHAIN_LOCKED", raising=False)
    return store


@pytest.fixture
def fake_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A writable tmp data dir wired up via SCOUT_DATA_DIR."""
    d = tmp_path / "Scout"
    d.mkdir()
    (d / ".scout-logs").mkdir()
    (d / ".scout-cache").mkdir()
    (d / ".scout-state").mkdir()
    (d / "knowledge-base").mkdir()
    (d / "action-items").mkdir()
    monkeypatch.setenv("SCOUT_DATA_DIR", str(d))
    yield d


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset any SCOUT_* env vars that might leak between tests (keeping
    _hermetic_env's SCOUT_LOCALTIME, which hides the host's timezone)."""
    for key in list(os.environ):
        if key.startswith("SCOUT_") and key != "SCOUT_LOCALTIME":
            monkeypatch.delenv(key, raising=False)


# ---- kb lint: throwaway git vault --------------------------------------------


@dataclass
class KbRepo:
    root: Path

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def stage(self, rel: str, text: str) -> None:
        self.write(rel, text)
        self.git("add", "--", rel)

    def commit(self, msg: str = "c") -> None:
        self.git("commit", "-q", "--no-verify", "-m", msg)


_KB_REPO_TEMPLATE: list[Path] = []


def _kb_repo_template() -> Path:
    """The kb_repo vault, built once per process: git init, four config lines and a commit are six git runs."""
    if not _KB_REPO_TEMPLATE or not _KB_REPO_TEMPLATE[0].is_dir():
        repo = KbRepo(Path(tempfile.mkdtemp(prefix="scout-kb-repo-")) / "vault")
        (repo.root / "knowledge-base").mkdir(parents=True)
        (repo.root / "action-items").mkdir()
        repo.git("init", "-q", "-b", "main")
        repo.git("config", "user.email", "t@example.com")
        repo.git("config", "user.name", "T")
        repo.git("config", "commit.gpgsign", "false")
        repo.git("config", "core.quotepath", "false")
        repo.write("README.md", "vault\n")
        # No detached `git maintenance run --auto` (git 2.47+) left running in the template while tests copy it.
        no_auto = ("-c", "maintenance.auto=false", "-c", "gc.auto=0")
        repo.git(*no_auto, "add", "--", "README.md")
        repo.git(*no_auto, "commit", "-q", "--no-verify", "-m", "init")
        atexit.register(shutil.rmtree, repo.root.parent, True)
        _KB_REPO_TEMPLATE[:] = [repo.root]
    return _KB_REPO_TEMPLATE[0]


@pytest.fixture
def kb_repo(tmp_path: Path) -> KbRepo:
    """A git vault with an empty knowledge-base/ and action-items/ and one commit (a README), copied from a
    per-process template. The index is refreshed for the copy's new inodes, as a fresh clone would be."""
    repo = KbRepo(tmp_path / "vault")
    shutil.copytree(_kb_repo_template(), repo.root, symlinks=True)
    repo.git("update-index", "-q", "--refresh")
    return repo
