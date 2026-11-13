"""The ``delegate:<id>`` reader: the owner's own agents off the laptop (#550 V1, spec §4.1, §4.5).

A delegate listed under ``scout-config.yaml → privacy.delegates`` sees the
vault's normal notes, labelled, and never a local-only path. With
``include_private: true`` it sees private notes too. An unlisted delegate sees
nothing, like ``agent:*``. Over the privacy fixture vault (spec §11.1 of
#453), with Alex / Priya / Sam stand-ins.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner, Result

from scout import cli
from scout.cli import app
from scout.hooks import kb_read_gate as gate
from scout.kb import privacy
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.privacy import Label, policy_from_config_text, policy_from_overlay, visible_to
from scout.kb.reader import FAIL_CLOSED_READER, is_scheduled, resolve_reader
from scout.scripts import pre_session_data as psd
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

runner = CliRunner()
NORMAL_ONLY = "delegate:grok-personal"
WITH_PRIVATE = "delegate:duet"
UNLISTED = "delegate:someone-else"
SAM = "knowledge-base/people/sam.md"
PRIYA = "knowledge-base/people/priya.md"
PERMIT = "knowledge-base/personal/task-renew-permit.md"
GIFT = "knowledge-base/personal/task-buy-gift.md"
TRADING = "trading/positions.md"
PRIVATE_CANARIES = {p: c for p, c in CANARIES.items() if p != TRADING}

DELEGATES_BLOCK = """\
  delegates:
    grok-personal: {}
    duet:
      include_private: true
