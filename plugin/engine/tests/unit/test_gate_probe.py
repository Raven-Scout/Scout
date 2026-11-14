"""The gate probe: do Scout's hooks fire under the run's claude flags? (spec 2026-11-13 §3.5, plan Task 1a.2).

A stub ``claude`` plays the client side against the fake endpoint, so no real
claude, model or network is involved.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.agent_runtimes import gate_probe
from scout.agent_runtimes.gate_probe import ProbeResult
from scout.cli import app

# The stub posts the first request, then answers the scripted tool call the way a
# session would: with the gate's deny, with the file's text (no hook), or not at all.
_STUB = """#!{python}
import json, os, sys, time, urllib.request
mode = {mode!r}
if "--version" in sys.argv:
    if os.environ.get("STUB_VERSION_LOG"):
        open(os.environ["STUB_VERSION_LOG"], "a").write("v\\n")
    print("9.9.9 (Claude Code)"); sys.exit(0)
if mode == "exit1":
    sys.exit(1)
if mode in ("hang", "silent"):  # silent: alive, but talking to some other endpoint
    time.sleep(30); sys.exit(0)
if mode == "honours_project_settings":  # what claude does with a project disableAllHooks
    cfg = os.path.join(".claude", "settings.local.json")
    mode = "ran" if os.path.exists(cfg) and "disableAllHooks" in open(cfg).read() else "deny"
base = os.environ["ANTHROPIC_BASE_URL"]
vault = os.environ["SCOUT_DATA_DIR"]
def post(body):
    req = urllib.request.Request(base + "/v1/messages", data=json.dumps(body).encode(),
                                 headers={{"content-type": "application/json"}}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())
tools = [] if mode == "no_tool" else [{{"name": "Bash", "input_schema": {{}}}}]
first = post({{"model": "m", "tools": tools, "messages": [{{"role": "user", "content": "probe"}}]}})
block = first["content"][0]
if block["type"] != "tool_use":
    print("done"); sys.exit(0)
if mode == "deny":
    result = {{"type": "tool_result", "tool_use_id": block["id"], "is_error": True,
               "content": "PreToolUse:Bash hook error: [scout:kb-read-gate] Scheduled runs don't read ..."}}
else:
    text = open(os.path.join(vault, "knowledge-base", "probe.md")).read()
    result = {{"type": "tool_result", "tool_use_id": block["id"], "is_error": False, "content": text}}
post({{"model": "m", "tools": tools, "messages": [{{"role": "user", "content": "probe"}},
      {{"role": "assistant", "content": [block]}}, {{"role": "user", "content": [result]}}]}})
print("done")
"""


@pytest.fixture(autouse=True)
def _isolated_claude_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The probe's cache key reads the user's Claude settings: point it at a throwaway one."""
    config = tmp_path / "claude-config"
    (config / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


def _stub(tmp_path: Path, mode: str) -> Path:
    path = tmp_path / f"claude-{mode}"
    path.write_text(_STUB.format(python=sys.executable, mode=mode))
    path.chmod(0o755)
    return path


def _plugin_root(tmp_path: Path, hooks: str = '{"hooks": {}}') -> Path:
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "hooks" / "hooks.json").write_text(hooks)
    return root


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".scout-state").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("instance_name: t\n")
    return vault


def test_probe_ok_when_gate_denies(tmp_path: Path) -> None:
    result = gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path))
    assert result.ok, result.reason
    assert result.claude_version == "9.9.9 (Claude Code)"


def test_probe_refused_when_tool_ran(tmp_path: Path) -> None:
    result = gate_probe.probe(_stub(tmp_path, "ran"), plugin_root=_plugin_root(tmp_path))
    assert not result.ok
    assert "gates are not loaded" in result.reason


@pytest.mark.parametrize("mode", ["exit1", "no_tool"])
def test_probe_infrastructure_failure_is_refusal(tmp_path: Path, mode: str) -> None:
    result = gate_probe.probe(_stub(tmp_path, mode), plugin_root=_plugin_root(tmp_path))
    assert not result.ok
    assert result.reason


