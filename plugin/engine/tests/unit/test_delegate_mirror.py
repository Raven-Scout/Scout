"""scoutctl delegate link | sync | preview | status | unlink (#550 V2): per-delegate filtered mirrors.

The remote is a local bare repository, so git runs for real. The vault is the
anonymised privacy fixture vault (``privacy_vault_helpers``) with two delegates:
``grok-personal`` (normal notes only) and ``duet`` (``include_private: true``).
Every blob of every mirror commit is read to prove what never reaches it.
"""

from __future__ import annotations

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
from scout.kb.outbound import PrivateTerms, clear_cache, find_mentions, note_from_text, terms_of
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


class GitHubRepo:
    """``gh api`` for one repository: its visibility, owner and collaborators."""

    def __init__(
        self,
        visibility: str = "private",
        owner_type: str = "User",
        collaborators: tuple[str, ...] = ("someone",),
        fork: str = "false",
    ) -> None:
        self.visibility = visibility
        self.owner_type = owner_type
        self.fork = fork
        self.collaborators = collaborators
        self.calls: list[list[str]] = []

    def __call__(self, args: object) -> subprocess.CompletedProcess[str]:
        a = list(args)  # type: ignore[call-overload]
        self.calls.append(a)
        if a[-1].endswith("@tsv"):
            out = f"{self.visibility}\t{self.owner_type}\tsomeone\t{self.fork}\n"
            return subprocess.CompletedProcess(["gh"], 0, out, "")
        if any(x.endswith("/collaborators") for x in a):
            return subprocess.CompletedProcess(["gh"], 0, "".join(f"{c}\n" for c in self.collaborators), "")
        raise AssertionError(f"unexpected gh call {a}")


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
    assert (
        "local_only_add" not in config["privacy"] or config["privacy"]["local_only_add"] == []
    )  # names no hidden path
    assert "trading" not in _show(bare, "scout-config.yaml") and "trading" not in _show(bare, rs.POLICY_PATH)
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
def test_only_github_and_local_remotes_are_accepted(remote: str, vault: Path) -> None:
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote(remote, vault=vault, gh=GitHubRepo())
    assert e.value.code == "remote_unsupported"


@pytest.mark.parametrize(
    "remote",
    ["https://github.com/o/r", "https://github.com/o/r.git", "git@github.com:o/r.git", "ssh://git@github.com/o/r"],
)
def test_a_github_remote_must_be_private_personal_and_unshared(remote: str, vault: Path) -> None:
    gh = GitHubRepo()
    dm.check_remote(remote, vault=vault, gh=gh)
    assert gh.calls == [
        ["api", "repos/o/r", "--jq", "[.visibility, .owner.type, .owner.login, .fork] | @tsv"],
        ["api", "--paginate", "repos/o/r/collaborators", "--jq", ".[].login"],
    ]
    for repo, code in (
        (GitHubRepo(visibility="public"), "remote_public"),
        (GitHubRepo(visibility="internal"), "remote_public"),
        (GitHubRepo(owner_type="Organization"), "remote_org_owned"),
        (GitHubRepo(collaborators=("someone", "teammate")), "remote_shared"),
        (GitHubRepo(fork="true"), "remote_fork"),
    ):
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_remote(remote, vault=vault, gh=repo)
        assert e.value.code == code
    for out, rc in (("", 1), ("garbled\n", 0), ("private\tUser\tsomeone\tmaybe\n", 0)):
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_remote(
                remote, vault=vault, gh=lambda _a, o=out, r=rc: subprocess.CompletedProcess(["gh"], r, o, "")
            )
        assert e.value.code == "remote_unverified"


def test_check_tree_refuses_a_commit_that_holds_a_hidden_note(vault: Path) -> None:
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    sam = rs._blob_at(vault, head, "knowledge-base/people/sam.md")
    assert sam is not None
    the_plan.files.append(dm.MirrorFile(sam.path, sam.mode, None, sam.sha))
    tree = dm.write_tree(vault, the_plan)
    commit = git(vault, "commit-tree", tree, "-m", "x").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", head)
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


