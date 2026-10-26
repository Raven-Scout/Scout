"""The labelled ``scoutctl kb`` read commands: the single read gate (#453, spec §6.1, §7.4).

Over the privacy fixture vault (spec §11.1): ``owner`` and ``autonomous`` get every
file, each result labelled and, in text form, private ones prefixed ``[private] ``;
an ``agent:*`` reader gets nothing at all (no canary, no path), and ``kb read``
denies it with exit 3. Also: the stat-keyed privacy index, ``kb search`` with and
without ``rg``, binary and non-UTF-8 files, and the audit log.
"""

from __future__ import annotations

import builtins
import datetime
import io
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner, Result

from scout.cli import app
from scout.kb import read as kb_read
from scout.kb.ontology import KnowledgeGraph, clear_frontmatter_cache
from scout.kb.privacy import Label
from scout.kb.read_log import LOG_FIELDS, log_event
from tests.unit.privacy_vault_helpers import CANARIES, build_privacy_vault

runner = CliRunner()
AGENT = "agent:person:priya@example.com"
FULL = ["owner", "autonomous"]
SAM = "knowledge-base/people/sam.md"
PERMIT = "knowledge-base/personal/task-renew-permit.md"
GIFT = "knowledge-base/personal/task-buy-gift.md"

# Every read command, with arguments that reach private notes on the fixture.
COMMANDS: list[list[str]] = [
    ["files"],
    ["files", "--glob", "knowledge-base/**"],
    ["search", "CANARY-PRIV"],
    ["search", "canary-priv-2s4m", "-i"],
    ["search", "CANARY-PRIV-2S4M", "--fixed", "--glob", "knowledge-base/**"],
    ["read", SAM],
    ["resolve", "sam"],
    ["resolve", "Sammy"],
    ["query"],
    ["query", "--type", "task", "--domain", "personal"],
    ["query", "--visibility", "private"],
    ["related", "priya"],
    ["traverse", "priya"],
    ["path", "priya", "sam"],
    ["entity", "sam"],
    ["name-lookup", "Sam"],
    ["export"],
    ["stats"],
    ["validate"],
]


@pytest.fixture(scope="module")
def shared_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_privacy_vault(tmp_path_factory.mktemp("kb-read"))


@pytest.fixture
def vault(shared_vault: Path) -> Path:
    clear_frontmatter_cache()
    return shared_vault


@pytest.fixture
def own_vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    return build_privacy_vault(tmp_path)


def kb(vault: Path, *args: str, reader: str | None = None, env: dict[str, str] | None = None) -> Result:
    full_env = dict(env or {})
    if reader is not None:
        full_env["SCOUT_READER"] = reader
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=full_env)


def kb_json(vault: Path, *args: str, reader: str | None = None) -> Any:
    result = kb(vault, *args, "--json", reader=reader)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _settle(vault: Path) -> None:
    """Backdate every file past the index's racy window."""
    old = time.time() - 86_400
    for p in vault.rglob("*"):
        if p.is_file() and not p.is_symlink():
            os.utime(p, (old, old))


def _labels_by_path(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["label"]["path"]: r["label"] for r in rows}


# ----- owner and autonomous: everything, labelled ----------------------------------


