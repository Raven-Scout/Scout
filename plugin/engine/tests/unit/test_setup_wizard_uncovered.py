"""The "Also connected" step: every uncovered server is offered; none is dead-ended (spec §4.3, §4.4)."""

from __future__ import annotations

from dataclasses import replace

from typer.testing import CliRunner

from scout import setup_wizard as sw
from scout.cli import app
from tests.unit.test_setup_wizard import Scripted, _deps, _opts

T = "mcp__claude_ai_Example_Suite__"
SERVERS = {
    "schema_version": 1,
    "error": None,
    "servers": [
        {"name": "claude.ai Example CRM", "slug": "claude_ai_Example_CRM", "status": "needs_auth", "evidence": ""},
        {"name": "claude.ai Example Suite", "slug": "claude_ai_Example_Suite", "status": "connected", "evidence": ""},
    ],
}
DRAFTED = {
    "status": "drafted",
    "definitions": [
        {
            "key": "suite_mail",
            "display_name": "Mail suite",
            "server": "claude_ai_Example_Suite",
            "probe": f"{T}list_folders",
            "preset": "mail",
            "inbound": {"tools": [f"{T}search_messages"]},
            "needs_user_input": ["mailbox"],
        },
    ],
    "summary": [{"key": "suite_mail", "scans": "new mail", "looks_up": "nothing"}],
}
DEAD_ENDS = ("not supported", "isn't supported", "can't read", "cannot read", "no probe")


def test_offer_drafts_keeps_and_signs_in(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: DRAFTED)
    p = Scripted(["y", "team", "y"])  # add Suite? mailbox, keep?
    kept = sw.offer_uncovered(p, deps)
    assert [k.definition["key"] for k in kept] == ["suite_mail"]
    assert kept[0].inputs == {"mailbox": "team"}
    said = "\n".join(p.said)
    assert "Example CRM" in said and "/mcp" in said
    assert "Mail suite · new mail · nothing" in said
    assert not any(d in said.lower() for d in DEAD_ENDS)


def test_a_failed_draft_offers_a_retry_never_a_dead_end(tmp_path):
    deps, _ = _deps(tmp_path)
    for status in ("no_read_tools", "invalid", "timeout", "error"):
        d = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s, st=status: {"status": st, "message": "x"})
        p = Scripted(["y"])
        assert sw.offer_uncovered(p, d) == []
        said = "\n".join(p.said).lower()
        assert "scoutctl connectors setup" in said
        assert not any(dead in said for dead in DEAD_ENDS)


def test_a_draft_that_needs_auth_says_to_sign_in(tmp_path):
    deps, _ = _deps(tmp_path)
    d = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: {"status": "needs_auth", "message": "x"})
    p = Scripted(["y"])
    assert sw.offer_uncovered(p, d) == []
    assert sum("/mcp" in s for s in p.said) == 2  # the CRM server and the Suite draft


def test_declining_to_keep_a_draft_keeps_nothing(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: DRAFTED)
    assert sw.offer_uncovered(Scripted(["y", "team", "n"]), deps) == []


def test_declining_a_server_drafts_nothing(tmp_path):
    deps, _ = _deps(tmp_path)
    drafted = []
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: drafted.append(s) or DRAFTED)
    assert sw.offer_uncovered(Scripted(["n"]), deps) == []
    assert drafted == []


def test_listing_error_is_said_not_hidden(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: {"schema_version": 1, "servers": [], "error": "boom"})
    p = Scripted([])
    assert sw.offer_uncovered(p, deps) == []
    assert any("couldn't list" in s.lower() for s in p.said)


