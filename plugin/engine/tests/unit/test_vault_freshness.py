"""templates/scripts/vault-freshness.py — the git-truth staleness ranking.

``hooks/kb-pre-filter.sh`` runs the script before each session and caches its
view in ``.scout-cache/vault-freshness.md``. It ranks knowledge-base files by
how far their last git commit is past their freshness budget. The budgets are
the kb-pre-filter tiers, so the git view and the claimed-date view agree on what
"stale" means. The script ships to vaults verbatim, so it is driven here the way
a vault runs it: as a CLI over a temporary git repository.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from scout.hooks import kb_pre_filter

SCRIPT = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "vault-freshness.py"
NOW = datetime.now(UTC)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vault_freshness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(vault: Path, *args: str, when: datetime | None = None) -> None:
    stamp = (when or NOW).isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Alex",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Alex",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }
    subprocess.run(["git", "-C", str(vault), *args], env=env, check=True, capture_output=True)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / "knowledge-base").mkdir(parents=True)
    _git(v, "init", "-q")
    return v


def _commit(vault: Path, rel: str, body: str = "# note\n", *, days_ago: float) -> None:
    path = vault / "knowledge-base" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    _git(vault, "add", str(path))
    _git(vault, "commit", "-q", "-m", f"update {rel}", when=NOW - timedelta(days=days_ago))


def _run(vault: Path, *args: str, script: Path = SCRIPT, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        env={**os.environ, "SCOUT_DATA_DIR": str(vault), **env},
        capture_output=True,
        text=True,
        check=True,
    )


def _json(vault: Path, *args: str) -> dict:
    return json.loads(_run(vault, "--json", *args).stdout)


def _files(payload: dict) -> list[str]:
    return [row["file"] for row in payload["files"]]


# ---------- ranking ----------


def test_files_past_their_budget_rank_most_overdue_first(vault: Path) -> None:
    _commit(vault, "projects/launch.md", "---\npriority: 🔴\n---\n", days_ago=10)  # 72h budget
    _commit(vault, "projects/roadmap.md", days_ago=10)  # default 168h budget
    _commit(vault, "projects/fresh.md", days_ago=1)

    payload = _json(vault)

    assert _files(payload) == ["knowledge-base/projects/launch.md", "knowledge-base/projects/roadmap.md"]
    launch, roadmap = payload["files"]
    assert (launch["status"], launch["budget_hours"]) == ("very-stale", 72)
    assert (roadmap["status"], roadmap["budget_hours"]) == ("stale", 168)
    assert payload["counts"] == {"ok": 1, "stale": 1, "very-stale": 1, "untracked": 0}


def test_all_lists_every_file_and_limit_caps_the_rows(vault: Path) -> None:
    for i in range(4):
        _commit(vault, f"projects/p{i}.md", days_ago=20 + i)
    _commit(vault, "projects/fresh.md", days_ago=1)

    assert len(_files(_json(vault, "--all"))) == 5
    assert _files(_json(vault, "--limit", "2")) == ["knowledge-base/projects/p3.md", "knowledge-base/projects/p2.md"]


def test_the_latest_commit_touching_a_file_is_the_one_that_counts(vault: Path) -> None:
    _commit(vault, "projects/roadmap.md", "v1\n", days_ago=30)
    _commit(vault, "projects/other.md", days_ago=20)
    _commit(vault, "projects/roadmap.md", "v2\n", days_ago=2)

    rows = {row["file"]: row for row in _json(vault, "--all")["files"]}

    assert rows["knowledge-base/projects/roadmap.md"]["status"] == "ok"
    assert rows["knowledge-base/projects/roadmap.md"]["last_commit"] == (NOW - timedelta(days=2)).date().isoformat()


def test_an_uncommitted_file_is_untracked_and_ranks_last(vault: Path) -> None:
    _commit(vault, "projects/old.md", days_ago=30)
    (vault / "knowledge-base" / "projects" / "new.md").write_text("# new\n", encoding="utf-8")

    rows = _json(vault)["files"]

    assert [(r["file"], r["status"]) for r in rows] == [
        ("knowledge-base/projects/old.md", "very-stale"),
        ("knowledge-base/projects/new.md", "untracked"),
    ]


# ---------- budgets ----------


def test_the_budget_tiers_are_the_kb_pre_filter_tiers() -> None:
    """One definition of "stale": if the kb-pre-filter tiers change, this script must follow."""
    mod = _module()
    assert mod.BASENAME_HOURS == kb_pre_filter.FRESHNESS_OVERRIDES
    assert mod.PRIORITY_HOURS == kb_pre_filter.PRIORITY_FRESHNESS
    assert mod.DEFAULT_HOURS == kb_pre_filter.DEFAULT_FRESHNESS_HOURS


@pytest.mark.parametrize(
    ("rel", "body"),
    [
        ("linear-issues.md", "# issues\n"),
        ("channels.md", "# channels\n"),
        ("projects/a.md", "---\npriority: 🟡 medium\n---\n"),
        ("projects/b.md", "**Priority:** 🟢\n"),
        ("projects/c.md", "priority: none\n"),
        ("projects/d.md", "# no priority\n"),
    ],
)
def test_a_file_gets_the_same_budget_as_in_the_kb_pre_filter(vault: Path, rel: str, body: str) -> None:
    path = vault / "knowledge-base" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    mod = _module()
    assert mod.freshness_hours(path, vault) == kb_pre_filter.freshness_hours_for(path)


@pytest.mark.parametrize("rel", ["people/alex.md", "personal/travel.md", "ontology/entities/org.md"])
def test_entity_files_get_a_thirty_day_budget(vault: Path, rel: str) -> None:
    _commit(vault, rel, days_ago=20)

    (row,) = _json(vault, "--all")["files"]

    assert (row["budget_hours"], row["status"]) == (720, "ok")


@pytest.mark.parametrize(
    "rel",
    [
        "archive/old-project.md",
        "projects/archived/old.md",
        "linear-issues-archive.md",
        "audits/audit-2026-01-02.md",
        "triage-2026-01-12.md",
        "events/summit-2026-01-05/notes/day-one.md",
    ],
)
def test_archives_and_dated_records_are_not_ranked(vault: Path, rel: str) -> None:
    """Archives and dated records (an audit, a triage note, an event's folder)
    are point-in-time by design: old is what they are meant to be."""
    _commit(vault, rel, days_ago=90)

    assert _json(vault, "--all")["files"] == []


# ---------- claimed-date divergence ----------


def test_a_claim_older_than_the_last_commit_is_flagged_as_a_cosmetic_edit(vault: Path) -> None:
    claimed = (NOW - timedelta(days=40)).date().isoformat()
    _commit(vault, "projects/roadmap.md", f"**Last verified:** {claimed}\n", days_ago=2)

    (row,) = _json(vault, "--divergence")["files"]

    assert row["divergence"] == "claim-lags-git"
    assert row["claimed_date"] == claimed


def test_a_claim_newer_than_any_commit_is_flagged(vault: Path) -> None:
    claimed = (NOW - timedelta(days=1)).date().isoformat()
    _commit(vault, "projects/roadmap.md", f"Last updated: {claimed} (see the thread)\n", days_ago=20)

    (row,) = _json(vault, "--divergence")["files"]

    assert row["divergence"] == "claim-ahead-of-git"


def test_a_month_name_claim_is_read(vault: Path) -> None:
    commit_day = NOW - timedelta(days=30)
    claimed = commit_day - timedelta(days=20)
    _commit(vault, "projects/roadmap.md", f"Last verified: {claimed:%B} {claimed.day}, {claimed.year}\n", days_ago=30)

    (row,) = _json(vault, "--all")["files"]

    assert row["claimed_date"] == claimed.date().isoformat()
    assert row["divergence"] == "claim-lags-git"


def test_an_aligned_or_missing_claim_is_not_flagged(vault: Path) -> None:
    _commit(vault, "projects/aligned.md", f"Last verified: {(NOW - timedelta(days=5)).date()}\n", days_ago=5)
    _commit(vault, "projects/none.md", days_ago=5)

    assert _json(vault, "--divergence")["files"] == []


# ---------- output ----------


def test_out_writes_the_cache_view_into_the_vault(vault: Path, tmp_path: Path) -> None:
    _commit(vault, "projects/roadmap.md", days_ago=10)

    subprocess.run(
        [sys.executable, str(SCRIPT), "--limit", "15", "--out", ".scout-cache/vault-freshness.md"],
        cwd=tmp_path,
        env={**os.environ, "SCOUT_DATA_DIR": str(vault)},
        check=True,
        capture_output=True,
    )

    view = (vault / ".scout-cache" / "vault-freshness.md").read_text(encoding="utf-8")
    assert view.startswith("# Vault Freshness")
    assert "| knowledge-base/projects/roadmap.md |" in view
    assert "🟡 stale" in view


def test_the_markdown_view_lists_divergent_claims(vault: Path) -> None:
    claimed = (NOW - timedelta(days=40)).date().isoformat()
    _commit(vault, "projects/roadmap.md", f"Last verified: {claimed}\n", days_ago=10)

    out = _run(vault).stdout

    assert "## Claimed-date divergence (1 file)" in out
    assert "git newer than claim" in out


def test_the_divergence_view_can_be_written_as_the_cache(vault: Path) -> None:
    _commit(vault, "projects/roadmap.md", days_ago=10)

    _run(vault, "--divergence", "--out", "div.md")

    assert "every claimed date aligns with git" in (vault / "div.md").read_text(encoding="utf-8")


# ---------- where the vault is ----------


def test_without_scout_data_dir_it_scans_the_vault_it_is_installed_in(vault: Path) -> None:
    _commit(vault, "projects/roadmap.md", days_ago=10)
    installed = vault / "scripts" / "vault-freshness.py"
    installed.parent.mkdir()
    shutil.copy(SCRIPT, installed)
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_DATA_DIR"}

    result = subprocess.run(
        [sys.executable, str(installed), "--json"], env=env, capture_output=True, text=True, check=True
    )

    assert _files(json.loads(result.stdout)) == ["knowledge-base/projects/roadmap.md"]


def test_a_vault_that_is_not_a_git_repo_lists_every_file_as_untracked(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "knowledge-base" / "notes.md").write_text("# notes\n", encoding="utf-8")

    (row,) = _json(vault)["files"]

    assert row["status"] == "untracked"


def test_a_vault_without_a_knowledge_base_scans_nothing(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    vault.mkdir()

    assert _json(vault)["counts"] == {"ok": 0, "stale": 0, "very-stale": 0, "untracked": 0}