def test_a_missing_gh_is_a_refusal(vault: Path) -> None:
    def gh(_args: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote("https://github.com/someone/grok-mirror.git", vault=vault, gh=gh)
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
    assert {"notes/pic.png", "notes/clean.json"} <= head
    assert "notes/latin1.txt" not in head  # not UTF-8, so it can't be scanned: left out
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


def _planned(vault: Path) -> tuple[dm.Plan, str]:
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    return the_plan, head


def test_check_tree_refuses_missing_generated_files_local_only_paths_mentions_and_stray_links(vault: Path) -> None:
    the_plan, head = _planned(vault)
    bare_tree = rs._write_tree(vault, [f"{f.mode} {f.sha}\t{f.path}" for f in the_plan.files if f.data is None])
    commit = git(vault, "commit-tree", bare_tree, "-m", "x").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", head)
    assert e.value.code == "policy_missing"
    trading = rs._blob_at(vault, head, "trading/positions.md")
    priya = rs._blob_at(vault, head, PRIYA)
    assert trading is not None and priya is not None
    for extra in (
        dm.MirrorFile(trading.path, trading.mode, None, trading.sha),
        dm.MirrorFile("notes/raw-priya.md", priya.mode, None, priya.sha),  # unredacted: links [[sam]]
        dm.MirrorFile("stray", "120000", b"knowledge-base/people/sam.md", ""),
    ):
        p, _ = _planned(vault)
        p.files.append(extra)
        commit = git(vault, "commit-tree", dm.write_tree(vault, p), "-m", "x").strip()
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, "grok-personal", head)
        assert e.value.code == "delegate_mirror_unsafe", extra.path


def test_the_cli_reports_failures_and_the_other_commands(vault: Path, tmp_path: Path) -> None:
    r = runner.invoke(app, ["delegate", "status"])
    assert "no delegate mirrors" in r.stdout
    r = runner.invoke(app, ["delegate", "preview", "unlisted", "--json"])
    assert r.exit_code == 2 and json.loads(r.stdout)["code"] == "delegate_not_listed"
    r = runner.invoke(app, ["delegate", "sync", "duet"])
    assert r.exit_code == 2 and "has no mirror" in r.output
    r = runner.invoke(app, ["delegate", "unlink", "duet"])
    assert r.exit_code == 2
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


def test_a_file_redaction_cannot_settle_is_left_out(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = dm.preview(vault, "grok-personal")
    monkeypatch.setattr(dm, "redact_lines", lambda *_a, **_k: None)
    result = dm.preview(vault, "grok-personal")
    assert result.left_out > 0 and result.files < full.files


def test_the_cli_refuses_an_unreadable_state(vault: Path) -> None:
    dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    dm.state_path(vault).write_text("[]", encoding="utf-8")
    for argv in (["delegate", "status"], ["delegate", "sync", "--all"]):
        r = runner.invoke(app, argv)
        assert r.exit_code == 2 and "can't be read" in r.output


# ----- review of #632 (coordinator, 2026-11-13) ---------------------------------------------------


def _commit_files(vault: Path, files: dict[str, bytes], subject: str) -> None:
    for rel, data in files.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_bytes(data)
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def test_text_that_is_not_utf8_is_left_out_not_kept_as_binary(vault: Path, tmp_path: Path) -> None:
    _commit_files(
        vault,
        {
            "notes/latin1.md": "---\ntitle: Café\n---\n\nSee [[sam]] and Sammy.\n".encode("latin-1"),
            "notes/latin1.txt": "café Sammy\n".encode("latin-1"),
            "notes/nul.txt": b"Sammy\x00\n",
        },
        "latin-1",
    )
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    assert not {"notes/latin1.md", "notes/latin1.txt", "notes/nul.txt"} & _head_files(bare)
    assert not any("Sammy" in t for t in _all_blobs(bare).values())


def test_the_config_and_policy_document_name_no_hidden_folder_or_note(vault: Path, tmp_path: Path) -> None:
    config = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    config = config.replace(
        'local_only_add:\n    - "trading/**"',
        'local_only_add:\n    - "trading/**"\n    - "hush-folder/**"\n'
        '  visibility_defaults:\n    - {glob: "secret-garden/**", visibility: private}\n'
        "  unknown_key: [hush-folder]",
    )
    config += "projects:\n  - Sammy\nteam: [Priya]\n"
    _commit_files(
        vault,
        {"scout-config.yaml": config.encode(), "secret-garden/plan.md": b"# Plan\n\nquiet\n"},
        "private folders",
    )
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    mirrored = _show(bare, "scout-config.yaml")
    policy_doc = _show(bare, rs.POLICY_PATH)
    for name in ("hush-folder", "secret-garden", "trading", "Sammy", "unknown_key"):
        assert name not in mirrored and name not in policy_doc, name
    assert yaml.safe_load(mirrored)["team"] == ["Priya"]
    assert "secret-garden/plan.md" not in _head_files(bare)


def test_a_file_whose_path_names_a_hidden_note_is_left_out(vault: Path, tmp_path: Path) -> None:
    _commit_files(vault, {"notes/Sammy plans.md": b"# Plans\n\nnothing private here\n"}, "named after")
    (vault / "Sammy-folder").symlink_to("knowledge-base")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "link")
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    files = _head_files(bare)
    assert "notes/Sammy plans.md" not in files and "Sammy-folder" not in files
    assert "Sammy" not in _show(bare, rs.POLICY_PATH)
    assert result.left_out >= 1


