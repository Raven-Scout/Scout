"""Smoke tests for engine/bin/scoutctl venv resolution.

The launcher is a bash script that has to find a venv across several
layouts (canonical install, legacy in-engine, Claude Code's cache→
marketplace split). We exercise it by laying out fake plugin trees in
tmp_path with a stub `python` that echoes which candidate fired.

The launcher probes a candidate (`python -c <probe>`) before trusting it, so
the stubs answer that probe too — see `_fake_python`.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import scout
from scout.scripts.engine_pointer import EnginePointer, write_pointer

LAUNCHER = Path(__file__).parent.parent.parent / "bin" / "scoutctl"


def _make_fake_venv(venv_dir: Path, label: str) -> None:
    """Write a stub `python` that echoes VENV=<label> and exits 0."""
    _fake_python(venv_dir, f"VENV={label}")


def _stage_launcher(plugin_root: Path) -> Path:
    """Copy the real launcher into a synthetic plugin tree."""
    bin_dir = plugin_root / "engine" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "scoutctl"
    shutil.copy2(LAUNCHER, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _run(launcher: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(launcher), "version"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_picks_plugin_root_venv(tmp_path):
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / ".venv", "plugin-root")
    result = _run(launcher)
    assert "VENV=plugin-root" in result.stdout, result


def test_picks_engine_venv_when_plugin_root_missing(tmp_path):
    """Legacy in-engine layout still works."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / "engine" / ".venv", "engine-legacy")
    result = _run(launcher)
    assert "VENV=engine-legacy" in result.stdout, result


