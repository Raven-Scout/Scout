"""The interface follow-ups that must land before the first non-Claude adapter (#652).

S5's launch environment is in ``test_agent_runtime_edges.py`` and S7's run-health finding in
``test_health_agent_runtime.py``; the runners' side of S6 and S7 is in
``tests/integration/test_runner_agent_runtime.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.agent_runtimes import check, gate_probe, limits, registry
from scout.agent_runtimes.base import (
    HOOK_CAPABILITIES,
    TIER_F_REQUIRES,
    TIER_L_REQUIRES,
    AgentRuntime,
    Capability,
    SandboxPaths,
    caps_own_spend,
)
from scout.agent_runtimes.claude_code import DECLARED_CAPABILITIES, FULL_CAPABILITIES, ClaudeCode
from scout.kb import hook_paths
from scout.sandbox import launch
from scout.sandbox import profile as sbx
from tests._agent_runtimes import DummyRuntime, registered

C = Capability


def _cli(*args: str) -> tuple[int, str]:
    from scout.cli import app

    res = CliRunner().invoke(app, list(args))
    return res.exit_code, res.output


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SCOUT_AGENT_RUNTIME", raising=False)
    return v


# -- the adapters keep the protocol -------------------------------------------------------------------------------


def test_claude_code_and_the_dummy_are_agent_runtimes() -> None:
    assert isinstance(ClaudeCode(), AgentRuntime)
    assert isinstance(DummyRuntime("d"), AgentRuntime)


def test_an_adapter_cant_declare_a_hook_capability(vault: Path) -> None:
    """Review of #806: runtime_probe trusts capabilities(), which adds the declared set."""
    liar = DummyRuntime("liar", declared=frozenset({C.PRE_TOOL_BLOCK}), binary=Path("/bin/true"))
    with pytest.raises(ValueError, match="pre_tool_block"):
        registry.register(liar)
    res = gate_probe.runtime_probe(liar, vault)
    assert not res.ok and "only a probe may prove" in res.reason


def test_no_adapter_can_take_claude_codes_id() -> None:
    with pytest.raises(ValueError):
        registry.register(DummyRuntime("claude-code", provider="anthropic"))
    assert registry.ANTHROPIC_RUNTIMES == frozenset({"claude-code"})
    for runtime_id in registry.ids():
        registry.check_adapter(registry.get(runtime_id))


# -- caps_own_spend is the declared budget_cap, not a second flag ---------------------------------------------


def test_claude_code_declares_what_needs_no_probe() -> None:
    claude = ClaudeCode()
    assert claude.declared_capabilities == DECLARED_CAPABILITIES == FULL_CAPABILITIES - HOOK_CAPABILITIES
    assert C.BUDGET_CAP in claude.declared_capabilities and caps_own_spend(claude)
    assert not (claude.declared_capabilities & HOOK_CAPABILITIES)  # those are the gate probe's to prove