def test_probe_timeout_is_refusal(tmp_path: Path) -> None:
    result = gate_probe.probe(_stub(tmp_path, "hang"), plugin_root=_plugin_root(tmp_path), timeout_s=2)
    assert not result.ok
    assert "timed out" in result.reason


def test_probe_missing_binary_is_refusal(tmp_path: Path) -> None:
    result = gate_probe.probe(tmp_path / "no-such-claude", plugin_root=_plugin_root(tmp_path))
    assert not result.ok
    assert "not found" in result.reason or "not executable" in result.reason


def test_probe_leaves_no_fixture_vault_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path))
    assert list((tmp_path / "tmp").iterdir()) == []


def test_probe_env_has_no_real_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    env = gate_probe.probe_env(base_url="http://127.0.0.1:1", vault=tmp_path)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["ANTHROPIC_API_KEY"] != "sk-ant-real"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1"
    assert env["SCOUT_MODE"] and env["SCOUT_READER"] == "autonomous"
    assert env["SCOUT_DATA_DIR"] == str(tmp_path)


def test_cached_probe_reuses_a_fresh_matching_result(tmp_path: Path) -> None:
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    calls: list[int] = []

    def fake_probe(claude_bin: Path, **kw: Any) -> ProbeResult:
        calls.append(1)
        return gate_probe.probe(_stub(tmp_path, "deny"), **kw)

    claude = _stub(tmp_path, "deny")
    first = gate_probe.cached_probe(vault, claude, plugin_root=root, runner=fake_probe)
    second = gate_probe.cached_probe(vault, claude, plugin_root=root, runner=fake_probe)
    assert first.ok and second.ok
    assert len(calls) == 1
    assert json.loads((vault / ".scout-state" / "gate-probe.json").read_text())["ok"] is True


@pytest.mark.parametrize("change", ["hooks", "age", "failed"])
def test_cached_probe_reprobes_on_hooks_change_age_or_failure(tmp_path: Path, change: str) -> None:
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    claude = _stub(tmp_path, "deny")
    calls: list[int] = []

    def counting(claude_bin: Path, **kw: object) -> ProbeResult:
        calls.append(1)
        return gate_probe.probe(claude, plugin_root=root)

    gate_probe.cached_probe(vault, claude, plugin_root=root, runner=counting)
    now = None
    if change == "hooks":
        (root / "hooks" / "hooks.json").write_text('{"hooks": {"PreToolUse": []}}')
    elif change == "age":
        now = datetime.now(UTC) + gate_probe.MAX_AGE + timedelta(minutes=1)
    else:
        cache = vault / ".scout-state" / "gate-probe.json"
        data = json.loads(cache.read_text())
        data["ok"] = False
        cache.write_text(json.dumps(data))
    gate_probe.cached_probe(vault, claude, plugin_root=root, runner=counting, now=now)
    assert len(calls) == 2


def test_cached_probe_reprobes_on_version_change(tmp_path: Path) -> None:
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    gate_probe.cached_probe(vault, _stub(tmp_path, "deny"), plugin_root=root)
    cache = vault / ".scout-state" / "gate-probe.json"
    data = json.loads(cache.read_text())
    data["claude_version"] = "0.0.1 (Claude Code)"
    cache.write_text(json.dumps(data))
    calls: list[int] = []

    def counting(claude_bin: Path, **kw: object) -> ProbeResult:
        calls.append(1)
        return gate_probe.probe(claude_bin, plugin_root=root)

    gate_probe.cached_probe(vault, _stub(tmp_path, "deny"), plugin_root=root, runner=counting)
    assert calls == [1]