def test_a_name_spelled_with_combining_accents_still_matches() -> None:
    terms = terms_of([note_from_text("people/jose-ruiz.md", "---\nname: José Ruiz\n---\n")])
    nfd = "Met José Ruiz today.\nOther.\n"
    assert find_mentions(nfd, terms, allow_markers=False) == [1]
    assert dm.redact_lines(nfd, terms, markdown=True) == ("Other.\n", 1)  # type: ignore[arg-type]
    assert find_mentions("JOSÉ RUIZ\n", terms, allow_markers=False) == [1]
    fullwidth = "ｊｏｓé ｒｕｉｚ\n"  # NFKC folds it to the plain spelling
    assert find_mentions(fullwidth, terms, allow_markers=False) == [1]


def test_every_link_form_and_a_bare_path_are_redacted(vault: Path, tmp_path: Path) -> None:
    lines = [
        "keep 0",
        "embed ![[sam]]",
        "alias [[Sammy]]",
        "heading [[sam#Open questions]]",
        "piped [[people/sam|someone]]",
        "md [who](../people/sam.md)",
        "md2 [who](<knowledge-base/people/sam.md#x>)",
        "encoded [who](../people/s%61m.md)",
        "[ref]: knowledge-base/people/sam.md",
        "bare knowledge-base/people/sam.md",
        "keep 1 [web](https://example.com/sam.md) and [plain](widget.md)",
    ]
    _commit_files(vault, {"knowledge-base/topics/links.md": ("\n".join(lines) + "\n").encode()}, "links")
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    assert _show(bare, "knowledge-base/topics/links.md") == "keep 0\n" + lines[-1] + "\n"