def test_prefers_plugin_root_over_engine(tmp_path):
    """When both venvs exist, the canonical install-venv.sh location wins."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / ".venv", "canonical")
    _make_fake_venv(plugin_root / "engine" / ".venv", "legacy")
    result = _run(launcher)
    assert "VENV=canonical" in result.stdout, result


def test_cache_path_falls_back_to_marketplace(tmp_path):
    """Launcher invoked from cache/ resolves to marketplaces/ venv."""
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.4.0"
    marketplace_root = plugins_dir / "marketplaces" / "scout-plugin"
    launcher = _stage_launcher(cache_root)
    # Venv only present in marketplaces/, not in cache/.
    _make_fake_venv(marketplace_root / ".venv", "marketplace")
    result = _run(launcher)
    assert "VENV=marketplace" in result.stdout, result


@pytest.mark.parametrize("venv_rel", ["plugin/.venv", "plugin/engine/.venv"])
def test_cache_path_falls_back_to_a_monorepo_marketplace_clone(tmp_path, venv_rel):
    """A Raven-Scout/Scout marketplace clone keeps the plugin under plugin/
    (marketplace source "./plugin"), so its venv is there, not at the root."""
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.12.0"
    marketplace_root = plugins_dir / "marketplaces" / "scout-plugin"
    launcher = _stage_launcher(cache_root)
    _make_fake_venv(marketplace_root / venv_rel, "monorepo-marketplace")
    result = _run(launcher)
    assert "VENV=monorepo-marketplace" in result.stdout, result


def _mark_monorepo_clone(clone: Path) -> None:
    """Give `clone` a Raven-Scout/Scout shape: marketplace at the root, plugin under plugin/."""
    (clone / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (clone / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "scout-plugin", "plugins": [{"name": "scout", "source": "./plugin"}]}\n', encoding="utf-8"
    )
    (clone / "plugin" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (clone / "plugin" / ".claude-plugin" / "plugin.json").write_text('{"name": "scout"}\n', encoding="utf-8")


# A monorepo clone's root .venv is a stale pre-pull editable install, so it must
# not win even when it still imports scout.cli (EngineLocator.installIfCheckout
# in the app never adopts it either).
@pytest.mark.parametrize("venv_rel", ["plugin/.venv", "plugin/engine/.venv"])
def test_cache_path_skips_a_monorepo_clones_root_venv(tmp_path, venv_rel):
    home = tmp_path / "home"
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.14.0"
    clone = plugins_dir / "marketplaces" / "scout-plugin"
    _mark_monorepo_clone(clone)
    _stage_launcher(cache_root)
    _fake_python(clone / ".venv", "ROOT_PY")
    _fake_python(clone / "engine" / ".venv", "ROOT_ENGINE_PY")
    _fake_python(clone / venv_rel, "PLUGIN_PY")

    assert _run_isolated(cache_root, home) == "PLUGIN_PY -m scout.cli version"
    assert not (clone / ".venv" / "calls").exists()
    assert not (clone / "engine" / ".venv" / "calls").exists()


def test_cache_path_ignores_a_monorepo_clone_with_only_a_root_venv(tmp_path):
    """No plugin/ venv: the stale root one is still skipped, and the launcher
    falls through to python3 rather than run the pre-pull engine."""
    home = tmp_path / "home"
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.14.0"
    clone = plugins_dir / "marketplaces" / "scout-plugin"
    _mark_monorepo_clone(clone)
    _stage_launcher(cache_root)
    _fake_python(clone / ".venv", "ROOT_PY")
    sysbin = _fake_system_python3(tmp_path)

    assert _run_isolated(cache_root, home, extra_path=str(sysbin)) == "SYSTEM_PY -m scout.cli version"
    assert not (clone / ".venv" / "calls").exists()


def test_cache_path_still_uses_a_legacy_clones_root_venv(tmp_path):
    """A legacy scout-plugin clone has its plugin at the root (marketplace
    source "./"), so the root venv is the right one and keeps priority."""
    home = tmp_path / "home"
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.11.0"
    clone = plugins_dir / "marketplaces" / "scout-plugin"
    (clone / ".claude-plugin").mkdir(parents=True)
    (clone / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "scout-plugin", "plugins": [{"name": "scout", "source": "./"}]}\n', encoding="utf-8"
    )
    (clone / ".claude-plugin" / "plugin.json").write_text('{"name": "scout"}\n', encoding="utf-8")
    _stage_launcher(cache_root)
    _fake_python(clone / ".venv", "ROOT_PY")
    _fake_python(clone / "plugin" / ".venv", "PLUGIN_PY")

    assert _run_isolated(cache_root, home) == "ROOT_PY -m scout.cli version"


def test_launcher_inside_a_monorepo_clone_ignores_the_clone_root_venv(tmp_path):
    """Run from the clone itself (<clone>/plugin/engine/bin/scoutctl), the
    plugin root is <clone>/plugin, so the clone-root venv is never a candidate."""
    home = tmp_path / "home"
    clone = tmp_path / "Scout"
    _mark_monorepo_clone(clone)
    _stage_launcher(clone / "plugin")
    _fake_python(clone / ".venv", "ROOT_PY")
    _fake_python(clone / "plugin" / ".venv", "PLUGIN_PY")

    assert _run_isolated(clone / "plugin", home) == "PLUGIN_PY -m scout.cli version"
    assert not (clone / ".venv" / "calls").exists()


def test_cache_path_prefers_local_venv_when_present(tmp_path):
    """If cache/ has its own venv, don't cross-jump."""
    plugins_dir = tmp_path / ".claude" / "plugins"
    cache_root = plugins_dir / "cache" / "scout-plugin" / "scout" / "0.4.0"
    marketplace_root = plugins_dir / "marketplaces" / "scout-plugin"
    launcher = _stage_launcher(cache_root)
    _make_fake_venv(cache_root / ".venv", "cache-local")
    _make_fake_venv(marketplace_root / ".venv", "marketplace")
    result = _run(launcher)
    assert "VENV=cache-local" in result.stdout, result


def test_caches_resolved_python_path(tmp_path):
    """Per #81: the launcher writes the resolved Python to .scoutctl-py-cache
    so the next invocation can skip the candidate probe."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / ".venv", "plugin-root")
    cache = plugin_root / ".scoutctl-py-cache"
    assert not cache.exists()

    result = _run(launcher)
    assert "VENV=plugin-root" in result.stdout, result
    assert cache.exists(), "first run should populate the cache"
    cached_py, *stamp = cache.read_text().splitlines()
    assert cached_py.endswith(".venv/bin/python")
    # The rest is the stamp: the files the probe resolved scout.cli through.
    assert stamp == [str(plugin_root / ".venv" / "src" / "scout" / "cli.py")]

    # Drop the venv stub; the cached path is now stale and should be ignored.
    # If the cache were honoured blindly, the launcher would fail trying to
    # exec a missing file.
    cached_path = Path(cached_py)
    cached_path.unlink()
    cache.write_text(str(cached_path) + "\n")  # leave the stale path
    result2 = _run(launcher)
    # With no venv and no usable cache, we fall through to `python3 -m scout.cli`.
    # The test environment doesn't have scout globally installed in tmp_path,
    # so this typically returns non-zero — that's OK; we only assert the
    # launcher itself didn't crash trying to exec a stale cached path.
    assert result2.returncode != 127, "launcher crashed on stale cache: " + result2.stderr


def test_cache_invalidates_when_target_disappears(tmp_path):
    """A cached path that no longer exists must trigger a fresh probe."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    _make_fake_venv(plugin_root / ".venv", "plugin-root")

    # Pre-seed the cache with a path that doesn't exist.
    cache = plugin_root / ".scoutctl-py-cache"
    cache.write_text("/nonexistent/python\n")

    # Should fall through to the real probe and pick the plugin-root venv.
    result = _run(launcher)
    assert "VENV=plugin-root" in result.stdout, result
    # And the cache should be updated to the correct path.
    cached_py = cache.read_text().splitlines()[0]
    assert cached_py == str(plugin_root / ".venv" / "bin" / "python")


