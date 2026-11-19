"""A delegate's inbox (#550 V4a1): link, key, propose, and the locked-down pull.

The inbox is a local bare repository, so git runs for real; GitHub is a fake
``gh``. The agent's side writes through ``propose`` into a real clone. Most
tests here are a hostile inbox: what the pull refuses, ignores or leaves, and
that nothing it reads reaches the vault but a recorded, exact copy.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_inbox as di
from scout import delegate_mirror as dm
from scout import delegate_proposals as dp
from scout.cli import app, delegate_write_refusal
from scout.repo_sync import RepoSyncError

from .privacy_vault_helpers import git_env

D = "grok-personal"
runner = CliRunner()


def _run(*args: str, cwd: Path | None = None, input: bytes | None = None, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        list(args), cwd=cwd, input=input, capture_output=True, check=True, env={**git_env(), **(env or {})}
    )
    return proc.stdout.decode().strip()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"  # the state key goes here, never to the real home
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    for key in ("SCOUT_MODE", "SCOUT_READER", "SCOUT_INBOX_DIR", "SCOUT_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(di.ALLOW_LOCAL_ENV, "1")  # local bare inboxes: tests only
    v = (tmp_path / "vault").resolve()
    v.mkdir()
    _run("git", "init", "-q", "-b", "main", str(v))
    (v / "README.md").write_text("vault\n", encoding="utf-8")
    _run("git", "-C", str(v), "add", "-A")
    _run("git", "-C", str(v), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "start")
    links = {
        d: dm.DelegateLink(id=d, remote=str(tmp_path / f"{d}-mirror.git"), linked_at="2026-11-19T00:00:00Z",
                           account_unshared=True)
        for d in (D, "duet")
    }  # fmt: skip
    dm.save_links(v, links)
    return v


def _bare(tmp_path: Path, name: str = "inbox") -> Path:
    bare = tmp_path / f"{name}.git"
    _run("git", "init", "-q", "--bare", "-b", "main", str(bare))
    _run("git", "-C", str(bare), "config", "uploadpack.allowFilter", "true")
    return bare


@pytest.fixture
def inbox(vault: Path, tmp_path: Path) -> Path:
    bare = _bare(tmp_path)
    di.link(vault, D, str(bare))
    return bare


@pytest.fixture
def clone(inbox: Path, tmp_path: Path) -> Path:
    c = tmp_path / "agent-inbox"
    _run("git", "clone", "-q", str(inbox), str(c))
    return c


def _agent_env(clone: Path) -> dict[str, str]:
    return {"SCOUT_READER": f"delegate:{D}", "SCOUT_INBOX_DIR": str(clone)}


def _propose(clone: Path, kind: str = "observation", body: dict[str, object] | None = None) -> di.Proposed:
    return di.propose(kind, body or {"text": "A deadline moved"}, push=True, env=_agent_env(clone))


def _main_files(bare: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"], capture_output=True)
    return set(out.stdout.decode().split()) if out.returncode == 0 else set()


def _commit_entries(bare: Path, entries: dict[str, tuple[str, bytes]], *, parent: bool = True) -> str:
    """A commit on the bare repository's main adding ``{path: (mode, data)}``, made with plumbing (any mode)."""
    index = {"GIT_INDEX_FILE": str(bare / "test.index")}
    head = subprocess.run(["git", "-C", str(bare), "rev-parse", "-q", "--verify", "main"], capture_output=True)
    base = head.stdout.decode().strip() if head.returncode == 0 and parent else None
    Path(index["GIT_INDEX_FILE"]).unlink(missing_ok=True)
    if base:
        _run("git", "-C", str(bare), "read-tree", base, env=index)
    records = b""
    for path, (mode, data) in entries.items():
        sha = "1" * 40 if mode == "160000" else _run("git", "-C", str(bare), "hash-object", "-w", "--stdin", input=data)
        records += f"{mode} {sha}\t".encode() + path.encode("utf-8", "surrogateescape") + b"\0"
    _run("git", "-C", str(bare), "update-index", "--add", "-z", "--index-info", input=records, env=index)
    tree = _run("git", "-C", str(bare), "write-tree", "--missing-ok", env=index)
    commit = _run("git", "-C", str(bare), "commit-tree", tree, *(["-p", base] if base else []), "-m", "agent")
    _run("git", "-C", str(bare), "update-ref", "refs/heads/main", commit)
    return commit


def _valid(text: str = "ok") -> dp.Proposal:
    return dp.make(D, "observation", {"text": text})


def _ulid_at(when: dt.datetime, tail: str = "0000000000000000") -> str:
    ms, head = int(when.timestamp() * 1000), ""
    for _ in range(10):
        ms, digit = divmod(ms, 32)
        head = dp._CROCKFORD[digit] + head
    return head + tail


# ----- the round trip --------------------------------------------------------------------------


def test_propose_push_and_pull_take_exact_copies_and_empty_the_inbox(vault: Path, inbox: Path, clone: Path) -> None:
    a = _propose(clone)
    b = _propose(clone, "action_item_op", {"op": "snooze", "tag": "AB12", "date": "2026-11-19",
                                           "args": {"until": "2026-11-25"}})  # fmt: skip
    assert a.pushed and b.pushed
    assert _main_files(inbox) == {a.path, b.path}

    r = di.pull(vault, D)
    assert sorted(r.taken) == sorted([a.id, b.id]) and r.removed == 2 and r.removal
    assert _main_files(inbox) == set()
    author = _run("git", "-C", str(inbox), "log", "-1", "--format=%an <%ae>|%s", "main")
    assert author == "Scout <scout@localhost>|Scout: took proposals from the inbox"
    state = di.load_state(vault)
    for p in (a, b):
        data = di.copy_path(vault, D, p.id).read_bytes()
        assert dp.parse(p.path, data, D).id == p.id
        assert state.taken[D][p.id]["mac"] == di.proposal_mac(D, p.id, data)
    assert di.ledger(vault, D) == {a.id, b.id}
    assert state.inboxes[D].base == r.removal and state.inboxes[D].high_water == max(a.id, b.id)

    again = di.pull(vault, D)
    assert again.taken == [] and again.removal is None and again.already == 0

    c = _propose(clone)  # the agent's clone is behind the removal commit: propose rebases, then pushes
    assert c.pushed
    assert di.pull(vault, D).taken == [c.id]


def test_no_main_is_reported_apart_from_an_empty_inbox(vault: Path, inbox: Path) -> None:
    r = di.pull(vault, D)
    assert r.no_main and r.taken == []
    _commit_entries(inbox, {"README.md": ("100644", b"the agent's notes\n")})
    r = di.pull(vault, D)
    assert not r.no_main and r.taken == [] and r.removal is None
    assert _main_files(inbox) == {"README.md"}


# ----- a hostile inbox -------------------------------------------------------------------------


