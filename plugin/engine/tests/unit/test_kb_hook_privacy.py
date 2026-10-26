"""The pre-commit hook when the privacy check can't run (#453, spec §8.3; Jordan, 2026-10-22).

If the privacy checker itself errors, a scheduled run's commit is refused with
the run-health event ``privacy.check_failed``; the owner's interactive commit
goes on with a loud warning and a ``privacy.check_unavailable`` audit row, and
the size/diary verdict is the result. The shell template does the same when
scoutctl can't run at all (``test_kb_hook.py``). Nothing prints note content.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb import lint_privacy, outbound
from scout.kb.hook import install_hook
from scout.kb.lint import run_hook
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git, git_env

DRAFT = "drafts/reply-to-priya.md"
SECRET = "CANARY-PRIV-2S4M in the error text"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    return build_privacy_vault(tmp_path)


def _append(vault: Path, rel: str, line: str) -> None:
    path = vault / rel
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    git(vault, "add", "--", rel)


def _hook(vault: Path, env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
    return run_hook(vault, env=env, out=buf), buf.getvalue()


def _boom(*_a: object, **_k: object) -> None:
    raise RuntimeError(SECRET)


def _events(vault: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return rows


def _reads(vault: Path) -> list[dict[str, object]]:
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


def test_a_checker_error_lets_the_owner_commit_with_a_warning_and_a_row(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lint_privacy, "private_terms", _boom)
    _append(vault, DRAFT, "Thanks again!")
    rc, out = _hook(vault, {})
    assert rc == 0
    lines = out.splitlines()
    i = lines.index("kb-lint: privacy check could not run")
    assert lines[i + 1] == (
        "kb-lint: WARNING: the privacy check could not run (RuntimeError); this commit was NOT checked "
        "for private links in shared files. Run scoutctl doctor."
    )
    assert "CANARY" not in out
    assert _events(vault) == []
    (row,) = _reads(vault)
    assert row["event"] == "privacy.check_unavailable" and row["reader"] == "owner"
    assert row["args_hash"] is None and row["blocked"] is False and row["command"] == "kb lint --staged"


def test_an_owner_commit_with_the_check_down_still_gets_the_size_verdict(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = vault / "scout-config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "kb_lint:\n  mode: block\n", encoding="utf-8")
    git(vault, "add", "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "block mode")
    monkeypatch.setattr(lint_privacy, "load_policy", _boom)
    _append(vault, "knowledge-base/people/priya.md", "x" * 1600)  # mega-line: a size/diary block
    rc, out = _hook(vault, {})
    assert rc == 1 and "[mega-line]" in out and "WARNING" in out
    assert "[privacy-check-failed]" not in out


def test_a_failed_audit_row_never_fails_the_owner_commit(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import read_log

    monkeypatch.setattr(lint_privacy, "private_terms", _boom)
    monkeypatch.setattr(read_log, "log_event", _boom)
    _append(vault, DRAFT, "Thanks again!")
    assert _hook(vault, {})[0] == 0


@pytest.mark.parametrize("env", [{"SCOUT_MODE": "morning-briefing"}, {"SCOUT_READER": "autonomous"}])
def test_a_checker_error_refuses_a_scheduled_commit(
    vault: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    monkeypatch.setattr(lint_privacy, "load_policy", _boom)
    _append(vault, "knowledge-base/people/priya.md", "More notes.")
    rc, out = _hook(vault, env)
    assert rc == 1 and "[privacy-check-failed]" in out
    assert "kb-lint: privacy check could not run" in out.splitlines()
    assert "WARNING" not in out and _reads(vault) == []
    (event,) = _events(vault)
    assert event["payload"] == {"error": "RuntimeError", "mode": env.get("SCOUT_MODE", "autonomous")}


def test_a_checker_error_refuses_a_run_commit_with_an_event(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint_privacy, "private_terms", _boom)
    _append(vault, DRAFT, "Thanks again!")
    rc, out = _hook(vault, {"SCOUT_MODE": "morning-briefing", "SCOUT_LINT_OVERRIDE": "x"})
    assert rc == 1
    assert "privacy.check_failed" in out and "CANARY" not in out
    (event,) = _events(vault)
    assert event["kind"] == "privacy.check_failed"
    assert event["payload"] == {"error": "RuntimeError", "mode": "morning-briefing"}
    assert "CANARY" not in json.dumps(event)


def test_a_crash_in_the_size_lint_still_reports_a_privacy_block(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint, "lint_staged", _boom)
    _append(vault, DRAFT, "See [[sam]].")
    rc, out = _hook(vault, {})
    assert rc == 1 and "[private-link]" in out


def test_a_crash_in_the_size_lint_alone_fails_open(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §8.3: only privacy fails closed. A size/diary crash with a clean privacy pass allows the commit."""
    from scout.kb import lint

    monkeypatch.setattr(lint, "lint_staged", _boom)
    _append(vault, DRAFT, "Thanks again!")
    rc, out = _hook(vault, {})
    assert rc == 0
    assert "kb-lint: size/diary check failed (RuntimeError) — not blocking" in out
    assert "CANARY" not in out


def test_a_crash_in_the_lint_config_alone_fails_open(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint, "load_lint_config", _boom)
    _append(vault, DRAFT, "Thanks again!")
    assert _hook(vault, {})[0] == 0


