"""The scheduled runs' write-sandbox profile, its model, the switch and the statuses (spec 2026-11-15, Phase 1c).

Platform-independent: nothing here runs sandbox-exec (tests/integration/test_run_sandbox_kernel.py does, on macOS).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scout import linear_activity
from scout.kb import hook_paths
from scout.sandbox import launch
from scout.sandbox import profile as sbx


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path.resolve()
    home, vault = root / "home", root / "vault"
    (home / ".claude").mkdir(parents=True)
    vault.mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "SCOUT_SECRETS_DIR", "GIT_DIR")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    env.pop("GIT_CONFIG_GLOBAL", None)
    env.pop(launch.ENV_SWITCH, None)
    hook_paths._ROOTS_CACHE.clear()
    return home, vault, env


def _rules(vault: Path, env: dict[str, str]) -> list[sbx.Rule]:
    return sbx.build_rules(vault, env)


# -- one list --------------------------------------------------------------------------------------------------


def test_protected_roots_are_the_specs_normalised(world) -> None:
    _, vault, env = world
    specs = hook_paths.protected_specs(vault, env)
    roots = hook_paths._build_roots(vault, env)
    expected = {
        (key, s.form, s.tree) for s in specs for key in (hook_paths.canon(s.path, env), hook_paths.spelled(s.path, env))
    }
    assert {(r.key, r.form, r.tree) for r in roots} == expected


def test_every_protected_spec_is_denied_by_the_profile(world) -> None:
    _, vault, env = world
    rules = _rules(vault, env)
    carved = [p for _, p in sbx.carve_outs(vault, env)]
    for spec in hook_paths.protected_specs(vault, env):
        real = os.path.realpath(spec.path)
        if real.startswith("/dev/"):
            continue
        if any(sbx._under(real, os.path.realpath(c)) for c in carved):
            continue  # a carve-out: pinned below
        assert sbx.decide(rules, real) == "deny", spec
        if spec.tree:
            assert sbx.decide(rules, real + "/anything/below") == "deny", spec


def test_the_switch_file_and_the_pointer_venv_are_protected_in_both_layers(world) -> None:
    home, vault, env = world
    venv = home / "venvs" / "engine"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    state = home / ".local" / "state" / "scout"
    state.mkdir(parents=True)
    (state / "engine.json").write_text(json.dumps({"python": str(venv / "bin" / "python")}))
    hook_paths._ROOTS_CACHE.clear()
    switch = state / hook_paths.SANDBOX_SWITCH_NAME
    assert hook_paths.protected_write(switch, vault, env) == hook_paths.OWNER_STATE
    assert hook_paths.protected_write(venv / "lib" / "x.py", vault, env) == hook_paths.ENGINE
    rules = _rules(vault, env)
    assert sbx.decide(rules, str(switch)) == "deny"
    assert sbx.decide(rules, str(venv / "lib" / "x.py")) == "deny"


def test_a_pointer_naming_something_other_than_a_venv_adds_nothing(world) -> None:
    home, vault, env = world
    state = home / ".local" / "state" / "scout"
    state.mkdir(parents=True)
    (state / "engine.json").write_text(json.dumps({"python": "/usr/bin/true"}))
    assert hook_paths._pointer_venv(state / "engine.json") is None


# -- carve-outs --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "objects/ab/cdef",
        "objects/pack/tmp_pack_x",
        "refs/heads/main",
        "refs/stash",
        "logs/HEAD",
        "index",
        "index.lock",
        "HEAD",
        "HEAD.lock",
        "ORIG_HEAD",
        "FETCH_HEAD",
        "AUTO_MERGE",
        "COMMIT_EDITMSG",
        "packed-refs.lock",
        "lfs/objects/ab/cd/abcd",
        "lfs/tmp/x",
        "gc.pid",
        "next-index-57253.lock",
        "index.stash.4242",
        "index.stash.4242.lock",
    ],
)
def test_git_writes_stay_allowed(world, rel: str) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), str(vault / ".git" / rel)) == "allow"


@pytest.mark.parametrize(
    "rel",
    [
        "config",
        "config.lock",
        "CONFIG",
        "config.worktree",
        "hooks/pre-commit",
        "hooks",
        "info/attributes",
        "info/exclude",
        "commondir",
        "objects/info/alternates",
        "description",
        "modules/x/config",
        "worktrees/w/gitdir",
        "something-git-adds-later",
        "next-index-x.lock",
        "hooks/next-index-1.lock",
        # Not carved out (review of #642: no run used them).
        "rebase-merge/git-rebase-todo",
        "rebase-apply/patch",
        "sequencer/todo",
        "BISECT_LOG",
    ],
)
def test_git_switches_stay_denied(world, rel: str) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), str(vault / ".git" / rel)) == "deny"


def test_a_hooks_path_inside_a_carve_out_is_denied_again(world) -> None:
    _, vault, env = world
    subprocess.run(["git", "-C", str(vault), "config", "core.hooksPath", ".git/refs/hooks"], check=True)
    hook_paths._ROOTS_CACHE.clear()
    rules = _rules(vault, env)
    assert sbx.decide(rules, str(vault / ".git" / "refs" / "hooks" / "pre-commit")) == "deny"
    assert sbx.decide(rules, str(vault / ".git" / "refs" / "heads" / "main")) == "allow"


def test_every_carve_out_is_under_a_protected_root(world) -> None:
    _, vault, env = world
    roots = [os.path.realpath(s.path) for s in hook_paths.protected_specs(vault, env) if s.tree]
    roots += [os.path.realpath(p) for p in sbx.kernel_trees(vault, env)]
    for kind, path in sbx.carve_outs(vault, env):
        if kind == "regex":
            path = path.removeprefix("^").split("/next-index")[0].split("/index\\.stash")[0].replace("\\", "")
        assert any(sbx._under(os.path.realpath(path), r) for r in roots), path


def test_plugin_data_is_carved_out_and_the_rest_of_the_store_is_not(world) -> None:
    home, vault, env = world
    rules = _rules(vault, env)
    plugins = home / ".claude" / "plugins"
    assert sbx.decide(rules, str(plugins / "data" / "some-plugin" / "state.json")) == "allow"
    for rel in ("installed_plugins.json", "known_marketplaces.json", "cache/scout/hooks/hooks.json", "config.json"):
        assert sbx.decide(rules, str(plugins / rel)) == "deny", rel


# -- what runs and claude write -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "notes/a.md",
        "knowledge-base/x.md",
        ".scout-logs/run.log",
        ".scout-state/x.json",
        ".scout-cache/c.json",
        "scripts/my-own-script.py",
    ],
)
def test_run_work_in_the_vault_is_allowed(world, rel: str) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), str(vault / rel)) == "allow"


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/projects/-x/1.jsonl",
        ".claude/todos/t.json",
        ".claude/shell-snapshots/s",
        ".claude/session-env/e",
        ".config/gh/hosts.yml",
        ".local/state/scout/self-update.json",
    ],
)
def test_claude_session_files_are_allowed(world, rel: str) -> None:
    home, vault, env = world
    assert sbx.decide(_rules(vault, env), str(home / rel)) == "allow"


@pytest.mark.parametrize(
    "rel",
    [
        ".claude.json",
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".gitconfig",
        ".config/git/config",
        ".zshrc",
        "Library/LaunchAgents/com.x.plist",
        ".codex/config.toml",
        ".local/state/scout/engine.json",
    ],
)
def test_home_switches_are_denied(world, rel: str) -> None:
    home, vault, env = world
    assert sbx.decide(_rules(vault, env), str(home / rel)) == "deny"


def test_runner_scripts_and_vault_settings_are_denied(world) -> None:
    _, vault, env = world
    rules = _rules(vault, env)
    for rel in (
        *hook_paths.RUNNER_FILES,
        ".claude/settings.local.json",
        ".claude/hooks/x.sh",
        ".mcp.json",
        ".gitattributes",
    ):
        assert sbx.decide(rules, str(vault / rel)) == "deny", rel


@pytest.mark.parametrize(
    "rel", [".claude/skills/x/SKILL.md", ".claude/worktrees/x", ".claude/anything.json", ".claude/agents/a.md"]
)
def test_the_vaults_claude_folder_is_denied_as_a_whole(world, rel: str) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), str(vault / rel)) == "deny"


@pytest.mark.parametrize("rel", ["CLAUDE.md", "agents/a.md", "skills/s/SKILL.md", "something-claude-adds-later"])
def test_the_rest_of_claudes_home_is_denied(world, rel: str) -> None:
    home, vault, env = world
    assert sbx.decide(_rules(vault, env), str(home / ".claude" / rel)) == "deny"


def test_only_the_listed_claude_home_folders_are_carved_out(world) -> None:
    home, vault, env = world
    rules = _rules(vault, env)
    for name in sbx.CLAUDE_HOME_CARVE_DIRS:
        assert sbx.decide(rules, str(home / ".claude" / name / "f")) == "allow", name


@pytest.mark.parametrize(
    "path",
    [
        "/private/tmp/clone/.git/hooks/pre-commit",
        "/private/tmp/clone/.git/config",
        "/Users/someone/dev/repo/.claude/settings.json",
        "/Users/someone/dev/repo/.claude/settings.local.json",
        "/Users/someone/dev/repo/.mcp.json",
        "/Users/someone/dev/repo/.GIT/CONFIG",
    ],
)
def test_any_repos_hooks_config_and_agent_settings_are_denied(world, path: str) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), path) == "deny"


@pytest.mark.parametrize(
    "path",
    ["/private/tmp/clone/.git/objects/ab/cd", "/private/tmp/clone/notes.md", "/private/tmp/clone/.git/configs/x"],
)
def test_other_repos_ordinary_files_stay_writable(world, path: str) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), path) == "allow"


def test_the_suffix_rules_use_no_character_classes() -> None:
    assert not any("[" in rx for rx in sbx.ANYWHERE_SUFFIXES)


def test_ssh_is_denied_except_known_hosts(world) -> None:
    home, vault, env = world
    rules = _rules(vault, env)
    ssh = home / ".ssh"
    assert hook_paths.protected_write(ssh / "config", vault, env) == hook_paths.SSH_CONFIG
    for rel in ("config", "id_ed25519", "authorized_keys", "rc"):
        assert sbx.decide(rules, str(ssh / rel)) == "deny", rel
    for rel in sbx.SSH_WRITABLE:
        assert sbx.decide(rules, str(ssh / rel)) == "allow", rel


def test_local_bin_is_protected_in_both_layers(world) -> None:
    home, vault, env = world
    (home / ".local" / "bin").mkdir(parents=True)
    env["PATH"] = "/usr/bin:/bin"  # not on PATH: protected anyway
    hook_paths._ROOTS_CACHE.clear()
    assert hook_paths.protected_write(home / ".local" / "bin" / "date", vault, env) == hook_paths.PROGRAMS
    assert sbx.decide(_rules(vault, env), str(home / ".local" / "bin" / "date")) == "deny"


def test_the_object_info_folder_cant_be_removed(world) -> None:
    _, vault, env = world
    rules = _rules(vault, env)
    assert sbx.decide(rules, str(vault / ".git" / "objects" / "info"), "unlink") == "deny"
    assert sbx.decide(rules, str(vault / ".git" / "objects" / "ab"), "unlink") == "allow"


def test_matching_is_case_insensitive_like_apfs(world) -> None:
    home, vault, env = world
    rules = _rules(vault, env)
    assert sbx.decide(rules, str(vault / ".GIT" / "Hooks" / "pre-commit")) == "deny"
    assert sbx.decide(rules, str(home / ".claude" / "Settings.Local.json")) == "deny"


def test_ancestors_cant_be_renamed_or_removed(world) -> None:
    home, vault, env = world
    rules = _rules(vault, env)
    for d in (vault, home, home / ".claude", vault / ".git"):
        assert sbx.decide(rules, str(d), "unlink") == "deny", d
    assert sbx.decide(rules, str(vault), "write") == "allow"  # chmod/utimes on the folder, new files in it
    assert sbx.decide(rules, str(vault / "notes"), "unlink") == "allow"


def test_dev_null_as_git_config_global_is_never_denied(world) -> None:
    _, vault, env = world
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    hook_paths._ROOTS_CACHE.clear()
    rules = _rules(vault, env)
    assert not any(r.path.startswith("/dev") for r in rules)
    assert sbx.decide(rules, "/dev/null") == "allow"


# -- secrets (#636's review; Q8, Jordan 2026-11-15) -----------------------------------------------------------


def test_secrets_are_unreadable_except_the_telegram_wrap_files(world) -> None:
    home, vault, env = world
    rules = _rules(vault, env)
    secrets = home / ".scout-secrets"
    for rel in ("delegate-mirrors/state.key", "linear-api-key", "agnes-knowledge-repo/x.token"):
        assert sbx.decide(rules, str(secrets / rel), "read") == "deny", rel
        assert sbx.decide(rules, str(secrets / rel), "write") == "deny", rel
    for name in sbx.SECRETS_READABLE:
        assert sbx.decide(rules, str(secrets / name), "read") == "allow"
        assert sbx.decide(rules, str(secrets / name), "write") == "deny"
    assert sbx.decide(rules, str(secrets), "read") == "allow"  # listing: names only
    assert sbx.decide(rules, str(secrets), "unlink") == "deny"


def test_secrets_dir_override_is_covered_and_matches_the_engine_helper(world, monkeypatch) -> None:
    home, vault, env = world
    other = home / "elsewhere" / "secrets"
    env["SCOUT_SECRETS_DIR"] = str(other)
    monkeypatch.setenv("SCOUT_SECRETS_DIR", str(other))
    assert linear_activity.secrets_dir() in sbx.secrets_dirs(env)
    rules = _rules(vault, env)
    assert sbx.decide(rules, str(other / "linear-api-key"), "read") == "deny"
    assert sbx.decide(rules, str(home / ".scout-secrets" / "linear-api-key"), "read") == "deny"


# -- rendering -----------------------------------------------------------------------------------------------------


def test_render_keeps_order_and_groups_runs(world) -> None:
    _, vault, env = world
    text = sbx.build_profile(vault, env)
    assert text.startswith("(version 1)\n")
    first_deny = text.index("(deny file-write*")
    first_allow = text.index("(allow file-write*")
    assert text.index("(allow default)") < first_deny < first_allow
    assert "(deny job-creation)" in text and "(deny file-mount)" in text and '"localhost:22"' in text
    assert text.count("(allow default)") == 1


def test_quotes_and_backslashes_are_escaped() -> None:
    rule = sbx.Rule("deny", "file-write*", "literal", '/a/"b"\\c')
    assert '(literal "/a/\\"b\\"\\\\c")' in sbx.render([rule])


@pytest.mark.parametrize("bad", ["/a/b\nc", "/a/\x00b", "relative/path"])
def test_a_path_sbpl_cant_carry_fails_the_build(bad: str) -> None:
    with pytest.raises(sbx.ProfileError):
        sbx._check_path(bad)


def test_an_empty_protected_list_fails_the_build(world, monkeypatch) -> None:
    _, vault, env = world
    monkeypatch.setattr(hook_paths, "protected_specs", lambda *_a, **_k: [])
    with pytest.raises(sbx.ProfileError):
        sbx.build_rules(vault, env)


def test_a_list_that_raises_fails_the_build(world, monkeypatch) -> None:
    _, vault, env = world

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(hook_paths, "protected_specs", boom)
    with pytest.raises(sbx.ProfileError, match="no"):
        sbx.build_rules(vault, env)


# -- the switch and the statuses ----------------------------------------------------------------------------------


def test_switch_defaults_on_and_env_wins(world) -> None:
    _, _, env = world
    assert launch.switch(env) == (True, "default")
    assert launch.switch({**env, "SCOUT_RUN_SANDBOX": "off"})[0] is False
    launch.set_switch(False, env)
    assert launch.switch(env)[0] is False
    assert launch.switch({**env, "SCOUT_RUN_SANDBOX": "on"})[0] is True
    launch.set_switch(True, env)
    assert launch.switch(env)[0] is True


def test_a_malformed_switch_file_counts_as_on(world) -> None:
    _, _, env = world
    path = launch.switch_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert launch.switch(env)[0] is True
    path.write_text(json.dumps({"scheduled_runs": "no"}))  # only a real false turns it off
    assert launch.switch(env)[0] is True


def _prepare(vault: Path, env: dict[str, str], **kw) -> launch.Prepared:
    kw.setdefault("available", lambda: True)
    kw.setdefault("self_test", lambda _p: None)
    return launch.prepare(vault, env, **kw)


def test_status_enforced(world) -> None:
    _, vault, env = world
    result = _prepare(vault, env)
    assert (result.status, result.exit_code) == ("enforced", 0)
    assert "(deny file-write*" in result.profile


def test_status_off(world) -> None:
    _, vault, env = world
    result = _prepare(vault, {**env, "SCOUT_RUN_SANDBOX": "off"})
    assert (result.status, result.exit_code, result.profile) == ("off", 7, "")


def test_status_unavailable(world) -> None:
    _, vault, env = world
    result = _prepare(vault, env, available=lambda: False, platform="linux")
    assert (result.status, result.exit_code) == ("unavailable", 6)
    assert "linux" in result.reason


def test_status_conflict_with_claude_codes_own_sandbox(world) -> None:
    home, vault, env = world
    (home / ".claude" / "settings.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    assert _prepare(vault, env).status == "conflict"
    (vault / ".claude").mkdir()
    (vault / ".claude" / "settings.local.json").write_text(json.dumps({"sandbox": {"enabled": False}}))
    assert _prepare(vault, env).status == "enforced"  # the project's local setting overrides the user's


def test_status_failed_on_a_bad_list_or_self_test(world, monkeypatch) -> None:
    _, vault, env = world
    result = _prepare(vault, env, self_test=lambda _p: "nope")
    assert (result.status, result.exit_code, result.profile) == ("failed", 3, "")
    monkeypatch.setattr(hook_paths, "protected_specs", lambda *_a, **_k: [])
    assert _prepare(vault, env).status == "failed"


def test_the_kernel_denies_runs_the_agents_inbox_copies(tmp_path: Path) -> None:
    """#550 V4b1: what agents proposed is untrusted text a run never reads (and never writes)."""
    from scout.sandbox import profile

    vault = tmp_path / "vault"
    (vault / ".scout-inbox" / "agents").mkdir(parents=True)
    rules = profile.build_rules(vault, {"HOME": str(tmp_path / "home")})
    for op in ("file-read-data", "file-write*"):
        assert any(r.action == "deny" and r.op == op and r.path.endswith("/.scout-inbox/agents") for r in rules), op


