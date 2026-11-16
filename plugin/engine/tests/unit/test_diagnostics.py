"""`scoutctl diagnostics`: a bug-report bundle that never carries vault content (app audit card C11).

The canary tests build a synthetic vault where every kind of owner content
carries a unique string: note names and bodies, folders (a dot-folder
included), the owner, delegates, custom connectors, transcripts, run logs,
secrets, a custom slot key, odd run-ledger values and a self-update's error
text. They then make every section that could quote them do so. The injected
sandbox and runtime stubs return raw text, so the report's own scrubbing is
what's tested. Finally they assert none of the strings reaches the report.
"""

from __future__ import annotations

import getpass
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli, diagnostics
from scout.diagnostics import ScrubContext, build_report, scrub

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

# One unique string per kind of vault content. None of these may appear in a report.
CANARIES = {
    "note_body": "CANARYBODYqv7",
    "note_name": "Zephyrine Canarywood",
    "note_name_nospace": "canarynote-kx2",
    "folder": "Project Quillfeather",
    "dot_folder_note": "Canarytrash Merger",
    "owner_name": "Ottoline Canarysmith",
    "owner_email": "ottoline.canary@example.com",
    "delegate": "canarydelegate",
    "custom_connector": "canaryconnector",
    "transcript": "CANARYTRANSCRIPTzz1",
    "run_log": "CANARYRUNLOGpp4",
    "token": "sk-ant-oat01-CANARYTOKENabcdefghijklmnopqrstuvwxyz0123456789",
    "github_token": "ghp_CANARYabcdefghijklmnopqrstuvwxyz012345",
    "slot_key": "canaryslot-acmecorp",
    "failure_class": "canaryclass Jane Private",
    "sandbox_value": "canarysandboxvalue",
    "integrity_value": "canaryintegrityvalue",
    "update_detail": "CANARYUPDATEdetail77",
    "exception": "CANARYEXCEPTIONx9",
    "missing_folder": "Old Canaryproject",
    "missing_file": "Kickoff Canarynotes",
    "home_document": "Acme Canarycontract",
    "encoded_folder": "Canaryenc Folder",
    "query_token": "canaryquerytoken123",
}
# Words of the multi-word canaries, which must not leak on their own either.
CANARY_WORDS = (
    "Zephyrine",
    "Canarywood",
    "Quillfeather",
    "Canarytrash",
    "Ottoline",
    "Canarysmith",
    "Canaryproject",
    "Canarynotes",
    "Canarycontract",
    "Canaryenc",
)


