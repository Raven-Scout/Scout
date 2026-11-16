"""The refusal rule (spec 2026-11-13 §8.3) and the run limits (§8.6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.agent_runtimes import check as rt_check
from scout.agent_runtimes import consent, limits
from scout.agent_runtimes.base import TIER_F_REQUIRES, TIER_L_REQUIRES, Capability
from scout.agent_runtimes.claude_code import FULL_CAPABILITIES, ClaudeCode
from tests._agent_runtimes import DummyRuntime, registered

C = Capability
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    return v


def _check(vault: Path, rt: object, **kw: object) -> rt_check.Refusal | None:
    args: dict[str, object] = {"reader": "autonomous", "mode": "dreaming", "interactive": False, "now": NOW}
    args.update(kw)
    return rt_check.check(vault, rt, **args)  # type: ignore[arg-type]


TIERS = {
    "F": TIER_F_REQUIRES,
    "L": TIER_L_REQUIRES,
    "S": frozenset({C.PATH_DENY}),
    "D": frozenset({C.SKILLS}),
}


@pytest.mark.parametrize("tier", ["F", "L", "S", "D"])
@pytest.mark.parametrize("restrict", [False, True])
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("consented", [False, True])
@pytest.mark.parametrize("version", ["1.0.0", None])
@pytest.mark.parametrize("interactive", [False, True])
def test_check_refusal_matrix(
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    tier: str,
    restrict: bool,
    provider: str,
    consented: bool,
    version: str | None,
    interactive: bool,
) -> None:
    if restrict:
        monkeypatch.setattr(rt_check, "_uses_restrict_tools", lambda v: True)
    else:
        monkeypatch.setattr(rt_check, "_uses_restrict_tools", lambda v: False)
    if consented and provider != "anthropic":
        consent.consent(vault, provider)
    caps = TIERS[tier] | {C.BUDGET_CAP}  # a ceiling isn't what this matrix is about
    dummy = DummyRuntime("dummy", provider=provider, caps=caps, binary=Path("/bin/true"), version=version)
    r = _check(vault, dummy, interactive=interactive)
    # The expected code, in the rule's order.
    if version is None:
        want: str | None = "version_unverified"
    elif tier not in ("F", "L"):
        want = "tier_too_low"
    elif provider != "anthropic" and not consented:
        want = "provider_not_consented"
    elif provider != "anthropic":
        want = "local_only_unenforced"
    else:
        want = None  # F and L hold pre_tool_block, so restrict_tools is enforceable
    assert (r.code if r else None) == want


def test_restrict_tools_needs_pre_tool_block_or_allowlist(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt_check, "_uses_restrict_tools", lambda v: True)
    # A Tier F set can't lack pre_tool_block, so drive the rule with a stand-in tier.
    monkeypatch.setattr(rt_check, "tier_for", lambda caps: rt_check.Tier.F)
    no_hook = DummyRuntime("d", provider="anthropic", caps=frozenset({C.BUDGET_CAP}), binary=Path("/bin/true"))
    r = _check(vault, no_hook)
    assert r is not None and r.code == "restrict_tools_unenforceable"
    allow = DummyRuntime(
        "d", provider="anthropic", caps=frozenset({C.BUDGET_CAP, C.MCP_TOOL_ALLOWLIST}), binary=Path("/bin/true")
    )
    assert _check(vault, allow) is None


def test_restrict_tools_is_read_from_the_vaults_connectors(vault: Path) -> None:
    assert rt_check._uses_restrict_tools(vault) is False


def test_missing_binary_is_version_unverified(vault: Path) -> None:
    r = _check(vault, DummyRuntime("d", caps=TIER_F_REQUIRES, binary=None))
    assert r is not None and r.code == "version_unverified"


def test_check_error_is_refusal(vault: Path) -> None:
    class Broken(DummyRuntime):
        def capabilities(self, vault: Path, binary: Path, version: str | None) -> frozenset[Capability]:
            raise RuntimeError("probe exploded")

    r = _check(vault, Broken("b", provider="anthropic", binary=Path("/bin/true")))
    assert r is not None and r.code == "check_failed"
    assert "probe exploded" in r.reason
    assert r.exit_code == rt_check.EXIT_REFUSE


def _claude(monkeypatch: pytest.MonkeyPatch, caps: frozenset[Capability]) -> ClaudeCode:
    monkeypatch.setattr(ClaudeCode, "locate", lambda self, hint: hint or Path("/usr/local/bin/claude"))
    monkeypatch.setattr(ClaudeCode, "version", lambda self, b: "2.1.0")
    monkeypatch.setattr(ClaudeCode, "capabilities", lambda self, v, b, ver: caps)
    return ClaudeCode()


def test_claude_code_check_is_the_gate_probe_and_nothing_more(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """For Claude Code, nothing but the probe can refuse a scheduled run: no new way for today's runs to be skipped."""

    def boom(*a: object, **k: object) -> object:
        raise AssertionError("Claude Code's check must not read this")

    monkeypatch.setattr(rt_check, "_uses_restrict_tools", boom)
    monkeypatch.setattr(consent, "is_consented", boom)
    monkeypatch.setattr(limits, "tokens_used", boom)
    (vault / "scout-config.yaml").write_text("privacy:\n  providers: [openai]\n", encoding="utf-8")
    assert _check(vault, _claude(monkeypatch, FULL_CAPABILITIES), binary=Path("/x/claude")) is None
    r = _check(vault, _claude(monkeypatch, FULL_CAPABILITIES - {C.PRE_TOOL_BLOCK}))
    assert r is not None and r.code == "tier_too_low"


