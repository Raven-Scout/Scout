"""The experiment sandbox profile (spec §6.3, §12 step 1): each refusal is checked against the real kernel.

Only the first test is pure text; the rest run ``sandbox-exec`` and skip only where it doesn't exist (not
macOS). They are the proof of the owner's decision that experiment scripts are held by an OS sandbox.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.errors import LabError
from scout.kb import privacy
from scout.lab import paths, runner, sandbox
from scout.sandbox import profile
from tests.unit import lab_runner_helpers as h

kernel = pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")


def test_profile_text_orders_deny_before_allow(tmp_path: Path) -> None:
    exp = tmp_path / "exp"
    (exp / ".tmp").mkdir(parents=True)
    private = tmp_path / "kb" / "p.md"
    private.parent.mkdir()
    private.write_text("x")
    gitdir = tmp_path / "vault-git"
    gitdir.mkdir()
    sock = Path("/tmp/scout-lab-x/m.sock")
    engine = tmp_path / "engine"
    text = sandbox.experiment_profile(
        exp,
        exp / ".tmp",
        sock,
        [private, gitdir],
        [Path("/usr/bin/git")],
        env={"HOME": str(tmp_path / "home")},
        allow_reads=[engine],
    )
    real = os.path.realpath
    assert text.startswith("(version 1)")
    deny = text.index('(deny file-write*\n  (subpath "/"))')
    allow = text.index("(allow file-write*")
    assert text.index("(allow default)") < deny < allow
    allow_form = text[allow : text.index("\n(", allow)]
    for item in (f'(subpath "{real(exp)}")', f'(subpath "{real(exp / ".tmp")}")', '(literal "/dev/null")'):
        assert item in allow_form
    # R20: the script's own terminal only, never a regex that reaches the owner's /dev/ttys* terminals.
    assert '(literal "/dev/tty")' in allow_form and "^/dev/tty" not in allow_form
    # I2: the other terminals are denied to read and to write, after every allow.
    for op in ("file-read*", "file-write*"):
        form = text[text.rindex(f"(deny {op}") :]
        assert '(regex #"^/dev/ttys[0-9]+$")' in form and '(regex #"^/dev/pty")' in form, op
    # I1/I5: signals and process information only within the experiment's own sandbox.
    for form in (
        "(deny signal)\n(allow signal (target same-sandbox))",
        "(deny process-info*)\n(allow process-info* (target same-sandbox))",
        '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))',
    ):
        assert form in text, form
    # R32: the home folder's reads are denied before the allow-backs, which come before the private denies.
    home_deny = text.index(f'(subpath "{real(tmp_path / "home")}")')
    allow_back = text.index(f'(allow file-read-data\n  (subpath "{real(engine)}")')
    assert text.index("(deny file-read-data") < home_deny < allow_back < text.index(f'(literal "{real(private)}")')
    # The experiment's own folders come back after every read deny: their files are private by path.
    own = text.index(f'(allow file-read-data\n  (subpath "{real(exp)}")\n  (subpath "{real(exp / ".tmp")}"))')
    assert own > text.index(f'(literal "{real(private)}")') and own > text.index(f'(subpath "{real(gitdir)}")')
    assert own > text.index(f'(literal "{real(tmp_path / "home" / ".netrc")}")')
    # Every path is resolved (on macOS /tmp is /private/tmp), and the socket sits in a path-literal.
    assert f'(path-literal "{real(sock)}")' in text and real(sock).startswith(real("/tmp") + "/")
    reads = text[text.index("(deny file-read-data") :]
    assert f'(literal "{real(private)}")' in reads
    assert f'(subpath "{real(gitdir)}")' in reads
    assert f'(subpath "{real(tmp_path / "home" / ".scout-secrets")}")' in reads
    for d in (".ssh", ".aws", ".config/gh", ".gnupg", "Library/Keychains"):  # R19
        assert f'(subpath "{real(tmp_path / "home" / d)}")' in reads, d
    assert f'(literal "{real(tmp_path / "home" / ".netrc")}")' in reads
    net = text.index("(deny network*)")
    assert text.index("(deny file-read-data") < net < text.index("(allow network-outbound (remote unix-socket")
    execs = text[text.index("(deny process-exec") :]
    assert '(literal "/usr/bin/git")' in execs and '(literal "/bin/launchctl")' in execs
    for form in ("(deny job-creation)", "(deny appleevent-send)", "(deny file-mount)"):
        assert form in text
    mach = text[text.index("(deny mach-lookup") :]
    names = ("com.apple.SecurityServer", "com.apple.securityd", "com.apple.pasteboard.1", "com.apple.nsurlsessiond")
    for name in names:
        assert f'(global-name "{name}")' in mach, name


def test_profile_refuses_a_path_sbpl_cant_carry(tmp_path: Path) -> None:
    with pytest.raises(profile.ProfileError):
        sandbox.experiment_profile(tmp_path, tmp_path / ".tmp", Path("/tmp/s"), [Path("/tmp/a\nb")], [], env={})
    for bad in (Path("/tmp/a\nb"), Path(os.fsdecode(b"/tmp/\xff"))):
        with pytest.raises(profile.ProfileError):
            sandbox.experiment_profile(tmp_path, tmp_path / ".tmp", Path("/tmp/s"), [], [], env={}, deny_access=[bad])


def test_committed_files_are_sealed_after_every_allow(tmp_path: Path) -> None:
    """R34: each op is denied after the experiment folder's allow-backs, file-read-data by name (a rule naming the
    operation wins over a later file-read* one)."""
    exp = tmp_path / "exp"
    (exp / ".tmp").mkdir(parents=True)
    sealed = os.path.realpath(exp / "helper.sh")
    text = sandbox.experiment_profile(
        exp, exp / ".tmp", Path("/tmp/s"), [], [], env={"HOME": str(tmp_path / "home")}, deny_access=[exp / "helper.sh"]
    )
    last_allow = max(text.rindex("(allow file-read-data"), text.rindex("(allow file-write*"))
    for op in sandbox.SEALED_OPS:
        form = text.index(f"(deny {op}\n  (literal {profile._quote(sealed)})")
        assert form > last_allow, op


# --- against the kernel ------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return h.make_vault(tmp_path)


@kernel
def test_write_outside_experiment_dir_refused(vault: Path, tmp_path: Path) -> None:
    target = vault / "knowledge-base" / "x.md"
    out = h.run(vault, tmp_path, f"echo x > '{target}'")
    assert out.exit != 0 and not out.timed_out
    assert not target.exists()
    assert "not permitted" in out.stderr_tail

    ok = h.run(vault, tmp_path, 'echo y > out.txt && echo t > "$TMPDIR/t.txt" && cat "$TMPDIR/t.txt"')
    assert ok.exit == 0, ok.stderr_tail
    assert (h.exp_dir(vault) / "out.txt").read_text() == "y\n"
    assert ok.stdout_tail.strip() == "t"

    home_file = tmp_path / "home" / "x"
    for path in (home_file, Path("/tmp") / f"scout-lab-test-{os.getpid()}", vault / "outside.md"):
        refused = h.run(vault, tmp_path, f"echo z > '{path}'")
        assert refused.exit != 0, path
        assert not path.exists(), path


@pytest.fixture
def other_socket() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(dir="/tmp"))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(d / "o.sock"))
    s.listen(1)
    try:
        yield d / "o.sock"
    finally:
        s.close()
        shutil.rmtree(d, ignore_errors=True)


@kernel
def test_direct_network_refused(vault: Path, tmp_path: Path, other_socket: Path) -> None:
    by_name = h.run(vault, tmp_path, "/usr/bin/curl -sS -m 5 https://example.com")
    assert by_name.exit != 0
    by_ip = h.run(vault, tmp_path, "/usr/bin/curl -sS -m 5 https://1.1.1.1")
    assert by_ip.exit != 0
    py = (
        f"{h.PY} -c \"import socket; s=socket.socket(socket.AF_UNIX); s.connect('{other_socket}'); print('connected')\""
    )
    unix = h.run(vault, tmp_path, py)
    assert unix.exit != 0 and "connected" not in unix.stdout_tail
    assert "not permitted" in unix.stderr_tail

    metered = h.run(vault, tmp_path, f"printf hi > p.txt && {h.CALL}")
    assert metered.exit == 0, metered.stderr_tail
    assert metered.stdout_tail.strip() == "echo:hi"
    assert metered.spent_usd == pytest.approx(0.4)


@kernel
def test_private_file_unreadable(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path / "a")
    private, normal = vault / h.PRIVATE_NOTE, vault / h.NORMAL_NOTE
    out = h.run(vault, tmp_path / "a", f"cat '{private}'")
    assert out.exit != 0 and "CANARY-PRIV-LAB" not in out.stdout_tail
    # Through the vault's other spelling of the path too: the kernel matches the resolved file.
    upper = str(private).replace("knowledge-base/people", "Knowledge-Base/PEOPLE")
    assert h.run(vault, tmp_path / "a", f"cat '{upper}'").exit != 0
    shown = h.run(vault, tmp_path / "a", f"cat '{normal}'")
    assert shown.exit == 0 and "normal-note-text" in shown.stdout_tail

    pvault = h.make_vault(tmp_path / "b", visibility="private")
    allowed = h.run(pvault, tmp_path / "b", f"cat '{pvault / h.PRIVATE_NOTE}'")
    assert allowed.exit == 0, allowed.stderr_tail
    assert "CANARY-PRIV-LAB" in allowed.stdout_tail


@kernel
def test_private_copies_outside_the_kb_unreadable(vault: Path, tmp_path: Path) -> None:
    """Git keeps every private note's bytes, the privacy index their frontmatter; the inbox is private; secrets."""
    obj = vault / ".git" / "objects" / "ab" / "cdef"
    obj.parent.mkdir(parents=True)
    obj.write_text("CANARY-PRIV-GIT")
    inbox = vault / ".scout-inbox" / "n.jsonl"
    inbox.parent.mkdir()
    inbox.write_text("CANARY-PRIV-INBOX")
    index = vault / ".scout-cache" / "privacy-index.json"  # written by the runner's own label read
    secret = tmp_path / "home" / ".scout-secrets" / "token"
    secret.parent.mkdir(parents=True)
    secret.write_text("CANARY-SECRET")
    for path in (obj, inbox, secret, index):
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, path
        assert "not permitted" in out.stderr_tail, path  # refused, not merely missing


