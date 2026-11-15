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
# Like a real claude, names its provider switches (the probe scans for them): CLAUDE_CODE_USE_BEDROCK
import json, os, sys, time, urllib.request
mode = {mode!r}
if os.environ.get("STUB_ARGV_LOG"):
    keep = ("ENABLE_CLAUDEAI_MCP_SERVERS", "DISABLE_AUTOUPDATER", "DISABLE_TELEMETRY", "NO_PROXY", "HTTPS_PROXY")
    open(os.environ["STUB_ARGV_LOG"], "w").write(json.dumps({{"argv": sys.argv[1:],
        "env": {{k: os.environ.get(k) for k in keep}}}}))
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
    monkeypatch.setattr(gate_probe, "MANAGED_DIRS", (tmp_path / "managed",))
    monkeypatch.setattr(gate_probe, "MDM_DIR", tmp_path / "mdm")
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


# Launch flags that don't decide whether hooks load, each with its value. Everything else must be PROBE_FLAGS.
_HOOK_NEUTRAL = {"--model", "--max-budget-usd", "--name", "-p"}


def _launch_flags(template_text: str) -> list[str]:
    """Every flag the runner passes claude, minus the hook-neutral ones and their values."""
    launch = template_text[template_text.index('"$CLAUDE_BIN" \\') :].split("|| EXIT_CODE")[0]
    words = launch.replace("\\\n", " ").split()[1:]  # drop "$CLAUDE_BIN"
    kept: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in _HOOK_NEUTRAL:
            skip = True
        else:
            kept.append(word)
    return kept


def test_probe_flags_match_the_runners_launch() -> None:
    """PROBE_FLAGS is a copy of the runners' flags that can matter to hooks; keep them in step (review #635)."""
    templates = Path(gate_probe.default_plugin_root()) / "templates"
    for name in ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"):
        assert _launch_flags((templates / name).read_text(encoding="utf-8")) == list(gate_probe.PROBE_FLAGS), name


def test_the_flag_pin_catches_a_new_launch_flag() -> None:
    text = (Path(gate_probe.default_plugin_root()) / "templates" / "run-scout.sh.tmpl").read_text(encoding="utf-8")
    with_bare = text.replace("--permission-mode auto \\", "--permission-mode auto \\\n    --bare \\", 1)
    assert with_bare != text
    assert _launch_flags(with_bare) != list(gate_probe.PROBE_FLAGS)


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


# --- re-review of #635: no request may leave the machine -----------------------------


@pytest.mark.parametrize(
    "switch",
    [
        "CLAUDE_CODE_USE_ANTHROPIC_AWS",
        "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
        "CLAUDE_CODE_USE_MANTLE",
        "CLAUDE_CODE_USE_GATEWAY",
        "CLAUDE_CODE_USE_SOMETHING_NEW",  # a switch a later CLI adds: treated as a provider
    ],
)
def test_a_provider_switch_in_a_settings_env_is_set_to_0(_isolated_claude_config: Path, switch: str) -> None:
    (_isolated_claude_config / "settings.json").write_text(json.dumps({"env": {switch: "1"}}))
    switches = gate_probe.provider_switches(None, gate_probe.settings_env_blocks())
    env = gate_probe.probe_env(base_url="http://127.0.0.1:1", vault=Path("/v"), base={switch: "1"}, switches=switches)
    assert env[switch] == "0"


