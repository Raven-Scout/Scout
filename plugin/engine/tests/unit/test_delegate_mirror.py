"""scoutctl delegate link | sync | preview | status | unlink (#550 V2): per-delegate filtered mirrors.

The remote is a local bare repository, so git runs for real. The vault is the
anonymised privacy fixture vault (``privacy_vault_helpers``) with two delegates:
``grok-personal`` (normal notes only) and ``duet`` (``include_private: true``).
Every blob of every mirror commit is read to prove what never reaches it.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from scout import delegate_mirror as dm
from scout import repo_sync as rs
from scout.cli import app, delegate_write_refusal
from scout.kb.outbound import clear_cache, note_from_text, terms_of
from scout.kb.privacy import load_frontmatter_yaml, load_policy
from scout.scripts import privacy_migrate

from .privacy_vault_helpers import CANARIES, build_privacy_vault, git, git_env

runner = CliRunner()

DELEGATES_BLOCK = """\
  delegates:
    grok-personal: {}
    duet:
      include_private: true
"""
PROVENANCE = "knowledge-base/topics/from-priya.md"
PROVENANCE_CANARY = "CANARY-SHARED-9Q2W"
INBOX_CANARY = CANARIES[".scout-inbox/phone-notifications-2026-10-01.jsonl"]
TRADING_CANARY = CANARIES["trading/positions.md"]
BROKEN_CANARY = CANARIES["knowledge-base/topics/misc/broken.md"]
PRIYA = "knowledge-base/people/priya.md"


def _commit(vault: Path, rel: str, text: str, subject: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_cache()
    v = build_privacy_vault(tmp_path / "v").resolve()
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    monkeypatch.setenv(rs.TEST_VAULT_ENV, str(v))
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    config = v / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + DELEGATES_BLOCK, encoding="utf-8")
    (v / PROVENANCE).write_text(
        "---\ntitle: From Priya\nvisibility: normal\n"
        "source: shared:priya@example.com:knowledge-base/topics/plan.md\n---\n\n"
        f"Notes from Priya's shared plan. {PROVENANCE_CANARY}\n",
        encoding="utf-8",
    )
    git(v, "add", "-A")
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "list delegates")
    privacy_migrate.accept(v)
    return v


def _bare(tmp_path: Path, name: str) -> Path:
    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return bare


def _bare_git(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(bare), *args], check=True, capture_output=True, text=True).stdout


def _all_blobs(bare: Path) -> dict[str, str]:
    """``{"<commit>:<path>": text}`` for every file in every commit of the mirror's main."""
    out: dict[str, str] = {}
    for rev in _bare_git(bare, "rev-list", "main").split():
        for rel in _bare_git(bare, "ls-tree", "-r", "--name-only", rev).splitlines():
            out[f"{rev}:{rel}"] = subprocess.run(
                ["git", "-C", str(bare), "show", f"{rev}:{rel}"], check=True, capture_output=True
            ).stdout.decode("utf-8", "replace")
    return out


def _head_files(bare: Path) -> set[str]:
    return set(_bare_git(bare, "ls-tree", "-r", "--name-only", "main").splitlines())


def _show(bare: Path, rel: str, rev: str = "main") -> str:
    return _bare_git(bare, "show", f"{rev}:{rel}")


def _link(vault: Path, delegate: str, bare: Path, **kw: object) -> dm.Result:
    return dm.link(vault, delegate, str(bare), account_unshared=True, **kw)  # type: ignore[arg-type]


def _private_gh(args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["gh"], 0, "true\n", "")


# ----- what a mirror holds ---------------------------------------------------------------


def test_a_normal_only_mirror_has_no_canary_no_local_only_path_and_a_label_per_file(
    vault: Path, tmp_path: Path
) -> None:
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    assert result.pushed and result.files > 0 and result.withheld > 0
    blobs = _all_blobs(bare)
    for key, text in blobs.items():
        assert "CANARY-" not in text, key
    files = _head_files(bare)
    assert not any(f.startswith(("trading/", ".scout-inbox/")) for f in files)
    assert PROVENANCE not in files and "knowledge-base/people/sam.md" not in files
    policy = json.loads(_show(bare, rs.POLICY_PATH))
    labelled = {lb["path"] for lb in policy["labels"]}
    assert labelled == files - dm.GENERATED
    assert list(policy["policy"]["delegates"]) == ["grok-personal"]


def test_a_delegate_with_private_sees_private_notes_but_never_the_inbox_provenance_trading_or_broken(
    vault: Path, tmp_path: Path
) -> None:
    bare = _bare(tmp_path, "duet")
    _link(vault, "duet", bare)
    files = _head_files(bare)
    assert "knowledge-base/people/sam.md" in files
    text = "\n".join(_all_blobs(bare).values())
    assert CANARIES["knowledge-base/people/sam.md"] in text
    for canary in (INBOX_CANARY, TRADING_CANARY, BROKEN_CANARY, PROVENANCE_CANARY):
        assert canary not in text


