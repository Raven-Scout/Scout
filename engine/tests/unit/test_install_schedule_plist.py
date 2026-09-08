"""Unit tests for engine/scout/scripts/install_schedule_plist.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.install_schedule_plist import (
    install_plist,
    resolve_scoutctl_bin,
    uninstall_plist,
)


def test_install_plist_writes_filled_template(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    written = target_dir / "com.scout.schedule-tick.plist"
    assert written.exists()
    content = written.read_text()
    assert "__USER_HOME__" not in content  # placeholders filled
    assert "__SCOUTCTL_BIN__" not in content
    assert str(tmp_path) in content
    assert "<integer>300</integer>" in content


def test_install_plist_substitutes_resolver_output(tmp_path):
    """The plist's ProgramArguments[0] is exactly what resolve_scoutctl_bin returns."""
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    content = (target_dir / "com.scout.schedule-tick.plist").read_text()
    assert f"<string>{resolve_scoutctl_bin()}</string>" in content


def test_resolve_scoutctl_bin_is_the_running_interpreters_sibling():
    """The scoutctl that matches the running engine is the console script
    beside the interpreter executing this test — whatever venv that is, and
    wherever it lives relative to the plugin tree (spec E1)."""
    import sys

    assert resolve_scoutctl_bin() == Path(sys.executable).absolute().parent / "scoutctl"


def test_resolve_scoutctl_bin_does_not_follow_symlinks(monkeypatch, tmp_path):
    """A venv's bin/python is a symlink to the base interpreter; resolving it
    would name a scoutctl that does not exist."""
    import sys

    real = tmp_path / "base" / "bin" / "python3"
    real.parent.mkdir(parents=True)
    real.write_text("")
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.symlink_to(real)
    monkeypatch.setattr(sys, "executable", str(venv_py))

    assert resolve_scoutctl_bin() == venv_py.parent / "scoutctl"


def test_install_plist_refuses_to_overwrite_without_force(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("# existing\n")
    with pytest.raises(FileExistsError):
        install_plist(home=tmp_path, agents_dir=target_dir, force=False)
    assert plist.read_text() == "# existing\n"


def test_install_plist_force_overwrites(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("# old\n")
    install_plist(home=tmp_path, agents_dir=target_dir, force=True)
    assert "<integer>300</integer>" in plist.read_text()


def test_uninstall_plist_removes_file(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("dummy\n")
    uninstall_plist(agents_dir=target_dir)
    assert not plist.exists()


def test_uninstall_plist_silent_when_missing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    # No exception when target plist doesn't exist.
    uninstall_plist(agents_dir=target_dir)


def test_install_plist_escapes_xml_metacharacters(tmp_path):
    """A home path with XML metacharacters (legal on macOS) must produce a
    well-formed plist launchd can load, not malformed XML (#49)."""
    import plistlib

    home = tmp_path / 'R&D <lab> "x"'
    home.mkdir()
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    target = install_plist(home=home, agents_dir=agents)

    raw = target.read_text(encoding="utf-8")
    assert "&amp;" in raw  # the bare & was escaped
    assert "R&D <lab>" not in raw  # not left as raw, XML-breaking text

    # Critically: the plist parses, and values round-trip to the real path.
    with target.open("rb") as f:
        data = plistlib.load(f)
    assert data["EnvironmentVariables"]["HOME"] == str(home)


def test_install_plist_bootstrap_boots_out_first(tmp_path, monkeypatch):
    """Re-install must bootout the loaded job before bootstrap: launchctl
    bootstrap EIOs on an already-loaded label and has no --force (#48, #23)."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("scout.scripts.install_schedule_plist.subprocess.run", fake_run)
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir, bootstrap=True)

    assert len(calls) == 2
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[0][2].endswith("/com.scout.schedule-tick")
    assert calls[1][:2] == ["launchctl", "bootstrap"]
