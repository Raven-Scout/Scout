"""``fit``'s data-safety rules: no open task ever ends up in neither file (#732 review rounds 1–2).

Fixtures are anonymized per CLAUDE.md (Alex / Priya / Sam, PROJ-1234,
example-org) and every `[#TAG]` here is invented.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.action_items import stale
from scout.action_items.budget import ActionItemsSettings
from scout.action_items.cli import app
from scout.action_items.fit import fit, plan_fit
from scout.action_items.past_due import History

TODAY = _dt.date(2026, 11, 19)
runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "no-vault"))


def _plan(daily: list[str], backlog: list[str] | None = None):  # noqa: ANN202
    return plan_fit(
        list(daily),
        list(backlog or []),
        today=TODAY,
        settings=ActionItemsSettings(),
        history=History(None, overrides={TODAY: list(daily)}),
    )


def _open(lines: list[str]) -> set[str]:
    return {b.tag for b in stale.task_blocks(lines)}


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "action-items" / "archive").mkdir(parents=True)
    return tmp_path


def _daily_path(vault: Path) -> Path:
    return vault / "action-items" / f"action-items-{TODAY.isoformat()}.md"


# A daily file and backlog where one apply does every kind of write: a note (KEEP's second prose
# bullet), a rewrite record, a leaver (NODU), a promotion (PRM1) and the final backlog.
DAILY = [
    "# Action Items",
    "",
    "## 🔴 Urgent",
    "",
    "- [ ] [#KEEP] **Keep** — due 2026-11-20",
    "  - first prose",
    "  - second prose moves to the note",
    "",
    "## 🟡 To Do",
    "",
    "- [ ] [#NODU] **No due date**",
    "",
]
BACKLOG = ["# Backlog", "", "## 🟡 To Do (soon)", "", "- [ ] [#PRM1] **Comes due** — due 2026-11-21", ""]
ALL_OPEN = {"KEEP", "NODU", "PRM1"}


def _seed(vault: Path) -> None:
    _daily_path(vault).write_text("\n".join(DAILY) + "\n", encoding="utf-8")
    stale.backlog_path(vault).write_text("\n".join(BACKLOG) + "\n", encoding="utf-8")


def _both(vault: Path) -> set[str]:
    daily = _daily_path(vault).read_text(encoding="utf-8").splitlines()
    backlog = stale.backlog_path(vault).read_text(encoding="utf-8").splitlines()
    return _open(daily) | _open(backlog)


# --- HIGH-1: a failed write never loses a task -------------------------------------------------


class _FailNth:
    """Wraps fit's two write functions; the ``fail_at``-th write (the report aside) raises."""

    def __init__(self, fail_at: int | None) -> None:
        self.fail_at = fail_at
        self.count = 0

    def __call__(self, real, *args):  # noqa: ANN001, ANN002, ANN204
        if not (isinstance(args[0], Path) and args[0].name == "action-items-fit.json"):
            n, self.count = self.count, self.count + 1
            if n == self.fail_at:
                raise OSError("disk full")
        return real(*args)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scout.action_items.fit as fit_module

        real_text, real_lines = fit_module._write_text, stale.write_lines_like
        monkeypatch.setattr(fit_module, "_write_text", lambda t, x: self(real_text, t, x))
        monkeypatch.setattr(stale, "write_lines_like", lambda t, w, ln: self(real_lines, t, w, ln))


def test_each_write_failing_in_turn_leaves_every_task_in_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _FailNth(None)
    with monkeypatch.context() as m:
        counter.install(m)
        vault = _vault(tmp_path / "count")
        _seed(vault)
        fit(data_dir=vault, date=TODAY, apply=True)
    writes = counter.count
    assert writes == 5  # note, rewrite records, staged backlog, daily, final backlog
    for fail_at in range(writes):
        vault = _vault(tmp_path / f"fail-{fail_at}")
        _seed(vault)
        with monkeypatch.context() as m:
            _FailNth(fail_at).install(m)
            with pytest.raises(OSError, match="disk full"):
                fit(data_dir=vault, date=TODAY, apply=True)
        assert _both(vault) == ALL_OPEN, f"write {fail_at} failing lost a task"
        # And the next fit reconciles it: each task in exactly one file.
        fit(data_dir=vault, date=TODAY, apply=True)
        daily = _open(_daily_path(vault).read_text().splitlines())
        backlog = _open(stale.backlog_path(vault).read_text().splitlines())
        assert daily | backlog == ALL_OPEN and not daily & backlog, f"after write {fail_at}: {daily} / {backlog}"


