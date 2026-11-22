"""Doctor warnings ranked by risk, and the owner's reviews of them (UI audit R13, #505): scout.doctor_triage."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import doctor_triage
from scout.doctor_triage import WarningMeta
from scout.kb import hook_paths
from scout.scripts.bootstrap_auto import doctor_dict
from scout.scripts.bootstrap_doctor import run_doctor

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    for rel in (
        "scripts/heartbeat.sh",
        "knowledge-base/ontology/parser.py",
        "action-items/render.py",
        "hooks/kb-pre-filter.sh",
    ):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n", encoding="utf-8")
    (v / ".scout-state" / "last-assembled").mkdir(parents=True)
    for name in ("SKILL", "DREAMING", "RESEARCH", "RESPOND"):
        (v / ".scout-state" / "last-assembled" / f"{name}.md").write_text("x\n", encoding="utf-8")
    (v / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: {}\n", encoding="utf-8")
    (v / "scout-config.yaml").write_text(
        "plugin:\n  version_at_last_setup: 0.18.0\n  version_at_last_update: 0.18.0\n", encoding="utf-8"
    )
    # Two checkers whose output stays in the vault, and one script that posts what it reads.
    for name in ("link-check.py", "tag-audit.py"):
        (v / "scripts" / name).write_text("open('knowledge-base/index.md').read()\n", encoding="utf-8")
    (v / "scripts" / "weekly-digest.py").write_text(
        "import requests\nrequests.post(URL, data=open('knowledge-base/index.md').read())\n", encoding="utf-8"
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    for var in ("SCOUT_MODE", "SCOUT_READER"):
        monkeypatch.delenv(var, raising=False)
    return v


def _doctor(vault: Path, home: Path) -> dict:
    return doctor_dict(run_doctor(vault=vault, check_jobs=False, home=home, now=NOW)) or {}


def _entry(payload: dict, kind: str) -> dict:
    return next(e for e in payload["warning_triage"] if e["kind"] == kind)


# -- ranking -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "import requests\n",
        "curl -X POST https://hooks.example.com/x\n",
        "subprocess.run(['scoutctl', 'notify', 'telegram'])\n",
        'gh issue create --body "$(cat knowledge-base/x.md)"\n',
        'claude -p "summarise $(cat knowledge-base/x.md)"\n',
        "open('drafts/digest.md', 'w')\n",
        "smtplib.SMTP(host).sendmail(a, b, body)\n",
        "import socket\n",
        "cat knowledge-base/x.md | nc host 9000\n",
        "scp out.txt host:/tmp/\n",
        "rsync -a out/ host:/srv/\n",
        "ssh host 'cat > x' < out.txt\n",
        "git push other main\n",
        "from ftplib import FTP\n",
        "osascript -e 'tell app \"Messages\" to send x'\n",
        '"$CLAUDE_BIN" -p "summarise"\n',
        "cp out.md ~/Library/Mobile\\ Documents/com~apple~CloudDocs/\n",
        "cp out.md ~/Dropbox/\n",
        "exec(open(helper).read())\n",
        "eval (payload)\n",
        "mod = importlib.import_module(name)\n",
        "m = __import__(name)\n",
        'source "$LIB"\n',
        '. "$HOME/lib.sh"\n',
        'bash -c "$CMD"\n',
    ],
)
def test_a_script_that_can_send_what_it_reads_is_high_risk(text: str) -> None:
    risk, why = doctor_triage.script_risk(text)
    assert risk == "high" and "may leave the vault" in why


def test_a_checker_is_low_risk_and_an_unreadable_script_high() -> None:
    assert doctor_triage.script_risk("for p in Path('knowledge-base').rglob('*.md'): check(p)\n")[0] == "low"
    assert doctor_triage.script_risk(None)[0] == "high"


def test_a_script_longer_than_scout_reads_is_high(tmp_path: Path) -> None:
    long = tmp_path / "long.py"
    long.write_text("x = 1\n" * (doctor_triage._READ_CAP // 6 + 10) + "requests.post(u)\n", encoding="utf-8")
    risk, why = doctor_triage.script_risk(doctor_triage.read_script(long))
    assert risk == "high" and "too long" in why


def test_a_script_that_uses_a_sending_helper_is_high(vault: Path) -> None:
    (vault / "scripts" / "sender.py").write_text("import requests\ndef send(x): requests.post(U, x)\n", "utf-8")
    (vault / "scripts" / "relay.sh").write_text("#!/bin/bash\nsource scripts/lib-post.sh\n", "utf-8")
    (vault / "scripts" / "lib-post.sh").write_text("post() { curl -d @- https://example.com; }\n", "utf-8")
    (vault / "scripts" / "link-check.py").write_text(
        "from sender import send\nsend(open('knowledge-base/index.md').read())\n", "utf-8"
    )
    ranks = doctor_triage.rank_vault_scripts(vault, ["scripts/link-check.py", "scripts/tag-audit.py"])
    assert ranks["scripts/link-check.py"] == (
        "high",
        "it uses scripts/sender.py, which may send what it is given out of the vault",
    )
    assert ranks["scripts/tag-audit.py"][0] == "low"


def test_a_script_another_vault_file_runs_is_high(vault: Path) -> None:
    (vault / "run-weekly.sh").write_text("#!/bin/bash\npython3 scripts/tag-audit.py | tee out.txt\n", "utf-8")
    ranks = doctor_triage.rank_vault_scripts(vault, ["scripts/tag-audit.py", "scripts/link-check.py"])
    assert ranks["scripts/tag-audit.py"] == (
        "high",
        "run-weekly.sh runs or uses it, so what it prints may be passed on",
    )
    assert ranks["scripts/link-check.py"][0] == "low"


def test_warnings_without_metadata_rank_by_what_they_are_about() -> None:
    assert doctor_triage.default_meta("privacy: 3 blocked outbound checks").risk == "high"
    assert doctor_triage.default_meta("gate probe failed (…)").risk == "high"
    assert doctor_triage.default_meta("runner backup present: run-scout.sh.bak.1").risk == "medium"


def test_the_doctor_ranks_its_warnings_high_first(vault: Path, home: Path) -> None:
    d = _doctor(vault, home)
    risks = [e["risk"] for e in d["warning_triage"]]
    assert risks == sorted(risks, key=doctor_triage.RISKS.index)
    high = _entry(d, "privacy.script-raw-kb-read")
    assert high["id"] == "privacy.script-raw-kb-read:scripts/weekly-digest.py"
    assert high["items"] == ["scripts/weekly-digest.py"] and high["reviewed_at"] is None
    low = _entry(d, "privacy.script-raw-kb-read.internal")
    assert low["risk"] == "low" and low["items"] == ["scripts/link-check.py", "scripts/tag-audit.py"]
    # The low-risk scripts are one grouped warning, apart from the one that posts.
    assert [g["kind"] for g in d["warning_groups"]] == ["privacy.script-raw-kb-read.internal"]
    assert {e["summary"] for e in d["warning_triage"]} == set(d["warnings"])
    assert all(len(e["fingerprint"]) == 16 for e in d["warning_triage"])


# -- reviews -----------------------------------------------------------------------------------------------------


def test_a_reviewed_warning_leaves_the_count_and_severity(vault: Path, home: Path) -> None:
    d = _doctor(vault, home)
    for kind in ("privacy.script-raw-kb-read", "privacy.script-raw-kb-read.internal"):
        e = _entry(d, kind)
        doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home, now=NOW)
    after = _doctor(vault, home)
    assert after["warnings"] == [] and after["warning_groups"] == [] and after["severity"] == "green"
    reviewed = [e for e in after["warning_triage"] if e["reviewed_at"]]
    assert {e["kind"] for e in reviewed} == {"privacy.script-raw-kb-read", "privacy.script-raw-kb-read.internal"}
    assert all(e["reviewed_at"] == "2026-11-19T09:00:00Z" for e in reviewed)


def test_editing_a_high_risk_script_brings_its_warning_back(vault: Path, home: Path) -> None:
    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home, now=NOW)
    assert _entry(_doctor(vault, home), "privacy.script-raw-kb-read")["reviewed_at"]
    with (vault / "scripts" / "weekly-digest.py").open("a", encoding="utf-8") as fh:
        fh.write("requests.post(OTHER_URL)\n")
    back = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    assert back["reviewed_at"] is None and back["fingerprint"] != e["fingerprint"]
    assert back["summary"] in _doctor(vault, home)["warnings"]


def test_any_edit_or_new_file_brings_a_low_risk_review_back(vault: Path, home: Path) -> None:
    def review() -> dict:
        e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read.internal")
        doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home, now=NOW)
        assert _entry(_doctor(vault, home), "privacy.script-raw-kb-read.internal")["reviewed_at"]
        return e

    review()
    # An edit with no sending marker in it (vault scripts are run-writable) still brings it back.
    with (vault / "scripts" / "link-check.py").open("a", encoding="utf-8") as fh:
        fh.write("print('ok')\n")
    assert _entry(_doctor(vault, home), "privacy.script-raw-kb-read.internal")["reviewed_at"] is None
    review()
    (vault / "scripts" / "orphans.py").write_text("open('knowledge-base/a.md').read()\n", encoding="utf-8")
    back = _entry(_doctor(vault, home), "privacy.script-raw-kb-read.internal")
    assert back["reviewed_at"] is None and "scripts/orphans.py" in back["items"]


def test_a_low_risk_script_that_starts_sending_moves_to_high(vault: Path, home: Path) -> None:
    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read.internal")
    doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home, now=NOW)
    (vault / "scripts" / "tag-audit.py").write_text(
        "import requests\nopen('knowledge-base/index.md').read()\n", encoding="utf-8"
    )
    d = _doctor(vault, home)
    assert "scripts/tag-audit.py" in _entry(d, "privacy.script-raw-kb-read")["items"]
    # The internal group changed too (one file left), so its review no longer holds either.
    assert all(e["reviewed_at"] is None for e in d["warning_triage"])


def test_a_text_only_warning_comes_back_when_its_text_changes(tmp_path: Path, home: Path) -> None:
    v = tmp_path / "v"
    v.mkdir()
    first = doctor_triage.apply(v, ["runner backup present: run-scout.sh.bak.1"], {}, home=home)
    (e,) = first.entries
    assert e.id.startswith("warning.") and e.risk == "medium"
    doctor_triage.set_review(v, e.id, e.fingerprint, home=home, now=NOW)
    assert doctor_triage.apply(v, [e.summary], {}, home=home).open_warnings == []
    again = doctor_triage.apply(v, ["runner backup present: run-scout.sh.bak.2"], {}, home=home)
    assert again.open_warnings == ["runner backup present: run-scout.sh.bak.2"]


def test_reviews_are_per_vault_and_undo_drops_them(tmp_path: Path, home: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    meta = {"x": WarningMeta(kind="install", risk="medium")}
    (e,) = doctor_triage.apply(a, ["x"], meta, home=home).entries
    doctor_triage.set_review(a, e.id, e.fingerprint, home=home)
    assert doctor_triage.apply(a, ["x"], meta, home=home).open_warnings == []
    assert doctor_triage.apply(b, ["x"], meta, home=home).open_warnings == ["x"]
    assert doctor_triage.set_review(a, e.id, None, home=home) is None
    assert doctor_triage.apply(a, ["x"], meta, home=home).open_warnings == ["x"]
    record = doctor_triage.reviews_path(home)
    assert json.loads(record.read_text(encoding="utf-8")) == {"schema": 1, "vaults": {}}
    assert oct(record.stat().st_mode & 0o777) == "0o600"


def test_a_broken_reviews_file_means_no_reviews(tmp_path: Path, home: Path) -> None:
    path = doctor_triage.reviews_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert doctor_triage.load_reviews(tmp_path, home=home) == {}
    path.write_text(json.dumps({"schema": 1, "vaults": {str(tmp_path.resolve()): {"w": "bad"}}}), encoding="utf-8")
    assert doctor_triage.load_reviews(tmp_path, home=home) == {}


@pytest.mark.parametrize(("wid", "fp"), [("", "0" * 16), ("ok", "nothex"), ("bad\nid", "0" * 16)])
def test_a_malformed_review_is_refused(tmp_path: Path, home: Path, wid: str, fp: str) -> None:
    with pytest.raises(ValueError):
        doctor_triage.set_review(tmp_path, wid, fp, home=home)


# -- the CLI ------------------------------------------------------------------------------------------------------


def _cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    from scout.cli import app

    result = CliRunner().invoke(app, list(args), env=env)
    return result.exit_code, result.output


def test_the_owner_marks_a_warning_reviewed_from_the_cli(vault: Path, home: Path) -> None:
    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    code, out = _cli("bootstrap", "review-warning", e["id"], "--fingerprint", e["fingerprint"], "--json")
    assert code == 0, out
    assert json.loads(out)["fingerprint"] == e["fingerprint"] and json.loads(out)["reviewed_at"]
    assert _entry(_doctor(vault, home), "privacy.script-raw-kb-read")["reviewed_at"]
    code, out = _cli("bootstrap", "review-warning", e["id"], "--undo", "--json")
    assert code == 0 and json.loads(out) == {"id": e["id"], "fingerprint": None, "reviewed_at": None}
    assert _entry(_doctor(vault, home), "privacy.script-raw-kb-read")["reviewed_at"] is None


def test_the_cli_needs_a_fingerprint_or_undo(vault: Path, home: Path) -> None:
    assert _cli("bootstrap", "review-warning", "x")[0] == 1
    assert _cli("bootstrap", "review-warning", "x", "--undo", "--fingerprint", "0" * 16)[0] == 1


@pytest.mark.parametrize("env", [{"SCOUT_MODE": "dreaming"}, {"SCOUT_READER": "autonomous"}])
def test_a_run_cant_mark_a_warning_reviewed(vault: Path, home: Path, env: dict[str, str]) -> None:
    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    code, out = _cli("bootstrap", "review-warning", e["id"], "--fingerprint", e["fingerprint"], env=env)
    assert code == 2 and "refused" in out
    assert not doctor_triage.reviews_path(home).exists()
    doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home)
    assert _cli("bootstrap", "review-warning", e["id"], "--undo", env=env)[0] == 2
    assert _entry(_doctor(vault, home), "privacy.script-raw-kb-read")["reviewed_at"]


def test_the_reviews_are_protected_in_both_layers(tmp_path: Path) -> None:
    from scout.sandbox import profile as sbx

    root = tmp_path.resolve()
    h, v = root / "h", root / "v"
    (h / ".claude").mkdir(parents=True)
    v.mkdir()
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    env = {k: val for k, val in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "SCOUT_SECRETS_DIR", "GIT_DIR")}
    env.update(HOME=str(h), XDG_CONFIG_HOME=str(h / ".config"))
    env.pop("GIT_CONFIG_GLOBAL", None)
    hook_paths._ROOTS_CACHE.clear()
    record = doctor_triage.reviews_path(env=env)
    assert record.name == hook_paths.REVIEWED_WARNINGS_NAME
    assert hook_paths.protected_write(record, v, env) == hook_paths.ENGINE
    assert sbx.decide(sbx.build_rules(v, env), str(record)) == "deny"
    assert hook_paths.protected_token(f"open('{record}', 'w')") is not None
    hook_paths._ROOTS_CACHE.clear()


# -- protection warnings can't be reviewed (security review of #717) ----------------------------------------------


def _review_everything(vault: Path, home: Path) -> dict:
    """Review every warning the doctor offers for review; return the doctor's answer after."""
    for e in _doctor(vault, home)["warning_triage"]:
        if e["reviewable"]:
            doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home, now=NOW)
    return _doctor(vault, home)


