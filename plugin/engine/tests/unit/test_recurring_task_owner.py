"""templates/scripts/recurring-task-status.py — who owns each recurring task.

A recurring task records who actually does it in ``observed_author`` (a run's
standing judgement) or ``last_completed_by`` (the latest completion). Nothing
read those fields, so every ``due`` cadence became the vault owner's 🔴 even when
a teammate writes it. Each row now carries ``owner`` / ``owner_source`` /
``owner_is_user``, matched against the vault owner's identities in
scout-config.yaml, and the table lists the ``due``/``overdue`` rows that are
someone else's to do.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "recurring-task-status.py"
TASKS = Path("knowledge-base") / "recurring-tasks"
FRIDAY = "2026-05-22"  # the due day of a weekly:friday cadence; Fridays 05-08 and 05-15 lapsed since 05-01

CONFIG = {
    "user": {"name": "Alex Doe", "email": "alex@example.com"},
    "connectors": {"inputs": {"github_username": "alex-gh", "user_slack_id": "U0123ALEX"}},
}


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("recurring_task_status", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rts() -> ModuleType:
    return _module()


@pytest.fixture
def alex(rts: ModuleType, tmp_path: Path):
    (tmp_path / "scout-config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    return rts.vault_user(tmp_path)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "scout-config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    (tmp_path / TASKS).mkdir(parents=True)
    return tmp_path


def _entity(vault: Path, slug: str, **extra: str) -> None:
    fields = {
        "type": "recurring_task",
        "name": slug,
        "cadence": "weekly:friday",
        "surface_window": "T-0 morning",
        "last_completed_date": "2026-05-01",
        **extra,
    }
    body = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True)
    path = vault / TASKS / f"{slug}.md"
    path.write_text(f"---\n{body}---\n\n# {slug}\n", encoding="utf-8")


def _run(vault: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dir", str(vault / TASKS), "--date", FRIDAY, *extra],
        capture_output=True,
        text=True,
        check=True,
    )


def _rows(vault: Path) -> dict[str, dict]:
    return {r["name"]: r for r in json.loads(_run(vault, "--json").stdout)}


# ----- resolve_owner -------------------------------------------------------------


def test_observed_author_wins_over_last_completed_by(rts: ModuleType, alex) -> None:
    entity = {"observed_author": "Priya", "last_completed_by": "Sam"}
    assert rts.resolve_owner(entity, alex) == ("Priya", "observed_author", False)


def test_falls_back_to_last_completed_by(rts: ModuleType, alex) -> None:
    assert rts.resolve_owner({"last_completed_by": "Sam"}, alex) == ("Sam", "last_completed_by", False)


def test_the_vault_owner_is_recognised(rts: ModuleType, alex) -> None:
    assert rts.resolve_owner({"observed_author": "Alex Doe"}, alex) == ("Alex Doe", "observed_author", True)


def test_no_owner_fields_leaves_the_task_the_vault_owners(rts: ModuleType, alex) -> None:
    assert rts.resolve_owner({"name": "weekly-update"}, alex) == ("", "", True)


@pytest.mark.parametrize(
    ("value", "owner", "is_user"),
    [
        ("Priya  # corrected: was Alex Doe", "Priya", False),
        ("Alex Doe   # confirmed by DM", "Alex Doe", True),
    ],
)
def test_an_inline_note_after_the_name_is_ignored(rts: ModuleType, alex, value: str, owner: str, is_user: bool) -> None:
    """Quoted values keep a trailing `# …` note that YAML would otherwise drop; read
    whole, every annotated entity would compare unequal and read as someone else's."""
    assert rts.resolve_owner({"observed_author": value}, alex) == (owner, "observed_author", is_user)


@pytest.mark.parametrize("value", ["alex", "ALEX DOE", "alex-gh", "alex@example.com", "U0123ALEX"])
def test_every_form_of_the_vault_owners_identity_matches(rts: ModuleType, alex, value: str) -> None:
    assert rts.resolve_owner({"observed_author": value}, alex)[2] is True


def test_someone_sharing_the_first_name_is_not_the_vault_owner(rts: ModuleType, alex) -> None:
    assert rts.resolve_owner({"observed_author": "Alex Other"}, alex)[2] is False


def test_an_empty_value_falls_through_to_the_next_field(rts: ModuleType, alex) -> None:
    entity = {"observed_author": "   ", "last_completed_by": "Sam"}
    assert rts.resolve_owner(entity, alex) == ("Sam", "last_completed_by", False)