@pytest.mark.skipif(shutil.which("python3") is None, reason="needs system python3 for last-resort exec")
def test_falls_back_to_system_python3_when_no_venv(tmp_path):
    """No venv anywhere → exec python3 -m scout.cli, which fails cleanly
    if scout isn't installed globally. We only assert the launcher ran the
    fallback path (non-zero exit + 'No module' message, OR scout output if
    the dev's global python happens to have it)."""
    plugin_root = tmp_path / "scout-plugin"
    launcher = _stage_launcher(plugin_root)
    result = _run(launcher)
    # Either system python complained that scout isn't installed, or it
    # succeeded (developer has scout globally). Both are acceptable — we
    # just want to be sure we didn't exit before reaching the fallback.
    assert result.returncode != 127, "launcher itself crashed: " + result.stderr


# ---------------------------------------------------------------------------
# Engine pointer candidate (A3 / E2b): ~/.local/state/scout/engine.json
#
# These run the launcher with a fully hermetic env (bare PATH, isolated HOME)
# rather than the ambient-PATH `_run` above, so "no venv, no pointer" reliably
# falls through to a controllable fake `python3` instead of whatever the host
# happens to have on PATH.
# ---------------------------------------------------------------------------


def _fake_python(venv: Path, tag: str, *, importable: bool = True) -> Path:
    """A stub interpreter that logs each call's first argument to <venv>/calls.

    Probed (`-c`), an importable stub answers like a healthy venv: it prints
    the scout/cli.py it "resolved", which the launcher stamps into its cache.
    Run (`-m`), it echoes its tag. A non-importable stub fails both the way a
    venv whose editable source moved does.
    """
    py = venv / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    if importable:
        cli = venv / "src" / "scout" / "cli.py"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.touch()
        probe, run = f'echo "{cli}"; exit 0', f'echo "{tag} $*"'
    else:
        probe = run = 'echo "No module named scout.cli" >&2; exit 1'
    py.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$1" >>"{venv}/calls"\nif [ "$1" = "-c" ]; then {probe}; fi\n{run}\n',
        encoding="utf-8",
    )
    py.chmod(0o755)
    return py


def _plugin_tree(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "engine" / "bin").mkdir(parents=True)
    dst = root / "engine" / "bin" / "scoutctl"
    shutil.copy(LAUNCHER, dst)
    dst.chmod(0o755)
    return root


def _write_pointer(home: Path, python: Path) -> None:
    write_pointer(
        EnginePointer(
            version="0.0.0",
            engine_root="/nonexistent",
            python=str(python),
            scoutctl=str(python.parent / "scoutctl"),
            vault=str(home / "Scout"),
            managed_by="scout-app",
            written_at="2026-01-01T00:00:00Z",
        ),
        home=home,
    )