@pytest.mark.parametrize("reader", FULL)
def test_owner_and_autonomous_see_private_labelled(vault: Path, reader: str) -> None:
    # files: every vault file but .git/, labelled; no double listing through knowledge_base/
    files = kb_json(vault, "files", reader=reader)
    paths = {r["path"] for r in files}
    assert all(r["path"] == r["label"]["path"] for r in files)
    assert set(CANARIES) <= paths
    assert not any(p.startswith((".git/", "knowledge_base/")) for p in paths)
    labels = _labels_by_path(files)
    assert labels[SAM]["visibility"] == "private"
    assert labels[PERMIT]["visibility_source"] == "path_default"
    assert labels[GIFT]["visibility"] == "normal"
    assert labels["inbox.md"]["visibility"] == "normal"
    assert labels["trading/positions.md"]["local_only"] is True
    text = kb(vault, "files", reader=reader).stdout.splitlines()
    assert f"[private] {SAM}" in text and "inbox.md" in text
    globbed = {r["path"] for r in kb_json(vault, "files", "--glob", "knowledge-base/people/*.md", reader=reader)}
    assert globbed == {SAM, "knowledge-base/people/priya.md"}

    # search: matches carry their file's label
    hits = kb_json(vault, "search", "CANARY-PRIV", reader=reader)
    assert {h["path"] for h in hits} >= {SAM, PERMIT}
    assert all(h["label"]["visibility"] == "private" for h in hits if h["path"] == SAM)
    assert any(CANARIES[SAM] in h["text"] for h in hits)
    text = kb(vault, "search", CANARIES[SAM], "--fixed", reader=reader).stdout.splitlines()
    assert text and all(line.startswith(f"[private] {SAM}:") for line in text)

    # read
    got = kb_json(vault, "read", SAM, reader=reader)
    assert got["label"]["visibility"] == "private" and CANARIES[SAM] in got["content"]
    text = kb(vault, "read", "knowledge_base/people/sam.md", reader=reader).stdout
    assert text.splitlines()[0] == f"[private] {SAM}" and CANARIES[SAM] in text
    plain = kb(vault, "read", "inbox.md", reader=reader).stdout
    assert plain == (vault / "inbox.md").read_text(encoding="utf-8")

    # resolve: name, alias, slug of a plain note
    for name in ("sam", "Sammy", "SAM"):
        (row,) = kb_json(vault, "resolve", name, reader=reader)
        assert row["path"] == SAM and row["label"]["visibility"] == "private", name
    (row,) = kb_json(vault, "resolve", "billing", reader=reader)
    assert row["path"] == "knowledge-base/topics/billing/billing.md" and row["label"]["visibility"] == "private"
    assert kb(vault, "resolve", "sam", reader=reader).stdout.splitlines() == [f"[private] {SAM}"]
    assert kb(vault, "resolve", "no-such-note", reader=reader).exit_code == 1

    # query
    tasks = kb_json(vault, "query", "--type", "task", "--domain", "personal", reader=reader)
    by_name = {t["name"]: t for t in tasks}
    assert set(by_name) == {"task-renew-permit", "task-buy-gift"}
    assert by_name["task-renew-permit"]["label"]["visibility"] == "private"
    assert by_name["task-renew-permit"]["path"] == PERMIT
    assert by_name["task-renew-permit"]["entity"]["domain"] == "personal"
    assert by_name["task-buy-gift"]["label"]["visibility"] == "normal"
    text = kb(vault, "query", "--type", "task", "--domain", "personal", reader=reader).stdout
    assert "  [private] [task] task-renew-permit" in text and "  [task] task-buy-gift" in text
    private = kb_json(vault, "query", "--visibility", "private", reader=reader)
    assert {p["name"] for p in private} == {"task-renew-permit", "sam"}

    # related / traverse / path: each node labelled
    rel = kb_json(vault, "related", "priya", reader=reader)
    sam_rel = next(r for r in rel if r["target"] == "sam")
    assert sam_rel["label"]["visibility"] == "private"
    assert "  [private] works_with → sam" in kb(vault, "related", "priya", reader=reader).stdout
    trav = kb_json(vault, "traverse", "priya", reader=reader)
    assert next(t for t in trav if t["entity"] == "sam")["label"]["visibility"] == "private"
    assert "[private] [1 hop] sam (via works_with)" in kb(vault, "traverse", "priya", reader=reader).stdout
    path = kb_json(vault, "path", "priya", "sam", reader=reader)
    assert path["steps"][-1]["node"] == "sam" and path["steps"][-1]["label"]["visibility"] == "private"
    assert "priya --works_with--> [private] sam" in kb(vault, "path", "priya", "sam", reader=reader).stdout

    # the parser-compat commands
    ent = json.loads(kb(vault, "entity", "sam", reader=reader).stdout)
    assert ent["_label"]["visibility"] == "private"
    (cand, *_) = kb_json(vault, "name-lookup", "Sam", reader=reader)
    assert cand["name"] == "sam" and cand["label"]["visibility"] == "private"
    assert "[private] [exact] sam" in kb(vault, "name-lookup", "Sam", reader=reader).stdout
    export = json.loads(kb(vault, "export", reader=reader).stdout)
    assert export["entities"]["sam"]["_label"]["visibility"] == "private"
    assert "Entities:" in kb(vault, "stats", reader=reader).stdout
    assert kb(vault, "validate", reader=reader).exit_code == 0


# ----- agents: nothing ----------------------------------------------------------


def _assert_nothing(out: str) -> None:
    for canary in CANARIES.values():
        assert canary not in out
    assert "knowledge-base/" not in out and ".md" not in out


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("args", COMMANDS, ids=lambda a: " ".join(a))
def test_agent_sees_nothing(vault: Path, args: list[str], as_json: bool) -> None:
    result = kb(vault, *args, *(["--json"] if as_json else []), reader=AGENT)
    _assert_nothing(result.output)
    if args[0] == "read":
        assert result.exit_code == 3
        assert json.loads(result.stdout) == {"denied": True, "reason": f"not visible to {AGENT}"}


def test_agent_read_denial_is_the_same_for_missing_files(vault: Path) -> None:
    there = kb(vault, "read", SAM, reader=AGENT)
    missing = kb(vault, "read", "knowledge-base/people/nobody.md", reader=AGENT)
    assert (there.exit_code, there.stdout) == (missing.exit_code, missing.stdout)