def test_a_failed_probe_leaves_claude_code_its_declared_capabilities(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("no probe here")

    monkeypatch.setattr(gate_probe, "cached_probe", boom)
    assert ClaudeCode().capabilities(vault, Path("/bin/true"), "2.1.0") == DECLARED_CAPABILITIES


def test_the_run_limits_follow_the_declared_budget_cap(vault: Path) -> None:
    capped = DummyRuntime("d", declared=frozenset({C.BUDGET_CAP}))
    uncapped = DummyRuntime("e")
    assert caps_own_spend(capped) and not caps_own_spend(uncapped)
    assert limits.limits_for(vault, capped).run_timeout_s is None
    assert limits.limits_for(vault, uncapped).run_timeout_s == 45 * 60


# -- check's interactive shortcut is the adapter's ------------------------------------------------------------------


def test_only_a_runtime_that_hosts_the_skills_skips_an_interactive_check(vault: Path) -> None:
    args = {"reader": "interactive", "mode": None, "interactive": True}
    host = DummyRuntime("d", provider="anthropic", interactive_host=True, binary=None)
    guest = DummyRuntime("e", provider="anthropic", interactive_host=False, binary=None)
    assert check.check(vault, host, **args) is None  # type: ignore[arg-type]
    refusal = check.check(vault, guest, **args)  # type: ignore[arg-type]
    assert refusal is not None and refusal.code == "version_unverified"  # it went on to locate the binary
    assert ClaudeCode().interactive_host


# -- limits.py names the built-in default, not "claude-code" ------------------------------------------------------


def test_limits_reads_the_builtin_default_from_the_registry() -> None:
    text = Path(limits.__file__).read_text(encoding="utf-8")
    assert '"claude-code"' not in text and "BUILTIN_DEFAULT" in text


# -- S6: the gate probe per runtime ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("caps", "ok"),
    [
        (TIER_F_REQUIRES, True),
        (TIER_L_REQUIRES, True),  # no post-tool context: Tier L still runs on the vault
        (TIER_L_REQUIRES - {C.PRE_TOOL_FAIL_CLOSED}, False),
        (frozenset({C.BUDGET_CAP, C.HEADLESS_EVENTS}), False),
    ],
)
def test_another_runtimes_probe_is_its_verified_hook_capabilities(
    vault: Path, caps: frozenset[Capability], ok: bool
) -> None:
    res = gate_probe.runtime_probe(DummyRuntime("d", caps=caps, binary=Path("/bin/true")), vault)
    assert (res.ok, res.runtime, res.version) == (ok, "d", "1.0.0")
    if not ok:
        assert "missing:" in res.reason


def test_the_probe_fails_closed(vault: Path) -> None:
    missing = gate_probe.runtime_probe(DummyRuntime("d", caps=TIER_F_REQUIRES, binary=None), vault)
    assert not missing.ok and "isn't installed" in missing.reason
    unread = gate_probe.runtime_probe(DummyRuntime("d", caps=TIER_F_REQUIRES, binary=Path("/x"), version=None), vault)
    assert not unread.ok and "version" in unread.reason

    class Broken(DummyRuntime):
        def capabilities(self, vault: Path, binary: Path, version: str | None) -> frozenset[Capability]:
            raise OSError("probe crashed")

    broken = gate_probe.runtime_probe(Broken("d", binary=Path("/bin/true")), vault)
    assert not broken.ok and "OSError" in broken.reason


def test_gates_probe_cli_takes_a_runtime(vault: Path) -> None:
    with registered(DummyRuntime("dummy", caps=TIER_L_REQUIRES, binary=Path("/bin/true"))):
        code, out = _cli("gates", "probe", "--runtime", "dummy", "--json")
        assert code == 0 and json.loads(out)["ok"] is True
    with registered(DummyRuntime("dummy", caps=frozenset(), binary=Path("/bin/true"))):
        code, out = _cli("gates", "probe", "--runtime", "dummy")
        assert code == 3 and "REFUSED" in out


def test_gates_probe_cli_refuses_an_unknown_runtime(vault: Path) -> None:
    code, out = _cli("gates", "probe", "--runtime", "nope")
    assert code == 3 and "unknown agent runtime" in out


# -- S6: the sandbox paths per runtime -----------------------------------------------------------------------------


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


def test_a_runtimes_protected_tree_and_its_carve_out(world: tuple[Path, Path, dict[str, str]]) -> None:
    home, v, env = world
    data = home / ".agentcli"
    paths = SandboxPaths(protected=(data,), writable=(data / "sessions",))
    rules = sbx.build_rules(v, env, runtime_paths=paths)
    assert sbx.decide(rules, str(data / "hooks.json")) == "deny"
    assert sbx.decide(rules, str(data / "sessions" / "s1.jsonl")) == "allow"
    # Without the runtime's paths, Scout's own list says nothing about the folder.
    assert sbx.decide(sbx.build_rules(v, env), str(data / "hooks.json")) == "allow"