def test_the_privacy_audit_count_is_never_reviewable(vault: Path, home: Path, monkeypatch) -> None:
    from scout.scripts import bootstrap_doctor

    # A rolling 7-day count: next week's two new blocked sends read exactly like this week's two.
    line = "privacy: 2 blocked outbound checks or denied reads in the last 7 days (.scout-logs/kb-reads.jsonl)"
    monkeypatch.setattr(bootstrap_doctor, "_check_privacy_audit", lambda **_: [line])
    audit = next(e for e in _doctor(vault, home)["warning_triage"] if e["summary"] == line)
    assert audit["reviewable"] is False and audit["risk"] == "high" and audit["id"].startswith("protection.")
    with pytest.raises(ValueError, match="can't be marked reviewed"):
        doctor_triage.set_review(vault, audit["id"], audit["fingerprint"], home=home)
    # Even a review written into the file by hand doesn't hold.
    path = doctor_triage.reviews_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {audit["id"]: {"fingerprint": audit["fingerprint"], "reviewed_at": "2026-11-18T00:00:00Z"}}
    path.write_text(json.dumps({"schema": 1, "vaults": {str(vault.resolve()): record}}), encoding="utf-8")
    after = _review_everything(vault, home)
    assert line in after["warnings"] and after["severity"] == "yellow"


