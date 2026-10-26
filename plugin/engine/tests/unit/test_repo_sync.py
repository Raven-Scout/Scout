"""scoutctl repo link | sync | unlink (Agnes R1): the filtered mirror and its gates.

The host is a fake over a local bare repository, so these tests run git for
real (ls-remote, fetch, push) without Agnes. The mirror's whole history is
walked to prove a Local-Only file never reaches it. The vault is the
anonymised privacy fixture vault (``privacy_vault_helpers``), whose overlay
keeps ``trading/**`` local-only.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import repo_sync as rs
from scout.cli import app
from scout.kb.privacy import label_file, load_policy
from scout.scripts import privacy_migrate

from .privacy_vault_helpers import CANARIES, build_privacy_vault, git, git_env

TRADING = "trading/positions.md"
PERSONAL = "knowledge-base/personal/task-renew-permit.md"


class FakeHost:
    """Agnes's REST contract over a local bare repository."""

    def __init__(self, bare: Path, local_only: Sequence[str] = ()) -> None:
        self.bare = bare
        self.exists = False
        self.local_only: tuple[str, ...] = tuple(local_only)
        self.puts: list[list[str]] = []
        self.deleted = False

    def status(self) -> rs.HostStatus:
        return rs.HostStatus(self.exists, True, str(self.bare), self.local_only)

    def create(self) -> rs.HostStatus:
        self.exists = True
        return self.status()

    def set_local_only(self, entries: Sequence[str]) -> tuple[str, ...]:
        self.puts.append(list(entries))
        self.local_only = tuple(entries)
        return self.local_only

    def delete(self) -> None:
        self.deleted = True

    def git_env(self) -> dict[str, str]:
        return {}

    lease: dict | None = None
    generation = 0
    lease_calls: list[str]

    def _lease_call(self, what: str) -> None:
        self.__dict__.setdefault("lease_calls", []).append(what)

    def lease_acquire(self, holder_ref: str, holder_label: str) -> dict:
        """Agnes's §5 semantics: free → granted; same holder → a new token; anyone else → 409."""
        self._lease_call("acquire")
        held = self.lease
        if held is not None and held["holder_ref"] != holder_ref:
            raise rs.RepoSyncError(
                "repository_lease_held", "held", {"holder": {"holder_kind": "agnes", "holder_label": held["label"]}}
            )
        if held is None:
            self.generation += 1
        token = f"lease-{self.generation}-{len(self.lease_calls)}"
        self.lease = {"holder_ref": holder_ref, "label": holder_label, "token": token}
        return {"token": token, "generation": self.generation, "expires_at": "2026-10-25T08:20:00Z", "held": True}

    def lease_renew(self, token: str) -> dict:
        self._lease_call("renew")
        if self.lease is None or self.lease["token"] != token:
            raise rs.RepoSyncError("repository_lease_lost", "lost")
        return {"generation": self.generation, "expires_at": "2026-10-25T08:40:00Z", "held": True}

    def lease_release(self, token: str) -> None:
        self._lease_call("release")
        if self.lease is None or self.lease["token"] != token:
            raise rs.RepoSyncError("repository_lease_lost", "lost")
        self.lease = None


def _bare_git(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(bare), *args], check=True, capture_output=True, text=True).stdout


def mirror_history_paths(bare: Path) -> set[str]:
    """Every path in every commit of the mirror's main."""
    out: set[str] = set()
    for rev in _bare_git(bare, "rev-list", "main").split():
        out.update(p for p in _bare_git(bare, "ls-tree", "-r", "--name-only", rev).splitlines())
    return out


def mirror_policy(bare: Path, rev: str = "main") -> dict:
    return json.loads(_bare_git(bare, "show", f"{rev}:{rs.POLICY_PATH}"))


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = build_privacy_vault(tmp_path).resolve()
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.setenv(rs.TEST_VAULT_ENV, str(v))
    privacy_migrate.accept(v)
    return v


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    bare = tmp_path / "mirror.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return FakeHost(bare)


def _link(vault: Path, host: FakeHost, **kw: object) -> rs.Result:
    return rs.link(vault, host, agnes_url="https://agnes.example.com", **kw)  # type: ignore[arg-type]