def test_a_failed_note_write_keeps_the_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviewer's repro: a note write raising used to leave PRM1 in neither file."""
    import scout.action_items.fit as fit_module

    vault = _vault(tmp_path)
    _seed(vault)
    real = fit_module._write_text

    def boom(target: Path, text: str) -> None:
        if "context" in target.parts:
            raise OSError("disk full / planted link / EACCES")
        real(target, text)

    monkeypatch.setattr(fit_module, "_write_text", boom)
    with pytest.raises(OSError):
        fit(data_dir=vault, date=TODAY, apply=True, settings=ActionItemsSettings(fit="apply"))
    assert "PRM1" in _both(vault)


# --- HIGH-2: Recently Completed pruning never deletes an open task --------------------------------


def test_an_open_row_with_strikethrough_in_recently_completed_stays() -> None:
    daily = [
        "## ✅ Recently Completed",
        "",
        "### 2026-11-01",
        "- [ ] [#OPN1] **Still open** — renamed from ~~old~~",
        "",
    ]
    assert "OPN1" in _open(_plan(daily).daily)


def test_a_done_parent_with_an_open_child_stays() -> None:
    daily = [
        "## ✅ Recently Completed",
        "",
        "### 2026-11-01",
        "- [x] [#DON1] **Parent done**",
        "  - [ ] [#CHD1] follow-up still open",
        "",
    ]
    result = _plan(daily)
    assert any("CHD1" in ln for ln in result.daily) and result.completed_dropped == 0


def test_a_recently_completed_row_never_dedupes_a_backlog_copy() -> None:
    row = "- [ ] [#DUP1] **X** ~~old~~"
    daily = ["## ✅ Recently Completed", "", "### 2026-11-01", row, ""]
    backlog = ["# Backlog", "", "## 🅿️ Parked", "", row, ""]
    result = _plan(daily, backlog)
    assert "DUP1" in _open(result.daily) | _open(result.backlog)
    assert result.deduped == []
    # And the open copy under ✅ keeps a backlog 🟡 copy from being promoted next to it.
    yellow = ["# Backlog", "", "## 🟡 To Do (soon)", "", "- [ ] [#DUP1] **X** — due 2026-11-20", ""]
    assert [m for m in _plan(daily, yellow).moves if m.to == "daily"] == []


def test_strikethrough_counts_only_from_the_start_of_the_row() -> None:
    entries = ["- ~~**Struck from the start**~~ — closed 2026-11-01", "- a note that mentions ~~this~~ — 2026-11-01"]
    result = _plan(["## ✅ Recently Completed", "", *entries])
    text = "\n".join(result.daily)
    assert "Struck from the start" not in text and "mentions ~~this~~" in text


def test_a_due_date_is_never_read_as_the_close_date() -> None:
    entries = ["- [x] [#DUE1] ✅ ~~**Done**~~ — due 2026-11-01"]  # seen only today: closed today
    assert _plan(["## ✅ Recently Completed", "", *entries]).completed_dropped == 0


# --- MEDIUM-4: a merge never drops a different task line ------------------------------------------


def test_a_differing_task_line_keeps_both_copies_and_is_named() -> None:
    daily = ["## 🅿️ Parked", "", "- [ ] [#MRG1] **New title**", ""]
    backlog = ["# Backlog", "", "## 🅿️ Parked", "", "- [ ] [#MRG1] **Old title** — note only here", "  - sub", ""]
    result = _plan(daily, backlog)
    text = "\n".join(result.backlog)
    assert "note only here" in text and "  - sub" in text and "**New title**" in text
    assert result.conflicts == ["MRG1"]
    again = _plan(result.daily, result.backlog)
    assert "note only here" in "\n".join(again.backlog) and again.conflicts == ["MRG1"]


def test_a_conflicting_backlog_tag_is_not_promoted() -> None:
    backlog = [
        "## 🟡 To Do (soon)",
        "- [ ] [#TWIN] **One** — due 2026-11-20",
        "- [ ] [#TWIN] **Two** — due 2026-11-20",
    ]
    result = _plan(["## 🟡 To Do", ""], backlog)
    assert result.conflicts == ["TWIN"] and [m for m in result.moves if m.to == "daily"] == []


# --- LOW: the lock's ancestor rule ------------------------------------------------------------------


def _busy(monkeypatch: pytest.MonkeyPatch, vault: Path, *, holder: int, marker: int | None, lock_age_s: float) -> None:
    import time

    import scout.kb.live_run as live_run
    import scout.scripts.bootstrap_lock as bootstrap_lock

    def busy(lock: Path, **_: object) -> None:
        raise bootstrap_lock.LockBusyError(lock, holder)

    monkeypatch.setattr(bootstrap_lock, "acquire_lock_with_wait", busy)
    monkeypatch.setattr(live_run, "lock_holder", lambda _v: holder)
    monkeypatch.setattr(live_run, "_marker", lambda _v: (marker, "briefing"))
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(holder))
    stamp = time.time() - lock_age_s
    os.utime(lock, (stamp, stamp))