def _launch(root: Path, home: Path, extra_path: str = "", stdin: str | None = None) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": f"{extra_path}:/usr/bin:/bin".lstrip(":")}
    return subprocess.run(
        [str(root / "engine" / "bin" / "scoutctl"), "version"],
        env=env,
        cwd=root,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_isolated(root: Path, home: Path, extra_path: str = "") -> str:
    out = _launch(root, home, extra_path)
    assert out.returncode == 0, out
    return out.stdout.strip()


def test_uses_pointer_python_when_tree_has_no_venv(tmp_path):
    home = tmp_path / "home"
    py = _fake_python(tmp_path / "outside-venv", "POINTER_PY")
    _write_pointer(home, py)
    assert _run_isolated(_plugin_tree(tmp_path), home) == "POINTER_PY -m scout.cli version"


def test_uses_pointer_python_under_a_non_ascii_path(tmp_path):
    """The pointer is UTF-8, not \\uXXXX escapes, so the launcher's sed reads
    back a real path (final review, Ruling 20). A fake system python3 makes a
    miss show up as SYSTEM_PY instead of a real interpreter's error."""
    home = tmp_path / "home"
    py = _fake_python(tmp_path / "Résumé" / "venv", "POINTER_PY")
    _write_pointer(home, py)
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    py3 = sysbin / "python3"
    py3.write_text('#!/bin/sh\necho "SYSTEM_PY $*"\n', encoding="utf-8")
    py3.chmod(0o755)
    assert _run_isolated(_plugin_tree(tmp_path), home, extra_path=str(sysbin)) == "POINTER_PY -m scout.cli version"


def test_prefers_in_tree_venv_over_pointer(tmp_path):
    """Edit-and-go: a dev checkout with its own venv keeps using it."""
    home = tmp_path / "home"
    _write_pointer(home, _fake_python(tmp_path / "outside-venv", "POINTER_PY"))
    root = _plugin_tree(tmp_path)
    _fake_python(root / ".venv", "TREE_PY")
    assert _run_isolated(root, home) == "TREE_PY -m scout.cli version"


def test_malformed_pointer_falls_through_to_system_python3(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "state" / "scout").mkdir(parents=True)
    (home / ".local" / "state" / "scout" / "engine.json").write_text("{not json", encoding="utf-8")
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    py3 = sysbin / "python3"
    py3.write_text('#!/bin/sh\necho "SYSTEM_PY $*"\n', encoding="utf-8")
    py3.chmod(0o755)
    assert _run_isolated(_plugin_tree(tmp_path), home, extra_path=str(sysbin)) == "SYSTEM_PY -m scout.cli version"


def test_pointer_python_that_no_longer_exists_is_skipped(tmp_path):
    home = tmp_path / "home"
    _write_pointer(home, tmp_path / "gone" / "bin" / "python")
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    py3 = sysbin / "python3"
    py3.write_text('#!/bin/sh\necho "SYSTEM_PY $*"\n', encoding="utf-8")
    py3.chmod(0o755)
    assert _run_isolated(_plugin_tree(tmp_path), home, extra_path=str(sysbin)) == "SYSTEM_PY -m scout.cli version"


# ---------------------------------------------------------------------------
# Interpreters that run but can't import scout.cli
#
# A venv outlives its editable source. When a checkout's engine/ moves (the
# monorepo layout moved it to plugin/engine/), .venv/bin/python still passes
# `[ -x ]`, but every call dies with `No module named scout.cli`. So the
# launcher probes a candidate before trusting it. The cache also carries a
# stamp: the files the probe resolved scout.cli through, which the fast path
# re-checks with plain file tests.
# ---------------------------------------------------------------------------


def _seed_cache(root: Path, python: Path, *stamp: Path) -> Path:
    cache = root / ".scoutctl-py-cache"
    cache.write_text("".join(f"{p}\n" for p in (python, *stamp)), encoding="utf-8")
    return cache


def _fake_system_python3(tmp_path: Path) -> Path:
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    py3 = sysbin / "python3"
    py3.write_text('#!/bin/sh\necho "SYSTEM_PY $*"\n', encoding="utf-8")
    py3.chmod(0o755)
    return sysbin


@pytest.mark.parametrize("stamped", [False, True], ids=["pre-stamp-cache", "stamped-source-gone"])
def test_cached_python_that_cannot_import_cli_falls_through_to_pointer(tmp_path, stamped):
    """The 2026-10-05 outage. The cache named an old checkout's venv. It was
    still executable, but its source had moved. The pointer named a working
    venv and was never reached."""
    home = tmp_path / "home"
    pointer_py = _fake_python(tmp_path / "app-venv", "POINTER_PY")
    _write_pointer(home, pointer_py)
    root = _plugin_tree(tmp_path)
    dead = _fake_python(tmp_path / "old-checkout" / ".venv", "DEAD_PY", importable=False)
    moved_source = [tmp_path / "old-checkout" / "engine" / "scout" / "cli.py"] if stamped else []
    cache = _seed_cache(root, dead, *moved_source)

    assert _run_isolated(root, home) == "POINTER_PY -m scout.cli version"
    # Re-resolved and re-stamped, so the next call takes the fast path to the pointer.
    assert cache.read_text().splitlines() == [str(pointer_py), str(tmp_path / "app-venv" / "src" / "scout" / "cli.py")]


def test_dead_in_tree_venv_falls_through_to_pointer(tmp_path):
    """The candidate loop probes too. An in-tree venv that can't import
    scout.cli must not shadow the engine pointer."""
    home = tmp_path / "home"
    _write_pointer(home, _fake_python(tmp_path / "app-venv", "POINTER_PY"))
    root = _plugin_tree(tmp_path)
    _fake_python(root / ".venv", "TREE_PY", importable=False)
    assert _run_isolated(root, home) == "POINTER_PY -m scout.cli version"


def test_valid_cache_takes_the_fast_path_without_probing(tmp_path):
    """A stamped cache whose files all exist execs straight away: no probe,
    so no Python start-up beyond the real call. The cached interpreter is not
    a candidate, so a re-probe would have picked TREE_PY instead."""
    home = tmp_path / "home"
    root = _plugin_tree(tmp_path)
    _fake_python(root / ".venv", "TREE_PY")
    cached = _fake_python(tmp_path / "elsewhere", "CACHED_PY")
    _seed_cache(root, cached, tmp_path / "elsewhere" / "src" / "scout" / "cli.py")

    assert _run_isolated(root, home) == "CACHED_PY -m scout.cli version"
    assert (tmp_path / "elsewhere" / "calls").read_text().splitlines() == ["-m"]
    assert not (root / ".venv" / "calls").exists()


def test_unusable_cache_is_deleted_when_nothing_resolves(tmp_path):
    """No candidate can import scout.cli, so the launcher falls back to
    python3, and the dead entry does not stay in the cache."""
    home = tmp_path / "home"
    root = _plugin_tree(tmp_path)
    dead = _fake_python(tmp_path / "old-checkout" / ".venv", "DEAD_PY", importable=False)
    cache = _seed_cache(root, dead)
    sysbin = _fake_system_python3(tmp_path)
    assert _run_isolated(root, home, extra_path=str(sysbin)) == "SYSTEM_PY -m scout.cli version"
    assert not cache.exists()


def test_probe_leaves_stdin_for_the_real_call(tmp_path):
    """Hooks get their payload on stdin, so probing a candidate must not consume it."""
    home = tmp_path / "home"
    root = _plugin_tree(tmp_path)
    cli = tmp_path / "src" / "scout" / "cli.py"
    cli.parent.mkdir(parents=True)
    cli.touch()
    py = root / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    # This probe drains stdin, as any interpreter that read it would.
    py.write_text(
        f'#!/bin/sh\nif [ "$1" = "-c" ]; then cat >/dev/null; echo "{cli}"; exit 0; fi\necho "GOT $(cat)"\n',
        encoding="utf-8",
    )
    py.chmod(0o755)
    out = _launch(root, home, stdin='{"tool_name": "Bash"}')
    assert out.stdout.strip() == 'GOT {"tool_name": "Bash"}', out


def test_probe_against_real_interpreters(tmp_path):
    """Everywhere else the stubs stand in for the probe; this runs it for real.
    The dead venv reproduces the outage. Its editable .pth names an engine/
    that moved and left only untracked leftovers (scout/__pycache__/), so
    `scout` imports as an empty namespace package with no cli module. The
    pointer is the test's own interpreter, which has scout installed."""
    old_engine = tmp_path / "old-checkout" / "engine"
    (old_engine / "scout" / "__pycache__").mkdir(parents=True)
    dead_venv = tmp_path / "old-checkout" / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(dead_venv)], check=True)
    site_packages = next(dead_venv.glob("lib/python*/site-packages"))
    (site_packages / "_editable_impl_scout_engine.pth").write_text(str(old_engine), encoding="utf-8")
    home = tmp_path / "home"
    _write_pointer(home, Path(sys.executable))
    root = _plugin_tree(tmp_path)
    cache = _seed_cache(root, dead_venv / "bin" / "python")

    assert _run_isolated(root, home) == scout.__version__
    cached_py, *stamp = cache.read_text().splitlines()
    assert cached_py == sys.executable
    assert stamp and all(Path(p).is_file() for p in stamp), stamp
    assert Path(stamp[0]).resolve() == (Path(scout.__file__).parent / "cli.py").resolve()
