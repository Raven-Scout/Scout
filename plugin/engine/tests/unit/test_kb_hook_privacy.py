"""The pre-commit hook's privacy path fails closed (#453, spec §8.3).

If the privacy checker itself errors, the commit is refused; the hook script
also refuses when scoutctl can't run at all (``test_kb_hook.py``). A scheduled run's refusal writes the
run-health event ``privacy.check_failed``; an interactive one names the error
class and points to ``scoutctl doctor``. Neither prints note content.
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


def test_a_checker_error_refuses_an_interactive_commit(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint_privacy, "private_terms", _boom)
    _append(vault, DRAFT, "Thanks again!")
    rc, out = _hook(vault, {})
    assert rc == 1
    assert "kb-lint:" in out and "RuntimeError" in out and "scoutctl doctor" in out
    assert "CANARY" not in out
    assert _events(vault) == []


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


def test_a_policy_load_error_refuses_too(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint_privacy, "load_policy", _boom)
    _append(vault, "knowledge-base/people/priya.md", "More notes.")
    rc, out = _hook(vault, {})
    assert rc == 1 and "RuntimeError" in out


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