def test_agent_opens_no_vault_file(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No command opens, lists or stats a vault file for an agent (the audit log aside)."""
    opened: list[str] = []
    root = str(vault.resolve())
    real_open, real_io, real_scandir = builtins.open, io.open, os.scandir

    def note(target: Any) -> None:
        name = os.fspath(target) if isinstance(target, (str, os.PathLike)) else ""
        if isinstance(name, str) and os.path.abspath(name).startswith(root) and ".scout-logs" not in name:
            opened.append(name)

    def b_open(file: Any, *a: Any, **k: Any) -> Any:
        note(file)
        return real_open(file, *a, **k)

    def i_open(file: Any, *a: Any, **k: Any) -> Any:
        note(file)
        return real_io(file, *a, **k)

    def scandir(path: Any = ".") -> Any:
        note(path)
        return real_scandir(path)

    monkeypatch.setattr(builtins, "open", b_open)
    monkeypatch.setattr(io, "open", i_open)
    monkeypatch.setattr(os, "scandir", scandir)
    for args in COMMANDS:
        kb(vault, *args, reader=AGENT)
    monkeypatch.undo()
    assert opened == []


def test_agent_with_federation_overlay_still_sees_nothing(own_vault: Path) -> None:
    config = own_vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "  federation:\n    enabled: true\n", encoding="utf-8")
    for args in COMMANDS:
        _assert_nothing(kb(own_vault, *args, "--json", reader=AGENT).output)
    # widget.md declares federation_scope: org; it's still nothing for an agent
    assert kb(own_vault, "read", "knowledge-base/projects/widget/widget.md", reader=AGENT).exit_code == 3


def test_reader_flag_only_narrows(vault: Path) -> None:
    assert kb(vault, "files", "--reader", "owner", reader="autonomous").exit_code == 2
    result = kb(vault, "files", "--reader", AGENT)
    assert result.exit_code == 0
    _assert_nothing(result.output)


# ----- binary and non-UTF-8 files (Review Focus 2) --------------------------------


def _odd_files(vault: Path) -> None:
    misc = vault / "knowledge-base" / "topics" / "misc"
    (misc / "latin.md").write_bytes(b"---\nvisibility: private\n---\ncaf\xe9 needle here\n\xff\xfe needle again\n")
    (misc / "blob.bin").write_bytes(b"\x00\x01\x02needle\x00\xff" * 50)
    (misc / "bad-front.md").write_bytes(b"---\nname: \xff\xfe\ntype: topic\n---\nneedle body\n")


@pytest.mark.parametrize("use_rg", [False, True])
def test_binary_and_non_utf8_files(own_vault: Path, monkeypatch: pytest.MonkeyPatch, use_rg: bool) -> None:
    if use_rg and kb_read.find_rg() is None:
        pytest.skip("rg not installed")
    if not use_rg:
        monkeypatch.setattr(kb_read, "find_rg", lambda: None)
    _odd_files(own_vault)
    labels = _labels_by_path(kb_json(own_vault, "files"))
    assert labels["knowledge-base/topics/misc/latin.md"]["visibility"] == "private"
    assert labels["knowledge-base/topics/misc/blob.bin"]["visibility"] == "normal"
    assert labels["knowledge-base/topics/misc/bad-front.md"]["visibility"] in ("normal", "private")

    hits = kb_json(own_vault, "search", "needle", "--glob", "knowledge-base/**")
    latin = [h for h in hits if h["path"] == "knowledge-base/topics/misc/latin.md"]
    assert [h["line"] for h in latin] == [4, 5]
    assert all(h["label"]["visibility"] == "private" for h in latin)
    assert all(isinstance(h["text"], str) for h in hits)
    assert kb(own_vault, "search", "needle").exit_code == 0

    got = kb_json(own_vault, "read", "knowledge-base/topics/misc/latin.md")
    assert "needle here" in got["content"] and "�" in got["content"]
    assert kb(own_vault, "read", "knowledge-base/topics/misc/blob.bin").exit_code == 0
    assert kb(own_vault, "query").exit_code == 0


# ----- search -------------------------------------------------------------------


def test_search_without_rg(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb_read, "find_rg", lambda: None)
    hits = kb_read.search(vault, "owner", "CANARY-PRIV-[0-9]S", ())
    assert {(m.path, m.visibility) for m in hits} == {(SAM, "private")}
    assert all(m.label.path == m.path for m in hits)
    fixed = kb_read.search(vault, "owner", "canary-priv-2s4m", (), fixed=True, ignore_case=True)
    assert {m.path for m in fixed} == {SAM}
    assert kb_read.search(vault, "owner", "[a-", (), fixed=True) == []
    assert kb_read.search(vault, AGENT, "CANARY", ()) == []
    assert kb(vault, "search", "[unclosed").exit_code == 2


def test_search_batches_paths(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``rg`` gets the visible paths in batches, never the vault root."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_k: Any) -> Any:
        calls.append(argv)

        class R:
            returncode = 1
            stdout = b""
            stderr = b""

        return R()

    monkeypatch.setattr(kb_read, "find_rg", lambda: "/usr/bin/rg")
    monkeypatch.setattr(kb_read, "SEARCH_BATCH", 4)
    monkeypatch.setattr(kb_read.subprocess, "run", fake_run)
    kb_read.search(vault, "owner", "x", ())
    n_files = len(kb_read.visible_files(vault, "owner"))
    assert len(calls) == -(-n_files // 4)
    for argv in calls:
        paths = argv[argv.index("--") + 1 :]
        assert 0 < len(paths) <= 4 and "." not in paths and str(vault) not in paths


def test_rg_json_output_is_parsed_safely(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text and base64 ``bytes`` fields (bad UTF-8) both decode; a path outside the batch is dropped."""
    import base64

    def ev(path: dict[str, str], line: int, lines: dict[str, str]) -> bytes:
        return json.dumps({"type": "match", "data": {"path": path, "line_number": line, "lines": lines}}).encode()

    out = b"\n".join(
        [
            json.dumps({"type": "begin", "data": {}}).encode(),
            ev({"text": SAM}, 13, {"text": f"# Sam {CANARIES[SAM]}\n"}),
            ev(
                {"bytes": base64.b64encode(b"inbox.md").decode()},
                2,
                {"bytes": base64.b64encode(b"caf\xe9\r\n").decode()},
            ),
            ev({"text": "not/visible.md"}, 1, {"text": "x\n"}),
            b"not json",
        ]
    )

    class R:
        returncode = 0
        stdout = out
        stderr = b""

    monkeypatch.setattr(kb_read, "find_rg", lambda: "/usr/bin/rg")
    monkeypatch.setattr(kb_read.subprocess, "run", lambda *_a, **_k: R())
    hits = kb_read.search(vault, "owner", "x", ("knowledge-base/people/*.md", "inbox.md"))
    assert [(m.path, m.line, m.text, m.visibility) for m in hits] == [
        ("inbox.md", 2, "caf�", "normal"),
        (SAM, 13, f"# Sam {CANARIES[SAM]}", "private"),
    ]

    class Bad:
        returncode = 2
        stdout = b""
        stderr = b"rg: regex parse error:\n    [a-\n    ^\nerror: unclosed character class"

    monkeypatch.setattr(kb_read.subprocess, "run", lambda *_a, **_k: Bad())
    with pytest.raises(kb_read.PatternError):
        kb_read.search(vault, "owner", "[a-", ())


@pytest.mark.skipif(kb_read.find_rg() is None, reason="rg not installed")
def test_rg_and_fallback_agree(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with_rg = kb_read.search(vault, "owner", "CANARY-PRIV", ())
    monkeypatch.setattr(kb_read, "find_rg", lambda: None)
    assert sorted(kb_read.search(vault, "owner", "CANARY-PRIV", ())) == sorted(with_rg)


# ----- the privacy index ----------------------------------------------------------


def test_index_makes_a_warm_listing_read_nothing(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _settle(own_vault)
    cold = kb_read.visible_files(own_vault, "owner")
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    assert index.is_file()
    opened: list[str] = []
    real_open = Path.open

    def spy(self: Path, *a: Any, **k: Any) -> Any:
        if self.suffix == ".md":
            opened.append(str(self))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", spy)
    warm = kb_read.visible_files(own_vault, "owner")
    monkeypatch.undo()
    assert opened == []
    assert warm == cold


def test_index_relabels_on_change(own_vault: Path) -> None:
    _settle(own_vault)
    kb_read.visible_files(own_vault, "owner")
    # an edited note relabels (its stat key moved)
    plain = own_vault / "knowledge-base" / "topics" / "misc" / "plain.md"
    plain.write_text("---\nvisibility: private\n---\nNow private.\n", encoding="utf-8")
    assert dict(kb_read.visible_files(own_vault, "owner"))["knowledge-base/topics/misc/plain.md"].visibility == (
        "private"
    )
    # a policy change relabels files whose stat key didn't move
    config = own_vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "  visibility_defaults:\n    - {glob: 'knowledge-base/projects/**', visibility: private}\n",
        encoding="utf-8",
    )
    labels = dict(kb_read.visible_files(own_vault, "owner"))
    assert labels["knowledge-base/projects/widget/notes.md"].visibility == "private"


def test_index_skips_fresh_files(own_vault: Path) -> None:
    _settle(own_vault)
    fresh = own_vault / "knowledge-base" / "fresh.md"
    fresh.write_text("hello\n", encoding="utf-8")
    kb_read.visible_files(own_vault, "owner")
    data = json.loads((own_vault / ".scout-cache" / kb_read.INDEX_FILENAME).read_text(encoding="utf-8"))
    assert "knowledge-base/fresh.md" not in data["files"]
    assert SAM in data["files"]


@pytest.mark.parametrize("junk", ["{torn", '{"version": 999, "files": {}}', '{"version": 1, "files": []}', "[]"])
def test_torn_or_foreign_index_is_rebuilt(own_vault: Path, junk: str) -> None:
    _settle(own_vault)
    want = kb_read.visible_files(own_vault, "owner")
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    index.write_text(junk, encoding="utf-8")
    assert kb_read.visible_files(own_vault, "owner") == want


def test_bad_index_entry_is_recomputed(own_vault: Path) -> None:
    _settle(own_vault)
    want = kb_read.visible_files(own_vault, "owner")
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    data = json.loads(index.read_text(encoding="utf-8"))
    data["files"][SAM]["label"] = {"visibility": "normal"}  # wrong shape: must not be trusted
    index.write_text(json.dumps(data), encoding="utf-8")
    assert dict(kb_read.visible_files(own_vault, "owner"))[SAM].visibility == "private"
    assert kb_read.visible_files(own_vault, "owner") == want


def test_unwritable_index_costs_only_speed(own_vault: Path) -> None:
    shutil.rmtree(own_vault / ".scout-cache", ignore_errors=True)
    (own_vault / ".scout-cache").write_text("not a dir\n", encoding="utf-8")
    labels = dict(kb_read.visible_files(own_vault, "owner"))
    assert labels[SAM].visibility == "private"


def test_directory_symlinks_are_not_followed(own_vault: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("outside the vault\n", encoding="utf-8")
    (own_vault / "linked").symlink_to(outside)
    paths = {p for p, _ in kb_read.visible_files(own_vault, "owner")}
    assert not any(p.startswith(("linked/", "knowledge_base/")) for p in paths)
    assert kb(own_vault, "read", "linked/leak.md").exit_code == 2


def test_visible_files_returns_labels(vault: Path) -> None:
    rows = kb_read.visible_files(vault, "owner", ("knowledge-base/people/*.md",))
    assert [p for p, _ in rows] == sorted(p for p, _ in rows)
    assert all(isinstance(label, Label) and label.path == p for p, label in rows)
    assert kb_read.visible_files(vault, AGENT) == []


# ----- read edge cases --------------------------------------------------------------


@pytest.mark.parametrize("path", ["knowledge-base/people/nobody.md", "../outside.md", ".git/config", "knowledge-base"])
def test_owner_read_refuses_non_files(vault: Path, path: str) -> None:
    assert kb(vault, "read", path).exit_code == 2


# ----- the audit log (spec §7.4) ------------------------------------------------------


def _log(vault: Path) -> list[dict[str, Any]]:
    path = vault / ".scout-logs" / "kb-reads.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_event_no_content(own_vault: Path) -> None:
    kb(own_vault, "search", CANARIES[SAM])  # owner: not logged
    kb(own_vault, "read", SAM)
    assert _log(own_vault) == []

    kb(own_vault, "search", CANARIES[SAM], "--fixed", reader="autonomous")
    kb(own_vault, "read", SAM, reader="autonomous")
    kb(own_vault, "query", "--type", "task", reader="autonomous")
    kb(own_vault, "read", SAM, reader=AGENT)
    kb(own_vault, "related", "sam", reader=AGENT, env={"SCOUT_MODE": "briefing"})
    rows = _log(own_vault)
    assert len(rows) == 5
    raw = (own_vault / ".scout-logs" / "kb-reads.jsonl").read_text(encoding="utf-8")
    for canary in CANARIES.values():
        assert canary not in raw
    assert "sam" not in raw.lower() and "task" not in raw
    for row in rows:
        assert set(row) == set(LOG_FIELDS)
        assert len(row["args_hash"]) == 64
        assert all(isinstance(row[k], int) for k in ("paths_returned", "paths_denied", "private_paths"))
    search, read, query, denied, related = rows
    assert (search["reader"], search["command"], search["blocked"]) == ("autonomous", "search", False)
    assert search["paths_returned"] >= 1 and search["private_paths"] >= 1
    assert (read["paths_returned"], read["private_paths"]) == (1, 1)
    assert query["private_paths"] == 1 and query["paths_returned"] >= 2
    assert (denied["reader"], denied["paths_returned"], denied["paths_denied"]) == (AGENT, 0, 1)
    assert related["scout_mode"] == "briefing" and related["paths_returned"] == 0
    assert search["args_hash"] != read["args_hash"]


def test_log_event_blocked_owner_is_logged(tmp_path: Path) -> None:
    log_event(
        tmp_path,
        reader="owner",
        command="check-outbound",
        surface="slack",
        args=["check-outbound", "draft.md"],
        returned=0,
        denied=0,
        private_paths=1,
        blocked=True,
    )
    log_event(
        tmp_path,
        reader="owner",
        command="read",
        surface=None,
        args=["read"],
        returned=1,
        denied=0,
        private_paths=0,
        blocked=False,
    )
    (row,) = _log(tmp_path)
    assert row["blocked"] is True and row["surface"] == "slack" and row["reader"] == "owner"
    assert row["args_hash"] == kb_read_hash(["check-outbound", "draft.md"])


def kb_read_hash(args: list[str]) -> str:
    import hashlib

    return hashlib.sha256("\x00".join(args).encode("utf-8")).hexdigest()


def test_unwritable_log_never_fails_the_read(own_vault: Path) -> None:
    (own_vault / ".scout-logs").write_text("not a dir\n", encoding="utf-8")
    result = kb(own_vault, "read", SAM, reader="autonomous")
    assert result.exit_code == 0
    assert CANARIES[SAM] in result.stdout
    assert "kb-reads" in result.stderr


# ----- review fixes -------------------------------------------------------------------


def test_index_rebuilds_after_a_classifier_or_engine_change(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Facts an older classifier stored must not keep labelling an unchanged file (fail closed)."""
    _settle(own_vault)
    kb_read.visible_files(own_vault, "owner")
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    data = json.loads(index.read_text(encoding="utf-8"))
    assert data["classifier"] == kb_read.CLASSIFIER_VERSION and data["engine"] == kb_read.__version__
    # an older classifier's verdict for SAM, under its current stat key
    stale = data["files"][SAM]
    stale["facts"] = {"state": "ok", "visibility": "normal", "declared": "none", "warnings": []}
    stale["label"] = {**stale["label"], "visibility": "normal", "visibility_source": "frontmatter"}
    for header, value in (("classifier", kb_read.CLASSIFIER_VERSION - 1), ("engine", "0.0.1")):
        index.write_text(json.dumps({**data, header: value}), encoding="utf-8")
        assert dict(kb_read.visible_files(own_vault, "owner"))[SAM].visibility == "private", header
        rebuilt = json.loads(index.read_text(encoding="utf-8"))
        assert rebuilt["classifier"] == kb_read.CLASSIFIER_VERSION and rebuilt["engine"] == kb_read.__version__
    # the same header trusts the planted entry: the header is what invalidates it
    index.write_text(json.dumps(data), encoding="utf-8")
    assert dict(kb_read.visible_files(own_vault, "owner"))[SAM].visibility == "normal"
    monkeypatch.setattr(kb_read, "CLASSIFIER_VERSION", kb_read.CLASSIFIER_VERSION + 1)
    assert dict(kb_read.visible_files(own_vault, "owner"))[SAM].visibility == "private"


def test_rg_failure_without_matches_is_an_error(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Failed:
        returncode = 2
        stdout = b""
        stderr = b"rg: some other failure\n"

    monkeypatch.setattr(kb_read, "find_rg", lambda: "/usr/bin/rg")
    monkeypatch.setattr(kb_read.subprocess, "run", lambda *_a, **_k: Failed())
    with pytest.raises(kb_read.PatternError):
        kb_read.search(vault, "owner", "x", ())
    result = kb(vault, "search", "x")
    assert result.exit_code == 2 and "some other failure" in result.stderr


def test_rg_drops_matches_in_binary_files(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def ev(kind: str, data: dict[str, Any]) -> bytes:
        return json.dumps({"type": kind, "data": data}).encode()

    out = b"\n".join(
        [
            ev("match", {"path": {"text": "inbox.md"}, "line_number": 1, "lines": {"text": "x\n"}}),
            ev("end", {"path": {"text": "inbox.md"}, "binary_offset": 12}),
            ev("match", {"path": {"text": SAM}, "line_number": 2, "lines": {"text": "x\n"}}),
            ev("end", {"path": {"text": SAM}, "binary_offset": None}),
        ]
    )

    class R:
        returncode = 0
        stdout = out
        stderr = b""

    monkeypatch.setattr(kb_read, "find_rg", lambda: "/usr/bin/rg")
    monkeypatch.setattr(kb_read.subprocess, "run", lambda *_a, **_k: R())
    assert [m.path for m in kb_read.search(vault, "owner", "x", ())] == [SAM]


@pytest.mark.skipif(kb_read.find_rg() is None, reason="rg not installed")
def test_rg_and_fallback_agree_on_odd_files(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _odd_files(own_vault)
    with_rg = kb_read.search(own_vault, "owner", "needle", ("knowledge-base/**",))
    monkeypatch.setattr(kb_read, "find_rg", lambda: None)
    without = kb_read.search(own_vault, "owner", "needle", ("knowledge-base/**",))
    assert [(m.path, m.line, m.text) for m in with_rg] == [(m.path, m.line, m.text) for m in without]
    assert not any(m.path.endswith("blob.bin") for m in with_rg)


# Machine plumbing (local_only.always) with a marker; stand-in content only.
PLUMBING = {
    ".venv/lib/site.py": "venv PLUMB-MARKER\n",
    ".scout-logs/run.jsonl": '{"x": "PLUMB-MARKER"}\n',
    ".claude/settings.json": "{}\n",
    ".env": "STAND_IN=PLUMB-MARKER\n",
    ".mcp.json": "{}\n",
    ".scout-state/other-state.json": "PLUMB-MARKER\n",
    ".scout-cache/session-context.json": "{}\n",
}
CARVED_OUT = ".scout-state/schedule.yaml"


def _plumb(vault: Path) -> None:
    for rel, text in {**PLUMBING, CARVED_OUT: "slots: []\n"}.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_listing_leaves_out_plumbing(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ruling R13: local_only.always (minus sync_allow) isn't listed, searched or walked by default."""
    _plumb(own_vault)
    walked: list[str] = []
    real_scandir = os.scandir

    def scandir(path: Any = ".") -> Any:
        walked.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    paths = {p for p, _ in kb_read.visible_files(own_vault, "owner")}
    monkeypatch.undo()
    assert not set(PLUMBING) & paths
    assert CARVED_OUT in paths  # a sync_allow carve-out
    assert "trading/positions.md" in paths  # overlay local_only_add is vault content
    for d in (".venv", ".scout-logs", ".claude", ".scout-cache"):
        assert not any(w.endswith(os.sep + d) or (os.sep + d + os.sep) in w for w in walked), d
    assert kb_read.search(own_vault, "owner", "PLUMB-MARKER", ()) == []
    # a wildcard-first glob never pulls plumbing in
    assert not set(PLUMBING) & {p for p, _ in kb_read.visible_files(own_vault, "owner", ("**",))}
    # kb read and kb classify still reach it
    assert "PLUMB-MARKER" in kb(own_vault, "read", ".env").stdout
    assert json.loads(kb(own_vault, "classify", ".env", "--json").stdout)[0]["local_only"] is True


@pytest.mark.parametrize(
    ("glob", "want"),
    [
        (".scout-logs/**", {".scout-logs/run.jsonl"}),
        (".env*", {".env"}),
        (".venv/**/*.py", {".venv/lib/site.py"}),
        (".scout-state/*", {".scout-state/other-state.json", CARVED_OUT}),
    ],
)
def test_explicit_glob_reaches_plumbing(own_vault: Path, glob: str, want: set[str]) -> None:
    _plumb(own_vault)
    assert {p for p, _ in kb_read.visible_files(own_vault, "owner", (glob,))} == want
    assert {r["path"] for r in kb_json(own_vault, "files", "--glob", glob)} == want
    # the default listing afterwards is unchanged (the index keeps both)
    assert not set(PLUMBING) & {p for p, _ in kb_read.visible_files(own_vault, "owner")}


def test_index_is_not_rewritten_by_the_audit_log(own_vault: Path) -> None:
    _settle(own_vault)
    kb(own_vault, "files", reader="autonomous")
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    before = index.stat().st_mtime_ns
    for _ in range(2):
        assert kb(own_vault, "files", reader="autonomous").exit_code == 0
    assert (own_vault / ".scout-logs" / "kb-reads.jsonl").is_file()
    assert index.stat().st_mtime_ns == before


def test_failed_calls_are_audited(own_vault: Path) -> None:
    assert kb(own_vault, "search", "[unclosed", reader="autonomous").exit_code == 2
    assert kb(own_vault, "read", "../outside.md", reader="autonomous").exit_code == 2
    assert kb(own_vault, "related", reader="autonomous").exit_code == 2  # missing NAME
    assert kb(own_vault, "query", "--visibility", "bogus", reader="autonomous").exit_code == 2
    assert kb(own_vault, "files", "--reader", "owner", reader=AGENT).exit_code == 2  # widening refused
    assert kb(own_vault, "classify", SAM, reader="autonomous").exit_code == 0
    assert kb(own_vault, "classify", SAM, reader=AGENT).exit_code == 0
    assert kb(own_vault, "policy", reader="autonomous").exit_code == 0
    rows = _log(own_vault)
    assert [r["command"] for r in rows] == [
        "search",
        "read",
        "related",
        "query",
        "files",
        "classify",
        "classify",
        "policy",
    ]
    assert all(r["paths_returned"] == 0 for r in rows[:5])
    assert rows[4]["reader"] == AGENT
    assert (rows[5]["paths_returned"], rows[5]["private_paths"]) == (1, 1)
    assert (rows[6]["paths_returned"], rows[6]["paths_denied"]) == (0, 1)
    raw = (own_vault / ".scout-logs" / "kb-reads.jsonl").read_text(encoding="utf-8")
    assert "unclosed" not in raw and "outside" not in raw and "sam" not in raw.lower()


# ----- the graph's parses in the index (ruling R15c) -----------------------------------


def _graph_json(vault: Path) -> str:
    return kb_read.load_graph(vault, "owner").export_json()


def _no_parse(self: KnowledgeGraph, path: Path) -> None:
    raise AssertionError(f"warm load parsed {path}")


def test_graph_reuses_the_index_across_processes(own_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh process's graph comes from the index: no note parsed, same entities and labels."""
    _settle(own_vault)
    cold = _graph_json(own_vault)
    kb_read.visible_files(own_vault, "owner")  # a listing in between keeps the stored parses
    data = json.loads((own_vault / ".scout-cache" / kb_read.INDEX_FILENAME).read_text(encoding="utf-8"))
    assert data["files"][SAM]["fm"]["name"]

    clear_frontmatter_cache()  # a new process: only the index is warm
    with monkeypatch.context() as m:
        m.setattr(KnowledgeGraph, "_extract_frontmatter", _no_parse)
        assert _graph_json(own_vault) == cold
        # a policy change relabels the stored parses without parsing again
        config = own_vault / "scout-config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8")
            + "  visibility_defaults:\n    - {glob: 'knowledge-base/**', visibility: private}\n",
            encoding="utf-8",
        )
        relabelled = json.loads(_graph_json(own_vault))
    assert relabelled != json.loads(cold)
    sources = {e["_label"]["visibility_source"] for e in relabelled["entities"].values()}
    assert "path_default" in sources and "default" not in sources
    clear_frontmatter_cache()
    (own_vault / ".scout-cache" / kb_read.INDEX_FILENAME).unlink()
    assert json.loads(_graph_json(own_vault)) == relabelled  # the same, parsed from the files


def test_tampered_stored_parse_is_parsed_again(own_vault: Path) -> None:
    _settle(own_vault)
    cold = _graph_json(own_vault)
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    data = json.loads(index.read_text(encoding="utf-8"))
    data["files"][SAM]["fm"] = {"\x00": "bogus", "v": 1}
    index.write_text(json.dumps(data), encoding="utf-8")
    clear_frontmatter_cache()
    assert _graph_json(own_vault) == cold


@pytest.mark.parametrize(
    "value",
    [
        {"name": "x", "type": "topic", "created": datetime.date(2026, 1, 2)},
        {"name": "x", "type": "topic", "at": yaml.safe_load("t: 2026-01-02 03:04:05.5+02:00")["t"]},
        {"name": "x", "type": "topic", "tags": [{None: None}], 1: [True, 1, 1.5, "1"]},
        {"name": "x", "type": "topic", "\x00": "a NUL key", "blob": b"\x00\xff", "s": {1, "a"}, "p": [("k", 2)]},
    ],
)
def test_stored_parse_is_lossless(value: dict[Any, Any]) -> None:
    back = kb_read._dec(json.loads(json.dumps(kb_read._enc(value))))
    assert back == value
    assert [type(v) for v in back.values()] == [type(v) for v in value.values()]


def test_a_parse_without_an_exact_json_form_is_not_stored(own_vault: Path) -> None:
    note = own_vault / "knowledge-base" / "topics" / "misc" / "nan.md"
    note.write_text("---\nname: nan-note\ntype: topic\nscore: .nan\n---\n", encoding="utf-8")
    _settle(own_vault)
    _graph_json(own_vault)
    data = json.loads((own_vault / ".scout-cache" / kb_read.INDEX_FILENAME).read_text(encoding="utf-8"))
    entry = data["files"]["knowledge-base/topics/misc/nan.md"]
    assert "fm" not in entry and entry["label"]["visibility"] == "normal"
    clear_frontmatter_cache()
    assert "nan-note" in kb_read.load_graph(own_vault, "owner").entities


@pytest.mark.parametrize(
    ("alias", "broken"),
    [
        ("x: &r [1, *r]", [1, None]),
        ("x: &r {k: *r, j: 2}", {"k": None, "j": 2}),
        ("x: &r [[*r]]", [[None]]),
    ],
)
def test_cyclic_alias_entity_does_not_crash(own_vault: Path, alias: str, broken: Any) -> None:
    """SafeLoader accepts a cyclic alias: every graph command keeps working, cold and warm.

    The back-reference becomes null in the entity (no JSON form has a cycle).
    """
    note = own_vault / "knowledge-base" / "topics" / "misc" / "cycle.md"
    note.write_text(f"---\nname: cycle-note\ntype: task\n{alias}\n---\n", encoding="utf-8")
    _settle(own_vault)
    for _ in range(2):  # cold, then warm (from the index)
        clear_frontmatter_cache()
        graph = kb_read.load_graph(own_vault, "owner")
        assert graph.entities["cycle-note"]["x"] == broken
        assert graph.label_of("cycle-note")["visibility"] == "normal"
        for args in (["query", "--type", "task"], ["query", "--type", "task", "--json"], ["export"]):
            result = kb(own_vault, *args, reader="owner")
            assert result.exit_code == 0, (args, result.output)
            assert "cycle-note" in result.output
        result = kb(own_vault, "entity", "cycle-note", reader="owner")
        assert result.exit_code == 0, result.output
    data = json.loads((own_vault / ".scout-cache" / kb_read.INDEX_FILENAME).read_text(encoding="utf-8"))
    entry = data["files"]["knowledge-base/topics/misc/cycle.md"]
    assert entry["label"]["visibility"] == "normal"


@pytest.mark.parametrize("alias", ["&r [*r]", "&r {k: *r}", "&r [[1, *r]]"])
def test_a_cyclic_value_is_never_stored(own_vault: Path, alias: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth: the encoder refuses a cycle, and ``put`` then stores no parse."""
    value = yaml.safe_load(f"name: x\ntype: topic\nv: {alias}\n")
    with pytest.raises(kb_read._NotJSON):
        kb_read._enc(value)
    store = kb_read.IndexStore(own_vault, kb_read.load_policy(own_vault))
    old = time.time_ns() - 86_400 * 10**9
    store.put("knowledge-base/x.md", (old, old, 1, 1), own_vault / "knowledge-base" / "x.md", None, value)
    assert "fm" not in store.files["knowledge-base/x.md"]


def test_shared_anchor_round_trips() -> None:
    value = yaml.safe_load("name: x\ntype: topic\na: &s [1, {k: v}]\nb: *s\nc: [*s, *s]\n")
    back = kb_read._dec(json.loads(json.dumps(kb_read._enc(value))))
    assert back == value


def test_index_is_owner_only(own_vault: Path) -> None:
    _settle(own_vault)
    kb_read.load_graph(own_vault, "owner")
    kb_read.visible_files(own_vault, "owner")
    index = own_vault / ".scout-cache" / kb_read.INDEX_FILENAME
    assert index.stat().st_mode & 0o777 == 0o600