def test_unreadable_cache_is_ignored(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    (vault / ".scout-state" / "gate-probe.json").write_text("{not json")
    assert gate_probe.read_cache(vault) is None


def test_cli_exit_codes(tmp_path: Path) -> None:
    runner = CliRunner()
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    base = ["gates", "probe", "--vault", str(vault), "--plugin-root", str(root), "--json"]
    ok = runner.invoke(app, [*base, "--claude-bin", str(_stub(tmp_path, "deny"))])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["ok"] is True
    bad = runner.invoke(app, [*base, "--claude-bin", str(_stub(tmp_path, "ran"))])
    assert bad.exit_code == 3
    assert json.loads(bad.stdout)["ok"] is False


def test_cli_cached_flag_uses_cache(tmp_path: Path) -> None:
    runner = CliRunner()
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    claude = str(_stub(tmp_path, "deny"))
    args = ["gates", "probe", "--cached", "--vault", str(vault), "--plugin-root", str(root), "--claude-bin", claude]
    assert runner.invoke(app, args).exit_code == 0
    assert (vault / ".scout-state" / "gate-probe.json").exists()


def test_gate_mark_matches_the_gate() -> None:
    from scout.hooks import kb_read_gate

    assert gate_probe.GATE_MARK == kb_read_gate.MARK


def test_probe_command_is_one_the_gate_denies() -> None:
    """The scripted call must be one the real gate denies, or the probe proves nothing."""
    from scout.kb.raw_read import raw_kb_read

    assert raw_kb_read(gate_probe.PROBE_COMMAND)


def test_default_plugin_root_holds_hooks_json() -> None:
    assert (gate_probe.default_plugin_root() / "hooks" / "hooks.json").is_file()


def test_probe_does_not_inherit_bare_switch_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE_CODE_SIMPLE (what --bare sets) is part of the cache key, so flipping it re-probes."""
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    key_a = gate_probe.env_key()
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    assert gate_probe.env_key() != key_a
    assert os.environ["CLAUDE_CODE_SIMPLE"] == "1"


# --- review of #635 ----------------------------------------------------------------------


def test_the_vaults_project_settings_reach_the_probe(tmp_path: Path) -> None:
    """A project-level disableAllHooks kills the run's gates, so it must fail the probe (review #635, must-fix 2)."""
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    claude = _stub(tmp_path, "honours_project_settings")
    assert gate_probe.probe(claude, plugin_root=root, vault=vault).ok
    (vault / ".claude").mkdir()
    (vault / ".claude" / "settings.local.json").write_text('{"disableAllHooks": true}')
    refused = gate_probe.probe(claude, plugin_root=root, vault=vault)
    assert not refused.ok and "gates are not loaded" in refused.reason


@pytest.mark.parametrize("change", ["vault_settings", "user_settings", "plugin_registry", "installed_hooks"])
def test_a_config_change_reprobes(tmp_path: Path, _isolated_claude_config: Path, change: str) -> None:
    """`claude plugin disable scout`, a new disableAllHooks, a plugin update: the next run probes again."""
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    config = _isolated_claude_config
    install = tmp_path / "installed-scout"
    (install / "hooks").mkdir(parents=True)
    (install / "hooks" / "hooks.json").write_text('{"hooks": {"PreToolUse": [1]}}')
    registry = {"version": 2, "plugins": {"scout@scout-plugin": [{"installPath": str(install), "version": "1"}]}}
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps(registry))
    claude = _stub(tmp_path, "deny")
    calls: list[int] = []

    def counting(claude_bin: Path, **kw: object) -> ProbeResult:
        calls.append(1)
        return gate_probe.probe(claude, plugin_root=root, vault=vault)

    gate_probe.cached_probe(vault, claude, plugin_root=root, runner=counting)
    gate_probe.cached_probe(vault, claude, plugin_root=root, runner=counting)
    assert len(calls) == 1
    if change == "vault_settings":
        (vault / ".claude").mkdir()
        (vault / ".claude" / "settings.json").write_text('{"disableAllHooks": true}')
    elif change == "user_settings":
        (config / "settings.json").write_text('{"enabledPlugins": {"scout@scout-plugin": false}}')
    elif change == "plugin_registry":
        registry["plugins"]["scout@scout-plugin"][0]["version"] = "2"
        (config / "plugins" / "installed_plugins.json").write_text(json.dumps(registry))
    else:
        (install / "hooks" / "hooks.json").write_text('{"hooks": {}}')
    gate_probe.cached_probe(vault, claude, plugin_root=root, runner=counting)
    assert len(calls) == 2


