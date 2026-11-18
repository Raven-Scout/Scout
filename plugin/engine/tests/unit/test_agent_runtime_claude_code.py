"""The claude-code adapter launches exactly what the runners launch today (spec 2026-11-13 §8.2, G4)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.agent_runtimes import claude_code, gate_probe
from scout.agent_runtimes.base import HOOK_CAPABILITIES, FailureKind, RunRequest, Tier, tier_for
from scout.agent_runtimes.claude_code import ClaudeCode

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
WRAPPER = TEMPLATES / "scripts" / "claude-with-retry.sh.tmpl"
RUNNERS = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl")


def _req(**over: object) -> RunRequest:
    base: dict[str, object] = {
        "vault": Path("/v"),
        "mode": "morning-briefing",
        "run_name": "scout-morning-briefing-20261116-0800",
        "prompt": "You are Scout.\nStep 1: read SKILL.md",
        "model": "opus",
        "budget_cap": "5.00",
        "reader": "autonomous",
        "binary": Path("/opt/homebrew/bin/claude"),
    }
    base.update(over)
    return RunRequest(**base)  # type: ignore[arg-type]


def test_headless_argv_is_todays_runner_line() -> None:
    launch = ClaudeCode().headless_argv(_req())
    assert launch.argv == (
        "/opt/homebrew/bin/claude",
        "--permission-mode",
        "auto",
        "--model",
        "opus",
        "--max-budget-usd",
        "5.00",
        "--name",
        "scout-morning-briefing-20261116-0800",
        "-p",
        "You are Scout.\nStep 1: read SKILL.md",
    )
    assert dict(launch.env) == {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}
    assert launch.stdin is None


@pytest.mark.parametrize("runner", RUNNERS)
def test_headless_argv_flags_match_each_runner_template(runner: str) -> None:
    """The flag words, in order, as the template's direct-claude launch writes them."""
    text = (TEMPLATES / runner).read_text(encoding="utf-8")
    m = re.search(
        r'"\$CLAUDE_BIN" \\\n((?:\s+--?[\w-]+ [^\n]*\\\n)+)\s+(-p "\$PROMPT")',
        text,
    )
    assert m, f"{runner}: no direct claude launch found"
    words: list[str] = []
    for line in m.group(1).splitlines():
        flag, _, value = line.strip().rstrip("\\").strip().partition(" ")
        words += [flag, value]
    words += ["-p", '"$PROMPT"']
    expected = [
        "--permission-mode",
        "auto",
        "--model",
        '"$SCOUT_MODEL"',
        "--max-budget-usd",
        "{{MAX_BUDGET}}",
        "--name",
        '"$RUN_NAME"',
        "-p",
        '"$PROMPT"',
    ]
    assert words == expected
    argv = ClaudeCode().headless_argv(_req(model="M", budget_cap="CAP", run_name="R", prompt="P")).argv
    assert list(argv[1:]) == [
        "--permission-mode",
        "auto",
        "--model",
        "M",
        "--max-budget-usd",
        "CAP",
        "--name",
        "R",
        "-p",
        "P",
    ]


@pytest.mark.parametrize("cap", ["5", "5.00", "2.50", "0.75", "12"])
def test_cap_formatting_matches_template(cap: str) -> None:
    """{{MAX_BUDGET}} reaches claude as the characters the runner wrote: never re-formatted as a float."""
    argv = ClaudeCode().headless_argv(_req(budget_cap=cap)).argv
    assert argv[argv.index("--max-budget-usd") + 1] == cap


def test_no_cap_means_no_flag() -> None:
    assert "--max-budget-usd" not in ClaudeCode().headless_argv(_req(budget_cap=None)).argv


def test_missing_binary_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ClaudeCode, "locate", lambda self, hint: None)
    with pytest.raises(FileNotFoundError):
        ClaudeCode().headless_argv(_req(binary=None))