@pytest.mark.parametrize(
    ("protected", "writable"),
    [
        ("~/.claude", "~/.claude/plugins"),  # Scout's kernel tree, even when the adapter claims it
        ("@/.claude", "@/.claude/skills"),  # the vault's .claude/ (review of #806)
        ("@/.git", "@/.git/info"),  # the vault's git dir: .git/info/attributes would become writable
        ("~/.agentcli", ".claude/skills"),  # relative
        ("~/.agentcli", "~/.agentcli/../.agentcli/x"),  # not normalised
        ("~/.agentcli", "~/.agentx/sessions"),  # not under the adapter's own protected tree
        ("~/.gemini", "~/.gemini"),  # a carve-out equal to its own tree re-opens it
        ("~/", "~/.scout-secrets"),  # holds Scout's secrets
        ("~/", "~/.local"),  # holds the owner's stores and the engine pointer
    ],
)
def test_a_runtime_carve_out_that_could_reopen_scouts_paths_is_refused(
    world: tuple[Path, Path, dict[str, str]], protected: str, writable: str
) -> None:
    home, v, env = world

    def path(spec: str) -> Path:
        if spec.startswith("~/"):
            return Path(str(home) + spec[1:]) if spec != "~/" else home
        if spec.startswith("@/"):
            return Path(str(v) + spec[1:])
        return Path(spec)

    paths = SandboxPaths(protected=(path(protected),), writable=(path(writable),))
    with pytest.raises(sbx.ProfileError):
        sbx.build_rules(v, env, runtime_paths=paths)


def test_a_carve_out_in_another_agent_clis_home_folder_is_allowed(world: tuple[Path, Path, dict[str, str]]) -> None:
    """agy's data dir is under ~/.gemini, which Scout protects as another CLI's hook folder (Phase 2's shape)."""
    home, v, env = world
    gemini = home / ".gemini"
    paths = SandboxPaths(protected=(gemini,), writable=(gemini / "antigravity-cli",))
    rules = sbx.build_rules(v, env, runtime_paths=paths)
    assert sbx.decide(rules, str(gemini / "antigravity-cli" / "brain" / "c1.jsonl")) == "allow"
    assert sbx.decide(rules, str(gemini / "config" / "hooks.json")) == "deny"


def test_a_refused_carve_out_fails_the_sandbox_so_the_run_is_skipped(
    world: tuple[Path, Path, dict[str, str]],
) -> None:
    home, v, env = world
    bad = SandboxPaths(protected=(v / ".claude",), writable=(v / ".claude" / "skills",))
    result = launch.prepare(v, env, available=lambda: True, self_test=lambda _p: None, runtime_paths=bad)
    assert result.status == "failed" and "inside Scout's protected" in result.reason


def test_claude_codes_profile_is_unchanged(world: tuple[Path, Path, dict[str, str]]) -> None:
    _home, v, env = world
    claude = ClaudeCode().sandbox_paths(env)
    assert sbx.build_rules(v, env, runtime_paths=claude) == sbx.build_rules(v, env)


def test_sandbox_profile_cli_refuses_an_unknown_runtime(vault: Path) -> None:
    code, out = _cli("sandbox", "profile", "--runtime", "nope")
    assert code == launch.Prepared("failed", "").exit_code
    assert "nope" in out


def test_sandbox_profile_cli_asks_the_runtimes_adapter(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[SandboxPaths | None] = []

    def fake(_vault: Path, *, runtime_paths: SandboxPaths | None = None) -> launch.Prepared:
        seen.append(runtime_paths)
        return launch.Prepared("unavailable", "test")

    monkeypatch.setattr(launch, "prepare", fake)
    paths = SandboxPaths(protected=(Path("/tmp/agentcli"),))
    with registered(DummyRuntime("dummy", sandbox=paths)):
        code, _ = _cli("sandbox", "profile", "--runtime", "dummy")
    assert code == 6 and seen == [paths]
    code, _ = _cli("sandbox", "profile")
    assert code == 6 and seen[-1] == SandboxPaths()  # claude-code's: nothing extra
    assert registry.BUILTIN_DEFAULT == "claude-code"


# -- N2: the owner's stores have their own label ------------------------------------------------------------------


def test_the_consent_file_isnt_labelled_scouts_engine(world: tuple[Path, Path, dict[str, str]]) -> None:
    home, v, env = world
    consent = home / ".local" / "state" / "scout" / hook_paths.PROVIDER_CONSENT_NAME
    assert hook_paths.protected_write(consent, v, env) == hook_paths.OWNER_STATE
    pointer = home / ".local" / "state" / "scout" / "engine.json"
    assert hook_paths.protected_write(pointer, v, env) == hook_paths.ENGINE
