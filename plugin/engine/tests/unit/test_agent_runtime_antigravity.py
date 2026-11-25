"""The antigravity adapter's skeleton (runtime-agnostic Phase 2A.1): who it is, where agy lives, its sandbox paths."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout.agent_runtimes import registry
from scout.agent_runtimes.antigravity import DECLARED_CAPABILITIES, Antigravity
from scout.agent_runtimes.base import HOOK_CAPABILITIES, AgentRuntime, Capability, RunRequest, caps_own_spend
from scout.kb import hook_paths
from scout.sandbox import launch
from scout.sandbox import profile as sbx


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path.resolve()
    home, v = root / "home", root / "vault"
    (home / ".claude").mkdir(parents=True)
    v.mkdir()
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "SCOUT_SECRETS_DIR", "GIT_DIR")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    env.pop("GIT_CONFIG_GLOBAL", None)
    env.pop(launch.ENV_SWITCH, None)
    hook_paths._ROOTS_CACHE.clear()
    return home, v, env


def test_antigravity_declares_no_budget_cap() -> None:
    a = Antigravity()
    assert (a.id, a.provider, a.display_name, a.interactive_host) == (
        "antigravity",
        "google",
        "Antigravity CLI",
        False,
    )
    assert not caps_own_spend(a)  # so its runs get the timeout and the token ceiling by default
    assert not (a.declared_capabilities & HOOK_CAPABILITIES)
    assert Capability.POST_TOOL_CONTEXT not in a.declared_capabilities  # Tier L at best


def test_it_keeps_the_protocol_and_passes_the_registrys_adapter_check() -> None:
    a = Antigravity()
    assert isinstance(a, AgentRuntime)
    registry.check_adapter(a)
    assert a.capabilities(Path("/v"), Path("/bin/true"), "1.5.16") == DECLARED_CAPABILITIES


def test_it_isnt_registered_before_2b() -> None:
    assert "antigravity" not in registry.ids()
    req = RunRequest(Path("/v"), "dreaming", "r", "p", "m", None, "autonomous")
    with pytest.raises(NotImplementedError):
        Antigravity().headless_argv(req)


def test_its_data_dir_is_writable_and_its_config_dir_isnt(world: tuple[Path, Path, dict[str, str]]) -> None:
    home, vault, env = world
    rules = sbx.build_rules(vault, env, runtime_paths=Antigravity().sandbox_paths(env), runtime_id="antigravity")
    assert sbx.decide(rules, str(home / ".gemini/antigravity-cli/brain/c1/x.jsonl")) == "allow"
    assert sbx.decide(rules, str(home / ".gemini/config/hooks.json")) == "deny"
    assert sbx.decide(rules, str(home / ".gemini/settings.json")) == "deny"
    assert sbx.decide(rules, str(home / ".gemini/antigravity-cli/settings.json")) == "deny"


def test_its_data_dir_is_the_profiles_listed_one(world: tuple[Path, Path, dict[str, str]]) -> None:
    home, _vault, env = world
    (rel,) = sbx.AGENT_DATA_DIRS["antigravity"]
    a = Antigravity()
    assert a.data_dir(env) == home / rel
    assert a.sandbox_paths(env).writable == (home / rel,) and a.sandbox_paths(env).protected == ()
    assert a.config_dir(env) == home / ".gemini" / "config"


def test_its_carve_out_is_refused_under_any_other_runtime_id(world: tuple[Path, Path, dict[str, str]]) -> None:
    _home, vault, env = world
    with pytest.raises(sbx.ProfileError, match="AGENT_DATA_DIRS"):
        sbx.build_rules(vault, env, runtime_paths=Antigravity().sandbox_paths(env), runtime_id="claude-code")


def _fake_agy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bin" / "agy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_locate_prefers_the_hint_then_path_then_local_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    local = home / ".local" / "bin" / "agy"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n")
    local.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    a = Antigravity()
    assert a.locate(None) == local
    on_path = _fake_agy(tmp_path, "exit 0")
    monkeypatch.setenv("PATH", f"{on_path.parent}:/usr/bin:/bin")
    assert a.locate(None) == on_path
    hint = _fake_agy(tmp_path / "hint", "exit 0")
    assert a.locate(hint) == hint
    local.unlink()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert a.locate(None) is None
    assert a.locate(tmp_path / "missing") is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('echo "agy 1.5.16 (abc123)"', "1.5.16"),
        ("echo 1.5.9", "1.5.9"),
        ("echo unknown", None),
        ("echo 1.5.16; exit 2", None),
    ],
)
def test_version_is_the_first_semver_or_none(tmp_path: Path, body: str, expected: str | None) -> None:
    assert Antigravity().version(_fake_agy(tmp_path, body)) == expected


def test_version_of_a_missing_binary_is_none(tmp_path: Path) -> None:
    assert Antigravity().version(tmp_path / "nope") is None