def test_every_kind_of_bad_entry_is_refused_removed_and_never_copied(vault: Path, inbox: Path) -> None:
    good = _valid()
    other = dp.make("duet", "observation", {"text": "x"})
    ahead = dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)
    future = dataclasses.replace(_valid(), id=_ulid_at(ahead))
    names = [dp.make(D, "observation", {"text": str(i)}).id for i in range(8)]
    bad = {
        f"inbox/{D}/{names[0]}.json": ("100644", b"x" * (dp.MAX_BYTES + 1)),
        f"inbox/{D}/{names[1]}.json": ("100644", b"{not json"),
        f"inbox/{D}/{names[2]}.json": ("120000", b"../../../etc/passwd"),
        f"inbox/{D}/{names[3]}.json": ("100755", good.to_bytes()),
        f"inbox/{D}/{names[4]}.json": ("160000", b""),
        f"inbox/{D}/{names[5]}.json": ("100644", other.to_bytes()),
        f"inbox/{D}/notes.json": ("100644", b"{}"),
        future.path: ("100644", future.to_bytes()),
    }
    left_alone = {
        f"inbox/duet/{other.id}.json": ("100644", other.to_bytes()),
        ".github/workflows/run.yml": ("100644", b"on: push\njobs: {}\n"),
        f"INBOX/{D}/{good.id}.json": ("100644", good.to_bytes()),
    }
    _commit_entries(inbox, {**bad, **left_alone, good.path: ("100644", good.to_bytes())})

    r = di.pull(vault, D)
    assert r.taken == [good.id]
    assert r.refused == {"too_big": 1, "not_json": 1, "bad_mode": 3, "bad_id": 1, "bad_path": 1, "future_id": 1}
    assert _main_files(inbox) == set(left_alone)
    copies = {p.name for p in vault.joinpath(*di.INBOX_DIR, D).iterdir()}
    assert copies == {f"{good.id}.json", di.LEDGER}
    state = di.load_state(vault)
    assert list(state.taken[D]) == [good.id]
    assert len(state.refused[D]) == 8 and all(set(e) == {"entry", "code", "at"} for e in state.refused[D])
    assert all("passwd" not in json.dumps(e) for e in state.refused[D])  # a refusal keeps a hash, never content


def test_a_tag_or_another_branch_is_never_read(vault: Path, inbox: Path) -> None:
    hidden = _valid()
    commit = _commit_entries(inbox, {hidden.path: ("100644", hidden.to_bytes())})
    _run("git", "-C", str(inbox), "tag", "v1", commit)
    _run("git", "-C", str(inbox), "branch", "other", commit)
    _run("git", "-C", str(inbox), "update-ref", "-d", "refs/heads/main")
    assert di.pull(vault, D).no_main


def test_a_rewritten_main_is_refused_until_the_owner_resets(vault: Path, inbox: Path, clone: Path) -> None:
    _propose(clone)
    di.pull(vault, D)
    planted = _valid()
    _commit_entries(inbox, {planted.path: ("100644", planted.to_bytes())}, parent=False)  # a new root: force-push
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_rewritten"
    state = di.load_state(vault)
    assert state.inboxes[D].last_error["code"] == "inbox_rewritten" and planted.id not in state.taken[D]
    with pytest.raises(RepoSyncError) as e:
        di.reset(vault, D, confirm=lambda d, m: False)
    assert e.value.code == "not_confirmed"
    shown: list[str] = []
    di.reset(vault, D, confirm=lambda d, m: shown.append(m) or True)
    assert shown == [_run("git", "-C", str(inbox), "rev-parse", "main")]
    assert di.pull(vault, D).taken == [planted.id]