@pytest.mark.parametrize(
    ("check", "line"),
    [
        (
            "_check_run_sandbox",
            "the last scheduled run ran without the write sandbox: SCOUT_RUN_SANDBOX=off was in the environment "
            "the scheduler started it with.",
        ),
        ("_check_run_sandbox", "run sandbox: failed (profile self-test failed). Scheduled runs are skipped until …"),
        ("_check_gate_probe", "gate probe failed (2026-11-18, claude 2.1): hooks didn't load."),
    ],
)
def test_run_protection_warnings_are_never_reviewable(
    vault: Path, home: Path, monkeypatch, check: str, line: str
) -> None:
    from scout.scripts import bootstrap_doctor

    # The host checks run only with check_jobs; stub the others so nothing touches this Mac.
    for name in (
        "_check_scheduled_jobs",
        "_check_scheduler_bin_path",
        "_check_scoutctl_shim",
        "_check_engine_pointer",
        "_check_runner_claude_bin",
        "_check_gate_probe",
        "_check_run_sandbox",
        "_check_claude_auth",
    ):
        monkeypatch.setattr(bootstrap_doctor, name, lambda **_: ([], []))
    monkeypatch.setattr(bootstrap_doctor, check, lambda **_: ([line], []))
    report = bootstrap_doctor.run_doctor(vault=vault, check_jobs=True, home=home, now=NOW)
    entry = next(e for e in report.warning_triage if e.summary == line)
    assert entry.reviewable is False and entry.risk == "high" and entry.kind == "protection"
    assert (
        line in doctor_dict(bootstrap_doctor.run_doctor(vault=vault, check_jobs=True, home=home, now=NOW))["warnings"]
    )