def test_the_runs_own_fit_goes_ahead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.action_items.fit import apply_lock

    vault = _vault(tmp_path)
    _busy(monkeypatch, vault, holder=os.getpid(), marker=os.getpid(), lock_age_s=0)
    with apply_lock(vault):
        pass


@pytest.mark.parametrize(
    ("marker", "lock_age_s"),
    [(None, 0), (1, 0), ("self", 10**7)],  # no marker, marker names another PID, a lock older than its holder
)
def test_a_lock_that_is_not_the_runs_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: int | str | None, lock_age_s: float
) -> None:
    from scout.action_items.fit import apply_lock
    from scout.errors import ActionItemError

    vault = _vault(tmp_path)
    pid = os.getpid()
    _busy(monkeypatch, vault, holder=pid, marker=pid if marker == "self" else marker, lock_age_s=lock_age_s)  # type: ignore[arg-type]
    with pytest.raises(ActionItemError, match="refused"), apply_lock(vault):
        pass


def test_a_ps_failure_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.kb.live_run as live_run
    from scout.action_items.fit import apply_lock
    from scout.errors import ActionItemError

    vault = _vault(tmp_path)
    _busy(monkeypatch, vault, holder=os.getpid(), marker=os.getpid(), lock_age_s=0)

    def no_ps() -> dict[int, object]:
        raise OSError("ps listed no processes")

    monkeypatch.setattr(live_run, "process_table", no_ps)
    with pytest.raises(ActionItemError, match="refused"), apply_lock(vault):
        pass


def test_archive_stale_takes_the_lock_unless_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    _daily_path(vault).write_text("\n".join(DAILY) + "\n", encoding="utf-8")
    args = ["archive-stale", "--date", TODAY.isoformat(), "--data-dir", str(vault)]
    _busy(monkeypatch, vault, holder=999_999, marker=999_999, lock_age_s=0)
    assert runner.invoke(app, [*args, "--dry-run"]).exit_code == 0
    out = runner.invoke(app, args)
    assert out.exit_code != 0 and "refused" in str(out.exception)


# --- LOW: reporting -----------------------------------------------------------------------------------


def test_materialize_logs_a_failed_fit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scout.action_items.fit as fit_module
    from scout.action_items.materialize import materialize

    def boom(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(fit_module, "fit", boom)
    vault = _vault(tmp_path)
    (vault / "action-items" / f"action-items-{(TODAY - _dt.timedelta(days=1)).isoformat()}.md").write_text(
        "\n".join(DAILY) + "\n", encoding="utf-8"
    )
    assert materialize(data_dir=vault, date=TODAY) is not None
    assert "action-items fit failed (RuntimeError: boom)" in capsys.readouterr().err
    report = json.loads((vault / ".scout-logs" / "action-items-fit.json").read_text())
    assert report == {"day": TODAY.isoformat(), "error": "RuntimeError: boom"}


def test_applied_reports_a_backlog_line_change_alone(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _seed(vault)
    fit(data_dir=vault, date=TODAY, apply=True)
    stale.backlog_path(vault).write_text("# Backlog\n", encoding="utf-8")
    result = fit(data_dir=vault, date=TODAY, apply=True)
    assert result is not None and not result.changed and result.applied
    out = runner.invoke(app, ["fit", "--date", TODAY.isoformat(), "--data-dir", str(vault), "--json"])
    assert json.loads(out.output)["applied"] is False  # report mode by default: nothing left to write anyway


# --- HIGH-3: the backlog has no lint budget -------------------------------------------------------------


def test_the_backlog_has_no_lint_budget_and_is_never_a_shrink_target(tmp_path: Path) -> None:
    from scout.kb.lint_config import load_lint_config
    from scout.scripts.bootstrap_doctor import _check_backlog_lint

    vault = _vault(tmp_path)
    cfg = load_lint_config(vault)
    assert cfg.budget_for("action-items/backlog.md") is None
    assert cfg.report_excluded("action-items/backlog.md")
    assert cfg.budget_for("action-items/context/ABCD.md") == 15360
    assert _check_backlog_lint(vault=vault) == []


def test_doctor_flags_a_vault_list_that_budgets_the_backlog(tmp_path: Path) -> None:
    from scout.scripts.bootstrap_doctor import _check_backlog_lint

    vault = _vault(tmp_path)
    (vault / "scout-config.yaml").write_text(
        'kb_lint:\n  budgets:\n    - {glob: "action-items/**", bytes: 15360}\n  report_exclude_globs: []\n',
        encoding="utf-8",
    )
    warnings = _check_backlog_lint(vault=vault)
    assert len(warnings) == 2
    assert "15 KB size budget" in warnings[0] and "report_exclude_globs" in warnings[1]