def test_a_local_remote_inside_the_vault_or_the_vault_itself_is_refused(vault: Path, tmp_path: Path) -> None:
    inner = vault / "inner.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(inner)], check=True)
    before = git(vault, "rev-parse", "HEAD")
    for remote in (inner, vault, vault.parent):
        with pytest.raises(rs.RepoSyncError) as e:
            _link(vault, "grok-personal", remote, replace_remote=True)
        assert e.value.code == "remote_in_vault", remote
    assert git(vault, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("kind", ["insteadOf", "pushInsteadOf"])
def test_a_remote_git_rewrites_is_refused(vault: Path, tmp_path: Path, kind: str) -> None:
    bare = _bare(tmp_path, "elsewhere")
    git(vault, "config", f"url.{bare}/.{kind}", "https://github.com/someone/")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_remote("https://github.com/someone/grok-mirror.git", vault=vault, gh=GitHubRepo())
    assert e.value.code == "remote_rewritten"


def test_the_github_check_runs_before_the_push(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    remote = "https://github.com/someone/grok-mirror.git"
    monkeypatch.setattr(dm, "remote_head", lambda *_a: None)
    monkeypatch.setattr(dm, "push", lambda *_a: events.append("push"))
    gh = GitHubRepo()

    def watched(args: object) -> subprocess.CompletedProcess[str]:
        events.append("gh")
        return gh(args)

    result = dm.link(vault, "grok-personal", remote, account_unshared=True, gh=watched)
    assert result.pushed and events == ["gh", "gh", "gh", "gh", "push"]  # link's check, the push's, then the push
    events.clear()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.link(vault, "duet", remote, account_unshared=True, gh=GitHubRepo(visibility="internal"))
    assert e.value.code == "remote_public" and events == []


def test_a_sync_that_died_after_its_push_adopts_the_pushed_commit(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    real_push = dm.push

    def push_then_die(*a: object) -> None:
        real_push(*a)  # type: ignore[arg-type]
        raise rs.RepoSyncError("push_failed", "connection dropped after the push landed")

    monkeypatch.setattr(dm, "push", push_then_die)
    with pytest.raises(rs.RepoSyncError):
        dm.sync(vault, "grok-personal")
    pending = dm.load_links(vault)["grok-personal"].pending_mirror
    assert pending == _bare_git(bare, "rev-parse", "main").strip()
    monkeypatch.setattr(dm, "push", real_push)
    result = dm.sync(vault, "grok-personal")
    assert result.unchanged
    link = dm.load_links(vault)["grok-personal"]
    assert link.last_mirror == pending and link.pending_mirror is None


def test_a_pending_commit_the_remote_never_took_is_dropped(vault: Path, tmp_path: Path) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    links = dm.load_links(vault)
    links["grok-personal"] = dataclasses.replace(links["grok-personal"], pending_mirror="0" * 40)
    dm.save_links(vault, links)
    dm.sync(vault, "grok-personal")
    assert dm.load_links(vault)["grok-personal"].pending_mirror is None


def test_a_pending_commit_with_a_moved_remote_still_refuses(vault: Path, tmp_path: Path) -> None:
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    (other / "x.md").write_text("agent\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "agent write")
    git(other, "push", "-q", "origin", "HEAD:main")
    links = dm.load_links(vault)
    links["grok-personal"] = dataclasses.replace(links["grok-personal"], pending_mirror="0" * 40)
    dm.save_links(vault, links)
    _commit(vault, "knowledge-base/topics/misc/new.md", "---\ntitle: New\n---\n\nnew\n", "new")
    with pytest.raises(rs.RepoSyncError) as e:
        dm.sync(vault, "grok-personal")
    assert e.value.code == "mirror_diverged"


def test_a_second_sync_while_one_holds_the_lock_is_refused(vault: Path, tmp_path: Path) -> None:
    _link(vault, "grok-personal", _bare(tmp_path, "grok"))
    with dm.locked(vault):
        with pytest.raises(rs.RepoSyncError) as e:
            dm.sync(vault, "grok-personal")
        assert e.value.code == "busy"
    assert git(vault, "check-ignore", dm.LOCK_REL).strip() == dm.LOCK_REL


def test_a_byte_order_mark_keeps_frontmatter_redaction_working() -> None:
    text = '﻿---\nname: x\nrelationships:\n  - target: "[[sam-lee]]"\n---\nbody\n'
    expected = "﻿---\nname: x\nrelationships:\n---\nbody\n"
    assert dm.redact_lines(text, _terms(), markdown=True) == (expected, 1)  # type: ignore[arg-type]


def test_gh_asks_github_com_whatever_the_environment_says(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str]] = []

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(dict(kw["env"]))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("GH_HOST", "ghe.example.com")
    monkeypatch.setenv("GH_REPO", "someone/elsewhere")
    monkeypatch.setattr(dm.subprocess, "run", fake_run)
    dm._gh(["api", "x"])
    assert seen[0]["GH_HOST"] == "github.com" and "GH_REPO" not in seen[0]


def test_config_keys_that_name_a_hidden_note_are_dropped_but_a_broken_privacy_block_refuses(vault: Path) -> None:
    scan = dm.Scanner(_terms())  # type: ignore[arg-type]
    text = "privacy:\n  surfaces: {owner_only: [x]}\nSammy: 1\nnotes: [Sam Lee]\nok: yes\n"
    out = yaml.safe_load(dm.mirror_config(text, "d", False, [], scan))
    assert set(out) == {"privacy", "ok"} and out["privacy"]["delegates"] == {"d": {}}
    with pytest.raises(rs.RepoSyncError) as e:
        dm.mirror_config("privacy:\n  surfaces: {owner_only: [Sam Lee]}\n", "d", False, [], scan)
    assert e.value.code == "config_names_hidden"
    assert dm._scoped(["a/**", {"glob": "b/**"}, 3, {"x": 1}], ["a/1.md"]) == ["a/**"]


def _tampered(vault: Path, delegate: str, swap: dict[str, bytes]) -> tuple[str, str]:
    """A mirror commit of HEAD for ``delegate`` with some files' contents swapped; (commit, head)."""
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, delegate, blobs)[0]
    finally:
        blobs.close()
    the_plan.files = [dm.MirrorFile(f.path, f.mode, swap[f.path], "") if f.path in swap else f for f in the_plan.files]
    return git(vault, "commit-tree", dm.write_tree(vault, the_plan), "-m", "x").strip(), head


def test_check_tree_refuses_an_unredacted_file_a_config_that_hides_or_relabels(vault: Path) -> None:
    raw_priya = (vault / PRIYA).read_bytes()
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    unlisted = yaml.safe_dump({**config, "privacy": {"delegates": {}}}).encode()
    relabel = {
        **config,
        "privacy": {
            "delegates": {"duet": {"include_private": True}},
            "visibility_defaults": [{"glob": "knowledge-base/topics/**", "visibility": "private"}],
        },
    }
    for delegate, swap in (
        ("grok-personal", {PRIYA: raw_priya}),
        ("grok-personal", {"scout-config.yaml": unlisted}),
        ("duet", {"scout-config.yaml": yaml.safe_dump(relabel).encode()}),
    ):
        commit, head = _tampered(vault, delegate, swap)
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, delegate, head)
        assert e.value.code == "delegate_mirror_unsafe", (delegate, list(swap))


def test_a_file_the_clone_would_label_differently_is_left_out(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = dm.preview(vault, "duet")
    real = dm.policy_from_config_text

    def stricter(text: str) -> object:
        policy = real(text)
        if text.startswith("#"):  # the vault's own config keeps its comments; the mirror's is generated
            return policy
        return dataclasses.replace(
            policy, visibility_defaults=(("knowledge-base/topics/**", "private"), *policy.visibility_defaults)
        )

    monkeypatch.setattr(dm, "policy_from_config_text", stricter)
    result = dm.preview(vault, "duet")
    assert result.left_out > full.left_out and result.files < full.files


def test_an_unrelated_url_rewrite_is_fine(vault: Path) -> None:
    git(vault, "config", "url.https://example.org/.insteadOf", "https://elsewhere.example/")
    dm.check_remote("https://github.com/someone/grok-mirror.git", vault=vault, gh=GitHubRepo())


# ----- second review of #632 (coordinator, 2026-11-14) --------------------------------------------


def test_a_private_root_note_with_a_generic_name_neither_blocks_nor_over_redacts(vault: Path, tmp_path: Path) -> None:
    private = "---\nvisibility: private\n---\n\nquiet\n"
    _commit_files(
        vault,
        {
            "Personal.md": private.encode(),
            "labels.md": private.encode(),
            "health.md": private.encode(),
            "notes/routine.md": b"# Routine\n\nA health check.\nMy personal best.\nSee health.md for more.\n",
        },
        "generic root names",
    )
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)  # neither config_names_hidden nor delegate_mirror_unsafe
    assert not {"Personal.md", "labels.md", "health.md"} & _head_files(bare)
    # the bare stems are ordinary words; a root file's full name still counts as naming it
    assert _show(bare, "notes/routine.md") == "# Routine\n\nA health check.\nMy personal best.\n"


def test_a_folder_path_is_still_a_term_as_written() -> None:
    scan = dm.Scanner(terms_of([]), ["clients/acme-renewal.md", "health.md"])
    assert scan.lines("see clients/acme-renewal for it\nhealth\nhealth.md\n") == [1, 3]


def test_a_case_variant_of_the_vault_path_is_refused(vault: Path) -> None:
    variant = vault.parent / vault.name.swapcase()
    if not variant.exists():
        pytest.skip("this volume is case-sensitive")
    inner = vault / "mirror2.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(inner)], check=True)
    with pytest.raises(rs.RepoSyncError) as e:
        _link(vault, "grok-personal", variant / "mirror2.git")
    assert e.value.code == "remote_in_vault"


def test_overlap_is_decided_by_file_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real = dm._inode
    assert real(tmp_path / "missing") is None
    ids = {"/v/Vault": (1, 7), "/v/vault": (1, 7), "/v": (1, 2), "/v/Up": (1, 2)}
    monkeypatch.setattr(dm, "_inode", lambda p: ids.get(str(p)))
    assert dm._overlaps(Path("/v/Vault/m.git"), Path("/v/vault"))  # inside, by another spelling
    assert dm._overlaps(Path("/v/Up"), Path("/v/vault"))  # a folder above, by another spelling
    assert not dm._overlaps(Path("/v/other/m.git"), Path("/v/vault"))


def test_text_with_a_binary_extension_is_scanned(vault: Path, tmp_path: Path) -> None:
    _commit_files(
        vault,
        {
            "attachments/summary.pdf": b"Notes on Sammy, see [[sam]]\n",
            "attachments/plain.pdf": b"nothing private\n",
            "attachments/real.pdf": b"%PDF-1.4\n\xe2\xe3\xcf\xd3\n\x00Sammy\n",
        },
        "pdfs",
    )
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    files = _head_files(bare)
    assert "attachments/summary.pdf" not in files
    assert {"attachments/plain.pdf", "attachments/real.pdf"} <= files  # a real binary: kept on its label
    commit, head = _tampered(vault, "grok-personal", {"attachments/plain.pdf": b"Sammy [[sam]]\n"})
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", head)
    assert e.value.code == "delegate_mirror_unsafe"


def test_a_symlink_whose_folder_empties_at_the_last_check_goes_too(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit_files(vault, {"solo/only.md": b"# Only\n\nfine\n"}, "solo")
    (vault / "solo-link").symlink_to("solo")
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "link")
    real = dm._same_label
    monkeypatch.setattr(dm, "_same_label", lambda a, b: not a.path.startswith("solo/") and real(a, b))
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)  # check_tree passes: no dangling link reached it
    assert not {"solo/only.md", "solo-link"} & _head_files(bare)