def test_switches_that_arent_providers_are_left_alone() -> None:
    blocks = [{"CLAUDE_CODE_USE_NATIVE_FILE_SEARCH": "1", "CLAUDE_CODE_USE_COWORK_PLUGINS": "1"}]
    switches = gate_probe.provider_switches(None, blocks)
    assert "CLAUDE_CODE_USE_NATIVE_FILE_SEARCH" not in switches
    assert "CLAUDE_CODE_USE_COWORK_PLUGINS" not in switches
    assert {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_MANTLE"} <= switches


def test_switches_the_binary_names_are_zeroed_too(tmp_path: Path) -> None:
    binary = tmp_path / "claude"
    binary.write_bytes(b"\x00junk CLAUDE_CODE_USE_FUTURECLOUD more junk CLAUDE_CODE_USE_POWERSHELL_TOOL\x00")
    switches = gate_probe.provider_switches(binary, [])
    assert "CLAUDE_CODE_USE_FUTURECLOUD" in switches
    assert "CLAUDE_CODE_USE_POWERSHELL_TOOL" not in switches


def test_base_urls_in_a_settings_env_point_at_the_fake(_isolated_claude_config: Path) -> None:
    (_isolated_claude_config / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BEDROCK_BASE_URL": "https://gw.example", "ANTHROPIC_BASE_URL": "https://x"}})
    )
    base_urls, other = gate_probe.endpoint_keys(gate_probe.settings_env_blocks())
    assert other == set()
    env = gate_probe.probe_env(base_url="http://127.0.0.1:9", vault=Path("/v"), base={}, base_urls=base_urls)
    assert (
        env["ANTHROPIC_BEDROCK_BASE_URL"] == "http://127.0.0.1:9" and env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
    )
    assert env["NO_PROXY"] == "*" and env["no_proxy"] == "*"


def test_managed_settings_envs_are_read(tmp_path: Path) -> None:
    drop_in = tmp_path / "managed" / "managed-settings.d"
    drop_in.mkdir(parents=True)
    (drop_in / "10-provider.json").write_text(json.dumps({"env": {"CLAUDE_CODE_USE_MANTLE": "1"}}))
    (tmp_path / "mdm").mkdir()
    import plistlib

    (tmp_path / "mdm" / "com.anthropic.claudecode.plist").write_bytes(
        plistlib.dumps({"env": {"ANTHROPIC_GATEWAY_URL": "https://gw.example"}})
    )
    blocks = gate_probe.settings_env_blocks()
    assert {"CLAUDE_CODE_USE_MANTLE": "1"} in blocks
    assert gate_probe.endpoint_keys(blocks)[1] == {"ANTHROPIC_GATEWAY_URL"}


def test_an_endpoint_that_cant_be_neutralised_refuses_without_a_sandbox(
    tmp_path: Path, _isolated_claude_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_isolated_claude_config / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_LLM_ENDPOINT": "https://x"}}))
    monkeypatch.setattr(gate_probe, "sandbox_prefix", lambda: [])
    refused = gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path))
    assert (
        not refused.ok
        and "can't probe safely" in refused.reason
        and "ANTHROPIC_LLM_ENDPOINT" in refused.reason
        and refused.unsafe
    )
    # Behind the network sandbox the same setting is harmless: nothing can leave anyway.
    monkeypatch.setattr(gate_probe, "sandbox_prefix", lambda: ["env"])
    assert gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path)).ok


def test_telemetry_endpoints_dont_refuse() -> None:
    assert gate_probe.endpoint_keys([{"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example"}]) == (set(), set())


@pytest.mark.skipif(sys.platform != "darwin" or not gate_probe.SANDBOX_EXEC.exists(), reason="macOS sandbox only")
def test_the_sandbox_lets_loopback_through_and_nothing_else(tmp_path: Path) -> None:
    import socket
    import subprocess

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    script = (
        "import socket, sys\n"
        f"socket.create_connection(('127.0.0.1', {port}), timeout=3).close()\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=3)\n"
        "    sys.exit('egress allowed')\n"
        "except OSError:\n"
        "    print('egress blocked')\n"
    )
    out = subprocess.run(
        [*gate_probe.sandbox_prefix(), sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    server.close()
    assert out.returncode == 0, out.stderr
    assert "egress blocked" in out.stdout


@pytest.mark.skipif(sys.platform != "darwin" or not gate_probe.SANDBOX_EXEC.exists(), reason="macOS sandbox only")
def test_on_macos_the_probe_runs_inside_the_sandbox(tmp_path: Path) -> None:
    result = gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path))
    assert result.ok and "loopback-only sandbox" in result.reason


def test_only_hook_deciding_keys_reach_the_fixture(tmp_path: Path) -> None:
    """A project hook, statusLine or apiKeyHelper never runs from the probe (re-review of #635, should-fix 2)."""
    source = tmp_path / "vault"
    (source / ".claude").mkdir(parents=True)
    full = {
        "disableAllHooks": True,
        "allowManagedHooksOnly": False,
        "enabledPlugins": {"scout@scout-plugin": True},
        "permissions": {"deny": []},
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "/abs/path/hook.sh"}]}]},
        "statusLine": {"type": "command", "command": "x"},
        "apiKeyHelper": "/abs/helper.sh",
        "env": {"CLAUDE_CODE_SIMPLE": "1", "CLAUDE_CODE_USE_BEDROCK": "1", "SECRET": "s"},
    }
    (source / ".claude" / "settings.local.json").write_text(json.dumps(full))
    fixture = gate_probe._fixture_vault(tmp_path / "probe", source)
    copied = json.loads((fixture / ".claude" / "settings.local.json").read_text())
    assert copied == {
        "disableAllHooks": True,
        "allowManagedHooksOnly": False,
        "enabledPlugins": {"scout@scout-plugin": True},
        "permissions": {"deny": []},
        "env": {"CLAUDE_CODE_SIMPLE": "1"},
    }