def _template_value(name: str) -> str:
    m = re.search(rf"^{name}='(.*)'$", WRAPPER.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, name
    return m.group(1)


def test_patterns_are_the_wrappers_own() -> None:
    assert claude_code.TRANSIENT_PATTERNS == _template_value("TRANSIENT_PATTERNS")
    assert claude_code.AUTH_PATTERNS == _template_value("AUTH_PATTERNS")
    assert claude_code.USAGE_LIMIT_PATTERN == _template_value("USAGE_LIMIT_PATTERN")


@pytest.mark.parametrize(
    ("tail", "kind"),
    [
        ("Error: socket connection was closed unexpectedly", FailureKind.TRANSIENT),
        ("API Error: Unable to connect to API (ECONNRESET)", FailureKind.TRANSIENT),
        ("API Error: Unable to connect to API (ConnectionRefused)", FailureKind.TRANSIENT),
        ("API Error: Unable to connect to API (ENOTFOUND)", FailureKind.TRANSIENT),
        ("API Error: Unable to connect to API (FailedToOpenSocket)", FailureKind.TRANSIENT),
        ("API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)", FailureKind.TRANSIENT),
        ("Stream idle timeout - partial response received", FailureKind.TRANSIENT),
        ("Connection closed mid-response", FailureKind.TRANSIENT),
        ("The machine went to sleep mid-response", FailureKind.TRANSIENT),
        ("API Error: 529 Overloaded", FailureKind.TRANSIENT),
        ("hook output\nAPI Error: 500 Internal server error.", FailureKind.TRANSIENT),
        ("API Error: 403 Attention Required! | Cloudflare", FailureKind.TRANSIENT),
        # The sign-in race (synthetic line; the real one names the account's email).
        (
            "Claude Code couldn't verify your sign-in with Anthropic yet (its sign-in token was still being "
            "refreshed, often by another Claude Code process), so it couldn't load your organization's settings.",
            FailureKind.TRANSIENT,
        ),
        ("Claude Code couldn't confirm it: its sign-in token was still being refreshed", FailureKind.TRANSIENT),
        # The session quoting the race mid-line is not one.
        ("Summary: Claude Code couldn't verify your sign-in with Anthropic yet", FailureKind.OTHER),
        ("Failed to authenticate. API Error: 401", FailureKind.AUTH),
        ("Invalid authentication credentials", FailureKind.AUTH),
        ("API Error: 401 {}", FailureKind.AUTH),
        ("OAuth token has expired", FailureKind.AUTH),
        ("Not logged in · Please run /login", FailureKind.AUTH),
        ("You've hit your session limit · resets 6:50pm", FailureKind.USAGE_LIMIT),
        ("Error: Exceeded USD budget (5)", FailureKind.BUDGET),
        ("something else broke", FailureKind.OTHER),
        # Anchored: a quoted auth error mid-line isn't one; a 5000-something isn't a 500.
        ("the log says: API Error: 401", FailureKind.OTHER),
        ("API Error: 5000 things", FailureKind.OTHER),
    ],
)
def test_classify_failure_patterns(tail: str, kind: FailureKind) -> None:
    assert ClaudeCode().classify_failure(tail, 1).kind is kind


def test_transient_wins_over_auth_as_in_the_wrapper() -> None:
    tail = "API Error: 401\nAPI Error: 529 Overloaded"
    assert ClaudeCode().classify_failure(tail, 1).kind is FailureKind.TRANSIENT


def test_usage_limit_parses_its_reset() -> None:
    f = ClaudeCode().classify_failure("Claude AI usage limit reached · resets 6:50pm", 1)
    assert f.kind is FailureKind.USAGE_LIMIT
    assert f.resets_at is not None


def _probe_result(ok: bool, version: str | None = "2.1.0") -> gate_probe.ProbeResult:
    return gate_probe.ProbeResult(
        ok=ok,
        reason="test",
        claude_version=version,
        hooks_sha256="h",
        flags=gate_probe.PROBE_FLAGS,
        env_key="e",
        config_sha256="c",
        checked_at=datetime.now(UTC).isoformat(),
    )


def test_capabilities_full_when_probe_passed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gate_probe, "cached_probe", lambda *a, **k: _probe_result(True))
    caps = ClaudeCode().capabilities(tmp_path, Path("/bin/claude"), "2.1.0")
    assert caps == claude_code.FULL_CAPABILITIES
    assert tier_for(caps) is Tier.F


def test_capabilities_drop_hooks_when_probe_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gate_probe, "cached_probe", lambda *a, **k: _probe_result(False))
    caps = ClaudeCode().capabilities(tmp_path, Path("/bin/claude"), "2.1.0")
    assert not (caps & HOOK_CAPABILITIES)
    assert tier_for(caps) is Tier.D


def test_capabilities_drop_hooks_when_probe_is_for_another_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(gate_probe, "cached_probe", lambda *a, **k: _probe_result(True, "2.0.9"))
    assert tier_for(ClaudeCode().capabilities(tmp_path, Path("/bin/claude"), "2.1.0")) is Tier.D


def test_capabilities_drop_hooks_when_probe_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*a: object, **k: object) -> gate_probe.ProbeResult:
        raise OSError("no fake endpoint")

    monkeypatch.setattr(gate_probe, "cached_probe", boom)
    assert tier_for(ClaudeCode().capabilities(tmp_path, Path("/bin/claude"), "2.1.0")) is Tier.D


def test_process_markers_are_live_runs_words() -> None:
    m = ClaudeCode().process_markers()
    assert m.argv_words == ("--name", "-p", "--max-budget-usd")
    assert dict(m.env) == {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}


def test_models_are_claudes_rule() -> None:
    c = ClaudeCode()
    assert c.validate_model(" Sonnet ", where="x") == "sonnet"
    assert c.default_model() == "opus"
    assert c.known_models() == ("opus", "sonnet", "haiku")