def test_check_tree_refuses_generated_files_the_tree_does_not_imply(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    the_plan, head = _planned(vault)
    real_doc, real_manifest = dm.Plan.policy_document, dm.Plan.manifest
    for attr, real, fake in (
        (
            "policy_document",
            real_doc,
            lambda self: real_doc(self).replace(b'"labels"', b'"extra": "Sammy",\n  "labels"', 1),
        ),
        ("manifest", real_manifest, lambda self: real_manifest(self)[:-3] + b',\n  "note": "Sammy"\n}\n'),
    ):
        monkeypatch.setattr(dm.Plan, attr, fake)
        commit = git(vault, "commit-tree", dm.write_tree(vault, the_plan), "-m", "x").strip()
        monkeypatch.setattr(dm.Plan, attr, real)
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, "grok-personal", head)
        assert e.value.code == "delegate_mirror_unsafe", attr


def test_the_manifest_check_accepts_only_what_a_sync_writes(vault: Path) -> None:
    the_plan, _ = _planned(vault)
    good = json.loads(the_plan.manifest())
    paths = {f.path for f in the_plan.files}

    def canonical(doc: object) -> bytes:
        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()

    assert dm._manifest_ok(canonical(good), "grok-personal", paths)
    for bad in (
        b"not json",
        canonical([]),
        canonical({**good, "delegate": "duet"}),
        canonical({**good, "files": -1}),
        canonical({**good, "files": True}),
        canonical({**good, "redacted_lines": {"knowledge-base/people/sam.md": 1}}),
        canonical({**good, "redacted_lines": ["x"]}),
    ):
        assert not dm._manifest_ok(bad, "grok-personal", paths), bad