# -- HOME and the account's home -------------------------------------------------------------------------------


@pytest.fixture
def account(world, monkeypatch, _owner_store_follows_home) -> Path:
    """The account database names a home other than the run's HOME (a custom launch, a harness, ``sudo -E``)."""
    import pwd
    import types

    home, _, _ = world
    folder = home.parent / "account"
    folder.mkdir()
    monkeypatch.setattr(hook_paths, "account_home", _owner_store_follows_home)  # the real lookup
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: types.SimpleNamespace(pw_dir=str(folder)))
    hook_paths._ROOTS_CACHE.clear()
    return folder


def test_homes_is_home_then_the_accounts_when_they_differ(world, account) -> None:
    home, _, env = world
    assert hook_paths.homes(env) == [home, account]
    assert hook_paths.homes({**env, "HOME": str(account)}) == [account]
    link = account.parent / "home-link"
    link.symlink_to(account)
    assert hook_paths.homes({**env, "HOME": str(link)}) == [link]  # the same folder: one home


def test_a_home_spelled_in_another_case_is_the_same_home_on_a_case_insensitive_disk(
    world, account, monkeypatch
) -> None:
    """``HOME=/users/x`` for an account home ``/Users/x`` is one folder on APFS: one home, and the account home
    adds no rule to what ``$HOME`` alone gives (no per-user path written twice, once per spelling)."""
    _, vault, env = world
    variant = {**env, "HOME": str(account).swapcase()}
    if not os.path.isdir(variant["HOME"]):
        pytest.skip("case-sensitive disk: another case is another folder")
    assert hook_paths.homes(variant) == [Path(variant["HOME"])]
    with_account = set(_rules(vault, variant))
    monkeypatch.setattr(hook_paths, "account_home", lambda: hook_paths.NO_ACCOUNT_HOME)
    hook_paths._ROOTS_CACHE.clear()
    assert with_account == set(_rules(vault, variant))


