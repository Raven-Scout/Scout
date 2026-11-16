"""Model-provider consent (spec 2026-11-13 §8.4, Q4; plan Task 1.7)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.agent_runtimes import check as rt_check
from scout.agent_runtimes import consent
from scout.agent_runtimes.base import TIER_F_REQUIRES
from scout.kb import hook_paths
from tests._agent_runtimes import DummyRuntime


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    for var in ("SCOUT_MODE", "SCOUT_READER"):
        monkeypatch.delenv(var, raising=False)
    return v


def test_default_providers_is_anthropic_only(home: Path, vault: Path) -> None:
    assert [c.provider for c in consent.consented(vault)] == ["anthropic"]
    assert consent.is_consented(vault, "anthropic")
    assert not consent.is_consented(vault, "openai")


def test_consent_records_provider_and_timestamp(home: Path, vault: Path) -> None:
    at = consent.consent(vault, " OpenAI ")
    assert at.endswith("Z")
    record = json.loads(consent.record_path().read_text(encoding="utf-8"))
    assert record["vaults"][str(vault.resolve())]["providers"]["openai"]["consented_at"] == at
    assert oct(consent.record_path().stat().st_mode & 0o777) == "0o600"
    assert [(c.provider, c.consented_at) for c in consent.consented(vault)] == [("anthropic", None), ("openai", at)]
    assert consent.consent(vault, "openai") == at  # idempotent: the first consent's time stays


def test_consent_is_per_vault(home: Path, vault: Path, tmp_path: Path) -> None:
    consent.consent(vault, "openai")
    other = tmp_path / "Other"
    other.mkdir()
    assert not consent.is_consented(other, "openai")


def test_revoke_removes_it(home: Path, vault: Path) -> None:
    consent.consent(vault, "google")
    assert consent.revoke(vault, "google") is True
    assert not consent.is_consented(vault, "google")
    assert consent.revoke(vault, "google") is False
    with pytest.raises(ValueError):
        consent.revoke(vault, "anthropic")


def test_the_overlay_can_narrow_never_grant(home: Path, vault: Path) -> None:
    (vault / "scout-config.yaml").write_text("privacy:\n  providers: [anthropic, google]\n", encoding="utf-8")
    # Listed in the overlay, not recorded: not consented. A run that edits scout-config can't grant.
    assert not consent.is_consented(vault, "google")
    consent.consent(vault, "google")
    consent.consent(vault, "openai")
    assert consent.is_consented(vault, "google")
    # Recorded, but the overlay leaves it out: not consented. A run that edits scout-config can revoke.
    assert not consent.is_consented(vault, "openai")
    (vault / "scout-config.yaml").write_text("privacy:\n  providers: openai\n", encoding="utf-8")
    with pytest.raises(ValueError, match="privacy.providers"):
        consent.consented(vault)


def test_a_corrupt_record_is_no_consent(home: Path, vault: Path) -> None:
    consent.record_path().parent.mkdir(parents=True)
    consent.record_path().write_text("{not json", encoding="utf-8")
    assert [c.provider for c in consent.consented(vault)] == ["anthropic"]


def test_check_refuses_unconsented_provider(home: Path, vault: Path) -> None:
    dummy = DummyRuntime("dummy", provider="openai", caps=TIER_F_REQUIRES, binary=Path("/bin/true"))
    r = rt_check.check(vault, dummy, reader="autonomous", mode="dreaming", interactive=False)
    assert r is not None and r.code == "provider_not_consented"
    assert "scoutctl privacy consent-provider openai" in r.reason
    assert r.exit_code == rt_check.EXIT_REFUSE
    # Consent doesn't lift the local-only exclusion, which the read gate learns in Phase 2.
    consent.consent(vault, "openai")
    r = rt_check.check(vault, dummy, reader="autonomous", mode="dreaming", interactive=False)
    assert r is not None and r.code == "local_only_unenforced"


# -- the CLI ------------------------------------------------------------------------------------------------


def _cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    from scout.cli import app

    res = CliRunner().invoke(app, list(args), env=env)
    return res.exit_code, res.output


def test_cli_consent_and_revoke(home: Path, vault: Path) -> None:
    code, out = _cli("privacy", "consent-provider", "openai")
    assert code == 0 and "consented: openai at" in out
    code, out = _cli("privacy", "providers", "--json")
    assert code == 0
    assert [r["provider"] for r in json.loads(out)] == ["anthropic", "openai"]
    code, out = _cli("privacy", "revoke-provider", "openai")
    assert code == 0 and "revoked: openai" in out


@pytest.mark.parametrize("env", [{"SCOUT_MODE": "dreaming"}, {"SCOUT_READER": "autonomous"}])
def test_a_run_cant_consent(home: Path, vault: Path, env: dict[str, str]) -> None:
    code, out = _cli("privacy", "consent-provider", "openai", env=env)
    assert code == 2 and "refused" in out
    assert not consent.record_path().exists()
    consent.consent(vault, "openai")
    code, _ = _cli("privacy", "revoke-provider", "openai", env=env)
    assert code == 2
    assert consent.is_consented(vault, "openai")


def test_the_consent_record_is_protected_in_both_layers(tmp_path: Path) -> None:
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
    record = consent.record_path(env)
    assert record.name == hook_paths.PROVIDER_CONSENT_NAME
    assert hook_paths.protected_write(record, v, env) == hook_paths.ENGINE
    assert sbx.decide(sbx.build_rules(v, env), str(record)) == "deny"
    assert hook_paths.protected_token(f"open('{record}', 'w')") is not None
    hook_paths._ROOTS_CACHE.clear()