def test_include_private_spelled_yes_widens_nothing(vault: Path, tmp_path: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("include_private: true", "include_private: yes"), "utf-8"
    )
    _commit(vault, "scout-config.yaml", config.read_text(encoding="utf-8"), "yes")
    bare = _bare(tmp_path, "duet")
    _link(vault, "duet", bare)
    assert not any("CANARY-PRIV" in t for t in _all_blobs(bare).values())


def test_a_normal_note_loses_the_lines_that_link_or_name_a_hidden_note(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    priya = _show(bare, PRIYA)
    assert "[[sam]]" not in priya and "Sammy" not in priya
    assert "Engineer at example-org" not in priya  # that line links [[sam]]
    fm = load_frontmatter_yaml(priya.split("---\n")[1])
    assert fm["relationships"] == [{"type": "works_on", "target": "[[widget]]"}]
    assert fm["name"] == "priya"
    manifest = json.loads(_show(bare, dm.MANIFEST_PATH))
    assert manifest["redacted_lines"][PRIYA] == 3
    assert manifest["delegate"] == "grok-personal"
    assert result.redacted_files >= 1


def test_the_mirror_config_lists_only_its_delegate_and_the_engine_there_reads_it(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    config = yaml.safe_load(_show(bare, "scout-config.yaml"))
    assert config["privacy"]["delegates"] == {"grok-personal": {}}
    assert config["privacy"]["local_only_add"] == ["trading/**"]
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", str(bare), str(clone)], check=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(clone))
    result = runner.invoke(
        app, ["kb", "files", "--json", "--repo", str(clone)], env={"SCOUT_READER": "delegate:grok-personal"}
    )
    assert result.exit_code == 0, result.output
    listed = {f["path"] for f in json.loads(result.stdout)}
    assert PRIYA in listed and "knowledge-base/people/sam.md" not in listed


def test_a_duet_mirror_config_keeps_include_private_literally_true(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "duet")
    _link(vault, "duet", bare)
    text = _show(bare, "scout-config.yaml")
    from scout.kb.privacy import policy_from_config_text

    rule = policy_from_config_text(text).delegate("delegate:duet")
    assert rule is not None and rule.include_private


def test_a_symlink_to_a_mirrored_folder_stays_and_one_to_a_hidden_note_goes(vault: Path, tmp_path: Path) -> None:
    (vault / "sam-link.md").symlink_to("knowledge-base/people/sam.md")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "links")
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    files = _head_files(bare)
    assert "knowledge_base" in files
    assert "sam-link.md" not in files


def test_another_text_file_that_names_a_hidden_note_is_left_out_whole(vault: Path, tmp_path: Path) -> None:
    _commit(vault, "notes/data.json", '{"who": "[[sam]]"}\n', "data")
    _commit(vault, "notes/list.csv", "who,what\n[[sam]],x\npriya,y\n", "csv")
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    files = _head_files(bare)
    assert "notes/data.json" not in files and result.left_out >= 1
    assert _show(bare, "notes/list.csv") == "who,what\npriya,y\n"


# ----- sync ---------------------------------------------------------------------------------


def test_sync_pushes_one_generic_commit_per_change_and_nothing_when_unchanged(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    assert dm.sync(vault, "grok-personal").unchanged
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nA new normal note.\n", "add new")
    _commit(vault, "knowledge-base/people/sam.md", "---\nname: sam\nvisibility: private\n---\n\nX\n", "sam edit")
    result = dm.sync(vault, "grok-personal")
    assert result.pushed
    log = _bare_git(bare, "log", "--format=%an <%ae>|%s", "main").splitlines()
    assert log == ["Scout <scout@localhost>|Scout delegate mirror: sync"] * 2
    assert "knowledge-base/topics/misc/new.md" in _head_files(bare)
    assert dm.load_links(vault)["grok-personal"].last_mirror == result.pushed


def test_a_private_only_change_pushes_nothing(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    git(vault, "add", "-A")  # link's .gitignore line
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "ignore state")
    dm.sync(vault, "grok-personal")
    before = _bare_git(bare, "rev-parse", "main")
    _commit(vault, "knowledge-base/people/sam.md", "---\nname: sam\nvisibility: private\n---\n\nY\n", "sam")
    assert dm.sync(vault, "grok-personal").unchanged
    assert _bare_git(bare, "rev-parse", "main") == before


def test_a_note_that_turns_private_leaves_head_and_the_owner_is_told_about_history(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    rel = "knowledge-base/topics/billing/rollout.md"
    assert rel in _head_files(bare)
    text = (
        (vault / rel)
        .read_text(encoding="utf-8")
        .replace("title: Billing rollout", "title: Billing rollout\nvisibility: private")
    )
    _commit(vault, rel, text, "private now")
    result = dm.sync(vault, "grok-personal")
    assert rel not in _head_files(bare)
    assert result.in_history == 1 and any("--replace-remote" in n for n in result.notes)


def test_replace_remote_starts_the_mirror_over_with_one_commit(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    dm.sync(vault, "grok-personal")
    _link(vault, "grok-personal", bare, replace_remote=True)
    assert len(_bare_git(bare, "rev-list", "main").split()) == 1


def test_a_remote_that_moved_is_refused_and_nothing_is_replayed(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    (other / "x.md").write_text("from the agent\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "agent write")
    git(other, "push", "-q", "origin", "HEAD:main")
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.sync(vault, "grok-personal")
    assert e.value.code == "mirror_diverged"
    assert not (vault / "x.md").exists()


def test_sync_all_keeps_going_past_a_failing_delegate(vault: Path, tmp_path: Path) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    duet = _bare(tmp_path, "duet")
    _link(vault, "duet", duet)
    subprocess.run(["rm", "-rf", str(duet)], check=True)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    done, failed = dm.sync_all(vault)
    assert [r.delegate for r in done] == ["grok-personal"] and set(failed) == {"duet"}


# ----- refusals -----------------------------------------------------------------------------


def test_link_refuses_an_unlisted_delegate_a_bad_id_and_no_account_confirmation(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "x")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "someone-else", bare)
    assert e.value.code == "delegate_not_listed"
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "Bad_Id", bare)
    assert e.value.code == "bad_delegate_id"
    with pytest.raises(rs.RepoSyncError) as e:
        dm.link(vault, "duet", str(bare), account_unshared=False)
    assert e.value.code == "account_unconfirmed"
    assert _bare_git(bare, "branch", "--list") == ""


def test_a_delegate_listed_only_in_the_uncommitted_config_is_refused(vault: Path, tmp_path: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "    later: {}\n", encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "later", _bare(tmp_path, "later"))
    assert e.value.code == "delegate_not_listed"


def test_link_refuses_a_scheduled_run(vault: Path, tmp_path: Path) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        dm.link(vault, "duet", str(_bare(tmp_path, "d")), account_unshared=True, env={"SCOUT_MODE": "briefing"})
    assert e.value.code == "headless_refused"


def test_link_refuses_a_remote_with_history_unless_replacing(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    dm.unlink(vault, "grok-personal")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "grok-personal", bare)
    assert e.value.code == "remote_not_empty"


def test_a_trading_path_that_is_not_local_only_blocks_the_mirror(vault: Path, tmp_path: Path) -> None:
    _commit(vault, "TRADING.md", "# Positions\n", "trading file")
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    assert e.value.code == "trading_paths_not_local_only"


@pytest.mark.parametrize(
    "remote",
    ["https://gitlab.com/o/r.git", "git@example.com:o/r.git", "relative/path", "https://github.com/o"],
)
def test_only_github_and_local_remotes_are_accepted(remote: str) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote(remote, gh=_private_gh)
    assert e.value.code == "remote_unsupported"


@pytest.mark.parametrize(
    "remote",
    ["https://github.com/o/r", "https://github.com/o/r.git", "git@github.com:o/r.git", "ssh://git@github.com/o/r"],
)
def test_a_github_remote_must_be_private(remote: str) -> None:
    asked: list[list[str]] = []

    def gh(args: object, out: str = "true\n", code: int = 0) -> subprocess.CompletedProcess[str]:
        asked.append(list(args))  # type: ignore[call-overload]
        return subprocess.CompletedProcess(["gh"], code, out, "")

    dm.check_remote(remote, gh=gh)
    assert asked == [["api", "repos/o/r", "--jq", ".private"]]
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote(remote, gh=lambda a: gh(a, "false\n"))
    assert e.value.code == "remote_public"
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote(remote, gh=lambda a: gh(a, "", 1))
    assert e.value.code == "remote_unverified"


def test_check_tree_refuses_a_commit_that_holds_a_hidden_note(vault: Path) -> None:
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan, policy, rules, terms = dm.plan(vault, head, "grok-personal", blobs)
    finally:
        blobs.close()
    sam = rs._blob_at(vault, head, "knowledge-base/people/sam.md")
    assert sam is not None
    the_plan.files.append(dm.MirrorFile(sam.path, sam.mode, None, sam.sha))
    tree = dm.write_tree(vault, the_plan, policy)
    commit = git(vault, "commit-tree", tree, "-m", "x").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", policy, rules, terms)
    assert e.value.code == "delegate_mirror_unsafe"


# ----- redaction ------------------------------------------------------------------------------


def _terms() -> object:
    return terms_of([note_from_text("people/sam-lee.md", "---\nname: Sam Lee\naliases: [Sammy]\n---\n")])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '---\nname: x\nrelationships:\n  - type: knows\n    target: "[[sam-lee]]"\n'
            "  - type: on\n    target: w\n---\nbody\n",
            "---\nname: x\nrelationships:\n  - type: on\n    target: w\n---\nbody\n",
        ),
        (
            '---\nname: x\nrelationships:\n- type: knows\n  target: "[[sam-lee]]"\n- type: on\n---\n',
            "---\nname: x\nrelationships:\n- type: on\n---\n",
        ),
        ("---\nname: x\nsummary: >\n  met Sammy\n  today\nkind: y\n---\n", "---\nname: x\nkind: y\n---\n"),
        ("---\naliases: [Sam Lee]\nname: x\n---\n", "---\nname: x\n---\n"),
        ("# Note\n\nSee [[sam-lee|him]].\nKeep this.\n", "# Note\n\nKeep this.\n"),
    ],
)
def test_redaction_keeps_frontmatter_whole(text: str, expected: str) -> None:
    out = dm.redact_lines(text, _terms(), markdown=True)  # type: ignore[arg-type]
    assert out is not None and out[0] == expected


def test_a_term_split_across_a_removed_line_is_caught_on_the_next_pass() -> None:
    out = dm.redact_lines("a\nSam\n[[sam-lee]]\nLee b\nc\n", _terms(), markdown=True)  # type: ignore[arg-type]
    assert out is not None
    assert "Sam" not in out[0] and "Lee" not in out[0] and out[0].startswith("a\n")


# ----- CLI and state ----------------------------------------------------------------------------


def test_the_cli_links_previews_syncs_lists_and_unlinks(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    r = runner.invoke(app, ["delegate", "preview", "grok-personal", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["files"] > 0
    assert _bare_git(bare, "branch", "--list") == ""
    r = runner.invoke(app, ["delegate", "link", "grok-personal", "--remote", str(bare)])
    assert r.exit_code == 2 and "--confirm-account-unshared" in r.output
    r = runner.invoke(app, ["delegate", "link", "grok-personal", "--remote", str(bare), "--confirm-account-unshared"])
    assert r.exit_code == 0, r.output
    assert "pushed a mirror commit" in r.stdout
    r = runner.invoke(app, ["delegate", "sync", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["synced"][0]["unchanged"]
    r = runner.invoke(app, ["delegate", "status"])
    assert "grok-personal:" in r.stdout
    assert git(vault, "check-ignore", dm.STATE_REL).strip() == dm.STATE_REL
    r = runner.invoke(app, ["delegate", "unlink", "grok-personal"])
    assert r.exit_code == 0, r.output
    assert dm.load_links(vault) == {}
    assert git(vault, "for-each-ref", dm.REF_PREFIX).strip() == ""


def test_sync_needs_one_delegate_or_all() -> None:
    assert runner.invoke(app, ["delegate", "sync"]).exit_code == 2
    assert runner.invoke(app, ["delegate", "sync", "duet", "--all"]).exit_code == 2


def test_a_delegate_reader_cannot_run_delegate_commands() -> None:
    env = {"SCOUT_READER": "delegate:duet"}
    for argv in (["delegate", "sync", "--all"], ["delegate", "link", "duet"], ["delegate", "status"]):
        assert delegate_write_refusal(argv, env) is not None


def test_the_agnes_link_state_is_untouched(vault: Path, tmp_path: Path) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    assert not rs.state_path(vault).exists()
    assert git(vault, "for-each-ref", "refs/scout-mirror/").strip() == ""
    assert load_policy(vault).delegate("delegate:grok-personal") is not None


# ----- V2b: the repository, the agent's deploy key, the push token ------------------------------

GITHUB = "https://github.com/someone/grok-mirror.git"


class FakeGh:
    """``gh`` for one private repository: records every call, answers like GitHub."""

    def __init__(self, *, read_only: str = "true", create_ok: bool = True, delete_ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.keys: dict[int, str] = {}
        self.read_only = read_only
        self.create_ok = create_ok
        self.delete_ok = delete_ok

    def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
        a = list(args)  # type: ignore[call-overload]
        self.calls.append(a)
        ok = subprocess.CompletedProcess(["gh"], 0, "", "")
        if a[:2] == ["repo", "create"]:
            return ok if self.create_ok else subprocess.CompletedProcess(["gh"], 1, "", "GraphQL: name already exists")
        if a[:2] == ["api", "-X"] and a[2] == "POST":
            new = 100 + len(self.keys)
            self.keys[new] = next(x[4:] for x in a if x.startswith("key="))
            return subprocess.CompletedProcess(["gh"], 0, f"{new}\t{self.read_only}\n", "")
        if a[:2] == ["api", "-X"] and a[2] == "DELETE":
            if not self.delete_ok:
                return subprocess.CompletedProcess(["gh"], 1, "", "HTTP 500")
            self.keys.pop(int(a[3].rsplit("/", 1)[1]), None)
            return ok
        if a[0] == "api" and a[2:] == ["--jq", ".private"]:
            return subprocess.CompletedProcess(["gh"], 0, "true\n", "")
        raise AssertionError(f"unexpected gh call {a}")


def _fake_keygen(path: Path, comment: str) -> None:
    path.write_text("PRIVATE-KEY-STAND-IN\n", encoding="utf-8")
    path.with_name(path.name + ".pub").write_text(f"ssh-ed25519 AAAAstandin {comment}\n", encoding="utf-8")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def github_link(vault: Path, tmp_path: Path, home: Path) -> Path:
    """A link pushed to a local bare repository, then pointed at a GitHub URL as if it lived there."""
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    links = dm.load_links(vault)
    links["grok-personal"] = dataclasses.replace(links["grok-personal"], remote=GITHUB)
    dm.save_links(vault, links)
    return vault


def test_a_deploy_key_is_added_read_only_and_its_private_half_kept_outside_the_vault(github_link: Path) -> None:
    gh = FakeGh()
    result = dm.deploy_key(github_link, "grok-personal", gh=gh, keygen=_fake_keygen)
    post = next(c for c in gh.calls if c[:3] == ["api", "-X", "POST"])
    assert post[3] == "repos/someone/grok-mirror/keys"
    assert "read_only=true" in post and "title=scout-delegate-grok-personal" in post
    key = dm.key_path(github_link, "grok-personal")
    assert key.read_text(encoding="utf-8") == "PRIVATE-KEY-STAND-IN\n"
    assert key.stat().st_mode & 0o777 == 0o600 and key.parent.stat().st_mode & 0o777 == 0o700
    assert not str(key).startswith(str(github_link))
    assert dm.load_links(github_link)["grok-personal"].deploy_key_id == 100
    assert any("git clone --depth 1 git@github.com:someone/grok-mirror.git" in n for n in result.notes)
    assert all("PRIVATE-KEY" not in n for n in result.notes)
    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(github_link, "grok-personal", gh=gh, keygen=_fake_keygen)
    assert e.value.code == "key_exists"


def test_rotate_replaces_the_key_and_revoke_deletes_it(github_link: Path) -> None:
    gh = FakeGh()
    dm.deploy_key(github_link, "grok-personal", gh=gh, keygen=_fake_keygen)
    dm.deploy_key(github_link, "grok-personal", rotate=True, gh=gh, keygen=_fake_keygen)
    assert list(gh.keys) == [101]
    assert dm.load_links(github_link)["grok-personal"].deploy_key_id == 101
    dm.deploy_key(github_link, "grok-personal", revoke=True, gh=gh)
    assert gh.keys == {} and not dm.key_path(github_link, "grok-personal").exists()
    assert dm.load_links(github_link)["grok-personal"].deploy_key_id is None


def test_a_key_github_records_as_writable_is_deleted_and_refused(github_link: Path) -> None:
    gh = FakeGh(read_only="false")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(github_link, "grok-personal", gh=gh, keygen=_fake_keygen)
    assert e.value.code == "key_not_read_only"
    assert gh.keys == {} and not dm.key_path(github_link, "grok-personal").exists()
    assert dm.load_links(github_link)["grok-personal"].deploy_key_id is None


def test_a_key_needs_a_github_mirror_and_the_owner(vault: Path, tmp_path: Path, home: Path) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(vault, "grok-personal", gh=FakeGh(), keygen=_fake_keygen)
    assert e.value.code == "key_needs_github"
    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(vault, "grok-personal", env={"SCOUT_MODE": "briefing"}, gh=FakeGh(), keygen=_fake_keygen)
    assert e.value.code == "headless_refused"


def test_unlink_revokes_the_key_and_keeps_the_link_when_it_cannot(github_link: Path) -> None:
    dm.deploy_key(github_link, "grok-personal", gh=FakeGh(), keygen=_fake_keygen)
    with pytest.raises(rs.RepoSyncError) as e:
        dm.unlink(github_link, "grok-personal", gh=FakeGh(delete_ok=False))
    assert e.value.code == "key_revoke_failed"
    assert "grok-personal" in dm.load_links(github_link)
    gh = FakeGh()
    result = dm.unlink(github_link, "grok-personal", gh=gh)
    assert ["api", "-X", "DELETE", "repos/someone/grok-mirror/keys/100"] in gh.calls
    assert "revoked the agent's deploy key" in result.notes
    assert dm.load_links(github_link) == {} and not dm.key_path(github_link, "grok-personal").exists()


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
def test_the_real_keygen_makes_an_ed25519_pair(tmp_path: Path) -> None:
    dm._keygen(tmp_path / "k", "scout-delegate-x")
    assert (tmp_path / "k.pub").read_text(encoding="utf-8").startswith("ssh-ed25519 ")
    assert (tmp_path / "k.pub").read_text(encoding="utf-8").strip().endswith("scout-delegate-x")


def test_make_repo_creates_a_private_repository(home: Path) -> None:
    gh = FakeGh()
    assert dm.make_repo("someone/grok-mirror", gh=gh) == GITHUB
    assert gh.calls == [
        ["repo", "create", "someone/grok-mirror", "--private", "--description", "Scout delegate mirror"]
    ]
    with pytest.raises(rs.RepoSyncError) as e:
        dm.make_repo("someone/grok-mirror", gh=FakeGh(create_ok=False))
    assert e.value.code == "repo_create_failed" and "already exists" in e.value.message
    for bad in ("grok-mirror", "a/b/c", "a/b c", "--private/x", "o/--public", "o/.x"):
        with pytest.raises(rs.RepoSyncError):
            dm.make_repo(bad, gh=gh)


def test_link_creates_the_repository_only_after_its_checks_pass(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[str] = []
    bare = _bare(tmp_path, "grok")

    def fake_make(repo: str, *, gh: object) -> str:
        made.append(repo)
        return str(bare)

    monkeypatch.setattr(dm, "make_repo", fake_make)
    for kw in ({"account_unshared": False}, {"env": {"SCOUT_MODE": "briefing"}}):
        with pytest.raises(rs.RepoSyncError):
            dm.link(vault, "grok-personal", "", **{"account_unshared": True, **kw, "create_repo": "o/r"})  # type: ignore[arg-type]
    with pytest.raises(rs.RepoSyncError) as e:
        dm.link(vault, "unlisted", "", account_unshared=True, create_repo="o/r")
    assert e.value.code == "delegate_not_listed"
    with pytest.raises(rs.RepoSyncError) as e:
        dm.link(vault, "grok-personal", str(bare), account_unshared=True, create_repo="o/r")
    assert e.value.code == "bad_arguments"
    assert made == []
    result = dm.link(vault, "grok-personal", "", account_unshared=True, create_repo="o/r")
    assert made == ["o/r"] and result.pushed
    assert dm.load_links(vault)["grok-personal"].remote == str(bare)


def test_the_push_token_goes_to_git_in_its_environment_for_github_only(vault: Path, home: Path) -> None:
    assert dm.auth_env(vault, GITHUB) == {}
    dm.save_push_token(vault, "github_pat_STANDIN\n")
    p = dm.push_token_path(vault)
    assert p.stat().st_mode & 0o777 == 0o600 and str(p).startswith(str(home))
    env = dm.auth_env(vault, GITHUB)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert base64.b64decode(env["GIT_CONFIG_VALUE_0"].split()[-1]).decode() == "x-access-token:github_pat_STANDIN"
    assert dm.auth_env(vault, "/some/local/path.git") == {}
    assert dm.auth_env(vault, "git@github.com:o/r.git") == {}
    p.chmod(0o644)
    with pytest.raises(rs.RepoSyncError) as e:
        dm.auth_env(vault, GITHUB)
    assert e.value.code == "token_insecure"
    with pytest.raises(rs.RepoSyncError):
        dm.save_push_token(vault, "  ")


def test_push_passes_the_token_in_the_environment_not_the_arguments(
    vault: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dm.save_push_token(vault, "github_pat_STANDIN")
    seen: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def fake_git(_vault: Path, *args: str, env: dict[str, str] | None = None, **_kw: object) -> object:
        seen.append((args, dict(env or {})))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(dm, "git", fake_git)
    dm.push(vault, GITHUB, "abc123", None)
    dm.remote_head(vault, GITHUB)
    assert len(seen) == 2
    for args, env in seen:
        assert not any("STANDIN" in a or "extraheader" in a for a in args)
        assert "GIT_CONFIG_VALUE_0" in env


# ----- edges: unreadable state, odd files, failures on the way --------------------------------


def test_an_unreadable_link_state_is_refused(vault: Path) -> None:
    dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    dm.state_path(vault).write_text("{not json", encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.load_links(vault)
    assert e.value.code == "state_unreadable"


def test_the_real_gh_runner_runs_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "true\n", "")

    monkeypatch.setattr(dm.subprocess, "run", fake_run)
    assert dm._gh(["api", "x"]).stdout == "true\n"
    assert seen == [["gh", "api", "x"]]


def test_a_missing_gh_is_a_refusal() -> None:
    def gh(_args: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote(GITHUB, gh=gh)
    assert e.value.code == "remote_unverified"


def test_head_without_a_committed_config_or_with_a_broken_one_is_refused(vault: Path) -> None:
    config = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    _commit(vault, "scout-config.yaml", "user: [unclosed\n", "break it")
    (vault / "scout-config.yaml").write_text(config, encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.preview(vault, "grok-personal")
    assert e.value.code == "privacy_overlay_unreadable"
    git(vault, "rm", "-q", "--cached", "scout-config.yaml")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "untrack")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.preview(vault, "grok-personal")
    assert e.value.code == "delegate_not_listed"


def test_a_vault_with_no_commits_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    v = tmp_path / "empty"
    v.mkdir()
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    git(v, "init", "-q")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.preview(v, "grok-personal")
    assert e.value.code == "no_commits"


def test_a_local_only_config_means_no_mirror(vault: Path, tmp_path: Path) -> None:
    config = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    _commit(
        vault, "scout-config.yaml", config.replace('- "trading/**"', '- "trading/**"\n    - scout-config.yaml'), "x"
    )
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    assert e.value.code == "config_not_mirrored"


def test_odd_files_binary_undecodable_absolute_links_lookalikes_and_label_changes(vault: Path, tmp_path: Path) -> None:
    files = {
        "notes/pic.png": b"\x89PNG\x00\x00binary [[sam]]",
        "notes/latin1.txt": b"caf\xe9 [[sam]]\n",
        "notes/clean.json": b'{"ok": true}\n',
        "notes/bare.md": b"# Bare\n\nWith [[sam]] here.\nKept line.\n",
        "knowledge-base/personal/ok.md": b"---\nvisibility: normal  # mentions [[sam]]\n---\n\nfine\n",
        ".SCOUT-STATE/Delegate-Mirror.json": b"{}\n",
    }
    for rel, data in files.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_bytes(data)
    (vault / "abs-link").symlink_to("/etc/hosts")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "odd files")
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    head = _head_files(bare)
    assert {"notes/pic.png", "notes/latin1.txt", "notes/clean.json"} <= head
    assert _show(bare, "notes/bare.md") == "# Bare\n\nKept line.\n"
    assert "knowledge-base/personal/ok.md" not in head  # its label would turn private without that line
    assert "abs-link" not in head and ".SCOUT-STATE/Delegate-Mirror.json" not in head
    assert result.left_out >= 1


def test_frontmatter_edges() -> None:
    terms = _terms()
    assert dm.redact_lines("---\nname: x\n[[sam-lee]]\n", terms, markdown=True) == ("---\nname: x\n", 1)  # type: ignore[arg-type]
    nested = '---\nrelationships:\n  - type: knows\n    note: a\n    target: "[[sam-lee]]"\n  - type: on\n---\n'
    assert dm.redact_lines(nested, terms, markdown=True) == ("---\nrelationships:\n  - type: on\n---\n", 3)  # type: ignore[arg-type]
    odd = '---\n  odd: "[[sam-lee]]"\nname: x\n---\n'
    assert dm.redact_lines(odd, terms, markdown=True) == ("---\nname: x\n---\n", 1)  # type: ignore[arg-type]
    assert dm._frontmatter_parses("---\nkey: [\n---\n") is False
    assert dm._frontmatter_parses("no frontmatter\n") is True


def test_redaction_that_never_settles_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dm, "find_mentions", lambda *_a, **_k: [1])
    assert dm.redact_lines("a\nb\nc\nd\ne\nf\ng\nh\n", _terms(), markdown=False) is None  # type: ignore[arg-type]


def test_an_unreadable_shared_reads_ledger_stops_the_build(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb.shared_reads import SharedReadsUnreadable

    def boom(*_a: object) -> object:
        raise SharedReadsUnreadable("bad ledger")

    monkeypatch.setattr(dm, "with_shared", boom)
    with pytest.raises(rs.RepoSyncError) as e:
        dm.preview(vault, "grok-personal")
    assert e.value.code == "shared_reads_unreadable"


def _planned(vault: Path) -> tuple[dm.Plan, object, object, object, str]:
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan, policy, rules, terms = dm.plan(vault, head, "grok-personal", blobs)
    finally:
        blobs.close()
    return the_plan, policy, rules, terms, head


def test_check_tree_refuses_missing_generated_files_local_only_paths_mentions_and_stray_links(vault: Path) -> None:
    the_plan, policy, rules, terms, head = _planned(vault)
    bare_tree = rs._write_tree(vault, [f"{f.mode} {f.sha}\t{f.path}" for f in the_plan.files if f.data is None])
    commit = git(vault, "commit-tree", bare_tree, "-m", "x").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", policy, rules, terms)  # type: ignore[arg-type]
    assert e.value.code == "policy_missing"
    trading = rs._blob_at(vault, head, "trading/positions.md")
    priya = rs._blob_at(vault, head, PRIYA)
    assert trading is not None and priya is not None
    for extra in (
        dm.MirrorFile(trading.path, trading.mode, None, trading.sha),
        dm.MirrorFile("notes/raw-priya.md", priya.mode, None, priya.sha),  # unredacted: links [[sam]]
        dm.MirrorFile("stray", "120000", b"knowledge-base/people/sam.md", ""),
    ):
        p, pol, ru, te, _ = _planned(vault)
        p.files.append(extra)
        commit = git(vault, "commit-tree", dm.write_tree(vault, p, pol), "-m", "x").strip()  # type: ignore[arg-type]
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, "grok-personal", pol, ru, te)  # type: ignore[arg-type]
        assert e.value.code == "delegate_mirror_unsafe", extra.path


def test_link_to_github_points_at_the_key_command(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dm, "github_repo", lambda _r: "someone/grok-mirror")
    result = dm.link(vault, "grok-personal", str(_bare(tmp_path, "grok")), account_unshared=True, gh=FakeGh())
    assert any("scoutctl delegate key grok-personal" in n for n in result.notes)


def test_not_linked_and_no_key_refusals(vault: Path, home: Path) -> None:
    for call in (
        lambda: dm.sync(vault, "duet"),
        lambda: dm.unlink(vault, "duet"),
        lambda: dm.deploy_key(vault, "duet", gh=FakeGh(), keygen=_fake_keygen),
    ):
        with pytest.raises(rs.RepoSyncError) as e:
            call()
        assert e.value.code == "not_linked"


def test_key_failures(github_link: Path, tmp_path: Path) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(github_link, "grok-personal", revoke=True, gh=FakeGh())
    assert e.value.code == "no_key"

    def failing(args: object) -> subprocess.CompletedProcess[str]:
        a = list(args)  # type: ignore[call-overload]
        if "POST" in a:
            return subprocess.CompletedProcess(["gh"], 1, "", "HTTP 422: key is already in use")
        return FakeGh()(a)

    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(github_link, "grok-personal", gh=failing, keygen=_fake_keygen)
    assert e.value.code == "key_add_failed" and "already in use" in e.value.message

    def garbled(args: object) -> subprocess.CompletedProcess[str]:
        a = list(args)  # type: ignore[call-overload]
        return subprocess.CompletedProcess(["gh"], 0, "{}\n", "") if "POST" in a else FakeGh()(a)

    with pytest.raises(rs.RepoSyncError) as e:
        dm.deploy_key(github_link, "grok-personal", gh=garbled, keygen=_fake_keygen)
    assert e.value.code == "key_add_failed"
    with pytest.raises(rs.RepoSyncError) as e:
        dm._keygen(tmp_path / "no-such-dir" / "k", "x")
    assert e.value.code == "keygen_failed"


def test_rotate_keeps_going_when_the_old_key_wont_delete(github_link: Path) -> None:
    dm.deploy_key(github_link, "grok-personal", gh=FakeGh(), keygen=_fake_keygen)
    result = dm.deploy_key(github_link, "grok-personal", rotate=True, gh=FakeGh(delete_ok=False), keygen=_fake_keygen)
    assert any(n.startswith("warning: couldn't delete deploy key 100") for n in result.notes)
    assert dm.load_links(github_link)["grok-personal"].deploy_key_id == 100  # the new key's id in that fake


def test_the_cli_reports_failures_and_the_other_commands(vault: Path, tmp_path: Path, home: Path) -> None:
    r = runner.invoke(app, ["delegate", "status"])
    assert "no delegate mirrors" in r.stdout
    r = runner.invoke(app, ["delegate", "preview", "unlisted", "--json"])
    assert r.exit_code == 2 and json.loads(r.stdout)["code"] == "delegate_not_listed"
    r = runner.invoke(app, ["delegate", "sync", "duet"])
    assert r.exit_code == 2 and "has no mirror" in r.output
    r = runner.invoke(app, ["delegate", "unlink", "duet"])
    assert r.exit_code == 2
    r = runner.invoke(app, ["delegate", "key", "duet", "--rotate", "--revoke"])
    assert r.exit_code == 2
    r = runner.invoke(app, ["delegate", "key", "duet"])
    assert r.exit_code == 2 and "has no mirror" in r.output
    r = runner.invoke(
        app, ["delegate", "link", "duet", "--remote", "https://gitlab.com/o/r", "--confirm-account-unshared"]
    )
    assert r.exit_code == 2 and "private GitHub repository" in r.output
    grok, duet = _bare(tmp_path, "grok"), _bare(tmp_path, "duet")
    _link(vault, "grok-personal", grok)
    _link(vault, "duet", duet)
    r = runner.invoke(app, ["delegate", "sync", "grok-personal"])
    assert r.exit_code == 0 and "up to date" in r.stdout
    r = runner.invoke(app, ["delegate", "status", "--json"])
    assert [x["id"] for x in json.loads(r.stdout)["links"]] == ["duet", "grok-personal"]
    shutil.rmtree(duet)
    git(vault, "add", "-A")  # link's .gitignore line: a change both mirrors carry
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "ignore state")
    r = runner.invoke(app, ["delegate", "sync", "--all"])
    assert r.exit_code == 2 and "duet:" in r.output and "grok-personal: pushed" in r.stdout
    r = runner.invoke(app, ["delegate", "sync", "--all", "--json"])
    assert r.exit_code == 2 and set(json.loads(r.stdout)["failed"]) == {"duet"}
    token = tmp_path / "token"
    token.write_text("github_pat_STANDIN\n", encoding="utf-8")
    r = runner.invoke(app, ["delegate", "push-token", "--token-file", str(token)])
    assert r.exit_code == 0 and str(dm.push_token_path(vault)) in r.stdout
    r = runner.invoke(app, ["delegate", "push-token"], input="github_pat_OTHER\n")
    assert r.exit_code == 0 and "OTHER" in dm.push_token_path(vault).read_text(encoding="utf-8")
    r = runner.invoke(app, ["delegate", "push-token", "--token-file", str(tmp_path / "missing")])
    assert r.exit_code == 2
    blank = tmp_path / "blank"
    blank.write_text("  \n", encoding="utf-8")
    r = runner.invoke(app, ["delegate", "push-token", "--token-file", str(blank)])
    assert r.exit_code == 2 and "no token" in r.output


def test_the_cli_key_command_and_an_unreadable_state(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool, bool]] = []

    def fake_key(_vault: Path, delegate: str, *, rotate: bool, revoke: bool) -> dm.Result:
        calls.append((delegate, rotate, revoke))
        return dm.Result(delegate=delegate, action="key", notes=["added a read-only deploy key on o/r"])

    monkeypatch.setattr(dm, "deploy_key", fake_key)
    r = runner.invoke(app, ["delegate", "key", "duet", "--rotate"])
    assert r.exit_code == 0 and "added a read-only deploy key" in r.stdout and calls == [("duet", True, False)]
    dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    dm.state_path(vault).write_text("[]", encoding="utf-8")
    for argv in (["delegate", "status"], ["delegate", "sync", "--all"]):
        r = runner.invoke(app, argv)
        assert r.exit_code == 2 and "can't be read" in r.output


def test_a_file_redaction_cannot_settle_is_left_out(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = dm.preview(vault, "grok-personal")
    monkeypatch.setattr(dm, "redact_lines", lambda *_a, **_k: None)
    result = dm.preview(vault, "grok-personal")
    assert result.left_out > 0 and result.files < full.files