def test_a_logging_failure_without_a_privacy_block_fails_open(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint, "_append_jsonl", _boom)
    _append(vault, "knowledge-base/people/priya.md", "See [[nowhere-at-all]].")  # a dangling-link warning
    rc, out = _hook(vault, {})
    assert rc == 0
    assert "could not write .scout-logs/kb-lint.jsonl (RuntimeError)" in out
    assert "[dangling-link]" in out


def test_a_formatting_failure_still_shows_the_privacy_block(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint, "format_findings", _boom)
    _append(vault, DRAFT, "See [[sam]].")
    rc, out = _hook(vault, {})
    assert rc == 1
    assert "kb-lint: privacy: private-link at drafts/reply-to-priya.md:" in out
    assert "Sammy" not in out and "[[sam" not in out


def test_a_logging_failure_does_not_lift_a_privacy_block(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint, "_append_jsonl", _boom)
    _append(vault, DRAFT, "See [[sam]].")
    rc, out = _hook(vault, {})
    assert rc == 1 and "[private-link]" in out


def test_a_logs_dir_failure_does_not_lift_a_privacy_block(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint.paths, "logs_dir", _boom)
    _append(vault, DRAFT, "See [[sam]].")
    assert _hook(vault, {})[0] == 1


def test_a_failed_event_write_does_not_lift_the_refusal(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.events

    monkeypatch.setattr(lint_privacy, "private_terms", _boom)
    monkeypatch.setattr(scout.events, "append_event", _boom)
    _append(vault, DRAFT, "Thanks again!")
    rc, out = _hook(vault, {"SCOUT_MODE": "morning-briefing"})
    assert rc == 1 and "[privacy-check-failed]" in out
    assert _events(vault) == []


def test_a_formatting_failure_without_a_privacy_block_fails_open(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    monkeypatch.setattr(lint, "format_findings", _boom)
    _append(vault, "knowledge-base/people/priya.md", "See [[nowhere-at-all]].")  # a warning only
    rc, out = _hook(vault, {})
    assert rc == 0
    assert "could not format the findings (RuntimeError)" in out and "kb-lint: privacy:" not in out


def _block_mode_mega_line(vault: Path) -> None:
    cfg = vault / "scout-config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "kb_lint:\n  mode: block\n", encoding="utf-8")
    git(vault, "add", "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "block mode")
    _append(vault, "knowledge-base/people/priya.md", "x" * 1600)  # mega-line: a size/diary block


def test_an_override_whose_log_fails_is_still_accepted(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Size/diary bookkeeping fails open: the owner's override stands even if it can't be logged."""
    from scout.kb import lint

    _block_mode_mega_line(vault)
    assert _hook(vault, {})[0] == 1  # the size/diary block itself
    monkeypatch.setattr(lint, "_append_jsonl", _boom)
    rc, out = _hook(vault, {"SCOUT_LINT_OVERRIDE": "fixing tomorrow"})
    assert rc == 0
    assert "could not log the override (RuntimeError)" in out and "override accepted" in out


def test_an_override_without_a_logs_dir_is_still_accepted(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import lint

    _block_mode_mega_line(vault)
    monkeypatch.setattr(lint.paths, "logs_dir", _boom)
    rc, out = _hook(vault, {"SCOUT_LINT_OVERRIDE": "fixing tomorrow"})
    assert rc == 0
    assert "could not log the override (OSError)" in out


class _BrokenStream(io.StringIO):
    def write(self, s: str) -> int:
        raise OSError("stderr closed")


def test_a_broken_output_stream_does_not_lift_a_privacy_block(vault: Path) -> None:
    _append(vault, DRAFT, "See [[sam]].")
    assert run_hook(vault, env={}, out=_BrokenStream()) == 1


def test_privacy_summary_names_rules_files_and_lines_only() -> None:
    from scout.kb.lint import Finding, _privacy_summary

    findings = [
        Finding("drafts/a.md", "private-link", True, "line 3 ...", 3),
        Finding("drafts/a.md", "private-mention", True, "line 5 ...", 5),
        Finding("knowledge-base/x.md", "privacy-widening", True, "a scheduled run may not ..."),
        Finding("knowledge-base/y.md", "dangling-link", False, "line 1 ...", 1),
        Finding("knowledge-base/z.md", "mega-line", True, "line 2 ...", 2),
    ]
    assert _privacy_summary(findings) == (
        "kb-lint: privacy: private-link,private-mention at drafts/a.md:3,5; privacy-widening at knowledge-base/x.md"
    )


def _scoutctl() -> str | None:
    beside = Path(sys.executable).parent / "scoutctl"
    return str(beside) if beside.exists() else shutil.which("scoutctl")


@pytest.mark.skipif(_scoutctl() is None, reason="needs the engine's scoutctl")
def test_the_installed_hook_refuses_a_real_commit(vault: Path) -> None:
    install_hook(vault, scoutctl=_scoutctl())
    _append(vault, DRAFT, "See [[Sammy]] for the details.")
    env = {**git_env(), "SCOUT_LINT_OVERRIDE": "need it", "SCOUT_DATA_DIR": str(vault)}
    proc = subprocess.run(
        ["git", "-C", str(vault), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "reply"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "[private-link]" in proc.stderr
    assert "Sammy" not in proc.stderr