def test_the_repo_sync_commit_hook_warning_is_never_reviewable(vault: Path, home: Path, monkeypatch) -> None:
    from scout.scripts import bootstrap_doctor

    line = "repo sync: the Local-Only list is 9 days old, so the commit hook can't block a scheduled run's move …"
    monkeypatch.setattr(bootstrap_doctor, "_check_repo_sync_host_cache", lambda **_: [line])
    after = _review_everything(vault, home)
    entry = next(e for e in after["warning_triage"] if e["summary"] == line)
    assert entry["reviewable"] is False and line in after["warnings"]


def test_an_untagged_protection_warning_falls_back_to_unreviewable() -> None:
    for line in ("privacy: something new", "the last scheduled run ran without the write sandbox: …", "repo sync: x"):
        meta = doctor_triage.default_meta(line)
        assert meta.reviewable is False and doctor_triage.warning_id(line, meta).startswith("protection.")
    assert doctor_triage.default_meta("runner backup present: x").reviewable is True


def test_a_run_hiding_its_environment_still_cant_review(vault: Path, home: Path, monkeypatch) -> None:
    from scout.kb import live_run

    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    monkeypatch.setattr(live_run, "commit_source_now", lambda _v: live_run.CommitSource(True, "dreaming", "ancestry"))
    code, out = _cli("bootstrap", "review-warning", e["id"], "--fingerprint", e["fingerprint"])
    assert code == 2 and "inside a scheduled run (dreaming, by ancestry)" in out
    assert not doctor_triage.reviews_path(home).exists()