"""


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    vault = build_privacy_vault(tmp_path_factory.mktemp("kb-delegate"))
    config = vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + DELEGATES_BLOCK, encoding="utf-8")
    return vault


@pytest.fixture
def vault(shared_vault: Path) -> Path:
    clear_frontmatter_cache()
    return shared_vault


def kb(vault: Path, *args: str, reader: str) -> Result:
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env={"SCOUT_READER": reader})


def kb_json(vault: Path, *args: str, reader: str) -> Any:
    result = kb(vault, *args, "--json", reader=reader)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _label(path: str, visibility: str = "normal", local_only: bool = False) -> Label:
    return Label(path=path, visibility=visibility, visibility_source="frontmatter", local_only=local_only)  # type: ignore[arg-type]


# ----- reader resolution --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("delegate:grok-personal", "delegate:grok-personal"),
        ("Delegate:Grok-Personal", "delegate:grok-personal"),
        ("delegate:a", "delegate:a"),
        ("delegate:" + "x" * 40, "delegate:" + "x" * 40),
    ],
)
def test_a_delegate_reader_is_canonicalised(raw: str, canonical: str) -> None:
    assert resolve_reader(None, {"SCOUT_READER": raw}) == canonical


@pytest.mark.parametrize(
    "raw",
    ["delegate:", "delegate:-grok", "delegate:_defaults", "delegate:grok personal", "delegate:" + "x" * 41, "delegate"],
)
def test_a_malformed_delegate_fails_closed(raw: str) -> None:
    assert resolve_reader(None, {"SCOUT_READER": raw}) == FAIL_CLOSED_READER


def test_a_delegate_counts_as_a_run() -> None:
    assert is_scheduled({"SCOUT_READER": NORMAL_ONLY})


@pytest.mark.parametrize(
    ("ambient", "flag", "ok"),
    [
        ("owner", NORMAL_ONLY, True),
        ("autonomous", NORMAL_ONLY, True),
        (NORMAL_ONLY, NORMAL_ONLY, True),
        (NORMAL_ONLY, "agent:person:priya@example.com", True),  # narrower
        (NORMAL_ONLY, WITH_PRIVATE, False),  # lateral
        (NORMAL_ONLY, "autonomous", False),  # wider
        (NORMAL_ONLY, "owner", False),
        ("agent:person:priya@example.com", NORMAL_ONLY, False),  # wider
    ],
)
def test_reader_flag_only_narrows_around_delegates(ambient: str, flag: str, ok: bool) -> None:
    env = {"SCOUT_READER": ambient}
    if ok:
        assert resolve_reader(flag, env) == flag
    else:
        with pytest.raises(typer.BadParameter):
            resolve_reader(flag, env)


# ----- policy keys (spec §4.5) -------------------------------------------------------------


def test_delegates_parse_with_only_a_literal_true_widening() -> None:
    policy = policy_from_overlay(
        {
            "delegates": {
                "grok-personal": {},
                "duet": {"include_private": True},
                "yes-string": {"include_private": "true"},
                "one": {"include_private": 1},
                "bare": None,
            }
        }
    )
    got = {d.id: d.include_private for d in policy.delegates}
    assert got == {"grok-personal": False, "duet": True, "yes-string": False, "one": False, "bare": False}
    assert "privacy-overlay-invalid" in policy.warnings  # the two non-boolean include_private values


@pytest.mark.parametrize(
    "overlay",
    [
        {"delegates": ["grok-personal"]},
        {"delegates": {"_defaults": {}}},
        {"delegates": {"Grok": {}}},
        {"delegates": {"grok": ["include_private"]}},
    ],
)
def test_bad_delegate_entries_are_dropped_with_a_warning(overlay: dict[str, Any]) -> None:
    policy = policy_from_overlay(overlay)
    assert policy.delegates == ()
    assert "privacy-overlay-invalid" in policy.warnings


def test_the_policy_json_names_delegates_only_when_set() -> None:
    assert "delegates" not in policy_from_overlay(None).to_json()
    data = policy_from_overlay({"delegates": {"duet": {"include_private": True}, "grok": {}}}).to_json()
    assert data["delegates"] == {"duet": {"include_private": True}, "grok": {"include_private": False}}


def test_an_unreadable_config_lists_no_delegates() -> None:
    # A delegate widens what a reader sees, so it never applies from a file that doesn't load.
    text = "user: [unclosed\nprivacy:\n  delegates:\n    duet:\n      include_private: true\n"
    policy = policy_from_config_text(text)
    assert policy.delegates == ()
    assert "privacy-overlay-unreadable" in policy.warnings


# ----- visible_to ---------------------------------------------------------------------------


POLICY = policy_from_overlay({"delegates": {"grok-personal": {}, "duet": {"include_private": True}}})


@pytest.mark.parametrize(
    ("reader", "label", "seen"),
    [
        (NORMAL_ONLY, _label("a.md"), True),
        (NORMAL_ONLY, _label("a.md", "private"), False),
        (NORMAL_ONLY, _label("a.md", local_only=True), False),
        (WITH_PRIVATE, _label("a.md", "private"), True),
        (WITH_PRIVATE, _label("a.md", local_only=True), False),
        (UNLISTED, _label("a.md"), False),
        ("owner", _label("a.md", "private", local_only=True), True),
        ("agent:person:priya@example.com", _label("a.md"), False),
    ],
)
def test_visible_to_for_delegates(reader: str, label: Label, seen: bool) -> None:
    assert visible_to(label, reader, POLICY) is seen


def test_visible_to_without_a_policy_shows_a_delegate_nothing() -> None:
    assert visible_to(_label("a.md"), NORMAL_ONLY) is False
    assert visible_to(_label("a.md"), "owner") is True


def test_reads_any() -> None:
    assert privacy.reads_any("owner") and privacy.reads_any(NORMAL_ONLY)
    assert not privacy.reads_any("agent:person:priya@example.com")
    assert not privacy.reads_any(FAIL_CLOSED_READER)


# ----- the kb read commands over the fixture vault ---------------------------------------------


def _no_private(out: str) -> None:
    """No private content: no canary, no private path, no private label, no private alias.

    The query word itself may come back ("Entity 'sam' not found"): that is the asker's own text.
    """
    for canary in CANARIES.values():
        assert canary not in out
    assert "people/sam" not in out and "[private]" not in out and '"private"' not in out, out
    assert re.search(r"\bsammy\b", out, re.IGNORECASE) is None, out


def test_a_normal_only_delegate_sees_normal_notes_labelled(vault: Path) -> None:
    files = kb_json(vault, "files", reader=NORMAL_ONLY)
    paths = {r["path"] for r in files}
    assert {PRIYA, GIFT, "inbox.md"} <= paths
    assert not paths & set(CANARIES)
    assert all(r["label"]["visibility"] == "normal" for r in files)

    assert kb_json(vault, "search", "CANARY-PRIV", reader=NORMAL_ONLY) == []
    assert kb(vault, "read", PRIYA, reader=NORMAL_ONLY).exit_code == 0
    denied = kb(vault, "read", SAM, reader=NORMAL_ONLY)
    assert denied.exit_code == 3 and CANARIES[SAM] not in denied.output
    assert kb(vault, "read", TRADING, reader=NORMAL_ONLY).exit_code == 3

    assert kb(vault, "resolve", "sam", reader=NORMAL_ONLY).exit_code == 1
    assert kb_json(vault, "query", "--visibility", "private", reader=NORMAL_ONLY) == []
    tasks = kb_json(vault, "query", "--type", "task", "--domain", "personal", reader=NORMAL_ONLY)
    assert {t["name"] for t in tasks} == {"task-buy-gift"}


@pytest.mark.parametrize(
    "args",
    [["related", "priya"], ["traverse", "priya"], ["export"], ["entity", "sam"], ["name-lookup", "Sam"]],
    ids=lambda a: " ".join(a),
)
def test_a_normal_only_delegate_never_reaches_a_private_entity_through_the_graph(vault: Path, args: list[str]) -> None:
    _no_private(kb(vault, *args, reader=NORMAL_ONLY).output)


def test_a_path_to_a_private_entity_reads_like_one_to_no_entity(vault: Path) -> None:
    hidden = kb(vault, "path", "priya", "sam", reader=NORMAL_ONLY)
    missing = kb(vault, "path", "priya", "nobody", reader=NORMAL_ONLY)
    assert hidden.output == "No path from 'priya' to 'sam'.\n"
    assert (hidden.exit_code, hidden.output.replace("sam", "nobody")) == (missing.exit_code, missing.output)


def test_a_delegate_with_private_sees_private_but_never_local_only(vault: Path) -> None:
    paths = {r["path"] for r in kb_json(vault, "files", reader=WITH_PRIVATE)}
    assert set(PRIVATE_CANARIES) <= paths
    assert TRADING not in paths
    got = kb_json(vault, "read", SAM, reader=WITH_PRIVATE)
    assert got["label"]["visibility"] == "private" and CANARIES[SAM] in got["content"]
    assert kb(vault, "read", TRADING, reader=WITH_PRIVATE).exit_code == 3
    assert "sam" in {e["name"] for e in kb_json(vault, "query", "--visibility", "private", reader=WITH_PRIVATE)}


@pytest.mark.parametrize(
    "args", [["files"], ["search", "CANARY-PRIV"], ["query"], ["related", "priya"], ["export"]], ids=" ".join
)
def test_an_unlisted_delegate_sees_nothing(vault: Path, args: list[str]) -> None:
    out = kb(vault, *args, reader=UNLISTED).output
    for canary in CANARIES.values():
        assert canary not in out
    assert ".md" not in out


def test_classify_answers_a_delegate_only_for_what_it_sees(vault: Path) -> None:
    rows = kb_json(vault, "classify", PRIYA, SAM, reader=NORMAL_ONLY)
    assert rows[0]["path"] == PRIYA and rows[0]["visibility"] == "normal"
    assert rows[1] == {"path": SAM, "denied": True}


def test_delta_drops_private_paths_for_a_delegate(vault: Path) -> None:
    commits = kb_json(vault, "delta", "--since", "52w", reader=NORMAL_ONLY)
    subjects = [c["subject"] for c in commits]
    assert "private only" not in subjects
    assert "private and normal" in subjects and "normal only" in subjects
    seen = {p["path"] for c in commits for p in c["paths"]}
    assert not seen & set(CANARIES)


# ----- the kb-read gate (a delegate session in Claude Code) -----------------------------------------


def _read(vault: Path, rel: str, reader: str) -> dict[str, Any] | None:
    body = {"tool_name": "Read", "tool_input": {"file_path": str(vault / rel)}, "cwd": str(vault), "session_id": "s"}
    return gate.run(stdin=io.StringIO(json.dumps(body)), env={"SCOUT_READER": reader, "SCOUT_DATA_DIR": str(vault)})


def test_the_read_gate_lets_a_delegate_read_only_what_kb_shows_it(vault: Path) -> None:
    assert _read(vault, PRIYA, NORMAL_ONLY) is None
    assert _read(vault, SAM, NORMAL_ONLY) is not None
    assert _read(vault, SAM, WITH_PRIVATE) is None
    assert _read(vault, PRIYA, UNLISTED) is not None


# ----- pre-session data --------------------------------------------------------------------------


ROLLOUT = "knowledge-base/topics/billing/rollout.md"
BILLING = "knowledge-base/topics/billing/billing.md"


def _dated_vault(tmp_path: Path) -> Path:
    """The fixture with a "Last updated" line in a normal and a private note (pre-session lists only dated notes)."""
    vault = build_privacy_vault(tmp_path)
    config = vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + DELEGATES_BLOCK, encoding="utf-8")
    for rel in (ROLLOUT, BILLING):
        note = vault / rel
        lines = note.read_text(encoding="utf-8").split("\n")
        lines.insert(lines.index("---", 1) + 1, "Last updated: 2026-11-01")
        note.write_text("\n".join(lines), encoding="utf-8")
    clear_frontmatter_cache()
    return vault


def test_pre_session_data_for_a_delegate_has_no_private_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _dated_vault(tmp_path)
    monkeypatch.setattr(psd, "get_pr_authored", list)
    monkeypatch.setattr(psd, "get_pr_review_requested", list)
    monkeypatch.setenv("SCOUT_READER", "owner")
    assert {ROLLOUT, BILLING} <= set(psd.gather("morning-briefing", scout_dir=vault).kb_file_dates)

    monkeypatch.setenv("SCOUT_READER", NORMAL_ONLY)
    ctx = psd.gather("morning-briefing", scout_dir=vault)
    dumped = json.dumps(ctx.__dict__, default=str)
    for canary in CANARIES.values():
        assert canary not in dumped
    assert ROLLOUT in ctx.kb_file_dates and BILLING not in ctx.kb_file_dates
    assert all(v["visibility"] == "normal" for v in ctx.kb_file_dates.values())
    assert [t["name"] for t in ctx.personal_tasks] == ["task-buy-gift"]
    assert ctx.linear_own_activity_status == "skipped"
    assert ctx.run_notes_before == [] and ctx.past_due_tasks == []
    assert all("private only" not in row["subject"] for row in ctx.git_recent)


# ----- the write guard (spec §6.5) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["kb", "files"],
        ["kb", "read", "x.md", "--json"],
        ["kb", "name-lookup", "Sam"],
        ["action-items", "list"],
        ["pre-session", "data"],
        ["bootstrap", "doctor", "--json"],
        ["hook", "kb-read-gate"],
        ["version"],
        ["--help"],
        ["action-items", "mark-done", "--help"],
    ],
)
def test_a_delegate_may_run_read_commands(argv: list[str]) -> None:
    assert cli.delegate_write_refusal(argv, {"SCOUT_READER": NORMAL_ONLY}) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["action-items", "mark-done", "OPS-1"],
        ["action-items", "render"],
        ["kb", "share", "knowledge-base/topics"],
        ["kb", "lint"],
        ["bootstrap", "install"],
        ["schedule", "tick"],
        ["repo", "sync"],
        ["notify", "telegram", "hi"],
        ["self-update", "check"],
        ["phases", "backport"],
        ["kb"],
        [],
    ],
)
def test_a_delegate_is_refused_every_other_command(argv: list[str]) -> None:
    message = cli.delegate_write_refusal(argv, {"SCOUT_READER": NORMAL_ONLY})
    assert message is not None and "delegate" in message and "inbox" in message


@pytest.mark.parametrize("env", [{}, {"SCOUT_READER": "autonomous"}, {"SCOUT_MODE": "consolidation"}])
def test_the_write_guard_leaves_other_readers_alone(env: dict[str, str]) -> None:
    assert cli.delegate_write_refusal(["action-items", "mark-done", "OPS-1"], env) is None


def test_main_refuses_a_delegate_write_with_exit_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCOUT_READER", NORMAL_ONLY)
    monkeypatch.setattr("sys.argv", ["scoutctl", "action-items", "mark-done", "OPS-1"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 3
    assert "delegate" in capsys.readouterr().err