def test_a_missing_home_is_compared_case_folded(world, account, monkeypatch) -> None:
    """When a home doesn't exist (nothing to stat), the two are compared as the roots are: real path, case-folded."""
    _, _, env = world
    gone = account.parent / "Gone"
    monkeypatch.setattr(hook_paths, "account_home", lambda: gone)
    assert hook_paths.homes({**env, "HOME": str(gone).swapcase()}) == [Path(str(gone).swapcase())]
    assert hook_paths.homes({**env, "HOME": str(account)}) == [account, gone]


def test_the_roots_cache_follows_the_account_home(world, account, monkeypatch) -> None:
    """The account's home isn't in the environment, so it is part of the roots cache's key: one process asked
    again after it changes (a test that patches it) gets the new home's roots, not the old ones."""
    _, vault, env = world
    store = hook_paths.CHANGES_OWNER_NAME
    first = account / ".local" / "state" / "scout" / store / "run-commits.jsonl"
    assert hook_paths.protected_write(first, vault, env) == hook_paths.OWNER_STATE
    other = account.parent / "other-account"
    other.mkdir()
    monkeypatch.setattr(hook_paths, "account_home", lambda: other)
    second = other / ".local" / "state" / "scout" / store / "run-commits.jsonl"
    assert hook_paths.protected_write(second, vault, env) == hook_paths.OWNER_STATE
    assert hook_paths.protected_write(first, vault, env) is None  # no longer a home of this process