# -- a failed drift scan can't hide the privacy findings (delta review of #717) ------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod-000 file anyway")
def test_an_unreadable_managed_file_keeps_the_privacy_warnings(vault: Path, home: Path, monkeypatch) -> None:
    from scout.scripts import vault_drift

    # A managed file with a snapshot, which the drift scan reads; a run makes it unreadable on purpose.
    managed = "scripts/kb-shard-log.py"
    (vault / managed).write_text("x = 1\n", encoding="utf-8")
    snap = vault_drift.snapshot_path(vault, managed)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text("x = 1\n", encoding="utf-8")
    (vault / managed).chmod(0)
    try:
        d = _doctor(vault, home)
        failure = next(e for e in d["warning_triage"] if e["summary"].startswith("could not check vault drift"))
        assert failure["reviewable"] is False and failure["kind"] == "protection"
        # The privacy findings are still there, high ones included.
        assert _entry(d, "privacy.script-raw-kb-read")["summary"] in d["warnings"]
        assert _entry(d, "privacy.script-raw-kb-read.internal")["summary"] in d["warnings"]
        after = _review_everything(vault, home)
        assert failure["summary"] in after["warnings"]
        assert _entry(after, "privacy.script-raw-kb-read")["reviewed_at"]  # reviewable ones still review
    finally:
        (vault / managed).chmod(0o644)