def test_claude_code_interactive_is_allowed_without_probing(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ClaudeCode, "capabilities", lambda *a: (_ for _ in ()).throw(AssertionError("probed")))
    assert _check(vault, ClaudeCode(), interactive=True) is None


# -- limits --------------------------------------------------------------------------------------------------


def _config(vault: Path, text: str) -> None:
    (vault / "scout-config.yaml").write_text(text, encoding="utf-8")


def test_limits_default_on_for_a_runtime_without_a_cap(vault: Path) -> None:
    lim = limits.limits_for(vault, DummyRuntime("d"))
    assert (lim.run_timeout_s, lim.timeout_source) == (45 * 60, "default")
    assert (lim.max_tokens_per_day, lim.ceiling_source) == (limits.FLOOR_TOKENS, "default")


def test_limits_default_off_for_claude_code(vault: Path) -> None:
    lim = limits.limits_for(vault, ClaudeCode())
    assert (lim.run_timeout_s, lim.max_tokens_per_day) == (None, None)
    assert (lim.timeout_source, lim.ceiling_source) == ("off", "off")


def test_limits_from_config(vault: Path) -> None:
    _config(
        vault,
        "budget:\n  per_runtime:\n"
        "    claude-code:\n      run_timeout_minutes: 90\n      max_tokens_per_day: auto\n"
        "    d:\n      run_timeout_minutes: off\n      max_tokens_per_day: 1000\n",
    )
    c = limits.limits_for(vault, ClaudeCode())
    assert (c.run_timeout_s, c.timeout_source) == (90 * 60, "config")
    assert (c.max_tokens_per_day, c.ceiling_source) == (limits.FLOOR_TOKENS, "config")
    d = limits.limits_for(vault, DummyRuntime("d"))
    assert (d.run_timeout_s, d.timeout_source) == (None, "off")
    assert (d.max_tokens_per_day, d.ceiling_source) == (1000, "config")


@pytest.mark.parametrize("bad", ["-5", "soon", "[1]", "true"])
def test_an_invalid_limit_keeps_the_default(vault: Path, bad: str) -> None:
    _config(vault, f"budget:\n  per_runtime:\n    d:\n      run_timeout_minutes: {bad}\n")
    assert limits.limits_for(vault, DummyRuntime("d")).run_timeout_s == 45 * 60
    _config(vault, f"budget:\n  per_runtime:\n    claude-code:\n      run_timeout_minutes: {bad}\n")
    assert limits.limits_for(vault, ClaudeCode()).run_timeout_s is None


def test_an_unreadable_config_keeps_the_defaults(vault: Path) -> None:
    _config(vault, "budget: [\n")
    assert limits.limits_for(vault, DummyRuntime("d")).run_timeout_s == 45 * 60
    assert limits.limits_for(vault, ClaudeCode()).run_timeout_s is None