def _commit(vault: Path, rel: str, text: str, subject: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


# ----- link -------------------------------------------------------------------------------


def test_link_pushes_a_filtered_snapshot_with_the_label_manifest(vault: Path, host: FakeHost) -> None:
    result = _link(vault, host)

    assert len(result.pushed) == 1
    assert _bare_git(host.bare, "rev-list", "--count", "main").strip() == "1"
    files = mirror_history_paths(host.bare)
    assert TRADING not in files
    assert PERSONAL in files
    assert rs.POLICY_PATH in files
    assert not any(p.startswith(".scout-state/") and p not in rs.HOST_SYNCED_STATE for p in files)

    doc = mirror_policy(host.bare)
    assert doc["policy"] == load_policy(vault).to_json()
    by_path = {lb["path"]: lb for lb in doc["labels"]}
    assert set(by_path) == files - {rs.POLICY_PATH}
    policy = load_policy(vault)
    for rel in by_path:
        assert by_path[rel] == label_file(vault, rel, policy).to_json()
    assert by_path[PERSONAL]["visibility"] == "private"

    assert host.puts and "trading/" in host.local_only
    state = rs.load_state(vault)
    assert state is not None and state.last_mirror == result.pushed[0]
    assert git(vault, "check-ignore", rs.STATE_REL).strip() == rs.STATE_REL
    env = json.loads((vault / ".claude" / "settings.json").read_text())["env"]
    assert env == rs.OTLP_CONTENT_OFF


def test_link_refuses_a_vault_that_has_not_run_privacy_migrate(vault: Path, host: FakeHost) -> None:
    privacy_migrate.marker_path(vault).unlink()
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "privacy_migrate_missing"
    assert "/scout-update" in e.value.message
    assert not host.exists


def test_link_refuses_a_stale_privacy_migrate_acceptance(vault: Path, host: FakeHost) -> None:
    _commit(vault, "investments/2026.md", "x\n", "new folder")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "privacy_migrate_stale"
    privacy_migrate.accept(vault)
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "trading_paths_unconfirmed" and "investments/**" in e.value.message
    assert not host.exists


def test_a_real_vault_needs_the_widening_guard_and_its_hook(
    vault: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib
    import sys
    import types

    monkeypatch.delenv(rs.TEST_VAULT_ENV)
    module, name = rs.WIDENING_GUARD
    try:
        importlib.import_module(module)
        present = True
    except ImportError:
        present = False
    if not present:
        with pytest.raises(rs.RepoSyncError) as e:
            _link(vault, host)
        assert e.value.code == "privacy_guard_missing"
        monkeypatch.setitem(sys.modules, module, types.SimpleNamespace(**{name: lambda *a, **k: []}))
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "privacy_hook_missing"
    assert not host.exists

    from scout.kb.hook import MARKER, hooks_dir, install_hook

    install_hook(vault, scoutctl="scoutctl")
    hook = hooks_dir(vault) / "pre-commit"
    if rs.HOOK_FAILS_CLOSED not in hook.read_text():
        with pytest.raises(rs.RepoSyncError) as e:
            _link(vault, host)
        assert e.value.code == "privacy_hook_missing"
        hook.write_text(f"#!/bin/sh\n{MARKER}\necho 'kb-lint: {rs.HOOK_FAILS_CLOSED}' >&2\n")
    assert _link(vault, host).pushed


def test_the_live_vault_is_never_a_test_vault(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(vault.parent))
    live = vault.parent / "Scout"
    vault.rename(live)
    monkeypatch.setenv(rs.TEST_VAULT_ENV, str(live))
    assert rs._test_vault(live, dict(__import__("os").environ)) is False


@pytest.mark.parametrize("name", ["TRADING.md", "my-Portfolio.csv", "brokerage_2026.md"])
def test_a_root_trading_file_blocks_link_until_local_only_or_confirmed(vault: Path, host: FakeHost, name: str) -> None:
    _commit(vault, name, "Holdings: CANARY-ROOT-TRADE\n", "root trading file")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "trading_paths_unconfirmed"
    assert name in e.value.message
    assert not host.exists and host.puts == []

    result = _link(vault, host, confirm_sync=[name])
    assert name in mirror_history_paths(host.bare)
    assert rs.load_state(vault).confirmed_sync == [name]  # type: ignore[union-attr]
    assert result.pushed


def test_a_root_trading_file_made_local_only_never_syncs(vault: Path, host: FakeHost) -> None:
    _commit(vault, "TRADING.md", "Holdings: CANARY-ROOT-TRADE\n", "root trading file")
    from scout.scripts.privacy_migrate import add_local_only

    add_local_only(vault, ["TRADING.md"])
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "keep it local")
    _link(vault, host)
    assert "TRADING.md" not in mirror_history_paths(host.bare)
    assert "TRADING.md" in host.local_only


def test_a_trading_folder_the_owner_declined_blocks_link(vault: Path, host: FakeHost) -> None:
    _commit(vault, "Brokerage Statements/2026.md", "x\n", "statements")
    assert privacy_migrate.accept(vault)["declined_local_only"] == ["Brokerage Statements/**"]
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert "Brokerage Statements/**" in e.value.message


def test_link_refuses_while_the_overlay_is_unreadable(vault: Path, host: FakeHost) -> None:
    (vault / "scout-config.yaml").write_text("privacy: [unclosed\n", encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "privacy_overlay_unreadable"


def test_link_refuses_a_remote_with_history_unless_replacing(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    rs.unlink(vault, None, env={})
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "remote_not_empty"
    _link(vault, host, replace_remote=True)
    assert _bare_git(host.bare, "rev-list", "--count", "main").strip() == "2"


# ----- sync ---------------------------------------------------------------------------------


def test_sync_mirrors_each_new_commit_and_never_a_local_only_path(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, "knowledge-base/people/priya.md", "Priya owns PROJ-1234.\n", "people: priya")
    _commit(vault, TRADING, "CANARY-TRADE-2\n", "trading only")
    _commit(vault, "TRADING/Notes.md", "folded spelling CANARY-TRADE-3\n", "trading, other case")
    _commit(vault, PERSONAL, "renew in May\n", "personal")

    result = rs.sync(vault, host)

    assert len(result.pushed) == 2
    assert result.skipped == 2
    files = mirror_history_paths(host.bare)
    assert TRADING not in files and "TRADING/Notes.md" not in files
    for rev in _bare_git(host.bare, "rev-list", "main").split():
        doc = mirror_policy(host.bare, rev)
        assert doc["labels"] and doc["policy"]["local_only"]["default"] == ["trading/**"]
    subjects = _bare_git(host.bare, "log", "--format=%s", "main").splitlines()
    assert subjects[:2] == ["personal", "people: priya"]
    assert rs.sync(vault, host).pushed == []


def test_a_host_local_only_path_never_reaches_the_mirror(vault: Path, host: FakeHost) -> None:
    host.local_only = ("Raw/",)
    _link(vault, host)
    _commit(vault, "Raw/scan.txt", "rescanned\n", "raw")
    _commit(vault, "raw/other.txt", "folded\n", "raw lower")
    rs.sync(vault, host)
    files = mirror_history_paths(host.bare)
    assert not any(fp.casefold().startswith("raw/") for fp in files)
    assert "Raw/" in host.local_only and "trading/" in host.local_only


def test_sync_refuses_while_the_overlay_is_unreadable(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    (vault / "scout-config.yaml").write_text(": : :\n", encoding="utf-8")
    assert "privacy-overlay-unreadable" in load_policy(vault).warnings
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "privacy_overlay_unreadable"


def test_a_new_root_trading_file_blocks_sync(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    _commit(vault, "Positions.md", "x\n", "positions")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "trading_paths_unconfirmed"
    assert "Positions.md" not in mirror_history_paths(host.bare)


def _commit_on_mirror(tmp_path: Path, host: FakeHost, edits: dict[str, str | None], subject: str) -> str:
    work = tmp_path / "agnes-side"
    if not work.exists():
        subprocess.run(["git", "clone", "-q", str(host.bare), str(work)], check=True, env=git_env())
    else:
        git(work, "pull", "-q", "--ff-only")
    for rel, text in edits.items():
        p = work / rel
        if text is None:
            git(work, "rm", "-q", rel)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "-c", "commit.gpgsign=false", "commit", "-q", "-m", subject)
    git(work, "push", "-q", "origin", "HEAD:main")
    return git(work, "rev-parse", "HEAD").strip()


def test_a_mirror_commit_replays_onto_the_vault_as_one_commit(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    before = int(git(vault, "rev-list", "--count", "HEAD"))
    mirror_sha = _commit_on_mirror(
        tmp_path,
        host,
        {"knowledge-base/topics/misc/cloud.md": "Written in Agnes.\n", "knowledge-base/people/sam.md": None},
        "research: cloud note",
    )

    result = rs.sync(vault, host)

    assert len(result.replayed) == 1
    assert int(git(vault, "rev-list", "--count", "HEAD")) == before + 1
    assert git(vault, "log", "-1", "--format=%s").strip() == "research: cloud note"
    assert f"{rs.REPLAY_TRAILER}: {mirror_sha}" in git(vault, "log", "-1", "--format=%B")
    assert (vault / "knowledge-base/topics/misc/cloud.md").read_text() == "Written in Agnes.\n"
    assert not (vault / "knowledge-base/people/sam.md").exists()
    assert git(vault, "status", "--porcelain", "--untracked-files=no").strip() == ""
    assert len(result.pushed) == 1
    labels = {lb["path"] for lb in mirror_policy(host.bare)["labels"]}
    assert "knowledge-base/topics/misc/cloud.md" in labels and "knowledge-base/people/sam.md" not in labels
    assert _bare_git(host.bare, "rev-parse", "main~1").strip() == mirror_sha
    assert (vault / TRADING).exists()
    assert rs.sync(vault, host).pushed == []


def test_a_server_removal_of_a_new_local_only_path_leaves_the_laptop_copy(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    _link(vault, host)
    _commit_on_mirror(tmp_path, host, {"Raw/scan.txt": None}, "Remove Local-Only Paths from the repository")
    host.local_only = (*host.local_only, "Raw/")
    rs.sync(vault, host)
    assert (vault / "Raw/scan.txt").exists()
    assert "Raw/scan.txt" in git(vault, "ls-files", "Raw")


def test_laptop_and_mirror_commits_together(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    _commit(vault, "knowledge-base/people/priya.md", "laptop edit\n", "laptop edit")
    _commit_on_mirror(tmp_path, host, {"knowledge-base/topics/misc/cloud.md": "cloud\n"}, "cloud edit")
    result = rs.sync(vault, host)
    assert len(result.replayed) == 1 and len(result.pushed) == 1
    tip = _bare_git(host.bare, "show", "main:knowledge-base/people/priya.md")
    assert tip == "laptop edit\n"
    assert _bare_git(host.bare, "show", "main:knowledge-base/topics/misc/cloud.md") == "cloud\n"
    assert rs.sync(vault, host).pushed == []


def test_a_conflicting_replay_writes_nothing(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    rel = "knowledge-base/people/priya.md"
    _commit(vault, rel, "laptop version\n", "laptop")
    _commit_on_mirror(tmp_path, host, {rel: "agnes version\n"}, "agnes")
    head = git(vault, "rev-parse", "HEAD")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "replay_conflict"
    assert git(vault, "rev-parse", "HEAD") == head
    assert (vault / f"{rel}.proposed-merge").exists()


def test_the_push_guard_refuses_a_mirror_commit_with_a_local_only_path(vault: Path) -> None:
    rules = rs.LocalOnlyRules.build([load_policy(vault)], ())
    head = git(vault, "rev-parse", "HEAD").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        rs.check_mirror_commits(vault, rules, [head])
    assert e.value.code == "local_only_path_in_push"
    assert CANARIES[TRADING] not in e.value.message and TRADING not in e.value.message


@pytest.mark.parametrize(
    ("rel", "refused"),
    [
        (".scout-state/schedule.yaml", False),
        (".scout-state/repo-sync.json", True),
        (".Scout-Logs/x.jsonl", True),
        (".env.local", True),
        ("keys/deploy.pem", True),
        (".scout-secrets/token", True),
        ("Trading/a.md", True),
        ("trading​/a.md", True),
        ("knowledge-base/personal/x.md", False),
    ],
)
def test_local_only_rules(vault: Path, rel: str, refused: bool) -> None:
    assert rs.LocalOnlyRules.build([load_policy(vault)], ()).refused(rel) is refused


def test_host_entry_conversion() -> None:
    assert rs.host_entry("trading/**") == "trading/"
    assert rs.host_entry("TRADING.md") == "TRADING.md"
    assert rs.host_entry("**/secret.md") is None
    assert rs.host_entry("notes/*.md") is None
    assert rs.host_entry("../up") is None


# ----- unlink and the CLI ---------------------------------------------------------------------------


def test_unlink_forgets_the_link(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    rs.save_token(vault, "pat-stand-in")
    result = rs.unlink(vault, host, delete_remote=True, env={})
    assert host.deleted
    assert rs.load_state(vault) is None
    assert not rs.token_path(vault).exists()
    assert git(vault, "for-each-ref", "refs/scout-mirror/").strip() == ""
    assert result.notes


def test_otlp_settings_keep_other_keys(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"env": {"KEEP": "1", "OTEL_LOG_USER_PROMPTS": "1"}, "model": "x"}))
    assert rs.otlp_content_off(tmp_path) is True
    data = json.loads(settings.read_text())
    assert data["model"] == "x" and data["env"]["KEEP"] == "1"
    assert data["env"]["OTEL_LOG_USER_PROMPTS"] == "0"
    assert rs.otlp_content_off(tmp_path) is False


def test_token_is_stored_owner_only(vault: Path) -> None:
    rs.save_token(vault, "pat-stand-in")
    assert rs.token_path(vault).stat().st_mode & 0o777 == 0o600
    assert rs.load_token(vault) == "pat-stand-in"
    assert not str(rs.token_path(vault)).startswith(str(vault))


def test_cli_refusals_exit_2_with_a_code(vault: Path) -> None:
    runner = CliRunner()
    out = runner.invoke(app, ["repo", "sync", "--json"])
    assert out.exit_code == 2
    assert json.loads(out.stdout)["code"] == "not_linked"

    privacy_migrate.marker_path(vault).unlink()
    token = vault.parent / "token"
    token.write_text("pat-stand-in\n")
    out = runner.invoke(app, ["repo", "link", "--agnes-url", "http://127.0.0.1:9", "--token-file", str(token)])
    assert out.exit_code == 2
    assert "/scout-update" in out.stderr


# ----- the Agnes client, state and error paths ---------------------------------------------------


class _Resp:
    def __init__(self, status: int, body: object = None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = text

    def json(self) -> object:
        return self._body


def test_agnes_host_speaks_the_rest_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    calls: list[tuple[str, str, object, dict]] = []
    replies = iter(
        [
            _Resp(200, {"exists": False, "can_create": True, "clone_url": "https://a/knowledge-repository.git"}),
            _Resp(201, {"exists": True, "can_create": True, "clone_url": "u", "local_only_paths": ["Raw/"]}),
            _Resp(200, {"local_only_paths": ["Raw/", "trading/"], "removed_from_head": []}),
            _Resp(204),
        ]
    )

    def fake(method: str, url: str, headers: dict, json: object = None, timeout: float = 0) -> _Resp:
        calls.append((method, url, json, headers))
        return next(replies)

    monkeypatch.setattr(requests, "request", fake)
    host = rs.AgnesHost("https://agnes.example.com/", "pat-stand-in")
    assert host.status() == rs.HostStatus(False, True, "https://a/knowledge-repository.git", ())
    assert host.create().local_only_paths == ("Raw/",)
    assert host.set_local_only(["Raw/", "trading/"]) == ("Raw/", "trading/")
    host.delete()
    assert [c[0] for c in calls] == ["GET", "POST", "PUT", "DELETE"]
    assert calls[2][2] == {"paths": ["Raw/", "trading/"]}
    assert all(c[1].startswith("https://agnes.example.com/api/me/knowledge-repository") for c in calls)
    assert calls[0][3]["Authorization"] == "Bearer pat-stand-in"
    env = host.git_env()
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader" and "pat-stand-in" not in env["GIT_CONFIG_VALUE_0"]


@pytest.mark.parametrize(
    ("reply", "code"),
    [(_Resp(401), "host_auth"), (_Resp(404), "host_not_enabled"), (_Resp(500, text="boom"), "host_error")],
)
def test_agnes_host_refusals(monkeypatch: pytest.MonkeyPatch, reply: _Resp, code: str) -> None:
    import requests

    monkeypatch.setattr(requests, "request", lambda *a, **k: reply)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.AgnesHost("https://agnes.example.com", "t").status()
    assert e.value.code == code


def test_agnes_host_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def boom(*a: object, **k: object) -> None:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.AgnesHost("https://agnes.example.com", "t").status()
    assert e.value.code == "host_unreachable"


def test_state_and_token_errors(vault: Path) -> None:
    rs.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    rs.state_path(vault).write_text('{"schema_version": 99}')
    with pytest.raises(rs.RepoSyncError) as e:
        rs.load_state(vault)
    assert e.value.code == "state_unreadable"
    with pytest.raises(rs.RepoSyncError) as e:
        rs.load_token(vault)
    assert e.value.code == "token_missing"
    rs.save_token(vault, "   ")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.load_token(vault)
    assert e.value.code == "token_missing"
    rs.token_path(vault).chmod(0o644)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.load_token(vault)
    assert e.value.code == "token_insecure"


@pytest.mark.parametrize("body", ["not json", "[1]", '{"env": [1]}'])
def test_otlp_settings_that_cannot_be_merged_are_refused(tmp_path: Path, body: str) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(body)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.otlp_content_off(tmp_path)
    assert e.value.code == "settings_unreadable"
    assert settings.read_text() == body


def test_gitignore_is_created_or_extended(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert rs.ensure_ignored(tmp_path) is True
    assert rs.ensure_ignored(tmp_path) is False
    (tmp_path / ".gitignore").write_text("x")
    assert rs.ensure_ignored(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text().startswith("x\n")


def test_link_and_sync_preconditions(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "not_linked"

    _link(vault, host)
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "already_linked"
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host, confirm_sync=["x"], env={"SCOUT_MODE": "dreaming", rs.TEST_VAULT_ENV: str(vault)})
    assert e.value.code == "headless_refused"

    host.exists = False
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "remote_missing"
    host.exists = True

    host.local_only = (*host.local_only, ".scout-state/privacy-policy.json")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "policy_path_local_only"


def test_link_refuses_without_commits_or_rollout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    v = build_privacy_vault(tmp_path).resolve()
    monkeypatch.setenv(rs.TEST_VAULT_ENV, str(v))
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    privacy_migrate.accept(v)
    bare = tmp_path / "m.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    class NoRollout(FakeHost):
        def status(self) -> rs.HostStatus:
            return rs.HostStatus(False, False, str(self.bare), ())

    with pytest.raises(rs.RepoSyncError) as e:
        _link(v, NoRollout(bare))
    assert e.value.code == "host_cannot_create"

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "scout-config.yaml").write_text("user: {name: Alex}\n")
    git(empty, "init", "-q")
    monkeypatch.setenv(rs.TEST_VAULT_ENV, str(empty.resolve()))
    privacy_migrate.accept(empty)
    with pytest.raises(rs.RepoSyncError) as e:
        _link(empty, FakeHost(bare))
    assert e.value.code == "no_commits"


def test_sync_refuses_an_emptied_or_rewritten_mirror(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    work = tmp_path / "rewrite"
    subprocess.run(["git", "clone", "-q", str(host.bare), str(work)], check=True, env=git_env())
    git(work, "commit", "-q", "--amend", "-m", "rewritten")
    git(work, "push", "-q", "--force", "origin", "HEAD:main")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "mirror_diverged"

    _bare_git(host.bare, "update-ref", "-d", "refs/heads/main")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "remote_empty"


def test_a_refused_push_is_reported_with_its_code(vault: Path, host: FakeHost) -> None:
    hook = host.bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'local_only_path_pushed Raw/' >&2\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "local_only_path_pushed"
    assert rs.load_state(vault) is None


def test_a_rewritten_vault_history_pushes_head(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    git(vault, "reset", "-q", "--hard", "HEAD~1")
    _commit(vault, "knowledge-base/people/priya.md", "new line\n", "after a reset")
    result = rs.sync(vault, host)
    assert len(result.pushed) == 1 and result.notes
    assert _bare_git(host.bare, "show", "main:knowledge-base/people/priya.md") == "new line\n"


def test_a_commit_with_an_unreadable_overlay_goes_up_squashed(vault: Path, host: FakeHost) -> None:
    _link(vault, host)
    good = (vault / "scout-config.yaml").read_text()
    _commit(vault, "scout-config.yaml", "privacy: [unclosed\n", "broken config")
    _commit(vault, "scout-config.yaml", good, "fixed config")
    _commit(vault, "knowledge-base/people/priya.md", "edit\n", "edit")
    result = rs.sync(vault, host)
    assert len(result.pushed) == 1
    assert "Scout sync: 3 commit(s)" in _bare_git(host.bare, "log", "-1", "--format=%B", "main")
    assert TRADING not in mirror_history_paths(host.bare)


def test_replay_merges_cleanly_and_refuses_dirty_or_one_sided_changes(
    vault: Path, host: FakeHost, tmp_path: Path
) -> None:
    rel = "knowledge-base/people/priya.md"
    _commit(vault, rel, "one\ntwo\nthree\nfour\nfive\n", "lines")
    _link(vault, host)
    _commit(vault, rel, "ONE\ntwo\nthree\nfour\nfive\n", "laptop top")
    _commit_on_mirror(tmp_path, host, {rel: "one\ntwo\nthree\nfour\nFIVE\n"}, "agnes bottom")
    _commit_on_mirror(tmp_path, host, {"knowledge-base/people/sam.md": None}, "agnes delete")
    rs.sync(vault, host)
    assert (vault / rel).read_text() == "ONE\ntwo\nthree\nfour\nFIVE\n"

    _commit_on_mirror(tmp_path, host, {rel: "changed on agnes\n"}, "agnes edit")
    (vault / rel).write_text("uncommitted\n")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "replay_dirty"
    assert (vault / rel).read_text() == "uncommitted\n"
    git(vault, "checkout", "--", rel)
    rs.sync(vault, host)

    _commit(vault, "knowledge-base/people/sam.md", "laptop re-adds\n", "re-add")
    _commit_on_mirror(tmp_path, host, {"knowledge-base/people/sam.md": "agnes re-adds\n"}, "agnes re-add")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(vault, host)
    assert e.value.code == "replay_conflict"


def test_replay_skips_a_change_the_laptop_already_has(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    rel = "knowledge-base/people/priya.md"
    _commit(vault, rel, "same\n", "laptop")
    _commit_on_mirror(tmp_path, host, {rel: "same\n"}, "agnes same")
    result = rs.sync(vault, host)
    assert result.replayed == []


def test_cli_link_sync_and_unlink(vault: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs, "AgnesHost", lambda url, token: host)
    runner = CliRunner()
    out = runner.invoke(app, ["repo", "link", "--agnes-url", "https://agnes.example.com"], input="pat-stand-in\n")
    assert out.exit_code == 0, out.output
    assert "pushed 1 commit(s)" in out.output
    assert rs.load_token(vault) == "pat-stand-in"

    _commit(vault, "knowledge-base/people/priya.md", "x\n", "edit")
    out = runner.invoke(app, ["repo", "sync"])
    assert out.exit_code == 0 and "pushed 1 commit(s)" in out.output
    out = runner.invoke(app, ["repo", "sync", "--json"])
    assert json.loads(out.stdout)["ok"] is True

    out = runner.invoke(app, ["repo", "unlink", "--delete-remote", "--json"])
    assert out.exit_code == 0 and host.deleted
    out = runner.invoke(app, ["repo", "unlink", "--delete-remote"])
    assert out.exit_code == 2

    out = runner.invoke(app, ["repo", "link", "--agnes-url", "x", "--token-file", "/nonexistent/token"])
    assert out.exit_code == 2
    empty = vault.parent / "empty-token"
    empty.write_text("\n")
    out = runner.invoke(app, ["repo", "link", "--agnes-url", "x", "--token-file", str(empty)])
    assert out.exit_code == 2 and "no token" in out.stderr


def test_git_plumbing_edges(vault: Path) -> None:
    assert rs.host_entry("./trading/**") == "trading/"
    with pytest.raises(rs.RepoSyncError) as e:
        rs.git(vault, "rev-parse", "--verify", "no-such-ref")
    assert e.value.code == "git_failed"
    with pytest.raises(rs.RepoSyncError):
        rs.cat_blob(vault, "0" * 40)
    assert rs._blob_at(vault, "HEAD", "knowledge-base") is None


def test_policy_at_follows_load_policy(vault: Path) -> None:
    blobs = rs.BlobReader(vault)
    try:
        base = load_policy(None)
        _commit(vault, "scout-config.yaml", "", "empty config")
        assert rs.policy_at(vault, "HEAD", blobs) == base
        _commit(vault, "scout-config.yaml", "- a list\n", "list config")
        assert rs.policy_at(vault, "HEAD", blobs).warnings == ("privacy-overlay-unreadable",)
        git(vault, "rm", "-q", "scout-config.yaml")
        git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "no config")
        assert rs.policy_at(vault, "HEAD", blobs) == base
    finally:
        blobs.close()


def test_mirror_trees_drop_gitlinks_and_any_committed_policy_file(vault: Path) -> None:
    head = git(vault, "rev-parse", "HEAD").strip()
    git(vault, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor/sub")
    (vault / rs.POLICY_PATH).parent.mkdir(exist_ok=True)
    (vault / rs.POLICY_PATH).write_text('{"forged": true}\n')
    git(vault, "add", rs.POLICY_PATH)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "gitlink and policy file")
    assert "vendor/sub" in git(vault, "ls-tree", "-r", "--name-only", "HEAD").split()
    builder = rs.MirrorBuilder(vault, rs.LocalOnlyRules.build([load_policy(vault)], ()), ())
    try:
        tree, _ = builder.tree("HEAD", load_policy(vault))
    finally:
        builder.close()
    names = git(vault, "ls-tree", "-r", "--name-only", tree).split()
    assert "vendor/sub" not in names
    assert json.loads(git(vault, "cat-file", "blob", f"{tree}:{rs.POLICY_PATH}"))["generator"] == "scoutctl repo sync"


def test_the_push_guard_needs_the_policy_file(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    _commit(repo, "a.md", "x\n", "one")
    rules = rs.LocalOnlyRules.build([load_policy(None)], ())
    with pytest.raises(rs.RepoSyncError) as e:
        rs.check_mirror_commits(repo, rules, [git(repo, "rev-parse", "HEAD").strip()])
    assert e.value.code == "policy_missing"


def test_a_trading_folder_on_the_host_list_is_not_asked_about(vault: Path) -> None:
    _commit(vault, "Brokerage/2026.md", "x\n", "statements")
    rules = rs.LocalOnlyRules.build([load_policy(vault)], ("Brokerage/",))
    assert "Brokerage/**" not in rs.trading_candidates(vault, rules)


def test_link_refuses_a_host_list_covering_the_policy_file(vault: Path, host: FakeHost) -> None:
    host.local_only = (".scout-state/privacy-policy.json",)
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, host)
    assert e.value.code == "policy_path_local_only"


def test_sync_adds_new_vault_local_only_paths_to_agnes(vault: Path, host: FakeHost, tmp_path: Path) -> None:
    _link(vault, host)
    privacy_migrate.add_local_only(vault, ["Raw/**"])
    seen: list[str] = []
    _commit_on_mirror(tmp_path, host, {"knowledge-base/topics/misc/cloud.md": "c\n"}, "cloud")
    rs.sync(vault, host, progress=seen.append)
    assert "Raw/" in host.local_only
    assert seen and seen[0].startswith("replayed ")
    assert not any(p.startswith("Raw/") for p in _bare_git(host.bare, "ls-tree", "-r", "--name-only", "main").split())


def test_unlink_delete_remote_needs_a_host(vault: Path) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        rs.unlink(vault, None, delete_remote=True, env={})
    assert e.value.code == "token_missing"