def _synthetic_vault(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "canaryhome-user"
    vault = home / "Scout"
    (vault / ".scout-state" / "last-assembled").mkdir(parents=True)
    (vault / ".scout-state" / "schedule.yaml").write_text(
        "schema_version: 1\nslots:\n"
        f"  {CANARIES['slot_key']}:\n    type: briefing\n    weekdays: [mon]\n    fires_at_local: '07:00'\n"
        "    on_miss: fire\n    runner: run-scout.sh\n    missed_window_hours: 4\n    cooldown_minutes: 0\n"
    )
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        (vault / f"{name}.md").write_text(f"# {name}\n")
        (vault / ".scout-state" / "last-assembled" / f"{name}.md").write_text(f"# {name}\n")
    (vault / "scout-config.yaml").write_text(
        f"user:\n  name: {CANARIES['owner_name']}\n  email: {CANARIES['owner_email']}\n"
        "plugin:\n  version_at_last_setup: '0.18.0'\n  version_at_last_update: '0.18.0'\n"
    )
    for rel in (
        "scripts/heartbeat.sh",
        "knowledge-base/ontology/parser.py",
        "action-items/render.py",
        "hooks/kb-pre-filter.sh",
    ):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text("# engine file\n")

    # Notes: a name with spaces, a name without, a folder, a body, and a note in a dot-folder.
    people = vault / "knowledge-base" / "people"
    people.mkdir(parents=True)
    (people / f"{CANARIES['note_name']}.md").write_text(f"# {CANARIES['note_name']}\n{CANARIES['note_body']}\n")
    (people / f"{CANARIES['note_name_nospace']}.md").write_text(f"[[{CANARIES['note_name']}]]\n")
    project = vault / "knowledge-base" / CANARIES["folder"]
    project.mkdir()
    (project / "kickoff.md").write_text(f"{CANARIES['note_body']}\n")
    (vault / ".trash").mkdir()
    (vault / ".trash" / f"{CANARIES['dot_folder_note']}.md").write_text(f"{CANARIES['note_body']}\n")

    # A custom connector the doctor complains about by name.
    (vault / "connectors.custom.yaml").write_text(
        f"schema_version: 1\nconnectors:\n  {CANARIES['custom_connector']}:\n    bogus: true\n"
    )
    # A delegate mirror state file whose signature doesn't verify.
    (vault / ".scout-state" / "delegate-mirrors.json").write_text(
        json.dumps({"links": {CANARIES["delegate"]: {"remote": "git@github.com:someone/private.git"}}})
    )
    # A transcript in the vault and in the home folder.
    (vault / ".claude" / "projects").mkdir(parents=True)
    (vault / ".claude" / "projects" / "s.jsonl").write_text(json.dumps({"text": CANARIES["transcript"]}) + "\n")
    (home / ".claude" / "projects" / "-x").mkdir(parents=True)
    (home / ".claude" / "projects" / "-x" / "t.jsonl").write_text(CANARIES["transcript"] + "\n")
    # A secret where Scout keeps them.
    (home / ".scout-secrets").mkdir()
    (home / ".scout-secrets" / "claude-oauth-token").write_text(CANARIES["token"] + "\n")

    # Failed runs whose log names its error with vault content in it: the doctor quotes that line.
    logs = vault / ".scout-logs"
    logs.mkdir()
    log_name = "scout-2026-11-16_08-00.log"
    (logs / log_name).write_text(
        f"=== start ===\nreading {vault}/knowledge-base/people/{CANARIES['note_name']}.md\n"
        f"Failed to authenticate. API Error: 401 {CANARIES['run_log']} {CANARIES['token']}\n"
    )
    started = int(NOW.timestamp()) - 3600
    rows: list[dict] = [
        {
            "slot": "morning-briefing",
            "mode": "morning-briefing",
            "started_at": started - 86400 * i,
            "finished_at": started - 86400 * i + 60,
            "exit_code": 1,
            "outcome": "failed",
            "failure_class": "oauth_expired",
            "log": log_name,
            "commits_in_window": 0,
            "newest_commit": "0123456789abcdef0123456789abcdef01234567",
        }
        for i in range(3, -1, -1)
    ]
    # An older run of the custom slot whose free-form fields hold content.
    rows.insert(
        0,
        {
            "slot": CANARIES["slot_key"],
            "mode": CANARIES["slot_key"],
            "started_at": started - 86400 * 5,
            "finished_at": started - 86400 * 5 + 60,
            "exit_code": 1,
            "outcome": "failed",
            "failure_class": CANARIES["failure_class"],
            "log": None,
            "sandbox": CANARIES["sandbox_value"],
            "integrity": CANARIES["integrity_value"],
        },
    )
    (logs / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    # A failed self-update whose error text the doctor quotes in its summary.
    (logs / "update-outcomes.jsonl").write_text(
        json.dumps(
            {
                "at": started,
                "from": "0.18.0",
                "target": "0.18.14",
                "managed_by": "scout-app",
                "result": "failed",
                "step": "fetch",
                "detail": f"fetch failed: {CANARIES['update_detail']} in {vault}/{CANARIES['missing_folder']}",
                "log": None,
            }
        )
        + "\n"
    )
    return vault, home


def _raw_sandbox(vault: Path) -> dict:
    """Stands in for the real sandbox check. Returns raw text: build_report must scrub it."""
    home = vault.parent
    reason = (
        f"can't write {vault}/knowledge-base/people/{CANARIES['note_name']}.md for {CANARIES['owner_email']},"
        f" {vault}/knowledge-base/{CANARIES['folder']}/kickoff.md,"
        f" {vault}/.trash/{CANARIES['dot_folder_note']}.md,"
        f" {vault}/{CANARIES['missing_folder']}/{CANARIES['missing_file']}.txt"
    )
    return {"status": "failed", "reason": f"{reason}; also {home}/Documents/{CANARIES['home_document']}.pdf"}


def _raw_runtime(vault: Path) -> dict:
    encoded = CANARIES["encoded_folder"].replace(" ", "%20")
    reason = (
        f"token {CANARIES['token']} {CANARIES['github_token']} refused for {CANARIES['delegate']}"
        f" via {CANARIES['custom_connector']}; see https://example.com/x?access_token={CANARIES['query_token']}"
        f" and file://{vault}/{encoded}/x.txt as {getpass.getuser()}"
    )
    return {"runtime": "claude-code", "reason": reason}


def _raising_runtime(vault: Path) -> dict:
    raise RuntimeError(f"boom {CANARIES['exception']} {vault}/{CANARIES['note_name']}.md")


def _assert_no_canary(text: str) -> None:
    for kind, canary in CANARIES.items():
        assert canary not in text, f"{kind} canary leaked: {canary!r}"
    for word in CANARY_WORDS:
        assert word not in text, f"{word!r} leaked"
    assert getpass.getuser() not in text, "the login name leaked"


def test_report_never_carries_vault_content(tmp_path: Path) -> None:
    vault, home = _synthetic_vault(tmp_path)
    report = build_report(vault, home=home, now=NOW, check_jobs=False, sandbox=_raw_sandbox, runtime=_raw_runtime)
    text = json.dumps(report)
    _assert_no_canary(text)
    assert str(home) not in text
    # It is still a useful report: the doctor ran, the failing runs show, structure is kept.
    doctor = report["doctor"]
    assert doctor["severity"] in {"yellow", "red"}
    findings = doctor["run_health"]["findings"]
    assert any(f["has_detail"] for f in findings)
    assert all("detail" not in f and "log" not in f for f in findings)
    assert any(f["id"].startswith("update.") for f in findings), "the self-update detail vector wasn't exercised"
    assert report["sandbox"]["reason"].startswith("can't write <vault>/<path>")
    # Each owner path runs to the next placeholder, swallowing the words between: nothing of them is left.
    assert report["sandbox"]["reason"] == "can't write " + "<vault>/<path>" * 4
    assert "see <url> and <vault>/<path>" in report["runtime"]["reason"]


def test_ledger_and_slot_fields_are_allowlisted(tmp_path: Path) -> None:
    vault, home = _synthetic_vault(tmp_path)
    report = build_report(vault, home=home, now=NOW, check_jobs=False, sandbox=_raw_sandbox, runtime=_raw_runtime)
    custom = report["recent_runs"][-1]
    assert custom["slot"] == "custom-1"
    assert custom["mode"] == "custom-1"
    assert custom["failure_class"] == diagnostics.OTHER
    assert custom["sandbox"] == diagnostics.OTHER
    assert custom["integrity"] == diagnostics.OTHER
    assert custom["outcome"] == "failed"
    assert [r["slot"] for r in report["recent_runs"][:-1]] == ["morning-briefing"] * 4
    assert report["vault"]["custom_slots"] == 1
    slot_keys = {s["key"] for s in report["doctor"]["run_health"]["slots"]}
    assert CANARIES["slot_key"] not in slot_keys
    assert slot_keys <= {"custom-1", diagnostics.OTHER} | diagnostics._DEFAULT_SLOTS


def test_failed_section_names_only_the_exception_type(tmp_path: Path) -> None:
    vault, home = _synthetic_vault(tmp_path)
    report = build_report(vault, home=home, now=NOW, check_jobs=False, sandbox=_raw_sandbox, runtime=_raising_runtime)
    assert report["runtime"] == {"unavailable": "RuntimeError"}
    _assert_no_canary(json.dumps(report))


def test_cli_report_never_carries_vault_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real command, with the real sandbox and runtime sections, against the synthetic vault."""
    vault, home = _synthetic_vault(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    result = CliRunner().invoke(cli.app, ["diagnostics", "--json", "--no-jobs"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["schema"] == diagnostics.SCHEMA_VERSION
    _assert_no_canary(result.output)
    assert str(home) not in result.output
    assert str(Path.home()) not in result.output, "the real home leaked when $HOME pointed elsewhere"


def test_runtime_section_starts_nothing_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No gate probe, no `claude --version`: the export reads the probe cache only."""
    vault, home = _synthetic_vault(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    import subprocess

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"diagnostics started a process: {args!r}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    before = sorted(p.relative_to(vault) for p in vault.rglob("*"))
    section = diagnostics._runtime_section(vault)
    assert section["runtime"] == "claude-code"
    assert section["gate_probe"] is None
    assert sorted(p.relative_to(vault) for p in vault.rglob("*")) == before


def test_text_form_never_carries_vault_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault, home = _synthetic_vault(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    result = CliRunner().invoke(cli.app, ["diagnostics", "--no-jobs"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("scout engine ")
    _assert_no_canary(result.output)


def test_missing_vault_still_reports(tmp_path: Path) -> None:
    report = build_report(tmp_path / "nope", home=tmp_path, now=NOW, check_jobs=False)
    assert report["vault"]["exists"] is False
    assert report["doctor"]["severity"] == "red"
    assert report["doctor"]["errors"] == ["vault directory missing: <vault>"]
    assert report["sandbox"] is None and report["recent_runs"] == []


def _ctx(tmp_path: Path, *names: str) -> ScrubContext:
    ctx = ScrubContext()
    ctx.add_path(tmp_path / "Scout", "<vault>")
    ctx.add_path(tmp_path, "~")
    for n in names:
        ctx.add(n, "<name>")
    return ctx.finish()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Scout's own folders and engine files stay readable.
        ("{v}/.scout-logs/scout-2026-11-16_08-00.log", "<vault>/.scout-logs/scout-2026-11-16_08-00.log"),
        ("missing {v}/.scout-state/schedule.yaml", "missing <vault>/.scout-state/schedule.yaml"),
        ("kept {v}/.scout-logs/a.log and more", "kept <vault>/.scout-logs/a.log and more"),
        ("cat-1 file missing: scripts/heartbeat.sh", "cat-1 file missing: scripts/heartbeat.sh"),
        ("{v}/SKILL.md is stale", "<vault>/SKILL.md is stale"),
        ("{v}/knowledge-base/ontology/parser.py", "<vault>/knowledge-base/ontology/parser.py"),
        # A note's path is not, spaces and all, to the end of the path.
        ("{v}/knowledge-base/people/Alex.md", "<vault>/<path>"),
        ("open {v}/Old Project/Kickoff Notes.txt", "open <vault>/<path>"),
        ("'{v}/Old Project/x.txt' failed", "'<vault>/<path>' failed"),
        # Other dot-folders are the owner's.
        ("{v}/.trash/Secret Merger.md", "<vault>/<path>"),
        ("{v}/.claude/projects/-Users-jane-Vault/a.jsonl", "<vault>/<path>"),
        # In .git only lock, HEAD and config files are Scout's; a ref can name the owner's work.
        ("{v}/.git/index.lock: File exists", "<vault>/.git/index.lock: File exists"),
        ("{v}/.git/refs/heads/acme-merger-notes is locked by Jane Roe", "<vault>/<path>"),
        # External volumes and temporary folders.
        ("/Volumes/Work Drive/Secret Plans/Q3 Board.pdf could not be read", "/Volumes/<path>"),
        ("/private/tmp/Acme Board Deck.key", "/private/<path>"),
        ("/tmp/Acme Board Deck.key", "/tmp/<path>"),
        ("/usr/bin/git failed", "/usr/bin/git failed"),
        # Scout's own vocabulary stays readable when quoted.
        (
            "invalid weekday 'funday'; must be one of ['fri', 'mon']",
            "invalid weekday '<quoted>'; must be one of ['fri', 'mon']",
        ),
        ("see notes/alex.md", "see <note>.md"),
        ("linked from [[Some Note]]", "linked from [[<note>]]"),
        ("note 'Board Meeting Prep' has no title", "note '<quoted>' has no title"),
        ("it's Alex's", "it's Alex's"),
        # Home, other users, encoded folders, file URLs, percent-encoding.
        ("{h}/Library/Caches/com.scout.Scout/x.json", "~/Library/Caches/com.scout.Scout/<path>"),
        ("{h}/.local/share/scout/engine/current", "~/.local/share/scout/engine/<path>"),
        (
            "{h}/Library/Application Support/Scout/vaults/Acme Plan/state",
            "~/Library/Application Support/Scout/vaults/<path>",
        ),
        ("{h}/Library/Caches/Other App/x.json", "~/<path>"),
        ("{h}/Library/Application Support/Scout/engine x", "~/Library/Application Support/Scout/engine x"),
        ("{h}/Documents/Acme Contract.pdf", "~/<path>"),
        ("/Users/jane/My Vault/x.txt", "/Users/<user>/<path>"),
        ("/Users/jane/.local/bin/claude", "/Users/<user>/.local/bin/claude"),
        ("-Users-jane-Vault", "-Users-<user>-Vault"),
        ("file:///Users/jane/My%20Vault/x.txt", "/Users/<user>/<path>"),
        ("{v}\\/Old Project\\/x.txt", "<vault>/<path>"),
        # Emails, URLs, remotes, tokens.
        ("ask alex@example.com", "ask <email>"),
        ("pushed to https://example.com/private/repo", "pushed to <url>"),
        ("see https://github.com/Raven-Scout/Scout/issues/1", "see https://github.com/Raven-Scout/Scout/issues/1"),
        ("https://github.com/Raven-Scout/Scout/issues?access_token=abc", "https://github.com/Raven-Scout/Scout/issues"),
        ("push to git@github.com:jane/acme-notes.git failed", "push to <email>:<repo> failed"),
        ("key sk-ant-api03-abcdef", "key <redacted>"),
        ("api_key=AbC123xyz789", "api_key=<redacted>"),
        ("Bearer abc.def", "Bearer <redacted>"),
        ("id 0123456789abcdef0123456789abcdef", "id <redacted>"),
    ],
)
def test_scrub_rules(tmp_path: Path, raw: str, expected: str) -> None:
    ctx = _ctx(tmp_path)
    raw = raw.format(v=tmp_path / "Scout", h=tmp_path)
    assert scrub(raw, ctx) == expected


def test_scrub_is_idempotent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "Acme Deal")
    once = scrub(f"open {tmp_path}/Scout/Old Project/x.txt for Acme Deal at {tmp_path}/Documents/x", ctx)
    assert scrub(once, ctx) == once


def test_scrub_names(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "Acme Deal", "notes", "Riverbend")
    # Distinctive names go anywhere, as whole words.
    assert scrub("runs failed for Acme Deal and Riverbend", ctx) == "runs failed for <name> and <name>"
    # A plain short word is hidden only as a path segment, so ordinary text survives.
    assert scrub("release notes", ctx) == "release notes"
    assert scrub("in notes/x and x/notes", ctx) == "in <name>/x and x/<name>"
    assert scrub("see notes/alex.md", ctx) == "see <note>.md"


def test_scrub_matches_names_in_any_unicode_form(tmp_path: Path) -> None:
    import unicodedata

    ctx = _ctx(tmp_path, "Zoë Quill")
    assert scrub("linked " + unicodedata.normalize("NFD", "Zoë Quill"), ctx) == "linked <name>"


def test_scrub_truncates_long_strings(tmp_path: Path) -> None:
    out = scrub("word " * 200, _ctx(tmp_path))
    assert len(out) == diagnostics.MAX_STRING + 1 and out.endswith("…")


def test_short_ledger_values_never_rewrite_other_words(tmp_path: Path) -> None:
    """D1: an odd failure class like "auth" or "crash" must not be replaced inside ids, enums or prose."""
    vault, home = _synthetic_vault(tmp_path)
    logs = vault / ".scout-logs"
    started = int(NOW.timestamp()) - 7 * 86400
    old = [
        {
            "slot": "morning-briefing",
            "mode": "morning-briefing",
            "started_at": started - i * 60,
            "finished_at": started - i * 60 + 30,
            "exit_code": 1,
            "outcome": "failed",
            "failure_class": fc,
        }
        for i, fc in enumerate(("auth", "crash"))
    ]
    (logs / "run-outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in old) + "\n" + (logs / "run-outcomes.jsonl").read_text()
    )
    report = build_report(vault, home=home, now=NOW, check_jobs=False, sandbox=_raw_sandbox, runtime=_raw_runtime)
    text = json.dumps(report)
    assert "o<other>_expired" not in text and "<other>.expired" not in text
    assert "auth.expired" in text and "oauth_expired" in text
    assert "claude auth login" in text
    assert [r["failure_class"] for r in report["recent_runs"][-2:]] == [diagnostics.OTHER, diagnostics.OTHER]
    _assert_no_canary(text)


def test_finding_ids_alias_the_slot_they_name() -> None:
    aliases = {"acme-renewal-prep": "custom-1"}
    assert diagnostics._finding_id("slot.stale:acme-renewal-prep", aliases) == "slot.stale:custom-1"
    assert diagnostics._finding_id("slot.stale:morning-briefing", aliases) == "slot.stale:morning-briefing"
    assert diagnostics._finding_id("lane.dark:dreaming", aliases) == "lane.dark:dreaming"
    assert diagnostics._finding_id("lane.dark:Acme Lane", aliases) == "lane.dark:<other>"
    assert diagnostics._finding_id("runs.failing", aliases) == "runs.failing"


def test_people_and_non_ascii_names_are_hidden_anywhere(tmp_path: Path) -> None:
    """D2: a short person's name and a CJK name are hidden in free text, not only next to a "/"."""
    vault = tmp_path / "Scout"
    people = vault / "knowledge-base" / "people"
    people.mkdir(parents=True)
    for name in ("Priya", "王小明", "Zoë"):
        (people / f"{name}.md").write_text("x")
    (vault / "knowledge-base" / "Acme").mkdir()
    ctx = diagnostics.build_context(vault, tmp_path)
    assert scrub("note Priya changed", ctx) == "note <name> changed"
    assert scrub("note 王小明 changed", ctx) == "note <name> changed"
    assert scrub("王小明的笔记", ctx) == "<name>的笔记"
    assert scrub("ask Zoë", ctx) == "ask <name>"
    # A short plain folder name elsewhere still only goes as a path segment.
    assert scrub("folder Acme changed", ctx) == "folder Acme changed"


def test_unknown_keys_are_scrubbed_known_ones_kept(tmp_path: Path) -> None:
    ctx = diagnostics.build_context(None, tmp_path)
    ctx.add("Zephyr Acme Renewal", "<name>")
    ctx.finish()
    out = diagnostics.scrub_tree({"doctor": {"Zephyr Acme Renewal": "x", "warnings": ["Zephyr Acme Renewal"]}}, ctx)
    assert out == {"doctor": {"<key-1>": "x", "warnings": ["<name>"]}}
    # A key that isn't the report's is replaced even when nothing marks it as content.
    assert diagnostics.scrub_tree({"Unknown Client": 1}, ctx) == {"<key-1>": 1}
    # Allowlisted fields are never rewritten.
    assert diagnostics.scrub_tree({"failure_class": "oauth_expired"}, ctx) == {"failure_class": "oauth_expired"}


def test_report_uses_only_known_keys(tmp_path: Path) -> None:
    vault, home = _synthetic_vault(tmp_path)
    report = build_report(vault, home=home, now=NOW, check_jobs=False)

    def keys(v: object, out: set[str]) -> set[str]:
        if isinstance(v, dict):
            for k, x in v.items():
                out.add(k)
                keys(x, out)
        elif isinstance(v, list):
            for x in v:
                keys(x, out)
        return out

    assert keys(report, set()) <= diagnostics.REPORT_KEYS