# ----- final review of #632 (coordinator, 2026-11-14) ---------------------------------------------


def test_sync_only_with_the_knowledge_base_symlink_keeps_every_kb_note(vault: Path, tmp_path: Path) -> None:
    """The standard layout: an allowlist of the KB and the config, and the ``knowledge_base`` symlink."""
    config = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    config += '  sync_only: ["knowledge-base/**", "scout-config.yaml"]\n'
    _commit_files(vault, {"scout-config.yaml": config.encode()}, "sync only the KB")
    privacy_migrate.accept(vault)
    full = dm.preview(vault, "grok-personal")
    assert full.left_out < 3 and not full.notes, full
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    files = _head_files(bare)
    kb = {f for f in files if f.startswith("knowledge-base/")}
    assert kb and "knowledge_base" not in files and result.left_out == 0
    assert not [n for n in result.notes if n.startswith("warning")]
    assert all(f.startswith(("knowledge-base/", ".scout-state/")) or f == "scout-config.yaml" for f in files), files


def test_path_terms_match_exactly_and_skip_symlinks_and_bare_root_names() -> None:
    hidden = ["notes/my_plan.md", "knowledge_base", "clients/acme-link", "budget.xlsx"]
    scan = dm.Scanner(terms_of([]), hidden, links={"clients/acme-link"})
    text = "notes/my-plan\nnotes/my_plan\nknowledge_base\nclients/acme-link\nsee budget.xlsx\n[x](knowledge_base)\n"
    assert scan.lines(text) == [2, 5, 6]  # an exact link still counts
    assert not scan.names("knowledge-base/people/sam.md")