def test_a_failed_privacy_scan_is_an_unreviewable_line(vault: Path, home: Path, monkeypatch) -> None:
    from scout.scripts import vault_drift

    def boom(_v: Path) -> list:
        raise PermissionError(13, "Permission denied", "scripts")

    monkeypatch.setattr(vault_drift, "privacy_drift", boom)
    d = _doctor(vault, home)
    (line,) = [e for e in d["warning_triage"] if e["summary"].startswith("privacy: could not check")]
    assert line["reviewable"] is False and line["summary"] in d["warnings"]


def test_an_unreadable_lint_config_is_never_reviewable(vault: Path, home: Path, monkeypatch) -> None:
    from scout.scripts import bootstrap_doctor

    line = "kb-lint: the kb_lint config can't be read (PermissionError), so no size or line check blocks"
    monkeypatch.setattr(bootstrap_doctor, "_check_kb_lint_config", lambda **_: [line, "kb-lint: max_lines is 0"])
    after = _review_everything(vault, home)
    entry = next(e for e in after["warning_triage"] if e["summary"] == line)
    assert entry["reviewable"] is False and line in after["warnings"]
    other = next(e for e in after["warning_triage"] if e["summary"] == "kb-lint: max_lines is 0")
    assert other["reviewable"] is True and other["reviewed_at"]


def test_any_text_file_a_script_runs_is_a_helper_but_only_a_script_is_a_caller(vault: Path) -> None:
    # A run writes the sender as a .txt; a reviewed checker runs it by name: the checker is high.
    (vault / "scripts" / "ship.txt").write_text("import requests\nrequests.post(U, data=open(0).read())\n", "utf-8")
    (vault / "scripts" / "link-check.py").write_text(
        "import subprocess\n"
        "subprocess.run(['python3', 'scripts/ship.txt'], input=open('knowledge-base/i.md').read())\n",
        "utf-8",
    )
    # A note that mentions tag-audit.py and a pipe doesn't run anything: tag-audit.py stays low.
    (vault / "scripts" / "notes.md").write_text("python3 scripts/tag-audit.py | slack\n", "utf-8")
    # A binary file a script names is no helper.
    (vault / "scripts" / "blob.bin").write_bytes(b"requests\0\0")
    (vault / "scripts" / "tag-audit.py").write_text("x = 'blob.bin'\nopen('knowledge-base/i.md').read()\n", "utf-8")
    ranks = doctor_triage.rank_vault_scripts(vault, ["scripts/tag-audit.py", "scripts/link-check.py"])
    assert ranks["scripts/link-check.py"] == (
        "high",
        "it uses scripts/ship.txt, which may send what it is given out of the vault",
    )
    assert ranks["scripts/tag-audit.py"][0] == "low"


