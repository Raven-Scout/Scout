"""A value PyYAML can't build in a config file: what each caller does with it.

``yaml.safe_load`` scans ``d: 2026-13-45`` fine, then raises a bare ``ValueError``
(``KeyError``, ``IndexError``, ``AttributeError``, ``OverflowError`` and
``RecursionError`` for other values). ``test_kb_privacy_unconstructible_yaml``
pins the frontmatter side; this file pins the config side:

- privacy findings keep blocking, under the ``privacy:`` block when it is intact;
- the other lint settings fall back to the defaults with a warning;
- ``load_config`` degrades, and the doctor and run health say what is wrong.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from scout import config as scout_config
from scout import connectors as scout_connectors
from scout import custom_connectors, health, health_notify
from scout import schedule as scout_schedule
from scout.cli import app
from scout.errors import ConfigError
from scout.kb import outbound
from scout.kb.lint import run_hook
from scout.kb.lint_config import load_lint_config
from scout.kb.lint_privacy import PolicyUnreadable, policy_from_config_text, privacy_staged
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.privacy import load_policy
from scout.scripts import auto_update_config, bootstrap, connector_probes, models_config, privacy_migrate
from scout.scripts.auto_update_config import AutoUpdateWriteError
from scout.scripts.bootstrap_doctor import run_doctor
from scout.scripts.models_config import ModelsWriteError
from scout.triggers import config as triggers_config
from scout.triggers.sources import slack
from scout.yaml_safe import YAMLValueError, error_line, load_yaml, load_yaml_key, yaml_problem

from .privacy_vault_helpers import build_privacy_vault, git
from .test_kb_privacy_unconstructible_yaml import UNCONSTRUCTIBLE

RUN = {"SCOUT_MODE": "morning-briefing"}
SECRET_NOTE = "knowledge-base/topics/misc/canary-plan.md"
LINK = "see [[canary-plan]]\n"
GOOD_CONFIG = "user:\n  timezone: UTC\nkb_lint:\n  mode: block\n"
OVERLAY = (
    "privacy:\n"
    "  shared_files_add:\n"
    "    - 'extra/**'\n"
    "  visibility_defaults:\n"
    "    - glob: 'knowledge-base/hidden/**'\n"
    "      visibility: private\n"
)

# Values that scan and compose but don't construct, so the rest of the file is still readable.
BUILD_STAGE = [
    pytest.param("d: 2026-13-45", id="value-error"),
    pytest.param('n: !!int ""', id="index-error"),
    pytest.param("n: !!bool foo", id="key-error"),
    pytest.param("n: !!timestamp foo", id="attribute-error"),
]


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(vault: Path, subject: str) -> None:
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def _break_config(vault: Path, config: str) -> None:
    """``config`` in the working tree, the index and HEAD, as a hand edit that got committed leaves it."""
    _write(vault, "scout-config.yaml", config)
    _commit(vault, "break the config")


def _blocking(vault: Path, env: dict[str, str] | None = None) -> list[tuple[str, str]]:
    return [(f.path, f.check) for f in privacy_staged(vault, env=env or {}) if f.blocking]


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_MODEL", raising=False)
    clear_frontmatter_cache()
    outbound.clear_cache()
    v = build_privacy_vault(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    _write(v, SECRET_NOTE, "---\nvisibility: private\n---\n# Canary Plan\n\nBody.\n")
    _commit(v, "a private note")
    return v


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    return tmp_path


# ----- the shared parse --------------------------------------------------------


def test_the_key_comes_back_when_the_bad_value_is_elsewhere() -> None:
    assert load_yaml_key("d: 2026-13-45\nprivacy:\n  a: 1\n", "privacy") == {"a": 1}


def test_a_missing_key_is_none() -> None:
    assert load_yaml_key("d: 2026-13-45\n", "privacy") is None


def test_the_last_duplicate_key_wins_as_in_safe_load() -> None:
    text = "privacy: 1\nprivacy: 2\n"
    assert load_yaml_key(text, "privacy") == yaml.safe_load(text)["privacy"]


def test_aliases_inside_the_key_resolve() -> None:
    assert load_yaml_key("a: &x [1]\nprivacy: *x\nd: 2026-13-45\n", "privacy") == [1]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("privacy:\n  d: 2026-13-45\n", id="bad-value-inside"),
        pytest.param("privacy: [unclosed\n", id="does-not-scan"),
        pytest.param("- a\n- b\n", id="not-a-mapping"),
        pytest.param("<<: {privacy: {a: 1}}\nd: 2026-13-45\n", id="merge-key-could-hide-it"),
        pytest.param("", id="empty"),
        pytest.param('n: "\\U99999999"\nprivacy: {a: 1}\n', id="fails-while-scanning"),
    ],
)
def test_the_key_is_unreadable(text: str) -> None:
    if text:
        with pytest.raises(yaml.YAMLError):
            load_yaml_key(text, "privacy")
    else:
        assert load_yaml_key(text, "privacy") is None


def test_a_loader_can_be_named() -> None:
    assert load_yaml("a: 1\n", loader=yaml.SafeLoader) == {"a": 1}


def test_error_line_names_the_top_level_value() -> None:
    assert error_line("a: 1\nb: 2\nd: 2026-13-45\ne: 3\n") == 3


def test_error_line_names_a_syntax_error() -> None:
    assert error_line("a: 1\nb: [unclosed\n") == 3


def test_error_line_is_none_when_it_cannot_tell() -> None:
    assert error_line("a: 1\n") is None
    assert error_line("- a\n") is None
    assert error_line("n: " + "[" * 5000 + "]" * 5000) is None


def test_yaml_problem_never_quotes_the_text() -> None:
    assert yaml_problem(YAMLValueError("ValueError"), "a: 1\nn: !!int CANARY-7Q2X\n") == "ValueError at line 2"
    with pytest.raises(yaml.YAMLError) as e:
        load_yaml("a: [CANARY-7Q2X\n")
    assert "CANARY" not in yaml_problem(e.value, "a: [CANARY-7Q2X\n")
    assert yaml_problem(YAMLValueError("RecursionError"), "x") == "RecursionError"


# ----- the two loaders ---------------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_read_yaml_raises_config_error(tmp_path: Path, body: str) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text(body + "\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        scout_config._read_yaml(path)


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_load_config_degrades_with_a_warning(
    config_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    (config_dir / "scout-config.yaml").write_text("kb_lint:\n  mode: block\n" + body + "\n", encoding="utf-8")
    monkeypatch.setenv("SCOUT_USER_EMAIL", "alex@example.com")
    cfg = scout_config.load_config(config_dir)
    assert cfg["kb_lint"]["mode"] != "block"
    assert cfg["user"]["email"] == "alex@example.com"  # the env layer still applies
    err = capsys.readouterr().err
    assert "ignoring unreadable scout-config.yaml" in err and "running on packaged defaults" in err


def test_the_warning_does_not_quote_the_value(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (config_dir / "scout-config.yaml").write_text("a: 1\nn: !!int CANARY-7Q2X\n", encoding="utf-8")
    scout_config.load_config(config_dir)
    err = capsys.readouterr().err
    assert "CANARY" not in err and "ValueError at line 2" in err


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_migration_loader_returns_none(body: str) -> None:
    assert privacy_migrate._load(body) is None


def test_the_migration_loader_still_refuses_duplicate_keys() -> None:
    assert privacy_migrate._load("a: 1\na: 2\n") is None
    assert privacy_migrate._load("a: 1\n") == {"a": 1}


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_add_local_only_refuses_and_leaves_the_file_alone(tmp_path: Path, body: str) -> None:
    config = tmp_path / "scout-config.yaml"
    config.write_text(body + "\n", encoding="utf-8")
    before = config.read_bytes()
    with pytest.raises(ValueError, match="doesn't parse"):
        privacy_migrate.add_local_only(tmp_path, ["trading/**"])
    assert config.read_bytes() == before


# ----- privacy fails closed ----------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_a_private_link_in_a_shared_file_still_blocks(vault: Path, body: str) -> None:
    _break_config(vault, GOOD_CONFIG + body + "\n")
    _write(vault, "drafts/post.md", LINK)
    git(vault, "add", "drafts/post.md")
    assert _blocking(vault) == [("drafts/post.md", "private-link")]
    out = io.StringIO()
    assert run_hook(vault, env={}, out=out) == 1
    assert "Commit blocked by the privacy checks" in out.getvalue()


@pytest.mark.parametrize("body", BUILD_STAGE)
def test_a_private_link_in_a_file_the_overlay_shares_still_blocks(vault: Path, body: str) -> None:
    _break_config(vault, GOOD_CONFIG + OVERLAY + body + "\n")
    _write(vault, "extra/note.md", LINK)
    git(vault, "add", "extra/note.md")
    assert _blocking(vault) == [("extra/note.md", "private-link")]
    assert run_hook(vault, env={}, out=io.StringIO()) == 1


@pytest.mark.parametrize("body", BUILD_STAGE)
def test_a_note_only_the_overlay_makes_private_still_counts(vault: Path, body: str) -> None:
    _write(vault, "knowledge-base/hidden/quiet-plan.md", "# Quiet Plan\n\nBody.\n")
    _break_config(vault, GOOD_CONFIG + OVERLAY + body + "\n")
    _write(vault, "drafts/post.md", "see [[quiet-plan]]\n")
    git(vault, "add", "drafts/post.md")
    assert _blocking(vault) == [("drafts/post.md", "private-link")]


def test_the_overlay_at_head_still_counts_when_the_commit_breaks_the_config(vault: Path) -> None:
    _write(vault, "scout-config.yaml", GOOD_CONFIG + OVERLAY)
    _commit(vault, "an overlay")
    # The whole config is unreadable in the commit, and there's no privacy: block left to recover.
    _write(vault, "scout-config.yaml", "d: 2026-13-45\n")
    _write(vault, "extra/note.md", LINK)
    git(vault, "add", "scout-config.yaml", "extra/note.md")
    assert _blocking(vault) == [("extra/note.md", "private-link")]


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_a_scheduled_commit_is_refused_while_the_config_is_broken(vault: Path, body: str) -> None:
    _break_config(vault, GOOD_CONFIG + body + "\n")
    _write(vault, "drafts/post.md", "nothing private\n")
    git(vault, "add", "drafts/post.md")
    with pytest.raises(PolicyUnreadable):
        privacy_staged(vault, env=RUN)
    assert run_hook(vault, env=RUN, out=io.StringIO()) == 1


def test_a_scheduled_commit_is_refused_even_with_the_block_recovered(vault: Path) -> None:
    _break_config(vault, GOOD_CONFIG + OVERLAY + "d: 2026-13-45\n")
    _write(vault, "drafts/post.md", "nothing private\n")
    git(vault, "add", "drafts/post.md")
    with pytest.raises(PolicyUnreadable):
        privacy_staged(vault, env=RUN)


@pytest.mark.parametrize("body", BUILD_STAGE)
def test_a_recovered_overlay_stays_flagged_unreadable(tmp_path: Path, body: str) -> None:
    text = OVERLAY + body + "\n"
    (tmp_path / "scout-config.yaml").write_text(text, encoding="utf-8")
    for policy in (load_policy(tmp_path), policy_from_config_text(text)):
        assert "privacy-overlay-unreadable" in policy.warnings  # so repo sync and scheduled commits still refuse
        assert "extra/**" in policy.shared_files
        assert ("knowledge-base/hidden/**", "private") in policy.visibility_defaults


def test_a_recovered_overlay_keeps_its_own_warnings() -> None:
    text = "privacy:\n  shared_files_add: nope\nd: 2026-13-45\n"
    assert set(policy_from_config_text(text).warnings) == {"privacy-overlay-invalid", "privacy-overlay-unreadable"}


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(OVERLAY + "  d: 2026-13-45\n", id="bad-value-inside-the-block"),
        pytest.param(OVERLAY + "n: [unclosed\n", id="syntax-error"),
    ],
)
def test_an_unrecoverable_overlay_warns_the_owner(vault: Path, config: str) -> None:
    """The one case left open (Jordan's ruling, 2026-10-22, spec 8.3): the overlay can't be read at all.

    The engine's default policy applies, the owner's commit goes on with a warning, and a
    scheduled run's commit is refused. Engine-default shared files keep blocking.
    """
    _break_config(vault, config)
    _write(vault, "extra/note.md", LINK)
    git(vault, "add", "extra/note.md")
    findings = privacy_staged(vault, env={})
    assert [(f.check, f.blocking) for f in findings] == [("privacy-overlay-unreadable", False)]
    with pytest.raises(PolicyUnreadable):
        privacy_staged(vault, env=RUN)
    _write(vault, "drafts/post.md", LINK)
    git(vault, "add", "drafts/post.md")
    assert _blocking(vault) == [("drafts/post.md", "private-link")]


# ----- the other lint settings fall back ---------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_size_lint_runs_on_the_defaults(vault: Path, capfd: pytest.CaptureFixture[str], body: str) -> None:
    _break_config(vault, "kb_lint:\n  mode: block\n" + body + "\n")
    _write(vault, "knowledge-base/topics/misc/plain.md", "# Plain\n\nBody.\n")
    git(vault, "add", "knowledge-base/topics/misc/plain.md")
    out = io.StringIO()
    assert run_hook(vault, env={}, out=out) == 0
    assert "size/diary check failed" not in out.getvalue()
    assert "ignoring unreadable scout-config.yaml" in capfd.readouterr().err
    assert load_lint_config(vault).mode == "report"


def test_kb_lint_report_keeps_working(vault: Path) -> None:
    _break_config(vault, "d: 2026-13-45\n")
    result = CliRunner().invoke(app, ["kb", "lint", "--report", "--repo", str(vault)])
    assert result.exit_code == 0, result.output
    assert "file(s) over budget" in result.stdout


# ----- where the owner hears about it ------------------------------------------


def _seed_doctor(vault: Path, config: str) -> None:
    _write(vault, ".scout-state/schedule.yaml", "schema_version: 1\nslots: {}\n")
    _write(vault, "scout-config.yaml", config)


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_doctor_reports_it_and_does_not_crash(tmp_path: Path, body: str) -> None:
    vault = tmp_path / "v"
    _seed_doctor(vault, body + "\n")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert any(e.startswith("scout-config.yaml invalid:") for e in report.errors)


def test_the_doctor_names_the_line_and_not_the_value(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _seed_doctor(vault, "a: 1\nn: !!int CANARY-7Q2X\n")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    joined = "\n".join(report.errors + report.warnings)
    assert "scout-config.yaml invalid: ValueError at line 2" in joined
    assert "CANARY" not in joined


def test_the_doctor_reports_a_config_that_is_not_a_mapping(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _seed_doctor(vault, "- a\n- b\n")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert "scout-config.yaml invalid: not a mapping" in report.errors


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_doctor_reports_a_schedule_with_such_a_value(tmp_path: Path, body: str) -> None:
    vault = tmp_path / "v"
    _seed_doctor(vault, "a: 1\n")
    _write(vault, ".scout-state/schedule.yaml", body + "\n")
    report = run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert any(e.startswith("schedule.yaml invalid:") for e in report.errors)


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_run_health_goes_red(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body + "\n", encoding="utf-8")
    report = health.evaluate(tmp_path, now=datetime(2026, 1, 15, 12, tzinfo=UTC), home=tmp_path / "home")
    (finding,) = [f for f in report.findings if f.id == "config.unreadable"]
    assert finding.severity == "red" and report.severity == "red"
    assert "scoutctl bootstrap doctor" in finding.fix


def test_run_health_names_the_line_and_not_the_value(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("a: 1\nn: !!int CANARY-7Q2X\n", encoding="utf-8")
    assert health.config_findings(tmp_path)[0].summary.count("line 2") == 1
    assert "CANARY" not in repr(health.config_findings(tmp_path))


@pytest.mark.parametrize(
    ("write", "problem"),
    [
        pytest.param(None, None, id="no-file"),
        pytest.param(b"a: 1\n", None, id="fine"),
        pytest.param(b"", None, id="empty"),
        pytest.param(b"- a\n", "not a mapping", id="not-a-mapping"),
        pytest.param(b"\xff\xfe\x00", "UnicodeDecodeError", id="binary"),
        pytest.param(b"a: [x\n", "ParserError at line 2", id="syntax-error"),
    ],
)
def test_config_load_problem(tmp_path: Path, write: bytes | None, problem: str | None) -> None:
    if write is not None:
        (tmp_path / "scout-config.yaml").write_bytes(write)
    assert scout_config.config_load_problem(tmp_path) == problem
    assert bool(health.config_findings(tmp_path)) is (problem is not None)


def test_config_load_problem_on_a_read_error(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").mkdir()
    assert scout_config.config_load_problem(tmp_path) == "IsADirectoryError"


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_push_path_survives_it(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body + "\n", encoding="utf-8")
    assert health_notify._configured_command(tmp_path) is None


# ----- scheduled runs ----------------------------------------------------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_model_resolve_gives_the_default_and_a_clear_line(config_dir: Path, body: str) -> None:
    (config_dir / "scout-config.yaml").write_text("models:\n  default: sonnet\n" + body + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["model", "resolve"])
    assert result.exit_code == 0, result.output
    assert "opus" in result.stdout
    assert "ignoring unreadable scout-config.yaml" in result.stderr


def test_self_update_reads_a_broken_config_as_disabled(config_dir: Path) -> None:
    from scout.scripts.self_update_run import auto_update_settings

    (config_dir / "scout-config.yaml").write_text("auto_update:\n  enabled: true\nd: 2026-13-45\n", encoding="utf-8")
    assert auto_update_settings(scout_config.load_config(config_dir))[0] is False


def test_planning_show_and_the_timezone_keep_working(config_dir: Path) -> None:
    (config_dir / "scout-config.yaml").write_text("user:\n  timezone: UTC\nd: 2026-13-45\n", encoding="utf-8")
    assert CliRunner().invoke(app, ["planning", "show"]).exit_code == 0
    assert scout_config.resolve_timezone(config_dir) is not None


# ----- the other config readers: same family, their own handlers ----------------


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_schedule_triggers_and_connectors_raise_config_error(tmp_path: Path, body: str) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(body + "\n", encoding="utf-8")
    for load in (scout_schedule._load_yaml, scout_connectors._load_yaml, triggers_config.load_triggers):
        with pytest.raises(ConfigError, match="malformed"):
            load(path)


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_custom_connector_readers_keep_their_promises(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body + "\n", encoding="utf-8")
    (tmp_path / custom_connectors.CUSTOM_FILE).write_text(body + "\n", encoding="utf-8")
    assert custom_connectors.enabled_keys(tmp_path) == set()
    assert custom_connectors.bash_binaries(tmp_path) == {}
    assert custom_connectors.load(tmp_path, plugin_root=tmp_path).issues


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_tool_gate_fails_closed(tmp_path: Path, body: str) -> None:
    path = tmp_path / custom_connectors.CUSTOM_FILE
    path.write_text("# restrict_tools\n" + body + "\n", encoding="utf-8")
    assert custom_connectors.GATE_ALL in custom_connectors.tool_gate(tmp_path)


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_slack_source_reads_no_user(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body + "\n", encoding="utf-8")
    assert slack._user_id_from_config(tmp_path) is None


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_the_config_writers_refuse(body: str) -> None:
    text = body + "\n"
    with pytest.raises(ModelsWriteError, match="cannot parse"):
        models_config.apply_models_default(text, "sonnet")
    with pytest.raises(AutoUpdateWriteError, match="cannot parse"):
        auto_update_config.apply_auto_update(text, enabled=True)
    assert auto_update_config._loads_as(text, {}) is False


@pytest.mark.parametrize("body", UNCONSTRUCTIBLE)
def test_bootstrap_and_probes_raise_a_yaml_error(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body + "\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        bootstrap.config_from_vault(tmp_path, plugin_root=tmp_path, plugin_version="0")
    probes = tmp_path / "connector-probes.yaml"
    probes.write_text(body + "\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        connector_probes.load_registry(probes)