def test_the_scanner_keeps_the_citation_and_shared_path_pre_check() -> None:
    from scout.kb.outbound import _CITATION

    terms = PrivateTerms(notes=(_CITATION,), any_term=_CITATION.pattern)
    scan = dm.Scanner(terms, ["clients/acme-renewal.md"])
    assert scan.lines("Filed from Priya's shared note today.\nclients/acme-renewal\nnothing\n") == [1, 2]


def test_a_sync_that_drops_most_files_says_so(vault: Path) -> None:
    the_plan, _ = _planned(vault)
    assert dm._drop_warning("grok-personal", the_plan) == []
    files = len(the_plan.files)
    the_plan.left_out = max(dm.DROP_WARN_MIN, files + 1)
    (note,) = dm._drop_warning("grok-personal", the_plan)
    assert note.startswith(f"warning: {the_plan.left_out} of the {files + the_plan.left_out} file(s)")
    the_plan.left_out = dm.DROP_WARN_MIN - 1
    the_plan.files = the_plan.files[:1]
    assert dm._drop_warning("grok-personal", the_plan) == []  # a few, even of a small mirror: no warning


def test_the_manifest_must_be_the_exact_bytes_a_sync_writes(vault: Path) -> None:
    the_plan, _ = _planned(vault)
    good = the_plan.manifest()
    paths = {f.path for f in the_plan.files}
    assert dm._manifest_ok(good, "grok-personal", paths)
    dup = good.replace(b'  "delegate": "grok-personal"', b'  "delegate": "Sam Lee",\n  "delegate": "grok-personal"', 1)
    assert dup != good and json.loads(dup)["delegate"] == "grok-personal"
    assert not dm._manifest_ok(dup, "grok-personal", paths)
    assert not dm._manifest_ok(json.dumps(json.loads(good)).encode(), "grok-personal", paths)  # not canonical