def test_managed_drop_ins_and_mdm_preferences_are_in_the_cache_key(tmp_path: Path) -> None:
    before = gate_probe.config_sha256(None)
    drop_in = tmp_path / "managed" / "managed-settings.d"
    drop_in.mkdir(parents=True)
    (drop_in / "20.json").write_text('{"disableAllHooks": true}')
    after_drop_in = gate_probe.config_sha256(None)
    (tmp_path / "mdm").mkdir()
    (tmp_path / "mdm" / "com.anthropic.claudecode.plist").write_bytes(b"x")
    assert len({before, after_drop_in, gate_probe.config_sha256(None)}) == 3


def test_every_claude_and_anthropic_variable_is_in_the_env_key_but_credentials() -> None:
    base = {"CLAUDE_CODE_SIMPLE": "0", "PATH": "/bin"}
    key = gate_probe.env_key(base)
    assert gate_probe.env_key({**base, "CLAUDE_CODE_DISABLE_HOOKS_X": "1"}) != key
    assert gate_probe.env_key({**base, "ANTHROPIC_MODEL": "m"}) != key
    assert gate_probe.env_key({**base, "PATH": "/usr/bin"}) == key
    assert gate_probe.env_key({**base, "CLAUDE_CODE_OAUTH_TOKEN": "t"}) == key  # a rotated token isn't a new config


# --- round 3 of #635's review: proxies, MCP, process-env switches, shims, Linux refusals ----------------

# A claude stand-in that does what the real one does with proxies and MCP servers: a settings env applies under
# the process env (the process env wins), a proxy carries traffic unless NO_PROXY covers it, and the user's MCP
# servers are contacted unless --strict-mcp-config. Then it answers the probe like a hooked claude.
_NETWORK_STUB = """#!{python}
# CLAUDE_CODE_USE_BEDROCK
import json, os, socket, sys, urllib.request
cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
def load(name):
    try:
        return json.load(open(os.path.join(cfg, name)))
    except Exception:
        return {{}}
env = {{**load("settings.json").get("env", {{}}), **os.environ}}
session = "--version" not in sys.argv  # --version prints and exits, as the real one does
for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
    proxy = env.get(var, "")
    if session and proxy and env.get("NO_PROXY") != "*":
        host, port = proxy.removeprefix("http://").split(":")
        s = socket.create_connection((host, int(port)), timeout=3)
        s.sendall(b"CONNECT api.example.com:443 HTTP/1.1\\r\\n\\r\\n")
        s.close()
if session and "--strict-mcp-config" not in sys.argv:
    for server in load(".claude.json").get("mcpServers", {{}}).values():
        try:
            urllib.request.urlopen(urllib.request.Request(server["url"], data=b"{{}}", method="POST"), timeout=3)
        except Exception:
            pass
"""


def _network_stub(tmp_path: Path) -> Path:
    from tests._gate_probe import HOOKED_CLAUDE_PRELUDE

    stub = tmp_path / "claude-net"
    stub.write_text(_NETWORK_STUB.format(python=sys.executable) + HOOKED_CLAUDE_PRELUDE)
    stub.chmod(0o755)
    return stub


def _logging_server() -> tuple[Any, list[str], int]:
    import socket
    import threading

    hits: list[str] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(20)

    def serve() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.settimeout(2)
                hits.append(conn.recv(200).split(b"\r\n", 1)[0].decode(errors="replace"))
            except OSError:
                hits.append("(no data)")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, hits, srv.getsockname()[1]


@pytest.mark.parametrize("sandboxed", [False, True])
def test_a_loopback_proxy_and_a_user_mcp_server_get_nothing(
    tmp_path: Path, _isolated_claude_config: Path, monkeypatch: pytest.MonkeyPatch, sandboxed: bool
) -> None:
    if sandboxed and not (sys.platform == "darwin" and gate_probe.SANDBOX_EXEC.exists()):
        pytest.skip("macOS sandbox only")
    srv, hits, port = _logging_server()
    url = f"http://127.0.0.1:{port}"
    (_isolated_claude_config / "settings.json").write_text(
        json.dumps({"env": {"HTTPS_PROXY": url, "HTTP_PROXY": url, "ALL_PROXY": url}})
    )
    (_isolated_claude_config / ".claude.json").write_text(json.dumps({"mcpServers": {"x": {"url": url + "/mcp"}}}))
    if not sandboxed:
        monkeypatch.setattr(gate_probe, "sandbox_prefix", lambda: [])
    stub = _network_stub(tmp_path)
    try:
        result = gate_probe.probe(stub, plugin_root=_plugin_root(tmp_path))
    finally:
        srv.close()
    assert result.ok, result.reason
    assert hits == []