def test_homes_ignores_an_account_with_no_home(world, monkeypatch, _owner_store_follows_home) -> None:
    import pwd

    home, _, env = world
    monkeypatch.setattr(hook_paths, "account_home", _owner_store_follows_home)

    def missing(_uid):
        raise KeyError("no such user")

    monkeypatch.setattr(pwd, "getpwuid", missing)
    assert hook_paths.account_home() == hook_paths.NO_ACCOUNT_HOME
    assert hook_paths.homes(env) == [home]


@pytest.mark.parametrize("name", hook_paths.OWNER_STATE_NAMES)
def test_an_owner_store_under_the_account_home_is_denied_when_home_is_elsewhere(world, account, name) -> None:
    """A run whose HOME is a temp dir can't write the owner stores under the account's home, from either layer,
    and the copies under its HOME stay protected too."""
    home, vault, env = world
    rules = _rules(vault, env)
    for base in (account, home):
        target = base / ".local" / "state" / "scout" / name
        for path in [target, *([] if target.suffix else [target / "run-commits.jsonl"])]:  # a folder: all of it
            assert hook_paths.protected_write(path, vault, env) == hook_paths.OWNER_STATE, path
            assert sbx.decide(rules, str(path)) == "deny", path


def test_the_changes_owner_store_the_engine_writes_is_the_one_denied(world, account) -> None:
    """The store scout.changes.store reads and writes (the account's home, whatever HOME says) is in the deny list
    of a run whose HOME is a temp dir: a run can't add rows to the run-commit ledger or the engagement rows."""
    from scout.changes import store

    home, vault, env = world
    owner = store.owner_dir(env)
    assert owner == account / ".local" / "state" / "scout" / hook_paths.CHANGES_OWNER_NAME
    assert not owner.is_relative_to(home)
    rules = _rules(vault, env)
    for rel in ("run-commits.jsonl", "engagement.jsonl", "settings.json"):
        assert hook_paths.protected_write(owner / rel, vault, env) == hook_paths.OWNER_STATE, rel
        assert sbx.decide(rules, str(owner / rel)) == "deny", rel