def test_setup_applies_kept_drafts_after_the_vault_exists(tmp_path):
    added = []
    deps, calls = _deps(tmp_path)
    deps = replace(
        deps,
        list_uncovered=lambda: SERVERS,
        draft=lambda s: DRAFTED,
        add_custom=lambda d, i: (
            added.append((d["key"], i, [c[0] for c in calls])) or {"status": "applied", "message": "Live"}
        ),
    )
    # instance, name, email, keep tz, toggle, slack id, add Suite?, mailbox, keep?, per-session, daily, first run
    p = Scripted(["", "", "", "", "", "U1", "y", "team", "y", "", "", "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert added == [("suite_mail", {"mailbox": "team"}, ["bootstrap"])]  # applied after bootstrap auto


def test_headless_setup_skips_drafting_and_prints_the_hint(tmp_path):
    deps, _ = _deps(tmp_path)
    drafted = []
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: drafted.append(s) or DRAFTED)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", first_run=False)
    p = Scripted([])
    p_headless = sw.HeadlessPrompter()
    p_headless.say = p.say  # capture
    assert sw.run_setup(opts, p_headless, deps) == 0
    assert drafted == []
    assert any("scoutctl connectors setup" in s for s in p.said)


def test_busy_apply_says_to_retry(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, add_custom=lambda d, i: {"status": "busy", "message": "A Scout session is running"})
    p = Scripted([])
    assert sw.apply_drafts([sw.KeptDraft(DRAFTED["definitions"][0], {})], p, deps) == 0
    assert any("scoutctl connectors setup" in s for s in p.said)


def test_a_rejected_add_says_why(tmp_path):
    deps, _ = _deps(tmp_path)
    out = {"status": "invalid", "message": "", "issues": [{"path": "connectors.suite_mail", "message": "bad probe"}]}
    deps = replace(deps, add_custom=lambda d, i: out)
    p = Scripted([])
    assert sw.apply_drafts([sw.KeptDraft(DRAFTED["definitions"][0], {})], p, deps) == 0
    assert any("Mail suite: invalid — connectors.suite_mail: bad probe" in s for s in p.said)


def test_a_rejected_add_without_issues_says_the_message(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, add_custom=lambda d, i: {"status": "error", "message": "disk full"})
    p = Scripted([])
    assert sw.apply_drafts([sw.KeptDraft({"key": "k"}, {})], p, deps) == 0
    assert p.said == ["  k: disk full"]


def test_connectors_setup_needs_a_vault(tmp_path):
    deps, _ = _deps(tmp_path)
    p = Scripted([])
    assert sw.run_connectors_setup(tmp_path / "missing", p, deps) == 2
    assert any("scoutctl setup" in s for s in p.said)


def _vault(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    return vault


def test_connectors_setup_on_a_vault(tmp_path):
    vault = _vault(tmp_path)
    added = []
    deps, _ = _deps(tmp_path)
    deps = replace(
        deps,
        list_uncovered=lambda: SERVERS,
        draft=lambda s: DRAFTED,
        add_custom=lambda d, i: added.append(d["key"]) or {"status": "applied", "message": "Live"},
    )
    assert sw.run_connectors_setup(vault, Scripted(["y", "team", "y"]), deps) == 0
    assert added == ["suite_mail"]


def test_connectors_setup_cancels_on_end_of_input(tmp_path):
    vault = _vault(tmp_path)
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: SERVERS)
    p = Scripted([])  # the first question hits EOF
    assert sw.run_connectors_setup(vault, p, deps) == 1
    assert sw.CANCELLED in p.said


def test_cli_connectors_setup_without_a_terminal_exits_2(monkeypatch):
    monkeypatch.setattr(sw.TtyPrompter, "open", classmethod(lambda cls: None))
    result = CliRunner().invoke(app, ["connectors", "setup"])
    assert result.exit_code == 2
    assert "needs a terminal" in result.output


def test_cli_connectors_setup_runs_with_the_vault_and_claude_bin(monkeypatch, tmp_path):
    seen = {}
    prompter = Scripted([])
    deps, _ = _deps(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(sw.TtyPrompter, "open", classmethod(lambda cls: prompter))
    monkeypatch.setattr(sw, "default_deps", lambda claude_bin="": seen.setdefault("bin", claude_bin) and deps)
    monkeypatch.setattr(sw, "run_connectors_setup", lambda vault, p, d: seen.update(vault=vault, p=p, deps=d) or 0)
    result = CliRunner().invoke(app, ["connectors", "setup", "--claude-bin", "/x/claude"])
    assert result.exit_code == 0
    assert seen == {"bin": "/x/claude", "vault": tmp_path / "Scout", "p": prompter, "deps": deps}