def test_helpers_that_name_each_other_end(vault: Path) -> None:
    (vault / "scripts" / "a_helper.py").write_text("import b_helper\n", "utf-8")
    (vault / "scripts" / "b_helper.py").write_text("import a_helper\n", "utf-8")
    (vault / "scripts" / "link-check.py").write_text("import a_helper\nopen('knowledge-base/i.md').read()\n", "utf-8")
    assert doctor_triage.rank_vault_scripts(vault, ["scripts/link-check.py"])["scripts/link-check.py"][0] == "low"


def test_a_vault_scout_cant_list_ranks_each_script_on_its_own(vault: Path, monkeypatch) -> None:
    def boom(_v: Path) -> list:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(doctor_triage, "_vault_files", boom)
    ranks = doctor_triage.rank_vault_scripts(vault, ["scripts/weekly-digest.py", "scripts/tag-audit.py"])
    assert ranks["scripts/weekly-digest.py"][0] == "high" and ranks["scripts/tag-audit.py"][0] == "low"


def test_a_binary_or_unreadable_file_ranks_high(tmp_path: Path) -> None:
    blob = tmp_path / "x.bin"
    blob.write_bytes(b"\0\1")
    assert doctor_triage.script_risk(doctor_triage.read_script(blob)) == (
        "high",
        "it is a binary file, so where its output goes is unknown",
    )
    assert doctor_triage.file_digest(tmp_path / "missing") == "unreadable"
    assert doctor_triage.read_script(tmp_path / "missing") is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod-000 file anyway")
def test_an_unreadable_script_is_its_own_high_finding(vault: Path, home: Path) -> None:
    hidden = vault / "scripts" / "hidden.py"
    hidden.write_text("print(1)\n", encoding="utf-8")
    hidden.chmod(0)
    try:
        d = _doctor(vault, home)
        high = _entry(d, "privacy.script-raw-kb-read")
        assert "scripts/hidden.py" in high["items"] and "scripts/weekly-digest.py" in high["items"]
        # The other scripts keep their own warnings: one bad file doesn't collapse the scan.
        assert _entry(d, "privacy.script-raw-kb-read.internal")["items"] == [
            "scripts/link-check.py",
            "scripts/tag-audit.py",
        ]
    finally:
        hidden.chmod(0o644)


# -- coverage: the store and the CLI's other paths ------------------------------------------------------------------


