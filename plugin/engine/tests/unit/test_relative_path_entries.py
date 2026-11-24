"""No Scout launcher runs a program planted in the working directory.

Skills and hooks start Scout from any cwd (a cloned repo, the vault), and a
user's PATH can hold relative entries: `.`, `./bin`, `node_modules/.bin`, or an
empty entry, which also means the cwd. A `python3`, `bash`, `scoutctl`, `uv`,
`git` or `dirname` planted in that cwd would then run as the user. Each test
here runs a real launcher with real bash (or sh) and such a PATH, from a folder
full of planted programs, and asserts none of them ever ran.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb.hook import default_scoutctl, render_hook
from scout.scripts.phase_assembly import render_template
from tests.unit.test_install_sh_runs_setup import INSTALL_SH, _world
from tests.unit.test_install_venv_script import SCRIPT as INSTALL_VENV
from tests.unit.test_install_venv_script import _fake_uv

ENGINE_DIR = Path(__file__).resolve().parents[2]
LAUNCHER = ENGINE_DIR / "bin" / "scoutctl"
SHIM_TEMPLATE = ENGINE_DIR.parent / "templates" / "scripts" / "scoutctl.sh.tmpl"
HOOKS_JSON = ENGINE_DIR.parent / "hooks" / "hooks.json"
PREFILTER = ENGINE_DIR.parent / "hooks" / "prefilter.sh"

# Everything a launcher here looks up by name, and the interpreters it might try.
PLANTED = (
    "bash sh env python python3 python3.11 python3.12 python3.13 scoutctl uv git claude curl "
    "dirname sed head tail sort rm cat mkdir tr cmp grep uname xcode-select ls chmod printf"
).split()

# The relative entries a careless or hostile PATH might carry: the cwd itself, a cwd-relative
# folder, and the empty entries (leading, doubled, trailing) that a shell also reads as the cwd.
RELATIVE = [".", "./bin", "node_modules/.bin", ""]


def _plant(cwd: Path) -> Path:
    """Fill cwd, cwd/bin and cwd/node_modules/.bin with programs that leave a mark if they run."""
    marker = cwd.parent / f"{cwd.name}-planted-ran"
    for folder in (cwd, cwd / "bin", cwd / "node_modules" / ".bin"):
        folder.mkdir(parents=True, exist_ok=True)
        for name in PLANTED:
            p = folder / name
            p.write_text(f'#!/bin/sh\necho "{folder.name}/{name} $*" >>"{marker}"\necho PLANTED\nexit 0\n')
            p.chmod(0o755)
    return marker


def _hostile_path(*absolute: str | Path) -> str:
    """A PATH that starts and ends with relative entries, with the absolute ones in between."""
    return ":".join(["", *RELATIVE, *map(str, absolute), *RELATIVE, ""])


def _assert_nothing_planted_ran(marker: Path, out: subprocess.CompletedProcess[str]) -> None:
    ran = marker.read_text() if marker.exists() else ""
    assert not ran, f"planted programs ran:\n{ran}\nstdout={out.stdout!r}\nstderr={out.stderr!r}"
    assert "PLANTED" not in out.stdout + out.stderr


def _fake_python3(folder: Path, tag: str = "SYSTEM_PY") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    py3 = folder / "python3"
    py3.write_text(f'#!/bin/sh\necho "{tag} $*"\n')
    py3.chmod(0o755)
    return folder


def _plugin_tree(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "engine" / "bin").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "engine" / "bin" / "scoutctl")
    (root / "engine" / "bin" / "scoutctl").chmod(0o755)
    return root


# --- engine/bin/scoutctl ---------------------------------------------------------------------


def _launch(root: Path, cwd: Path, home: Path, path: str) -> subprocess.CompletedProcess[str]:
    # Executed directly, as the hooks and skills do, so the shebang is part of what is tested:
    # `#!/usr/bin/env bash` would have env run the planted bash.
    return subprocess.run(
        [str(root / "engine" / "bin" / "scoutctl"), "version"],
        env={"HOME": str(home), "PATH": path},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_launcher_last_resort_takes_python3_from_an_absolute_path_entry(tmp_path: Path) -> None:
    """No venv, no pointer: the planted python3 in `.` comes first on PATH, the real one later."""
    root = _plugin_tree(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)
    sysbin = _fake_python3(tmp_path / "sysbin")

    out = _launch(root, repo, tmp_path / "home", _hostile_path(sysbin, "/usr/bin", "/bin"))

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert out.stdout.strip() == "SYSTEM_PY -P -m scout.cli version"


@pytest.mark.parametrize("path", ["", ".", ".:./bin", "./bin:", "::"], ids=repr)
def test_launcher_with_only_relative_path_entries_never_runs_a_planted_program(tmp_path: Path, path: str) -> None:
    """PATH=.:./bin and friends: nothing absolute to search, so the launcher falls back to the
    system folders. Whatever python3 it finds there (or none), it is never the cwd's."""
    root = _plugin_tree(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = _launch(root, repo, tmp_path / "home", path)

    _assert_nothing_planted_ran(marker, out)


def test_launcher_hands_the_engine_a_path_without_relative_entries(tmp_path: Path) -> None:
    """The engine's own children (git, claude) look PATH up too, so they inherit the cleaned one."""
    root = _plugin_tree(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    (sysbin / "python3").write_text('#!/bin/sh\nprintf "%s\\n" "$PATH"\n')
    (sysbin / "python3").chmod(0o755)

    out = _launch(root, repo, tmp_path / "home", _hostile_path(sysbin, "/usr/bin", "/bin"))

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == f"{sysbin}:/usr/bin:/bin"


def test_launcher_keeps_glob_characters_in_path_entries_literal(tmp_path: Path) -> None:
    """`g*` must not be expanded to the folders it matches while the filter splits PATH."""
    root = _plugin_tree(tmp_path)
    (tmp_path / "g1").mkdir()
    (tmp_path / "g2").mkdir()
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    (sysbin / "python3").write_text('#!/bin/sh\nprintf "%s\\n" "$PATH"\n')
    (sysbin / "python3").chmod(0o755)
    path = f"{tmp_path}/g*:{tmp_path}/g?:{sysbin}:/usr/bin:/bin"

    out = _launch(root, tmp_path, tmp_path / "home", path)

    assert out.stdout.strip() == path, out


def test_launcher_venv_still_wins_under_a_hostile_path(tmp_path: Path) -> None:
    """The cleaning changes nothing for a normal install: the venv runs, with its args intact."""
    root = _plugin_tree(tmp_path)
    cli = root / ".venv" / "src" / "scout" / "cli.py"
    cli.parent.mkdir(parents=True)
    cli.touch()
    py = root / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    py.write_text(f'#!/bin/sh\nif [ "$1 $2" = "-P -c" ]; then echo "{cli}"; exit 0; fi\necho "VENV_PY $*"\n')
    py.chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = _launch(root, repo, tmp_path / "home", _hostile_path("/usr/bin", "/bin"))

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == "VENV_PY -P -m scout.cli version"


def test_launcher_shebang_never_searches_path() -> None:
    assert LAUNCHER.read_text().splitlines()[0] == "#!/bin/bash"


# --- the vault's scripts/scoutctl ------------------------------------------------------------


def test_vault_shim_never_runs_a_planted_scoutctl(tmp_path: Path) -> None:
    """The shim's first choice is a scoutctl on PATH. A relative entry must not make that the cwd's."""
    engine = tmp_path / "engine" / "scoutctl"
    engine.parent.mkdir()
    engine.write_text('#!/bin/sh\necho "real-engine $*"\n')
    engine.chmod(0o755)
    vault = tmp_path / "vault"
    shim = vault / "scripts" / "scoutctl"
    shim.parent.mkdir(parents=True)
    shim.write_text(render_template(SHIM_TEMPLATE.read_text(encoding="utf-8"), {"SCOUTCTL_BIN": str(engine)}))
    shim.chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = subprocess.run(
        [str(shim), "kb", "vault"],
        env={"HOME": str(tmp_path / "home"), "PATH": _hostile_path("/usr/bin", "/bin")},
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert out.stdout.strip() == "real-engine kb vault"


def test_vault_shim_still_prefers_an_absolute_path_scoutctl(tmp_path: Path) -> None:
    on_path = tmp_path / "onpath"
    on_path.mkdir()
    (on_path / "scoutctl").write_text('#!/bin/sh\necho "path-engine $*"\n')
    (on_path / "scoutctl").chmod(0o755)
    vault = tmp_path / "vault"
    shim = vault / "scripts" / "scoutctl"
    shim.parent.mkdir(parents=True)
    shim.write_text(render_template(SHIM_TEMPLATE.read_text(encoding="utf-8"), {"SCOUTCTL_BIN": "/nonexistent"}))
    shim.chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = subprocess.run(
        [str(shim), "health"],
        env={"HOME": str(tmp_path / "home"), "PATH": _hostile_path(on_path, "/usr/bin", "/bin")},
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == "path-engine health"


# --- scripts/install-venv.sh -----------------------------------------------------------------


def test_install_venv_never_runs_a_planted_uv(tmp_path: Path) -> None:
    """/scout-update runs install-venv.sh with the vault as cwd; uv is found by name."""
    root = tmp_path / "plugin"
    (root / "engine").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(INSTALL_VENV, root / "scripts" / "install-venv.sh")
    uv = _fake_uv(tmp_path)  # tmp_path/fakebin/uv
    log = tmp_path / "uv.log"
    repo = tmp_path / "vault"
    marker = _plant(repo)

    out = subprocess.run(
        ["/bin/bash", str(root / "scripts" / "install-venv.sh")],
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": _hostile_path(uv.parent, "/usr/bin", "/bin"),
            "FAKE_UV_LOG": str(log),
        },
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert f"using uv ({uv})" in out.stdout


# --- install.sh ------------------------------------------------------------------------------


def test_install_sh_never_runs_a_planted_tool(tmp_path: Path) -> None:
    """`curl … | bash` runs from whatever folder the user is in; claude, git, uv, python3 and
    curl are all found by name, and so is everything the engine setup starts after them."""
    env, log = _world(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)
    env["PATH"] = _hostile_path(tmp_path / "bin", "/usr/bin", "/bin")

    out = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--yes"],
        env=env,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert log.read_text().splitlines()[0] == "setup"


# --- the vault's git pre-commit hook ---------------------------------------------------------


def test_pre_commit_hook_never_runs_a_planted_scoutctl(tmp_path: Path) -> None:
    """The hook's fallback is `command -v scoutctl`, and git runs it with the vault as cwd."""
    vault = tmp_path / "vault"
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    marker = _plant(vault)
    real = tmp_path / "realbin"
    real.mkdir()
    (real / "scoutctl").write_text('#!/bin/sh\necho "real-scoutctl $*" >&2\nexit 0\n')
    (real / "scoutctl").chmod(0o755)
    hook = tmp_path / "pre-commit"
    hook.write_text(render_hook(str(tmp_path / "gone" / "scoutctl")))  # baked path is gone: fallback
    hook.chmod(0o755)

    out = subprocess.run(
        ["/bin/sh", str(hook)],
        env={"HOME": str(tmp_path / "home"), "PATH": _hostile_path(real, "/usr/bin", "/bin")},
        cwd=vault,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert "real-scoutctl kb lint --staged" in out.stderr


def test_default_scoutctl_skips_relative_path_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scoutctl baked into the hook is never a cwd-relative path."""
    repo = tmp_path / "cloned-repo"
    _plant(repo)
    real = tmp_path / "realbin"
    real.mkdir()
    (real / "scoutctl").write_text("#!/bin/sh\n")
    (real / "scoutctl").chmod(0o755)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-venv" / "python"))

    monkeypatch.setenv("PATH", _hostile_path(real))
    assert default_scoutctl() == str(real / "scoutctl")

    monkeypatch.setenv("PATH", ":".join(RELATIVE))
    assert default_scoutctl() == "scoutctl"


def test_default_scoutctl_reads_a_leading_tilde_as_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bash's own PATH search expands a leading `~`, so `PATH="~/.local/bin:$PATH"` works in a
    shell; the filter keeps such an entry as $HOME/... rather than dropping it."""
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "scoutctl").write_text("#!/bin/sh\n")
    (home / "bin" / "scoutctl").chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-venv" / "python"))
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.setenv("PATH", "~/bin")
    assert default_scoutctl() == str(home / "bin" / "scoutctl")

    monkeypatch.setenv("HOME", "")
    assert default_scoutctl() == "scoutctl"


# --- a leading ~ in the shell filters ---------------------------------------------------------


def test_launcher_reads_a_leading_tilde_as_home(tmp_path: Path) -> None:
    root = _plugin_tree(tmp_path)
    home = tmp_path / "home"
    sysbin = home / "sysbin"
    sysbin.mkdir(parents=True)
    (sysbin / "python3").write_text('#!/bin/sh\nprintf "%s\\n" "$PATH"\n')
    (sysbin / "python3").chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = _launch(root, repo, home, "~/sysbin:.:/usr/bin::/bin:~")

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == f"{sysbin}:/usr/bin:/bin:{home}"


def test_vault_shim_and_pre_commit_hook_read_a_leading_tilde_as_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "scoutctl").write_text('#!/bin/sh\necho "home-scoutctl $*" >&2\n')
    (home / "bin" / "scoutctl").chmod(0o755)
    env = {"HOME": str(home), "PATH": "~/bin:.:/usr/bin:/bin"}
    vault = tmp_path / "vault"
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    marker = _plant(vault)
    shim = vault / "scripts" / "scoutctl"
    shim.parent.mkdir(parents=True)
    shim.write_text(render_template(SHIM_TEMPLATE.read_text(encoding="utf-8"), {"SCOUTCTL_BIN": "/nonexistent"}))
    shim.chmod(0o755)
    hook = tmp_path / "pre-commit"
    hook.write_text(render_hook(str(tmp_path / "gone" / "scoutctl")))
    hook.chmod(0o755)

    for argv in ([str(shim), "health"], ["/bin/sh", str(hook)]):
        out = subprocess.run(argv, env=env, cwd=vault, capture_output=True, text=True, timeout=30, check=False)
        _assert_nothing_planted_ran(marker, out)
        assert out.returncode == 0, out
        assert "home-scoutctl" in out.stderr, out


# --- plugin/hooks/hooks.json -------------------------------------------------------------------
#
# Claude Code runs these in every session's cwd, on every matching tool call, in any repo. Each entry
# starts hooks/prefilter.sh with /bin/sh by absolute path; the script filters PATH before anything else.


def _tool_hooks() -> list[tuple[str, str]]:
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    return [
        (f"{event} {group.get('matcher')}", hook["command"])
        for event in ("PreToolUse", "PostToolUse")
        for group in data["hooks"][event]
        for hook in group["hooks"]
    ]


def _script_code() -> list[str]:
    """prefilter.sh without its comment and blank lines."""
    lines = PREFILTER.read_text(encoding="utf-8").splitlines()
    return [line for line in lines[1:] if line.strip() and not line.lstrip().startswith("#")]


def _filter_prefix() -> str:
    text = PREFILTER.read_text(encoding="utf-8")
    block = text[text.index("# >>> path-filter\n") : text.index("# <<< path-filter\n")]
    (code,) = [line for line in block.splitlines() if not line.startswith("#")]
    return code + "; "


def _plugin_with_prefilter(plugin: Path) -> Path:
    """Adds the real prefilter.sh to a stand-in plugin root."""
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "hooks" / "prefilter.sh").symlink_to(PREFILTER)
    return plugin


def test_every_tool_hook_filters_path_first_and_none_calls_dirname() -> None:
    prefix = _filter_prefix()
    assert prefix.startswith("sp=; ") and prefix.endswith("export PATH; ")
    # The filter is the script's first command, and each entry reaches the script by absolute path
    # through /bin/sh, with nothing before it that a PATH search could answer.
    # Line 1 opens the brace group that holds the whole file; the PATH filter is its first command.
    assert PREFILTER.read_text(encoding="utf-8").startswith("{ #")
    assert _script_code()[0] + "; " == prefix
    # Shell builtins and keywords, the two assignments, and the two programs an entry starts, both by
    # absolute path: /bin/sh and scoutctl (which filters PATH itself).
    allowed = {"[", "{", "}", "if", "then", "else", "fi", "exit", "exec", "echo", "/bin/sh", '"$c"', "s=$?"}
    for name, command in _tool_hooks():
        assert (
            'h="${CLAUDE_PLUGIN_ROOT}/hooks/prefilter.sh"; c="${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl"; ' in command
        )
        words = command.replace("$(", " ").replace("`", " ")
        for sep in ("&&", "||", ";", "then ", "else ", "exec "):
            words = words.replace(sep, "\n")
        firsts = {part.split()[0] for part in words.split("\n") if part.split()}
        assert firsts - allowed <= {
            'h="${CLAUDE_PLUGIN_ROOT}/hooks/prefilter.sh"',
            'c="${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl"',
        }, (
            name,
            firsts - allowed,
        )
    assert "dirname" not in HOOKS_JSON.read_text(encoding="utf-8")
    assert not any("dirname" in line for line in _script_code())


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
@pytest.mark.parametrize(
    ("path", "home", "expected"),
    [
        ("~/x:.:/usr/bin::~:/bin:./bin", "/h", "/h/x:/usr/bin:/h:/bin"),
        ("~/x:/usr/bin", "", "/usr/bin"),
        (".:./bin:", "/h", "/usr/bin:/bin"),
        ("~other/bin:/usr/bin", "/h", "/usr/bin"),
        ("/u*/bin:/usr/b?n:/usr/bin", "/h", "/u*/bin:/usr/b?n:/usr/bin"),  # glob characters stay literal
    ],
)
def test_hook_path_filter(shell: str, path: str, home: str, expected: str) -> None:
    out = subprocess.run(
        [shell, "-c", _filter_prefix() + 'printf "%s|%s" "$PATH" "$IFS"'],
        env={"HOME": home, "PATH": path},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert out.stdout == f"{expected}| \t\n"


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/bash"])
@pytest.mark.parametrize(
    "payload",
    [
        '{"tool_name": "Bash", "tool_input": {"command": "ls"}}',
        '{"tool_name": "Read", "tool_input": {"file_path": "/v/knowledge-base/a.md"}}',
    ],
    ids=["bash", "kb-read"],
)
@pytest.mark.parametrize(("name", "command"), _tool_hooks(), ids=[n for n, _ in _tool_hooks()])
def test_tool_hooks_never_run_a_planted_program(
    tmp_path: Path, shell: str, payload: str, name: str, command: str
) -> None:
    """A scheduled run (so every gate goes past its early exit), from a cloned repo inside the
    vault (so the vault walk climbs), with cat, dirname, grep, tr and cmp planted in the cwd."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("x: 1\n")
    (vault / "connectors.custom.yaml").write_text("y: 1\n")  # no restrict_tools: grep, tr, cmp all run
    repo = vault / "projects" / "cloned-repo"
    marker = _plant(repo)
    plugin = _plugin_with_prefilter(tmp_path / "plugin")
    (plugin / "engine" / "bin").mkdir(parents=True)
    calls = tmp_path / "scoutctl.log"
    (plugin / "engine" / "bin" / "scoutctl").write_text(f'#!/bin/sh\necho "$*" >>"{calls}"\nexit 0\n')
    (plugin / "engine" / "bin" / "scoutctl").chmod(0o755)

    out = subprocess.run(
        [shell, "-c", command],
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": _hostile_path("/usr/bin", "/bin"),
            "SCOUT_MODE": "scout",
            "CLAUDE_PLUGIN_ROOT": str(plugin),
        },
        cwd=repo,
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out


def test_mcp_tool_gate_walk_still_finds_the_vault_from_a_deep_cwd(tmp_path: Path) -> None:
    """`${d%/*}` in place of dirname: the walk climbs to the vault, and stops at / outside one."""
    command = dict(_tool_hooks())["PreToolUse mcp__.*"]
    vault = tmp_path / "vault"
    deep = vault / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("x: 1\n")
    (vault / "connectors.custom.yaml").write_text("restrict_tools: {}\n")
    plugin = _plugin_with_prefilter(tmp_path / "plugin")
    (plugin / "engine" / "bin").mkdir(parents=True)
    (plugin / "engine" / "bin" / "scoutctl").write_text('#!/bin/sh\necho "gate $*"\n')
    (plugin / "engine" / "bin" / "scoutctl").chmod(0o755)
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(plugin)}

    out = subprocess.run(
        ["/bin/sh", "-c", command],
        env=env,
        cwd=deep,
        input="{}",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert out.stdout.strip() == "gate hook mcp-tool-gate", out

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    out = subprocess.run(
        ["/bin/sh", "-c", command],
        env=env,
        cwd=outside,
        input="{}",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (out.returncode, out.stdout) == (0, ""), out