def test_the_base_is_found_by_deepening_and_only_so_far(
    vault: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(di, "MAX_DEEPEN", 48)
    monkeypatch.setattr(di, "DEEPEN_STEP", 16)
    _commit_entries(inbox, {"README.md": ("100644", b"0\n")})
    di.pull(vault, D)
    for i in range(20):
        _commit_entries(inbox, {"README.md": ("100644", f"{i}\n".encode())})
    late = _valid()
    _commit_entries(inbox, {late.path: ("100644", late.to_bytes())})
    assert di.pull(vault, D).taken == [late.id]  # 21 commits ahead: deepened, still found
    for i in range(di.MAX_DEEPEN + 5):
        _commit_entries(inbox, {"README.md": ("100644", f"far {i}\n".encode())})
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_rewritten"


def test_a_flood_is_taken_fifty_a_pull_and_two_hundred_a_day(vault: Path, inbox: Path) -> None:
    flood = [_valid(text=f"n{i}") for i in range(di.MAX_TAKEN_PER_DAY + 10)]
    _commit_entries(inbox, {p.path: ("100644", p.to_bytes()) for p in flood})
    now = dt.datetime.now(dt.UTC)
    got: list[str] = []
    for _ in range(4):
        r = di.pull(vault, D, now=now)
        assert len(r.taken) == dp.MAX_PER_PULL
        got += r.taken
    assert got == sorted(p.id for p in flood)[: di.MAX_TAKEN_PER_DAY]  # oldest first
    r = di.pull(vault, D, now=now)
    assert r.taken == [] and r.waiting == 10 and "wait for a later pull" in r.notes[-1]
    assert len(_main_files(inbox)) == 10
    assert len(di.pull(vault, D, now=now + dt.timedelta(days=1)).taken) == 10


def test_a_proposal_in_the_ledger_is_never_taken_again(vault: Path, inbox: Path) -> None:
    p = _valid()
    folder = vault.joinpath(*di.INBOX_DIR, D)
    folder.mkdir(parents=True)
    (folder / di.LEDGER).write_text(f"{p.id}\nnot-a-ulid\n", encoding="utf-8")  # taken on another computer
    _commit_entries(inbox, {p.path: ("100644", p.to_bytes())})
    r = di.pull(vault, D)
    assert r.taken == [] and r.already == 1 and r.removed == 1
    assert not di.copy_path(vault, D, p.id).exists() and p.id not in di.load_state(vault).taken.get(D, {})


def test_a_crash_before_the_removal_takes_nothing_twice(
    vault: Path, inbox: Path, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _propose(clone)

    def boom(self: object, commit: str, expect: str) -> bool:
        raise RepoSyncError("inbox_push_failed", "refused: network")

    real = di._Sandbox.push
    monkeypatch.setattr(di._Sandbox, "push", boom)
    with pytest.raises(RepoSyncError):
        di.pull(vault, D)
    assert p.id in di.load_state(vault).taken[D] and di.copy_path(vault, D, p.id).exists()
    monkeypatch.setattr(di._Sandbox, "push", real)  # not undo(): that would drop the test's HOME too
    di.copy_path(vault, D, p.id).unlink()  # and say the copy never landed either
    r = di.pull(vault, D)
    assert r.taken == [] and r.already == 1 and r.removed == 1
    assert di.copy_path(vault, D, p.id).exists()  # copied again: its bytes still match the record
    ledger = vault.joinpath(*di.INBOX_DIR, D, di.LEDGER).read_text(encoding="utf-8")
    assert ledger.count(p.id) == 1


def test_a_lost_race_with_the_agent_runs_the_pull_once_more(
    vault: Path, inbox: Path, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _propose(clone)
    real = di._Sandbox.remove
    pushed: list[str] = []

    def agent_pushes_meanwhile(self: di._Sandbox, main: str, paths: object, when: dt.datetime) -> str:
        if not pushed:
            pushed.append(_propose(clone).id)
        return real(self, main, paths, when)  # type: ignore[arg-type]

    monkeypatch.setattr(di._Sandbox, "remove", agent_pushes_meanwhile)
    r = di.pull(vault, D)
    assert r.taken == pushed and r.already == 1 and r.removal is not None
    assert set(di.load_state(vault).taken[D]) == {first.id, *pushed}
    assert _main_files(inbox) == set()


def test_a_planted_symlink_or_hard_link_in_the_copies_folder_refuses_before_recording(
    vault: Path, inbox: Path, clone: Path, tmp_path: Path
) -> None:
    p = _propose(clone)
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / ".scout-inbox").mkdir()
    (vault / ".scout-inbox" / "agents").symlink_to(outside)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_folder_unsafe"
    assert list(outside.iterdir()) == [] and D not in di.load_state(vault).taken
    assert _main_files(inbox) == {p.path}  # still there for the next pull

    (vault / ".scout-inbox" / "agents").unlink()
    folder = vault.joinpath(*di.INBOX_DIR, D)
    folder.mkdir(parents=True)
    target = tmp_path / "victim.txt"
    target.write_text("keep\n", encoding="utf-8")
    os.link(target, folder / di.LEDGER)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_folder_unsafe" and target.read_text(encoding="utf-8") == "keep\n"


# ----- the link: binding and checks ------------------------------------------------------------


def test_link_refuses_what_isnt_an_inbox_of_its_own(vault: Path, tmp_path: Path) -> None:
    def refused(code: str, delegate: str = D, remote: str = "", **kw: object) -> None:
        with pytest.raises(RepoSyncError) as e:
            di.link(vault, delegate, remote, **kw)  # type: ignore[arg-type]
        assert e.value.code == code, e.value.message

    refused("not_linked", "nobody", str(_bare(tmp_path, "x")))
    refused("bad_arguments")
    refused("inbox_taken", remote=str(tmp_path / f"{D}-mirror.git"))  # the mirror's own repository
    refused("remote_in_vault", remote=str(vault / "inbox.git"))
    refused("remote_unsupported", remote="https://example.com/inbox.git")
    shared = _bare(tmp_path, "shared")
    di.link(vault, D, str(shared))
    refused("inbox_exists", remote=str(shared))
    refused("inbox_taken", "duet", str(shared))
    refused("headless_refused", "duet", str(_bare(tmp_path, "y")), env={"SCOUT_MODE": "briefing"})
    assert ".scout-state/delegate-inbox/" in (vault / ".gitignore").read_text(encoding="utf-8")


def test_an_inbox_replaced_at_the_same_path_is_refused(vault: Path, inbox: Path, tmp_path: Path) -> None:
    subprocess.run(["rm", "-rf", str(inbox)], check=True)
    _bare(tmp_path)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_rebound"


def test_a_forged_state_is_never_trusted(vault: Path, inbox: Path) -> None:
    path = di.state_path(vault)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["taken"] = {D: {"01JD5Z0000000000000000ABCD": {"mac": "0" * 64, "at": "x"}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_state_unverified"


def test_the_fetch_runs_locked_down(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath'='/x'")
    env = di.sandbox_env(vault, "https://github.com/someone/inbox.git", tmp_path / "x.git")
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs["core.hooksPath"] == "/dev/null" and pairs["transfer.fsckObjects"] == "true"
    assert pairs["protocol.allow"] == "never" and pairs["protocol.https.allow"] == "always"
    assert "protocol.file.allow" not in pairs and pairs["http.sslVerify"] == "true"
    assert pairs["submodule.recurse"] == "false" and pairs["http.followRedirects"] == "false"
    assert env["GIT_NO_LAZY_FETCH"] == "1" and env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert env["GIT_DIR"] == str(tmp_path / "x.git")
    for gone in ("GIT_SSL_NO_VERIFY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_AUTHOR_NAME"):
        assert gone not in env


# ----- GitHub: binding by id, the checks, the write key ----------------------------------------

REPO = "someone/grok-inbox"
URL = f"https://github.com/{REPO}.git"


class FakeGh:
    """``gh`` for one GitHub inbox repository; every answer can be changed per test."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.facts = {"id": "777", "owner_id": "42", "owner": "someone", "owner_type": "User",
                      "visibility": "private", "fork": "false", "has_pages": "false", "full_name": REPO}  # fmt: skip
        self.actions = "false"
        self.collaborators = ["someone"]
        self.read_only = "false"
        self.keys: dict[int, str] = {}
        self.apps: list[str] = []
        self.key_read_only: dict[int, str] = {}

    def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
        a = [str(x) for x in args]  # type: ignore[attr-defined]
        self.calls.append(a)

        def ok(out: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(["gh"], 0, out, "")

        if a[:2] == ["api", "user"]:
            return ok("someone\n")
        if a[:2] == ["repo", "create"]:
            return ok()
        if a[:3] == ["api", "-X", "PUT"]:
            self.actions = "false"
            return ok()
        if a[:2] == ["api", "user/installations"]:
            return ok("".join(f"{x}\n" for x in self.apps))
        if a[:3] == ["api", "-X", "POST"]:
            new = 500 + len(self.keys)
            self.keys[new] = next(x[6:] for x in a if x.startswith("title="))
            assert "read_only=false" in a
            return ok(f"{new}\t{self.read_only}\n")
        if a[:3] == ["api", "-X", "DELETE"]:
            self.keys.pop(int(a[3].rsplit("/", 1)[1]), None)
            return ok()
        if a[:3] == ["api", "--paginate", f"repos/{REPO}/keys"]:
            return ok("".join(f"{k}\t{self.key_read_only.get(k, 'false')}\n" for k in self.keys))
        if a[0] == "api" and "/keys/" in a[1]:
            key_id = int(a[1].rsplit("/", 1)[1])
            if key_id not in self.keys:
                return subprocess.CompletedProcess(["gh"], 1, "", "gh: Not Found (HTTP 404)")
            return ok(self.keys[key_id] + "\n")
        if a[1].endswith("/actions/permissions"):
            return ok(self.actions + "\n")
        if any(x.endswith("/collaborators") for x in a):
            return ok("".join(f"{c}\n" for c in self.collaborators))
        if a[1] in (f"repos/{REPO}", f"repositories/{self.facts['id']}"):
            keys = ("id", "owner_id", "owner", "owner_type", "visibility", "fork", "has_pages", "full_name")
            return ok("\t".join(self.facts[k] for k in keys) + "\n")
        raise AssertionError(f"unexpected gh call {a}")


def _fake_keygen(path: Path, comment: str) -> None:
    path.write_text("PRIVATE-KEY-STAND-IN\n", encoding="utf-8")
    path.with_name(path.name + ".pub").write_text(f"ssh-ed25519 AAAAstandin {comment}\n", encoding="utf-8")


def test_create_repo_turns_actions_off_and_binds_by_id(vault: Path) -> None:
    gh = FakeGh()
    gh.actions = "true"
    gh.apps = ["some-ci-app"]
    r = di.link(vault, D, create_repo=REPO, gh=gh)
    assert ["api", "-X", "PUT", f"repos/{REPO}/actions/permissions", "-F", "enabled=false"] in gh.calls
    assert di.load_state(vault).inboxes[D].bound_to == "github:777:42"
    assert any("some-ci-app" in n for n in r.notes)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"visibility": "public"}, "remote_public"),
        ({"fork": "true"}, "remote_fork"),
        ({"owner_type": "Organization"}, "remote_org_owned"),
        ({"has_pages": "true"}, "inbox_pages_on"),
        ({"full_name": "someone/renamed"}, "inbox_rebound"),
        ({"owner_id": "43"}, "inbox_rebound"),
    ],
)
def test_github_inbox_is_checked_before_every_use(vault: Path, change: dict[str, str], code: str) -> None:
    gh = FakeGh()
    di.link(vault, D, URL, gh=gh)
    gh.facts.update(change)
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    assert e.value.code == code
    assert not gh.keys


def test_github_actions_or_a_collaborator_refuse(vault: Path) -> None:
    gh = FakeGh()
    gh.actions = "true"
    with pytest.raises(RepoSyncError) as e:
        di.link(vault, D, URL, gh=gh)
    assert e.value.code == "inbox_actions_on" and D not in di.load_state(vault).inboxes
    gh.actions, gh.collaborators = "false", ["someone", "friend"]
    with pytest.raises(RepoSyncError) as e:
        di.link(vault, D, URL, gh=gh)
    assert e.value.code == "remote_shared"


def test_the_inbox_key_is_writable_titled_and_kept_apart(vault: Path) -> None:
    gh = FakeGh()
    di.link(vault, D, URL, gh=gh)
    r = di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    assert gh.keys == {500: f"scout-delegate-inbox-{D}"}
    key = di.key_path(vault, D)
    assert key.exists() and key != dm.key_path(vault, D) and oct(key.stat().st_mode & 0o777) == "0o600"
    assert di.load_state(vault).inboxes[D].deploy_key_id == 500 and any("--inbox" in n for n in r.notes)
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    assert e.value.code == "key_exists"
    di.deploy_key(vault, D, rotate=True, gh=gh, keygen=_fake_keygen)
    assert list(gh.keys) == [501]
    gh.keys[501] = "someone-elses-key"
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, revoke=True, gh=gh, keygen=_fake_keygen)
    assert e.value.code == "key_not_ours" and 501 in gh.keys
    gh.keys[501] = f"scout-delegate-inbox-{D}"
    di.deploy_key(vault, D, revoke=True, gh=gh, keygen=_fake_keygen)
    assert gh.keys == {} and not key.exists()


def test_a_key_github_records_read_only_is_deleted_and_refused(vault: Path) -> None:
    gh = FakeGh()
    gh.read_only = "true"
    di.link(vault, D, URL, gh=gh)
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    assert e.value.code == "key_not_writable" and gh.keys == {}


def test_unlink_revokes_the_key_and_keeps_the_copies(vault: Path) -> None:
    gh = FakeGh()
    di.link(vault, D, URL, gh=gh)
    di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    di.unlink(vault, D, gh=gh)
    assert gh.keys == {} and D not in di.load_state(vault).inboxes


# ----- the agent's side ------------------------------------------------------------------------


def test_propose_runs_only_as_a_delegate_in_an_inbox_clone(vault: Path, clone: Path, tmp_path: Path) -> None:
    def refused(code: str, env: dict[str, str], kind: str = "observation", body: object = None) -> None:
        with pytest.raises(RepoSyncError) as e:
            di.propose(kind, body or {"text": "x"}, env=env)  # type: ignore[arg-type]
        assert e.value.code == code, e.value.message

    refused("not_a_delegate", {"SCOUT_INBOX_DIR": str(clone)})
    refused("no_inbox_clone", {"SCOUT_READER": f"delegate:{D}"})
    refused("no_inbox_clone", {**_agent_env(clone), "SCOUT_INBOX_DIR": str(tmp_path)})
    refused("not_an_inbox", {**_agent_env(clone), "SCOUT_INBOX_DIR": str(vault)})
    refused("not_an_inbox", {**_agent_env(clone), "SCOUT_DATA_DIR": str(clone.parent)})
    refused("proposal_bad_field", _agent_env(clone), body={"text": "see [[a note]]"})
    refused("proposal_bad_op", _agent_env(clone), "action_item_op", {"op": "dismiss", "tag": "AB12",
                                                                     "date": "2026-11-19", "args": {}})  # fmt: skip
    out = di.propose("observation", {"text": "x"}, env=_agent_env(clone))
    assert not out.pushed and (clone / out.path).is_file()
    author = _run("git", "-C", str(clone), "log", "-1", "--format=%an")
    assert author == f"delegate-{D}"


def test_a_delegate_checkout_may_propose_but_not_pull_or_link() -> None:
    env = {"SCOUT_READER": f"delegate:{D}"}
    assert delegate_write_refusal(["inbox", "propose", "observation", "--text", "x"], env) is None
    assert delegate_write_refusal(["inbox", "pull", D], env) is not None
    assert delegate_write_refusal(["delegate", "inbox", D, "--reset"], env) is not None


def test_cli_link_pull_and_propose(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    bare = _bare(tmp_path, "cli")
    r = runner.invoke(app, ["delegate", "inbox", D, "--remote", str(bare)])
    assert r.exit_code == 0, r.output
    c = tmp_path / "cli-clone"
    _run("git", "clone", "-q", str(bare), str(c))
    with monkeypatch.context() as m:
        m.setenv("SCOUT_READER", f"delegate:{D}")
        m.setenv("SCOUT_INBOX_DIR", str(c))
        r = runner.invoke(app, ["inbox", "propose", "action", "mark-done", "--tag", "AB12", "--date", "2026-11-19",
                                "--push", "--json"])  # fmt: skip
        assert r.exit_code == 0, r.output
        made = json.loads(r.output)
        assert made["pushed"]
    r = runner.invoke(app, ["inbox", "pull", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["taken"] == [made["id"]]
    r = runner.invoke(app, ["delegate", "inbox", D, "--reset"])  # no terminal, no --confirm
    assert r.exit_code == 2
    r = runner.invoke(app, ["delegate", "inbox", D])
    assert r.exit_code == 2


def test_pull_records_the_failure(vault: Path, inbox: Path) -> None:
    link = di.load_state(vault).inboxes[D]
    state = di.load_state(vault)
    state.inboxes[D] = dataclasses.replace(link, base="f" * 40)
    di.save_state(vault, state)
    _commit_entries(inbox, {"README.md": ("100644", b"x\n")})
    with pytest.raises(RepoSyncError):
        di.pull(vault, D)
    results = di.pull_all(vault)
    assert isinstance(results[0], RepoSyncError) and results[0].detail["delegate"] == D


# ----- more refusals ---------------------------------------------------------------------------


def test_a_state_file_that_cant_be_read_or_has_the_wrong_shape_is_refused(vault: Path, inbox: Path) -> None:
    path = di.state_path(vault)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RepoSyncError) as e:
        di.load_state(vault)
    assert e.value.code == "inbox_state_unreadable"
    key = dm._state_key(create=True)
    assert key is not None
    doc: dict[str, object] = {"schema_version": 1, "inboxes": {D: {"id": D}}}
    doc["mac"] = dm._sign(key, di.SIGN_KIND, doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RepoSyncError) as e:
        di.load_state(vault)
    assert e.value.code == "inbox_state_unreadable"


def test_one_inbox_command_at_a_time(vault: Path, inbox: Path) -> None:
    with di.locked(vault), pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "busy"


def test_a_ulid_far_behind_the_newest_taken_is_refused(vault: Path, inbox: Path) -> None:
    state = di.load_state(vault)
    state.inboxes[D].high_water = _ulid_at(dt.datetime.now(dt.UTC) + dt.timedelta(days=2))
    di.save_state(vault, state)
    old = _valid()
    _commit_entries(inbox, {old.path: ("100644", old.to_bytes())})
    r = di.pull(vault, D)
    assert r.taken == [] and r.refused == {"stale_id": 1} and _main_files(inbox) == set()


def test_a_path_that_isnt_utf8_is_refused_without_touching_the_disk(vault: Path, inbox: Path) -> None:
    raw = f"inbox/{D}/".encode() + b"\xff\xfe.json"
    _commit_entries(inbox, {raw.decode("utf-8", "surrogateescape"): ("100644", b"{}")})
    r = di.pull(vault, D)
    assert r.refused == {"bad_path": 1} and _main_files(inbox) == set()


def test_a_ledger_that_isnt_a_plain_file_refuses_before_recording(
    vault: Path, inbox: Path, clone: Path, tmp_path: Path
) -> None:
    p = _propose(clone)
    folder = vault.joinpath(*di.INBOX_DIR, D)
    folder.mkdir(parents=True)
    (folder / di.LEDGER).symlink_to(tmp_path / "elsewhere.txt")
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_folder_unsafe" and D not in di.load_state(vault).taken
    assert not (tmp_path / "elsewhere.txt").exists() and _main_files(inbox) == {p.path}
    assert di.ledger(vault, D) == set()


def test_make_repo_refuses_what_it_shouldnt_make(vault: Path) -> None:
    gh = FakeGh()
    for repo, code in (("not a name", "bad_arguments"), ("someone-else/inbox", "remote_org_owned")):
        with pytest.raises(RepoSyncError) as e:
            di.make_repo(repo, gh=gh)
        assert e.value.code == code

    def failing(step: list[str]) -> FakeGh:
        class Failing(FakeGh):
            def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
                a = [str(x) for x in args]  # type: ignore[attr-defined]
                if a[: len(step)] == step:
                    return subprocess.CompletedProcess(["gh"], 1, "", "HTTP 500")
                return super().__call__(a)

        return Failing()

    for step, code in ((["repo", "create"], "repo_create_failed"), (["api", "-X", "PUT"], "inbox_actions_on")):
        with pytest.raises(RepoSyncError) as e:
            di.make_repo(REPO, gh=failing(step))
        assert e.value.code == code
    gh = failing(["api", "user/installations"])
    r = di.link(vault, D, URL, gh=gh)
    assert any("check that no GitHub App" in n for n in r.notes)


def test_key_refusals_and_old_keys(vault: Path) -> None:
    gh = FakeGh()
    di.link(vault, D, URL, gh=gh)
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, revoke=True, gh=gh, keygen=_fake_keygen)
    assert e.value.code == "no_key"
    di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    state = di.load_state(vault)
    gh.keys[900] = f"scout-delegate-inbox-{D}"
    gh.keys[901] = "someone-elses-key"
    state.inboxes[D].stale_key_ids = [900, 901]
    di.save_state(vault, state)
    r = di.deploy_key(vault, D, rotate=True, gh=gh, keygen=_fake_keygen)
    assert 900 not in gh.keys and 901 in gh.keys  # an old key of ours deleted; one titled otherwise left alone
    assert di.load_state(vault).inboxes[D].stale_key_ids == []
    assert any("someone-elses-key" in n for n in r.notes)
    del gh.keys[901]  # a key that isn't this inbox's refuses every later use (inbox_other_key), so it goes

    class NoAdd(FakeGh):
        answer = subprocess.CompletedProcess(["gh"], 1, "", "HTTP 422")

        def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
            a = [str(x) for x in args]  # type: ignore[attr-defined]
            return self.answer if a[:3] == ["api", "-X", "POST"] else super().__call__(a)

    bad = NoAdd()
    bad.keys = gh.keys
    for answer, code in (
        (subprocess.CompletedProcess(["gh"], 1, "", "HTTP 422"), "key_add_failed"),
        (subprocess.CompletedProcess(["gh"], 0, "garbage\n", ""), "key_add_failed"),
    ):
        bad.answer = answer
        with pytest.raises(RepoSyncError) as e:
            di.deploy_key(vault, D, rotate=True, gh=bad, keygen=_fake_keygen)
        assert e.value.code == code

    local = _bare(vault.parent, "local-inbox")
    di.link(vault, "duet", str(local))
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, "duet", gh=gh, keygen=_fake_keygen)
    assert e.value.code == "key_needs_github"


def test_a_proposal_that_cant_be_pushed_stays_committed(vault: Path, clone: Path, inbox: Path) -> None:
    subprocess.run(["rm", "-rf", str(inbox)], check=True)
    out = _propose(clone)
    assert not out.pushed and "couldn't push" in out.notes[-1]
    assert (clone / out.path).is_file()


# ----- the #718 security review: each attack, refused ------------------------------------------


def test_h1_no_entry_can_wedge_a_pull(vault: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = _valid()
    big_ulid = "8" + good.id[1:]  # past 2^128: ulid_time would raise if anything reached it
    odd_op = json.loads(dp.make(D, "action_item_op", {"op": "mark-done", "tag": "AB12", "date": "2026-11-19",
                                                       "args": {}}).to_bytes())  # fmt: skip
    odd_op["op"] = []
    odd_id = dp.make(D, "observation", {"text": "x"}).id
    odd_op["id"] = odd_id
    _commit_entries(
        inbox,
        {
            f"inbox/{D}/{big_ulid}.json": ("100644", b"{}"),
            f"inbox/{D}/{odd_id}.json": ("100644", json.dumps(odd_op).encode()),
            good.path: ("100644", good.to_bytes()),
        },
    )
    r = di.pull(vault, D)
    assert r.taken == [good.id] and r.refused == {"bad_path": 1, "bad_op": 1}
    assert _main_files(inbox) == set()

    late = _valid()
    _commit_entries(inbox, {late.path: ("100644", late.to_bytes())})

    def explodes(*a: object, **k: object) -> object:
        raise MemoryError("anything at all")

    monkeypatch.setattr(dp, "parse", explodes)
    r = di.pull(vault, D)
    assert r.refused == {"bad_entry": 1} and r.taken == [] and _main_files(inbox) == set()


def test_h1_one_failing_inbox_never_stops_the_others(
    vault: Path, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    di.link(vault, "duet", str(_bare(tmp_path, "duet-inbox")))
    real = di.pull

    def flaky(v: Path, delegate: str, **kw: object) -> di.Result:
        if delegate == "duet":
            raise ValueError("boom")
        return real(v, delegate, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(di, "pull", flaky)
    results = di.pull_all(vault)
    assert [type(r).__name__ for r in results] == ["RepoSyncError", "Result"]
    assert results[0].code == "pull_failed" and results[0].detail["delegate"] == "duet"  # type: ignore[union-attr]


def test_h2_a_symlink_in_the_state_folder_is_replaced_never_followed(vault: Path, inbox: Path, tmp_path: Path) -> None:
    victim = tmp_path / "hooks.json"
    victim.write_text("keep\n", encoding="utf-8")
    folder = vault.joinpath(*di.STATE_DIR)
    (folder / f".{di.STATE_NAME}.{os.getpid()}.tmp").symlink_to(victim)
    (folder / "spare").symlink_to(victim)
    di.save_state(vault, di.load_state(vault))
    assert victim.read_text(encoding="utf-8") == "keep\n"
    assert not (folder / di.STATE_NAME).is_symlink() and di.load_state(vault).inboxes[D]
    (folder / di.STATE_NAME).unlink()
    (folder / di.STATE_NAME).symlink_to(victim)
    with pytest.raises(RepoSyncError) as e:
        di.load_state(vault)  # read without following it
    assert e.value.code == "inbox_state_unreadable"
    (folder / di.STATE_NAME).unlink()
    (folder / di.LOCK_NAME).unlink()
    (folder / di.LOCK_NAME).symlink_to(victim)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_state_unsafe" and victim.read_text(encoding="utf-8") == "keep\n"


def test_h2_the_state_folder_must_be_a_private_folder_of_ours(vault: Path, inbox: Path, tmp_path: Path) -> None:
    folder = vault.joinpath(*di.STATE_DIR)
    os.chmod(folder, 0o755)
    with pytest.raises(RepoSyncError) as e:
        di.load_state(vault)
    assert e.value.code == "inbox_state_unsafe"
    os.chmod(folder, 0o700)
    elsewhere = tmp_path / "elsewhere"
    folder.rename(elsewhere)
    folder.symlink_to(elsewhere)
    with pytest.raises(RepoSyncError) as e:
        di.load_state(vault)
    assert e.value.code == "inbox_folder_unsafe"


def test_h2_a_symlinked_gitignore_is_refused(vault: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep\n", encoding="utf-8")
    (vault / ".gitignore").unlink(missing_ok=True)
    (vault / ".gitignore").symlink_to(victim)
    with pytest.raises(RepoSyncError) as e:
        di.ensure_ignored(vault)
    assert e.value.code == "inbox_gitignore_unsafe" and victim.read_text(encoding="utf-8") == "keep\n"


def test_m1_each_pull_fetches_inside_the_protected_folder(vault: Path, inbox: Path) -> None:
    link = di.load_state(vault).inboxes[D]
    with di._Sandbox(vault, link.remote) as box:
        assert box.root.parent == vault.joinpath(*di.STATE_DIR)
        assert box.root.stat().st_mode & 0o777 == 0o700
        assert not box.name == "inbox" and box.name.startswith("scout")
    assert not box.root.exists()
    from scout.kb import hook_paths

    protected = {str(s.path) for s in hook_paths.protected_specs(vault, os.environ) if s.tree}
    assert str(vault.joinpath(*di.STATE_DIR)) in protected


def test_m2_any_other_deploy_key_refuses(vault: Path) -> None:
    gh = FakeGh()
    gh.keys[42] = "someone's key"
    with pytest.raises(RepoSyncError) as e:
        di.link(vault, D, URL, gh=gh)
    assert e.value.code == "inbox_other_key"
    del gh.keys[42]
    di.link(vault, D, URL, gh=gh)
    di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)  # ours: allowed from now on
    gh.keys[43] = "read-only, but not ours"
    gh.key_read_only[43] = "true"
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, rotate=True, gh=gh, keygen=_fake_keygen)
    assert e.value.code == "inbox_other_key"


def test_m3_a_trailing_newline_never_aliases_a_proposal(vault: Path, inbox: Path) -> None:
    p = _valid()
    _commit_entries(inbox, {p.path: ("100644", p.to_bytes()), p.path + "\n": ("100644", p.to_bytes())})
    r = di.pull(vault, D)
    assert r.taken == [p.id] and r.refused == {"bad_path": 1}
    ledger = vault.joinpath(*di.INBOX_DIR, D, di.LEDGER).read_text(encoding="utf-8")
    assert ledger.count(p.id) == 1


def test_case_collisions_are_refused_or_never_listed(vault: Path, inbox: Path) -> None:
    p = _valid()
    _commit_entries(
        inbox,
        {
            p.path: ("100644", p.to_bytes()),
            f"inbox/{D}/{p.id.lower()}.json": ("100644", p.to_bytes()),
            f"inbox/{D}/{p.id}.JSON": ("100644", p.to_bytes()),
            f"Inbox/{D}/{p.id}.json": ("100644", p.to_bytes()),
        },
    )
    r = di.pull(vault, D)
    assert r.taken == [p.id] and r.refused == {"bad_path": 2}
    assert _main_files(inbox) == {f"Inbox/{D}/{p.id}.json"}


def test_low_main_is_matched_exactly(vault: Path, inbox: Path) -> None:
    p = _valid()
    commit = _commit_entries(inbox, {p.path: ("100644", p.to_bytes())})
    _run("git", "-C", str(inbox), "update-ref", "refs/heads/a/refs/heads/main", commit)
    _run("git", "-C", str(inbox), "update-ref", "-d", "refs/heads/main")
    assert di.pull(vault, D).no_main


def test_low_a_users_remote_config_cant_redirect_the_fetch(
    vault: Path, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = _bare(tmp_path, "decoy")
    planted = _valid()
    _commit_entries(decoy, {planted.path: ("100644", planted.to_bytes())})
    real = _valid()
    _commit_entries(inbox, {real.path: ("100644", real.to_bytes())})
    config = tmp_path / "gitconfig"
    config.write_text(f'[remote "inbox"]\n\turl = {decoy}\n\tpushurl = {decoy}\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert di.pull(vault, D).taken == [real.id]


def test_low_a_run_never_pulls_and_a_flood_is_listed_in_parts(
    vault: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D, env={"SCOUT_MODE": "briefing"})
    assert e.value.code == "headless_refused"
    monkeypatch.setattr(di, "MAX_ENTRIES", 5)
    many = [_valid(text=str(i)) for i in range(8)]
    _commit_entries(inbox, {p.path: ("100644", p.to_bytes()) for p in many})
    r = di.pull(vault, D)
    assert len(r.taken) == 5 and "wait for a later pull" in " ".join(r.notes)
    assert len(di.pull(vault, D).taken) == 3


def test_low_reset_clears_a_poisoned_high_water_mark(vault: Path, inbox: Path) -> None:
    state = di.load_state(vault)
    state.inboxes[D].high_water = _ulid_at(dt.datetime.now(dt.UTC) + dt.timedelta(days=400))
    di.save_state(vault, state)
    di.reset(vault, D, confirm=lambda d, m: True)
    assert di.load_state(vault).inboxes[D].high_water is None


def test_low_old_taken_entries_are_pruned(vault: Path, inbox: Path) -> None:
    old = _ulid_at(dt.datetime.now(dt.UTC) - dt.timedelta(days=100))
    state = di.load_state(vault)
    stamp = (dt.datetime.now(dt.UTC) - dt.timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    young_ulid_old_take = _ulid_at(dt.datetime.now(dt.UTC))  # the agent's ULID time says nothing: the stamp does
    state.taken[D] = {old: {"mac": "0" * 64, "at": stamp}, young_ulid_old_take: {"mac": "0" * 64, "at": stamp}}
    di.save_state(vault, state)
    p = _valid()
    _commit_entries(inbox, {p.path: ("100644", p.to_bytes())})
    di.pull(vault, D)
    assert set(di.load_state(vault).taken[D]) == {p.id}


def test_low_the_credential_helper_is_scouts_choice(vault: Path, tmp_path: Path) -> None:
    env = di.sandbox_env(vault, "https://github.com/someone/inbox.git", tmp_path / "x.git")
    helpers = [
        env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env["GIT_CONFIG_COUNT"]))
        if env[f"GIT_CONFIG_KEY_{i}"] == "credential.helper"
    ]
    assert helpers == ["", di.gh_credential()]  # the user's helpers cleared, then gh's only (an absolute path)
    local = di.sandbox_env(vault, str(tmp_path / "inbox.git"), tmp_path / "x.git")
    assert [
        local[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(local["GIT_CONFIG_COUNT"]))
        if local[f"GIT_CONFIG_KEY_{i}"] == "credential.helper"
    ] == [""]


# ----- the delta re-review ---------------------------------------------------------------------


def test_n1_a_deep_path_is_removed_by_its_top_folder(vault: Path, inbox: Path) -> None:
    good = _valid()
    deep = f"inbox/{D}/" + "a/" * 990 + "x.json"
    _commit_entries(inbox, {deep: ("100644", b"{}"), f"inbox/{D}/a/b.json": ("100644", b"{}"),
                            good.path: ("100644", good.to_bytes())})  # fmt: skip
    r = di.pull(vault, D)
    assert r.taken == [good.id] and r.refused == {"bad_path": 2} and r.removal is not None
    assert _main_files(inbox) == set()
    assert di.load_state(vault).inboxes[D].base == r.removal


def test_n1_an_unexpected_failure_is_recorded_and_reported(
    vault: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _valid()
    _commit_entries(inbox, {p.path: ("100644", p.to_bytes())})

    def boom(*a: object, **k: object) -> str:
        raise RecursionError("deep")

    monkeypatch.setattr(di._Sandbox, "remove", boom)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "pull_failed" and di.load_state(vault).inboxes[D].last_error["code"] == "pull_failed"


def test_n2_a_local_inbox_is_for_tests_only(
    vault: Path, inbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(di.ALLOW_LOCAL_ENV)
    with pytest.raises(RepoSyncError) as e:
        di.link(vault, "duet", str(_bare(tmp_path, "duet-local")))
    assert e.value.code == "inbox_local_refused"
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)  # linked while the flag was on: still refused without it
    assert e.value.code == "inbox_local_refused"


def test_a_pull_sweeps_what_a_killed_pull_left(vault: Path, inbox: Path) -> None:
    folder = vault.joinpath(*di.STATE_DIR)
    (folder / "fetch-leftover" / "inbox.git").mkdir(parents=True)
    di.pull(vault, D)
    assert not (folder / "fetch-leftover").exists()
    assert [n for n in os.listdir(folder) if n.startswith("fetch-")] == []


def test_a_reader_other_than_the_owner_never_pulls(vault: Path, inbox: Path) -> None:
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D, env={"SCOUT_READER": "autonomous"})
    assert e.value.code == "headless_refused"


def test_a_replaced_fetch_folder_refuses(vault: Path, inbox: Path, tmp_path: Path) -> None:
    link = di.load_state(vault).inboxes[D]
    with di._Sandbox(vault, link.remote) as box:
        box.root.rename(tmp_path / "moved")
        box.root.mkdir()
        with pytest.raises(RepoSyncError) as e:
            box.fetch("--depth=1")
        assert e.value.code == "inbox_state_unsafe"


# ----- the remaining refusal paths (coverage floor) --------------------------------------------


def test_state_before_any_inbox_and_a_state_file_that_isnt_a_file(vault: Path) -> None:
    assert di.load_state(vault) == di.InboxState()  # no folder yet
    with di.locked(vault):
        pass
    assert di.load_state(vault) == di.InboxState()  # a folder, no state yet
    vault.joinpath(*di.STATE_DIR, di.STATE_NAME).mkdir()
    with pytest.raises(RepoSyncError) as e:
        di.load_state(vault)
    assert e.value.code == "inbox_state_unreadable"


def test_ensure_ignored_keeps_what_the_gitignore_had_and_refuses_a_folder(vault: Path, tmp_path: Path) -> None:
    (vault / ".gitignore").write_text("notes.tmp", encoding="utf-8")  # no final newline
    assert di.ensure_ignored(vault)
    assert (vault / ".gitignore").read_text(encoding="utf-8").startswith("notes.tmp\n# Delegate inboxes")
    assert not di.ensure_ignored(vault)
    (vault / ".gitignore").unlink()
    (vault / ".gitignore").mkdir()
    with pytest.raises(RepoSyncError) as e:
        di.ensure_ignored(vault)
    assert e.value.code == "inbox_gitignore_unsafe"


def test_odd_github_answers_and_bindings_refuse(vault: Path, inbox: Path) -> None:
    gh = FakeGh()
    gh.facts["id"] = "not-a-number"
    with pytest.raises(RepoSyncError) as e:
        di.link(vault, "duet", URL, gh=gh)
    assert e.value.code == "remote_unverified"
    state = di.load_state(vault)
    state.inboxes[D].remote = URL  # bound to a local path, now claiming GitHub
    di.save_state(vault, state)
    with pytest.raises(RepoSyncError) as e:
        di.deploy_key(vault, D, gh=FakeGh(), keygen=_fake_keygen)
    assert e.value.code == "inbox_rebound"


def test_a_local_inbox_that_went_away_refuses(vault: Path, inbox: Path) -> None:
    subprocess.run(["rm", "-rf", str(inbox)], check=True)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "remote_unverified"
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, "nobody")
    assert e.value.code == "inbox_not_linked"


def test_an_inbox_that_is_a_mirror_by_github_id_refuses(vault: Path) -> None:
    links = dm.load_links(vault)
    links[D] = dataclasses.replace(links[D], remote="https://github.com/someone/grok-mirror.git")
    dm.save_links(vault, links)

    class SameRepo(FakeGh):
        def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
            a = [str(x) for x in args]  # type: ignore[attr-defined]
            if a[:2] == ["api", "repos/someone/grok-mirror"]:  # the mirror, renamed: GitHub's id is the inbox's
                return super().__call__(["api", f"repos/{REPO}", *a[2:]])
            return super().__call__(a)

    with pytest.raises(RepoSyncError) as e:
        di.link(vault, D, URL, gh=SameRepo())
    assert e.value.code == "inbox_taken"


def test_unlink_without_a_key_and_a_key_file_that_isnt_ours(vault: Path) -> None:
    gh = FakeGh()
    di.link(vault, D, URL, gh=gh)
    key = di.key_path(vault, D)
    key.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = key.parent / "elsewhere"
    elsewhere.write_text("keep\n", encoding="utf-8")
    key.symlink_to(elsewhere)  # never removed through a link
    di.unlink(vault, D, gh=gh)
    assert key.is_symlink() and elsewhere.read_text(encoding="utf-8") == "keep\n"
    assert not any("revoked" in c for c in map(" ".join, gh.calls))


def test_keys_github_wont_delete_stay_on_record(vault: Path) -> None:
    class NoDelete(FakeGh):
        def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
            a = [str(x) for x in args]  # type: ignore[attr-defined]
            if a[:3] == ["api", "-X", "DELETE"]:
                return subprocess.CompletedProcess(["gh"], 1, "", "HTTP 500")
            return super().__call__(a)

    gh = NoDelete()
    di.link(vault, D, URL, gh=gh)
    di.deploy_key(vault, D, gh=gh, keygen=_fake_keygen)
    r = di.deploy_key(vault, D, rotate=True, gh=gh, keygen=_fake_keygen)  # the old key can't be deleted
    link = di.load_state(vault).inboxes[D]
    assert link.deploy_key_id == 501 and link.stale_key_ids == [500] and any("warning" in n for n in r.notes)
    di.deploy_key(vault, D, rotate=True, gh=gh, keygen=_fake_keygen)  # retried, still failing: kept
    assert 500 in di.load_state(vault).inboxes[D].stale_key_ids


def test_the_sandboxs_own_failures(vault: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    link = di.load_state(vault).inboxes[D]
    with di._Sandbox(vault, link.remote) as box:
        with pytest.raises(RepoSyncError) as e:
            box.git("cat-file", "-p", "0" * 40)
        assert e.value.code == "inbox_fetch_failed"
        with pytest.raises(RepoSyncError) as e:
            box.entries("0" * 40, D)  # ls-tree fails: never an empty inbox
        assert e.value.code == "inbox_fetch_failed"
        with pytest.raises(RepoSyncError) as e:
            box.push("1" * 40, "")  # the lease holds (no main yet), but there is no such commit to push
        assert e.value.code == "inbox_push_failed"
        assert box.push("0" * 40, "1" * 40) is False  # a lease on a main the inbox doesn't have: a race
        timeout = di.GIT_TIMEOUT
        monkeypatch.setattr(di, "GIT_TIMEOUT", 0.000001)
        with pytest.raises(RepoSyncError) as e:
            box._run(["hash-object", "--stdin"], input=b"x" * 1000)
        assert e.value.code == "remote_timeout"
        monkeypatch.setattr(di, "GIT_TIMEOUT", timeout)  # not undo(): that would drop the fixture's HOME too
        subprocess.run(["rm", "-rf", str(box.root)], check=True)
        with pytest.raises(RepoSyncError) as e:
            box.fetch("--depth=1")
        assert e.value.code == "inbox_state_unsafe"


def test_a_leftover_file_is_swept_and_a_race_on_main_retries(
    vault: Path, inbox: Path, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = vault.joinpath(*di.STATE_DIR)
    (folder / "fetch-stray-file").write_text("x", encoding="utf-8")
    p = _propose(clone)
    real = di._Sandbox.remote_main
    calls: list[int] = []

    def moved(self: di._Sandbox) -> str | None:
        calls.append(1)
        return "f" * 40 if len(calls) == 1 else real(self)  # the first ls-remote saw another main

    monkeypatch.setattr(di._Sandbox, "remote_main", moved)
    r = di.pull(vault, D)
    assert r.taken == [p.id] and len(calls) == 2
    assert not (folder / "fetch-stray-file").exists()


def test_a_recorded_proposal_whose_bytes_changed_isnt_copied_again(vault: Path, inbox: Path, clone: Path) -> None:
    p = _propose(clone)
    state = di.load_state(vault)
    state.taken[D] = {p.id: {"mac": "0" * 64, "at": "2026-11-19T00:00:00Z"}}  # recorded, but for other bytes
    di.save_state(vault, state)
    r = di.pull(vault, D)
    assert r.already == 1 and r.taken == [] and not di.copy_path(vault, D, p.id).exists()


def test_a_git_failure_while_judging_stops_the_pull(
    vault: Path, inbox: Path, clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _propose(clone)

    def git_failed(*a: object, **k: object) -> object:
        raise RepoSyncError("inbox_fetch_failed", "refused: git cat-file failed")

    monkeypatch.setattr(di, "_judge", git_failed)
    with pytest.raises(RepoSyncError) as e:
        di.pull(vault, D)
    assert e.value.code == "inbox_fetch_failed" and _main_files(inbox) == {p.path}


def test_propose_reports_a_git_failure(vault: Path, clone: Path) -> None:
    (clone / ".git" / "index.lock").write_text("", encoding="utf-8")  # another git is "running"
    with pytest.raises(RepoSyncError) as e:
        di.propose("observation", {"text": "x"}, env=_agent_env(clone))
    assert e.value.code == "inbox_git_failed"


def test_the_gh_runner_asks_github_com_with_an_absolute_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen["argv"], seen["env"] = argv, kw["env"]
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(di, "_tool", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(di.subprocess, "run", fake_run)
    monkeypatch.setenv("GH_REPO", "someone/elsewhere")
    assert di._gh(["api", "user"]).stdout == "ok"
    assert seen["argv"] == ["/opt/bin/gh", "api", "user"]
    env = seen["env"]
    assert isinstance(env, dict) and env["GH_HOST"] == "github.com" and "GH_REPO" not in env