def test_the_lab_veto_store_is_denied_under_both_homes(world, account) -> None:
    """The run brokers' owner folder (its limits, counts and the Lab's vetoes) is protected as a tree."""
    home, vault, env = world
    rules = _rules(vault, env)
    for base in (account, home):
        vetoes = base / ".local" / "state" / "scout" / hook_paths.RUN_BROKERS_NAME / "lab-vetoes.jsonl"
        assert hook_paths.protected_write(vetoes, vault, env) == hook_paths.OWNER_STATE
        assert sbx.decide(rules, str(vetoes)) == "deny"


@pytest.mark.parametrize(
    "rel",
    [
        ".gitconfig",
        ".config/git/config",
        ".zshrc",
        "Library/LaunchAgents/x.plist",
        ".claude/settings.json",
        ".claude/CLAUDE.md",
        ".claude.json",
        ".codex/config.toml",
        ".scout-secrets/linear-api-key",
        ".ssh/config",
        ".local/state/scout/engine.json",
    ],
)
def test_every_per_user_path_is_denied_under_the_account_home_too(world, account, rel) -> None:
    _, vault, env = world
    assert sbx.decide(_rules(vault, env), str(account / rel)) == "deny", rel


def test_the_account_homes_claude_and_ssh_carve_outs_stay_writable(world, account) -> None:
    """ssh adds to the account's known_hosts whatever HOME says, and claude may keep its sessions there."""
    _, vault, env = world
    rules = _rules(vault, env)
    for rel in sbx.SSH_WRITABLE:
        assert sbx.decide(rules, str(account / ".ssh" / rel)) == "allow", rel
    for name in sbx.CLAUDE_HOME_CARVE_DIRS:
        assert sbx.decide(rules, str(account / ".claude" / name / "x")) == "allow", name
    assert sbx.decide(rules, str(account / "Documents" / "x.txt")) == "allow"


def test_secrets_under_the_account_home_are_unreadable(world, account) -> None:
    _, vault, env = world
    secret = account / ".scout-secrets" / "linear-api-key"
    assert sbx.decide(_rules(vault, env), str(secret), "read") == "deny"
    assert hook_paths.secret_read(secret, env) == hook_paths.SECRETS