def test_other_providers_are_switched_off_not_just_unset() -> None:
    """A settings env can turn Bedrock/Vertex/Foundry back on; the process env wins (review #635, should-fix 3)."""
    env = gate_probe.probe_env(
        base_url="http://127.0.0.1:1",
        vault=Path("/v"),
        base={"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "PATH": "/bin"},
    )
    for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        assert env[var] == "0"
    assert env["ANTHROPIC_API_KEY"] == gate_probe.DUMMY_KEY and env["SCOUT_MODE"] == gate_probe.PROBE_MODE


def test_a_claude_that_never_asks_the_fake_fails_fast(tmp_path: Path) -> None:
    started = datetime.now(UTC)
    result = gate_probe.probe(_stub(tmp_path, "silent"), plugin_root=_plugin_root(tmp_path), first_request_s=1)
    assert not result.ok and "sent no request" in result.reason
    assert (datetime.now(UTC) - started).total_seconds() < 15


@pytest.mark.parametrize(
    "corrupt",
    [
        {"checked_at": "2026-11-13T21:29:05"},  # naive: comparing it with an aware now raised (exit 70)
        {"ok": "false"},
        {"ok": 1},
        {"flags": "--permission-mode auto"},
        {"config_sha256": None, "drop": "config_sha256"},
        {"checked_at": "yesterday"},
    ],
)
def test_a_corrupt_cache_is_a_miss_and_gets_rewritten(tmp_path: Path, corrupt: dict) -> None:
    """Never a wedge: an unreadable cache re-probes and is replaced (review #635, should-fix 4)."""
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    claude = _stub(tmp_path, "deny")
    gate_probe.cached_probe(vault, claude, plugin_root=root)
    cache = vault / ".scout-state" / "gate-probe.json"
    data = json.loads(cache.read_text())
    drop = corrupt.pop("drop", None)
    data.update(corrupt)
    if drop:
        del data[drop]
    cache.write_text(json.dumps(data))
    assert gate_probe.read_cache(vault) is None
    result = gate_probe.cached_probe(vault, claude, plugin_root=root)
    assert result.ok
    assert gate_probe.read_cache(vault) is not None


def test_probe_flags_match_the_runners_launch(tmp_path: Path) -> None:
    """PROBE_FLAGS is a copy of the runners' permission flags; keep them in step (review #635, should-fix 6)."""
    templates = Path(gate_probe.default_plugin_root()) / "templates"
    for name in ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"):
        text = (templates / name).read_text(encoding="utf-8")
        launch = text[text.index('"$CLAUDE_BIN" \\') :].split("-p ")[0]
        words = [w for w in launch.replace("\\", " ").split() if w]
        mode_at = words.index("--permission-mode")
        assert tuple(words[mode_at : mode_at + 2]) == gate_probe.PROBE_FLAGS, name


def test_a_cache_miss_asks_claude_for_its_version_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "versions.log"
    monkeypatch.setenv("STUB_VERSION_LOG", str(log))
    gate_probe.cached_probe(_vault(tmp_path), _stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path))
    assert log.read_text().count("v") == 1


def test_a_fake_that_cant_bind_is_named_as_such(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_bind(self: object) -> str:
        raise OSError("address in use")

    monkeypatch.setattr(gate_probe.FakeAnthropic, "start", no_bind)
    result = gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path))
    assert not result.ok and "local endpoint" in result.reason and "could not start claude" not in result.reason


def test_stale_probe_dirs_are_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate_probe.tempfile, "gettempdir", lambda: str(tmp_path))
    old, fresh, other = (
        tmp_path / f"{gate_probe.TMP_PREFIX}old",
        tmp_path / f"{gate_probe.TMP_PREFIX}new",
        tmp_path / "x",
    )
    for d in (old, fresh, other):
        d.mkdir()
    two_hours_ago = datetime.now(UTC).timestamp() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    os.utime(other, (two_hours_ago, two_hours_ago))
    gate_probe.sweep_stale_tmp()
    assert not old.exists() and fresh.exists() and other.exists()