@kernel
def test_spawn_routes_and_git_refused(vault: Path, tmp_path: Path) -> None:
    for cmd in ("/bin/launchctl list", "/usr/bin/osascript -e 'return 1'", "/usr/bin/open /nonexistent-scout-lab"):
        out = h.run(vault, tmp_path, cmd)
        assert out.exit != 0, cmd
        assert "not permitted" in out.stderr_tail, cmd
    git = shutil.which("git")
    if git is not None:
        out = h.run(vault, tmp_path, f"'{os.path.realpath(git)}' --version")
        assert out.exit != 0 and "git version" not in out.stdout_tail


@kernel
def test_other_checkouts_raw_and_action_items_unreadable(tmp_path: Path) -> None:
    """R18: copies of private notes in other checkouts of the repo, Raw/ drops and action items."""
    vault = h.make_vault(tmp_path / "a")
    note = (vault / h.PRIVATE_NOTE).read_text()
    nested = vault / ".claude" / "worktrees" / "x" / h.PRIVATE_NOTE  # a worktree-style checkout in the vault
    nested.parent.mkdir(parents=True)
    nested.write_text(note)
    other = tmp_path / "a" / "elsewhere-checkout"  # a git worktree of the vault's repo outside it
    (other / "knowledge-base" / "people").mkdir(parents=True)
    (other / h.PRIVATE_NOTE).write_text(note)
    (other / ".git").write_text(f"gitdir: {vault / '.git' / 'worktrees' / 'w'}\n")
    pointer = vault / ".git" / "worktrees" / "w" / "gitdir"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"{other / '.git'}\n")
    raw = vault / "Raw" / "Drop" / "scan.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("CANARY-PRIV-RAW")
    items = vault / "action-items" / "action-items-2026-11-21.md"
    items.parent.mkdir()
    items.write_text("CANARY-PRIV-ITEMS")
    logs = vault / ".scout-logs" / "connector-calls-2026-11-21.jsonl"  # in bounds (spec §6.3)
    logs.parent.mkdir()
    logs.write_text("in-bounds-log")
    for path in (nested, other / h.PRIVATE_NOTE, raw, items):
        out = h.run(vault, tmp_path / "a", f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, path
        assert "not permitted" in out.stderr_tail, path
    shown = h.run(vault, tmp_path / "a", f"cat '{logs}'")
    assert shown.exit == 0 and "in-bounds-log" in shown.stdout_tail

    pvault = h.make_vault(tmp_path / "b", visibility="private")
    (pvault / "action-items").mkdir()
    (pvault / "action-items" / "a.md").write_text("CANARY-PRIV-ITEMS")
    allowed = h.run(pvault, tmp_path / "b", f"cat '{pvault / 'action-items' / 'a.md'}'")
    assert allowed.exit == 0 and "CANARY-PRIV-ITEMS" in allowed.stdout_tail


@kernel
def test_credential_stores_unreadable(vault: Path, tmp_path: Path) -> None:
    """R19, on a fake HOME tree (the env's HOME is inside tmp_path)."""
    home = tmp_path / "home"
    files = [
        home / ".ssh" / "id_test",
        home / ".aws" / "credentials",
        home / ".config" / "gh" / "hosts.yml",
        home / ".netrc",
        home / ".gnupg" / "secring.gpg",
        home / "Library" / "Keychains" / "test.keychain-db",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("CANARY-CREDENTIAL")
    for path in files:
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, path
        assert "not permitted" in out.stderr_tail, path


@kernel
def test_keychain_unreachable(vault: Path, tmp_path: Path) -> None:
    """R19: a lookup of an item that doesn't exist fails because the keychain can't be reached at all.

    Nothing in the owner's keychain is created, read or changed: the service name is made up, and the
    lookup runs only inside the sandbox.
    """
    name = f"scout-lab-test-no-such-item-{os.getpid()}"
    out = h.run(vault, tmp_path, f"/usr/bin/security find-generic-password -s '{name}'")
    assert out.exit != 0
    # Denied: the search itself can't be created (errSecParam), before any "not found" from its empty result.
    assert "SecKeychainSearchCreateFromAttributes" in out.stderr_tail, out.stderr_tail
    listed = h.run(vault, tmp_path, "/usr/bin/security list-keychains")
    assert listed.exit != 0 and "SecKeychainCopySearchList" in listed.stderr_tail


@kernel
def test_other_terminals_unwritable(vault: Path, tmp_path: Path) -> None:
    """R20: the owner's open terminals (/dev/ttys*) can't be written; an empty write, so a failure shows nothing."""
    ttys = sorted(Path("/dev").glob("ttys[0-9]*"))
    if not ttys:
        pytest.skip("no /dev/ttys* device on this machine")
    out = h.run(vault, tmp_path, f"printf '' > '{ttys[0]}'")
    assert out.exit != 0 and "not permitted" in out.stderr_tail


def _git(cwd: Path, *args: str) -> None:
    """git with no user or system config (the owner's signing, hooks and defaults can't change the result)."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    ident = ["-c", "user.name=Alex", "-c", "user.email=alex@example.org", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", *ident, *args], cwd=cwd, env=env, check=True, capture_output=True)


@kernel
@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to make a linked worktree")
@pytest.mark.parametrize("layout", ["beside", "inside"])
def test_main_checkout_unreadable_from_a_linked_worktree(tmp_path: Path, layout: str) -> None:
    """I2b: an experiment in a ``git worktree add`` checkout can't read the main checkout's private material,
    whether the worktree sits beside the main checkout or inside it (``.claude/worktrees/x``, the vault's
    own layout)."""
    main = h.make_vault(tmp_path)
    _git(main, "init", "-q")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "seed")
    linked = tmp_path / "linked" if layout == "beside" else main / ".claude" / "worktrees" / "x"
    _git(main, "worktree", "add", "-q", str(linked))
    items = main / "action-items" / "action-items-2026-11-21.md"
    items.parent.mkdir()
    items.write_text("CANARY-PRIV-ITEMS")
    raw = main / "Raw" / "Drop" / "scan.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("CANARY-PRIV-RAW")
    for path in (main / h.PRIVATE_NOTE, items, raw):
        out = h.run(linked, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, path
        assert "not permitted" in out.stderr_tail, path
    own = h.run(linked, tmp_path, f"cat '{linked / h.NORMAL_NOTE}'")
    assert own.exit == 0 and "normal-note-text" in own.stdout_tail


@kernel
def test_other_terminals_unreadable(vault: Path, tmp_path: Path) -> None:
    """I2: a terminal can't be opened to read what is typed in it. The terminal is a pty this test opens
    itself, never one of the owner's, and nothing is read from it: the open alone is refused."""
    master, slave = os.openpty()
    try:
        name = os.ttyname(slave)
        probe = f"import os; os.close(os.open({name!r}, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)); print('opened')"
        out = h.run(vault, tmp_path, f'{h.PY} -I -c "{probe}"')
        assert out.exit != 0 and "opened" not in out.stdout_tail
        assert "not permitted" in out.stderr_tail, out.stderr_tail
    finally:
        os.close(master)
        os.close(slave)


@kernel
def test_the_runner_and_other_processes_cant_be_signalled_but_own_children_can(vault: Path, tmp_path: Path) -> None:
    """I1: the script's parent is this test process; only signal 0 (no signal at all) is sent to it, and to an
    unrelated process of the test's own. A real signal goes only to the script's own child."""
    other = subprocess.Popen(["/bin/sleep", "60"])
    try:
        script = (
            f"kill -0 $PPID && echo PARENT-REACHED; kill -0 {other.pid} && echo OTHER-REACHED; "
            "/bin/sleep 30 & c=$!; kill -TERM $c && echo CHILD-SIGNALLED; wait $c; echo done"
        )
        out = h.run(vault, tmp_path, script)
        assert "PARENT-REACHED" not in out.stdout_tail and "OTHER-REACHED" not in out.stdout_tail
        assert "CHILD-SIGNALLED" in out.stdout_tail and "done" in out.stdout_tail, out.stderr_tail
        assert "not permitted" in out.stderr_tail
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


_PROCINFO = """
import ctypes, os, subprocess
libc = ctypes.CDLL(None, use_errno=True)
libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
def args(pid):
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2: argv and environment
    size = ctypes.c_size_t(1 << 20)
    buf = ctypes.create_string_buffer(size.value)
    return libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) == 0
def path(pid):
    buf = ctypes.create_string_buffer(4096)
    return libproc.proc_pidpath(pid, buf, 4096) > 0
child = subprocess.Popen(["/bin/sleep", "5"])
print("parent-args", args(os.getppid()), "parent-path", path(os.getppid()))
print("child-args", args(child.pid), "child-path", path(child.pid))
child.kill(); child.wait()
"""


@kernel
def test_the_runners_arguments_and_environment_cant_be_read(vault: Path, tmp_path: Path) -> None:
    """I5: the runner (here this test process, the script's parent) holds the model-call token in its
    environment; the script can't read its arguments, environment or path, but can read its own child's."""
    probe = tmp_path / "procinfo.py"
    probe.write_text(_PROCINFO, encoding="utf-8")
    out = h.run(vault, tmp_path, f"{h.PY} -I '{probe}'")
    assert out.exit == 0, out.stderr_tail
    assert "parent-args False parent-path False" in out.stdout_tail, out.stdout_tail
    assert "child-args True child-path True" in out.stdout_tail, out.stdout_tail
    ps = h.run(vault, tmp_path, "/bin/ps -ww -E -p $PPID -o command=")
    assert ps.exit != 0 and "not permitted" in ps.stderr_tail


@kernel
def test_home_is_unreadable_but_the_vault_and_interpreters_work(tmp_path: Path) -> None:
    """R32 / I6, on a fake HOME that holds the vault: a file elsewhere under HOME is refused; the vault, its
    session transcripts, the engine's interpreter and the shell still work."""
    home = tmp_path / "home"
    vault = h.make_vault(home)  # <home>/vault
    notes = home / "Documents" / "notes.txt"
    notes.parent.mkdir(parents=True)
    notes.write_text("CANARY-HOME")
    sessions = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(vault))
    sessions.mkdir(parents=True)
    (sessions / "s.jsonl").write_text("in-bounds-transcript")
    elsewhere = home / ".claude" / "projects" / "-other-project"
    elsewhere.mkdir(parents=True)
    (elsewhere / "s.jsonl").write_text("CANARY-OTHER-PROJECT")
    for path in (notes, elsewhere / "s.jsonl"):
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, path
        assert "not permitted" in out.stderr_tail, path
    for path, text in ((vault / h.NORMAL_NOTE, "normal-note-text"), (sessions / "s.jsonl", "in-bounds-transcript")):
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit == 0 and text in out.stdout_tail, (path, out.stderr_tail)
    py = h.run(vault, tmp_path, f"{h.PY} -c 'import json, sqlite3; print(\"py-ok\")'")
    assert py.exit == 0 and "py-ok" in py.stdout_tail, py.stderr_tail
    private = h.run(vault, tmp_path, f"cat '{vault / h.PRIVATE_NOTE}'")  # the private deny still holds inside
    assert private.exit != 0 and "CANARY-PRIV-LAB" not in private.stdout_tail


def _executable(path: Path, text: str = "#!/bin/sh\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def test_interpreter_root_allows_a_conda_base_prefix(tmp_path: Path) -> None:
    """A conda base install sits right inside the home folder; its whole prefix is the interpreter's."""
    home = Path(os.path.realpath(tmp_path / "home"))
    (home / "miniconda3" / "conda-meta").mkdir(parents=True)
    py = _executable(home / "miniconda3" / "bin" / "python3.12")
    assert runner._interpreter_root(str(py), [str(home)]) == str(home / "miniconda3")


def test_interpreter_root_still_narrows_dot_local(tmp_path: Path) -> None:
    home = Path(os.path.realpath(tmp_path / "home"))
    py = _executable(home / ".local" / "bin" / "python3")
    assert runner._interpreter_root(str(py), [str(home)]) == str(home / ".local" / "bin")


@pytest.mark.parametrize("manager", ["pyenv", "volta"])
def test_shim_interpreter_is_refused(tmp_path: Path, manager: str) -> None:
    """A version-manager shim reads its manager's config under the home folder, which the sandbox denies: the run
    is refused up front with the fix, not left to fail inside the script."""
    home = Path(os.path.realpath(tmp_path / "home"))
    if manager == "pyenv":
        found = _executable(home / ".pyenv" / "shims" / "python3", '#!/bin/sh\nexec pyenv exec python3 "$@"\n')
    else:
        shim = _executable(home / ".volta" / "bin" / "volta-shim")
        found = home / ".volta" / "bin" / "node"
        found.symlink_to(shim)
    env = {"PATH": f"{found.parent}:{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(home), "LANG": "C"}
    with pytest.raises(LabError, match="version-manager shim") as caught:
        runner._allow_reads(tmp_path / "vault", env, [])
    assert "put the real interpreter first on PATH" in str(caught.value)


def _private_side_thread(vault: Path) -> dict[str, Path]:
    """A second, private thread whose timeline, script output and run log carry no frontmatter of their own (a
    timeline written before the rule, and the files a script and the runner write), plus a private experiment
    in the normal thread with an output of its own."""
    side = paths.thread_dir(vault, "side")
    (side / "experiments" / "side-E1-probe").mkdir(parents=True)
    (side / "side.md").write_text("---\ntype: lab-thread\ntitle: Side\nvisibility: private\n---\n# Side\n", "utf-8")
    (side / "side-timeline.md").write_text("# Side — timeline\nCANARY-PRIV-TIMELINE\n", "utf-8")
    note = side / "experiments" / "side-E1-probe.md"
    note.write_text("---\nid: E1\nvisibility: private\n---\n## Method\nCANARY-PRIV-NOTE\n", "utf-8")
    (side / "experiments" / "side-E1-probe" / "out.csv").write_text("CANARY-PRIV-OUTPUT\n", "utf-8")
    (side / "experiments" / "side-E1-probe" / "run-20261120-1500.log").write_text("CANARY-PRIV-LOG\n", "utf-8")
    e1 = paths.experiment_note(vault, h.SLUG, "E1")
    assert e1 is not None
    e1.write_text(
        e1.read_text("utf-8").replace("type: lab-experiment\n", "type: lab-experiment\nvisibility: private\n")
    )
    (paths.experiment_dir(e1)).mkdir()
    (paths.experiment_dir(e1) / "out.csv").write_text("CANARY-PRIV-E1-OUTPUT\n", "utf-8")
    return {
        "timeline": side / "side-timeline.md",
        "note": note,
        "output": side / "experiments" / "side-E1-probe" / "out.csv",
        "log": side / "experiments" / "side-E1-probe" / "run-20261120-1500.log",
        "e1 output": paths.experiment_dir(e1) / "out.csv",
    }


@kernel
def test_a_private_threads_and_a_private_experiments_files_are_unreadable(vault: Path, tmp_path: Path) -> None:
    """I4: a normal experiment can't read any file of a private thread, nor of a private experiment of its own
    thread, whatever those files' own labels say (a CSV or a log has none)."""
    files = _private_side_thread(vault)
    for what, path in files.items():
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, what
        assert "not permitted" in out.stderr_tail, what
    shown = h.run(vault, tmp_path, f"cat '{vault / h.NORMAL_NOTE}'")
    assert shown.exit == 0 and "normal-note-text" in shown.stdout_tail


@kernel
def test_a_hard_link_to_a_file_the_script_cant_write_is_refused(vault: Path, tmp_path: Path) -> None:
    """A link made in the experiment folder to a vault file would let the script write that file through it."""
    target = vault / h.NORMAL_NOTE
    out = h.run(vault, tmp_path, f"ln '{target}' linked.md && echo LINKED")
    assert out.exit != 0 and "LINKED" not in out.stdout_tail
    assert not (h.exp_dir(vault) / "linked.md").exists()
    assert target.stat().st_nlink == 1


# --- experiment folders are private by path (#761 review item 3) ------------------------------------------------


@kernel
def test_the_experiments_own_folder_stays_readable_and_writable(vault: Path, tmp_path: Path) -> None:
    """Its files are private by path now (inputs, outputs, earlier run logs), and the profile allows the folder
    back explicitly: the script reads and writes its own folder and scratch dir."""
    exp = h.exp_dir(vault)
    (exp / "inputs").mkdir(parents=True)
    (exp / "inputs" / "pages.csv").write_text("OWN-INPUT\n", "utf-8")
    (exp / "run-20261120-1500.log").write_text("OWN-OLD-LOG\n", "utf-8")
    policy = privacy.load_policy(vault)
    for path in (exp / "inputs" / "pages.csv", exp / "run-20261120-1500.log"):
        assert privacy.label_file(vault, path.relative_to(vault).as_posix(), policy).visibility == "private"
    out = h.run(
        vault,
        tmp_path,
        "cat inputs/pages.csv run-20261120-1500.log && echo made > inputs/new.txt && cat inputs/new.txt "
        '&& ls inputs && echo t > "$TMPDIR/t.txt" && cat "$TMPDIR/t.txt"',
    )
    assert out.exit == 0, out.stderr_tail
    for text in ("OWN-INPUT", "OWN-OLD-LOG", "made", "pages.csv", "t"):
        assert text in out.stdout_tail.split(), text


@kernel
def test_the_own_folder_allow_back_beats_a_covering_deny_but_not_the_seals(tmp_path: Path) -> None:
    """The order itself, on the kernel: a read deny of a folder holding the experiment (a thread's) doesn't take
    the experiment's own files, while its siblings stay denied and a sealed file (R34) stays sealed."""
    thread = tmp_path / "thread"
    exp = thread / "experiments" / "x-E1-a"
    (exp / ".tmp").mkdir(parents=True)
    (exp / "in.csv").write_text("OWN-INPUT\n", "utf-8")
    (exp / "helper.sh").write_text("CANARY-SEALED\n", "utf-8")
    sibling = thread / "experiments" / "x-E2-b"
    sibling.mkdir()
    (sibling / "out.csv").write_text("CANARY-SIBLING\n", "utf-8")
    (thread / "x-timeline.md").write_text("CANARY-THREAD\n", "utf-8")
    text = sandbox.experiment_profile(
        exp,
        exp / ".tmp",
        Path("/tmp/scout-lab-none.sock"),
        [thread],
        [],
        env={"HOME": str(tmp_path / "home")},
        deny_access=[exp / "helper.sh"],
    )
    sb = tmp_path / "experiment.sb"
    sb.write_text(text, "utf-8")

    def cat(path: Path) -> subprocess.CompletedProcess[str]:
        argv = [str(profile.SANDBOX_EXEC), "-f", str(sb), "/bin/cat", str(path)]
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)

    own = cat(exp / "in.csv")
    assert own.returncode == 0 and "OWN-INPUT" in own.stdout, own.stderr
    for path in (sibling / "out.csv", thread / "x-timeline.md", exp / "helper.sh"):
        refused = cat(path)
        assert refused.returncode != 0 and "CANARY" not in refused.stdout, path
        assert "not permitted" in refused.stderr, path


@kernel
def test_a_normal_experiment_cant_read_another_experiments_folder(vault: Path, tmp_path: Path) -> None:
    """Every other experiment's folder is denied whole, its note normal or not, and a folder with no note too."""
    e1 = paths.experiment_note(vault, h.SLUG, "E1")
    assert e1 is not None and "visibility" not in e1.read_text("utf-8")  # E1's note is normal
    (paths.experiment_dir(e1) / "deep").mkdir(parents=True)
    targets = {
        "output": paths.experiment_dir(e1) / "out.csv",
        "nested": paths.experiment_dir(e1) / "deep" / "run.py",
        "log": paths.experiment_dir(e1) / "run-20261120-1000.log",
        "stray": paths.thread_dir(vault, h.SLUG) / "experiments" / "scratch-copy" / "pages.csv",
    }
    targets["stray"].parent.mkdir()
    for what, path in targets.items():
        path.write_text(f"CANARY-OTHER-{what.upper()}\n", "utf-8")
    for what, path in targets.items():
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, what
        assert "not permitted" in out.stderr_tail, what
    listing = h.run(vault, tmp_path, f"ls '{paths.experiment_dir(e1)}'")
    assert listing.exit != 0 and "out.csv" not in listing.stdout_tail
    shown = h.run(vault, tmp_path, f"cat '{e1}'")  # the note itself is a normal KB note
    assert shown.exit == 0 and "## Hypothesis" in shown.stdout_tail


@kernel
@pytest.mark.parametrize("visibility", ["normal", "private"])
def test_the_vault_state_and_the_broker_queue_are_unreadable(tmp_path: Path, visibility: str) -> None:
    """The run broker's queue holds outgoing commit and Telegram text; .scout-state is denied whole, to a private
    experiment too (one that may read private notes)."""
    vault = h.make_vault(tmp_path, visibility=visibility)
    state = vault / ".scout-state"
    queued = state / "broker-queue" / "telegram" / "20261121T133000-1.json"
    queued.parent.mkdir(parents=True)
    queued.write_text('{"text": "CANARY-QUEUED-MESSAGE"}\n', "utf-8")
    failed = state / "broker-queue" / "failed" / "commit-1.json"
    failed.parent.mkdir(parents=True)
    failed.write_text('{"message": "CANARY-QUEUED-COMMIT"}\n', "utf-8")
    labels = state / "privacy-policy.json"
    labels.write_text('{"labels": "CANARY-LABELS"}\n', "utf-8")
    for path in (queued, failed, labels):
        out = h.run(vault, tmp_path, f"cat '{path}'")
        assert out.exit != 0 and "CANARY" not in out.stdout_tail, path
        assert "not permitted" in out.stderr_tail, path
    listing = h.run(vault, tmp_path, f"ls '{state / 'broker-queue'}'")
    assert listing.exit != 0 and "telegram" not in listing.stdout_tail