def test_the_network_stub_does_reach_a_proxy_and_mcp_without_the_probes_env(
    tmp_path: Path, _isolated_claude_config: Path
) -> None:
    """The control: the stand-in above really would leak, so the zero above means something."""
    import subprocess

    srv, hits, port = _logging_server()
    url = f"http://127.0.0.1:{port}"
    (_isolated_claude_config / "settings.json").write_text(json.dumps({"env": {"HTTPS_PROXY": url}}))
    (_isolated_claude_config / ".claude.json").write_text(json.dumps({"mcpServers": {"x": {"url": url + "/mcp"}}}))
    stub = _network_stub(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith("PROXY")}
    subprocess.run([str(stub), "-p", "x"], env=env, capture_output=True, timeout=30)
    srv.close()
    assert len(hits) == 2  # the proxy's CONNECT and the MCP server's POST


def test_the_probe_runs_with_no_mcp_servers_no_proxy_and_no_background_traffic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "argv.json"
    monkeypatch.setenv("STUB_ARGV_LOG", str(log))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert gate_probe.probe(_stub(tmp_path, "deny"), plugin_root=_plugin_root(tmp_path)).ok
    seen = json.loads(log.read_text())
    argv = seen["argv"]
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers": {}}' and "--strict-mcp-config" in argv
    assert seen["env"] == {
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "NO_PROXY": "*",
        "HTTPS_PROXY": "",
    }


def test_provider_switches_already_in_the_process_env_are_zeroed() -> None:
    env = gate_probe.probe_env(
        base_url="http://127.0.0.1:1",
        vault=Path("/v"),
        base={"CLAUDE_CODE_USE_NEWPROVIDER": "1", "CLAUDE_CODE_USE_NATIVE_FILE_SEARCH": "1"},
        switches=(),
    )
    assert env["CLAUDE_CODE_USE_NEWPROVIDER"] == "0"
    assert env["CLAUDE_CODE_USE_NATIVE_FILE_SEARCH"] == "1"


def test_a_launcher_script_is_followed_to_the_claude_it_runs(tmp_path: Path) -> None:
    real = tmp_path / "lib" / "claude-real"
    real.parent.mkdir()
    real.write_bytes(b"\x00 CLAUDE_CODE_USE_FROMTHESHIM \x00")
    shim = tmp_path / "bin" / "claude"
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\nbasedir=$(dirname "$0")\nexec "$basedir/../lib/claude-real" "$@"\n')
    shim.chmod(0o755)
    assert "CLAUDE_CODE_USE_FROMTHESHIM" in gate_probe.provider_switches(shim, [])


def test_a_claude_whose_switches_cant_be_read_refuses_without_a_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opaque = tmp_path / "claude-opaque"
    opaque.write_text(_STUB.format(python=sys.executable, mode="deny").replace("CLAUDE_CODE_USE_BEDROCK", "nothing"))
    opaque.chmod(0o755)
    monkeypatch.setattr(gate_probe, "sandbox_prefix", lambda: [])
    refused = gate_probe.probe(opaque, plugin_root=_plugin_root(tmp_path))
    assert not refused.ok and refused.unsafe and "no provider switch could be read" in refused.reason
    monkeypatch.setattr(gate_probe, "sandbox_prefix", lambda: ["env"])
    assert gate_probe.probe(opaque, plugin_root=_plugin_root(tmp_path)).ok


@pytest.mark.parametrize("key", ["DOCKER_HOST", "GH_HOST", "JIRA_URL", "DATABASE_URL", "OTEL_EXPORTER_OTLP_ENDPOINT"])
def test_other_tools_endpoints_dont_refuse(key: str) -> None:
    assert gate_probe.endpoint_keys([{key: "x"}])[1] == set()


@pytest.mark.parametrize(
    "key", ["AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "ANTHROPIC_LLM_ENDPOINT", "CLOUD_ML_ENDPOINT", "VERTEX_HOST"]
)
def test_endpoints_claude_reads_refuse_without_a_sandbox(key: str) -> None:
    assert gate_probe.endpoint_keys([{key: "x"}])[1] == {key}


def test_an_unsafe_refusal_exits_4(
    tmp_path: Path, _isolated_claude_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_isolated_claude_config / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_LLM_ENDPOINT": "https://x"}}))
    monkeypatch.setattr(gate_probe, "sandbox_prefix", lambda: [])
    vault, root = _vault(tmp_path), _plugin_root(tmp_path)
    args = [
        "gates",
        "probe",
        "--vault",
        str(vault),
        "--plugin-root",
        str(root),
        "--claude-bin",
        str(_stub(tmp_path, "deny")),
    ]
    assert CliRunner().invoke(app, args).exit_code == 4


def test_the_sandbox_has_no_unix_socket_allowance() -> None:
    """No DNS (mDNSResponder) and no local daemon socket: claude needs neither once everything goes to the fake."""
    assert "unix-socket" not in gate_probe.SANDBOX_PROFILE