def _rows(vault: Path, *rows: dict[str, object]) -> None:
    with (vault / ".scout-logs" / "usage-tracker.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        f.write("not json\n")


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_tokens_used_counts_this_runtimes_last_24_hours(vault: Path) -> None:
    _rows(
        vault,
        {"ts": _ts(1), "agent_runtime": "d", "tokens": 400},
        {"ts": _ts(2), "agent_runtime": "d", "input_tokens": 100, "output_tokens": 50},
        {"ts": _ts(30), "agent_runtime": "d", "tokens": 9999},
        {"ts": _ts(1), "agent_runtime": "other", "tokens": 9999},
        {"ts": _ts(1), "tokens": 7},  # no key: claude-code
        {"ts": _ts(1), "agent_runtime": "d", "tokens": True},
    )
    assert limits.tokens_used(vault, "d", now=NOW) == 550
    assert limits.tokens_used(vault, "claude-code", now=NOW) == 7
    assert limits.tokens_used(vault / "nowhere", "d", now=NOW) == 0


def test_the_token_ceiling_skips_as_budget(vault: Path) -> None:
    _config(vault, "budget:\n  per_runtime:\n    d:\n      max_tokens_per_day: 500\n")
    dummy = DummyRuntime("d", provider="anthropic", caps=TIER_F_REQUIRES, binary=Path("/bin/true"))
    _rows(vault, {"ts": _ts(1), "agent_runtime": "d", "tokens": 499})
    assert _check(vault, dummy) is None
    _rows(vault, {"ts": _ts(1), "agent_runtime": "d", "tokens": 500})
    r = _check(vault, dummy)
    assert r is not None and r.code == "token_ceiling"
    assert r.exit_code == rt_check.EXIT_BUDGET
    assert "max_tokens_per_day" in r.reason
    # The skills' preamble isn't a scheduled run: no ceiling there.
    assert _check(vault, dummy, interactive=True) is None


def test_the_default_ceiling_is_one_replaceable_function(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "default_daily_token_ceiling", lambda d, rid: 123)
    assert limits.limits_for(vault, DummyRuntime("d")).max_tokens_per_day == 123


# -- the default ceiling's formula (Phase 0, #650 §3) ----------------------------------------------------------

SLOT = """\
  {key}:
    type: {kind}
    runner: run-{runner}.sh
    fires_at_local: "02:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 60
{extra}"""


def _schedule(vault: Path, *slots: tuple[str, str, str]) -> None:
    body = "".join(
        SLOT.format(key=k, kind=kind, runner="scout" if kind in ("briefing", "consolidation") else kind, extra=e)
        for k, kind, e in slots
    )
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots:\n" + body, encoding="utf-8")


def _boot(hours_ago: float, kind: str, usd: float, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ts": _ts(hours_ago),
        "type": kind,
        "source": "runner",
        "cost_source": "transcript",
        "budget_spent": usd,
    }
    row.update(extra)
    return row


def test_formula_floor_with_no_rows(vault: Path) -> None:
    with registered(DummyRuntime("d")):
        _schedule(vault, ("night", "dreaming", "    agent_runtime: d\n"))
        assert limits.default_daily_token_ceiling(vault, "d", now=NOW) == limits.FLOOR_TOKENS


def test_formula_bootstraps_from_claudes_measured_runs(vault: Path) -> None:
    with registered(DummyRuntime("d")):
        _schedule(
            vault,
            ("night", "dreaming", "    agent_runtime: d\n"),
            ("night-2", "dreaming", "    agent_runtime: d\n"),
            ("web", "research", "    agent_runtime: d\n"),
            ("brief", "briefing", ""),  # Claude Code's: not counted for d
        )
        # dreaming p90 of [2, 4, 6, 8, 10] = 10 USD; research p90 of [3] = 3 USD.
        _rows(
            vault,
            *[_boot(24 * i + 1, "dreaming-nightly", usd) for i, usd in enumerate([2, 4, 6, 8, 10])],
            _boot(5, "research", 3),
            _boot(5, "dreaming", 0),  # the $0 rows the run_cost fix left behind: never counted
            _boot(5, "research", 99, cost_source="usage"),  # not measured from the transcript
            _boot(5, "research", 99, source="session"),
            _boot(24 * 20, "research", 99),  # outside the 14 days
        )
        want = 2 * (10 + 10 + 3) * 1_000_000
        assert limits.default_daily_token_ceiling(vault, "d", now=NOW) == want


def test_formula_uses_the_runtimes_own_rows_after_a_week(vault: Path) -> None:
    with registered(DummyRuntime("d")):
        _schedule(vault, ("night", "dreaming", "    agent_runtime: d\n"))
        boot = [_boot(5, "dreaming", 50)]
        young = [{"ts": _ts(24 * 3), "agent_runtime": "d", "type": "dreaming", "tokens": 9_000_000}]
        _rows(vault, *boot, *young)
        assert limits.default_daily_token_ceiling(vault, "d", now=NOW) == 2 * 50 * 1_000_000  # still bootstrap
        old = [{"ts": _ts(24 * 8), "agent_runtime": "d", "type": "dreaming", "tokens": 7_000_000}]
        _rows(vault, *boot, *young, *old)
        # Own rows: p90 of [7M, 9M] = 9M; 2 * 9M = 18M.
        assert limits.default_daily_token_ceiling(vault, "d", now=NOW) == 18_000_000


def test_formula_a_kind_without_rows_uses_all_rows(vault: Path) -> None:
    with registered(DummyRuntime("d")):
        _schedule(vault, ("web", "research", "    agent_runtime: d\n"))
        _rows(vault, _boot(5, "dreaming", 8))
        assert limits.default_daily_token_ceiling(vault, "d", now=NOW) == 16_000_000


def test_formula_errors_keep_the_floor(vault: Path) -> None:
    (vault / ".scout-state" / "schedule.yaml").write_text("slots: [\n", encoding="utf-8")
    assert limits.default_daily_token_ceiling(vault, "d", now=NOW) == limits.FLOOR_TOKENS