def test_a_failed_write_leaves_no_temp_file(tmp_path: Path, home: Path, monkeypatch) -> None:
    def fail(*_a: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(doctor_triage.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        doctor_triage.set_review(tmp_path, "warning.x", "0123456789abcdef", home=home)
    folder = doctor_triage.reviews_path(home).parent
    assert list(folder.iterdir()) == []


def test_load_reviews_survives_a_broken_reader(tmp_path: Path, home: Path, monkeypatch) -> None:
    def boom(_p: Path) -> dict:
        raise RuntimeError("bug")

    monkeypatch.setattr(doctor_triage, "_read", boom)
    assert doctor_triage.load_reviews(tmp_path, home=home) == {}
    path = doctor_triage.reviews_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.undo()
    path.write_text(json.dumps({"schema": 1, "vaults": {str(tmp_path.resolve()): ["not", "a", "dict"]}}), "utf-8")
    assert doctor_triage.load_reviews(tmp_path, home=home) == {}
    # A record whose block isn't a dict is replaced, not crashed on, when the owner reviews.
    assert doctor_triage.set_review(tmp_path, "warning.x", "0123456789abcdef", home=home)


def test_the_cli_says_what_it_did_in_text(vault: Path, home: Path) -> None:
    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    code, out = _cli("bootstrap", "review-warning", e["id"], "--fingerprint", e["fingerprint"])
    assert code == 0 and out.startswith(f"{e['id']}: reviewed ")
    code, out = _cli("bootstrap", "review-warning", e["id"], "--undo")
    assert code == 0 and out.strip() == f"{e['id']}: not reviewed"


def test_the_cli_refuses_a_malformed_or_protection_review(vault: Path, home: Path) -> None:
    code, out = _cli("bootstrap", "review-warning", "warning.x", "--fingerprint", "nothex")
    assert code == 1 and "not a warning fingerprint" in out
    code, out = _cli("bootstrap", "review-warning", "protection.abc", "--fingerprint", "0123456789abcdef")
    assert code == 1 and "can't be marked reviewed" in out


def test_the_cli_reports_a_store_it_cant_write(vault: Path, home: Path, monkeypatch) -> None:
    def fail(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(doctor_triage, "set_review", fail)
    code, out = _cli("bootstrap", "review-warning", "warning.x", "--fingerprint", "0123456789abcdef")
    assert code == 1 and "couldn't save the review" in out


def test_the_text_doctor_lists_reviewed_warnings(vault: Path, home: Path, monkeypatch) -> None:
    from scout.scripts import bootstrap_doctor

    e = _entry(_doctor(vault, home), "privacy.script-raw-kb-read")
    doctor_triage.set_review(vault, e["id"], e["fingerprint"], home=home, now=NOW)
    real = bootstrap_doctor.run_doctor
    monkeypatch.setattr(bootstrap_doctor, "run_doctor", lambda **kw: real(**{**kw, "home": home, "now": NOW}))
    _code, out = _cli("bootstrap", "doctor", "--no-jobs")
    assert f"reviewed 2026-11-19T09:00:00Z: {e['id']}: " in out
    assert f"warning: {e['summary']}" not in out


def test_duplicate_warnings_keep_unique_ids_and_reviewed_lists_reviews(tmp_path: Path, home: Path) -> None:
    triage = doctor_triage.apply(tmp_path, ["same", "same"], {}, home=home)
    first, second = (e.id for e in triage.entries)
    assert second == f"{first}#2"
    assert triage.reviewed == []
    doctor_triage.set_review(tmp_path, first, triage.entries[0].fingerprint, home=home)
    again = doctor_triage.apply(tmp_path, ["same", "same"], {}, home=home)
    assert [e.id for e in again.reviewed] == [first] and again.open_warnings == ["same"]


def test_a_wrong_schema_and_an_unresolvable_vault_are_handled(tmp_path: Path, home: Path, monkeypatch) -> None:
    path = doctor_triage.reviews_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 99, "vaults": {}}), encoding="utf-8")
    assert doctor_triage.load_reviews(tmp_path, home=home) == {}

    def boom(self: Path, strict: bool = False) -> Path:
        raise OSError("loop")

    monkeypatch.setattr(Path, "resolve", boom)
    assert doctor_triage._vault_key(tmp_path) == str(tmp_path.absolute())


def test_privacy_drift_reports_what_it_cant_look_at(tmp_path: Path, monkeypatch) -> None:
    from scout.scripts import vault_drift

    v = tmp_path / "v"
    (v / "scripts").mkdir(parents=True)
    (v / "run-dir.sh").mkdir()  # not a file: no runner to judge
    (v / "scripts" / "blob.bin").write_bytes(b"\0\1knowledge-base")  # binary: readable, so not reported
    (v / "scripts" / "odd.py").write_text("print(1)\n", encoding="utf-8")
    real = vault_drift.snapshot_path

    def flaky(vault: Path, rel: str) -> Path:
        if rel == "scripts/odd.py":
            raise PermissionError(13, "Permission denied")
        return real(vault, rel)

    monkeypatch.setattr(vault_drift, "snapshot_path", flaky)
    found = vault_drift.privacy_drift(v)
    assert [(f.path, f.rule) for f in found] == [("scripts/odd.py", "script-raw-kb-read")]