@pytest.mark.parametrize("config", [None, "", "- not a mapping\n", "user: [oops]\n"])
def test_without_the_vault_owners_identity_every_task_stays_theirs(rts: ModuleType, tmp_path: Path, config) -> None:
    if config is not None:
        (tmp_path / "scout-config.yaml").write_text(config, encoding="utf-8")
    nobody = rts.vault_user(tmp_path)
    assert rts.resolve_owner({"observed_author": "Priya"}, nobody) == ("Priya", "observed_author", True)


# ----- compute_status --------------------------------------------------------------


def test_someone_elses_due_cadence_stays_due_and_says_whose_it_is(rts: ModuleType, alex) -> None:
    entity = {
        "name": "project-weekly-update",
        "cadence": "weekly:friday",
        "surface_window": "T-0 morning",
        "last_completed_date": "2026-05-01",
        "observed_author": "Priya",
    }
    row = rts.compute_status(entity, date.fromisoformat(FRIDAY), None, alex)
    assert row.status == "due"  # the lapse stays visible
    assert row.windows_missed == 2
    assert (row.owner, row.owner_source, row.owner_is_user) == ("Priya", "observed_author", False)
    assert "[not Alex Doe's — observed_author: Priya; a 🟢 Watching item waiting on Priya" in row.reason


def test_the_vault_owners_own_cadence_is_not_annotated(rts: ModuleType, alex) -> None:
    entity = {"name": "mine", "cadence": "weekly:friday", "observed_author": "Alex Doe"}
    row = rts.compute_status(entity, date.fromisoformat(FRIDAY), None, alex)
    assert row.status == "due"
    assert row.owner_is_user is True
    assert "not Alex Doe's" not in row.reason


# ----- the CLI -----------------------------------------------------------------------


def test_json_rows_carry_the_owner(vault: Path) -> None:
    _entity(vault, "theirs", observed_author="Priya")
    _entity(vault, "mine", last_completed_by="alex@example.com")
    _entity(vault, "unrecorded")
    rows = _rows(vault)
    assert {k: rows["theirs"][k] for k in ("owner", "owner_source", "owner_is_user")} == {
        "owner": "Priya",
        "owner_source": "observed_author",
        "owner_is_user": False,
    }
    assert rows["mine"]["owner_is_user"] is True
    assert (rows["unrecorded"]["owner"], rows["unrecorded"]["owner_is_user"]) == ("", True)


def test_table_has_an_owner_column_and_lists_due_rows_that_are_someone_elses(vault: Path) -> None:
    _entity(vault, "theirs", observed_author="Priya")
    _entity(vault, "secret-theirs", last_completed_by="Sam", visibility="private")
    _entity(vault, "mine", observed_author="Alex Doe")
    table = _run(vault).stdout
    assert "| Name | Status | Owner | Missed | Cadence |" in table
    assert "| theirs | **due** | **Priya** (not Alex Doe) | **2** | weekly:friday |" in table
    assert "| mine | **due** | Alex Doe | **2** |" in table
    footer = table.split("⚠️", 1)[1]
    assert "2 `due`/`overdue` row(s) above are not Alex Doe's to do" in footer
    assert "  - `theirs` → Priya (observed_author)" in footer
    # The private mark goes before the name, in the footer as in the row.
    assert "| [private] secret-theirs | **due** | **Sam** (not Alex Doe) |" in table
    assert "  - `[private] secret-theirs` → Sam (last_completed_by)" in footer
    assert "`mine`" not in footer


def test_no_footer_when_every_due_row_is_the_vault_owners(vault: Path) -> None:
    _entity(vault, "mine", observed_author="Alex")
    _entity(vault, "unrecorded")
    assert "⚠️" not in _run(vault).stdout


def test_someone_elses_row_that_is_not_due_gets_no_footer(vault: Path) -> None:
    _entity(vault, "theirs", observed_author="Priya", last_completed_date=FRIDAY)
    out = _run(vault).stdout
    assert "| theirs | **done** | **Priya** (not Alex Doe) |" in out
    assert "⚠️" not in out


def test_warns_when_the_vault_owner_is_unknown(vault: Path) -> None:
    (vault / "scout-config.yaml").unlink()
    _entity(vault, "theirs", observed_author="Priya")
    proc = _run(vault, "--json")
    assert json.loads(proc.stdout)[0]["owner_is_user"] is True
    assert "can't read the vault owner's name or email" in proc.stderr
