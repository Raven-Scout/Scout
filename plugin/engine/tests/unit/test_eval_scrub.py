"""The eval fixture never names a real person, org, host or tracker (spec 2026-10-12 §5.3).

The check is an allowlist: a denylist would have to hold the real names it
protects, and this repo is public.
"""

from __future__ import annotations

from pathlib import Path

from tests.eval.harness.scrub import EVAL_ROOT, Finding, load_allowlist, scan, scan_fixture


def _write(root: Path, name: str, text: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _found(findings: list[Finding]) -> set[tuple[str, str]]:
    return {(f.token, f.kind) for f in findings}


def test_committed_fixture_scans_clean() -> None:
    findings = scan_fixture()
    assert findings == [], "\n".join(f"{f.path}:{f.line}: {f.kind} {f.token!r}" for f in findings)


def test_allowlist_holds_the_shared_stand_ins() -> None:
    allow = load_allowlist(EVAL_ROOT / "allowlist.txt")
    assert {"Alex", "Priya", "Sam", "example-org", "example.com", "acme-co.slack.com", "PROJ"} <= allow


def test_unknown_person_and_email_domain_are_reported(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", "Contact Morgan at morgan@realcorp.com\n")
    assert _found(scan(tmp_path, {"Contact"})) == {("Morgan", "word"), ("realcorp.com", "email")}


def test_unknown_url_host_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "b.md", "see https://realcorp.atlassian.net/x and https://example.com/ok\n")
    assert _found(scan(tmp_path, {"example.com"})) == {("realcorp.atlassian.net", "host")}


def test_unknown_tracker_prefix_and_repo_owner_are_reported(tmp_path: Path) -> None:
    _write(tmp_path, "c.md", "- Refs: ACME-12 · PROJ-1 · bigco/payments#3 · example-org/widgets#4\n")
    allow = {"Refs", "PROJ", "example-org"}
    assert _found(scan(tmp_path, allow)) == {("ACME", "id"), ("bigco", "repo")}


def test_unknown_handle_is_reported_and_known_one_is_not(tmp_path: Path) -> None:
    _write(tmp_path, "d.md", "ping @morgan and @alex\n")
    assert _found(scan(tmp_path, {"alex"})) == {("morgan", "handle")}


def test_non_ascii_names_are_reported(tmp_path: Path) -> None:
    _write(tmp_path, "f.md", "met Ondřej, Łukasz, José and Zoë\n")
    assert _found(scan(tmp_path, set())) == {(n, "word") for n in ("Ondřej", "Łukasz", "José", "Zoë")}


def test_bare_repo_in_refs_or_backticks_is_reported_but_wikilinks_are_not(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "g.md",
        "- Refs: bigco/payments · example-org/api\n"
        "- Repo: `megacorp/billing` and see [[projects/widgets/widgets]] and `action-items/context/AB.md`\n",
    )
    found = _found(scan(tmp_path, {"Refs", "Repo", "example-org", "AB"}))
    assert found == {("bigco", "repo"), ("megacorp", "repo")}


def test_file_paths_are_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "people/Morgan-Realcorp.md", "ok\n")
    found = scan(tmp_path, set())
    assert {(f.token, f.kind, f.line) for f in found} == {("Morgan", "path", 0), ("Realcorp", "path", 0)}


def test_files_of_any_suffix_are_scanned_and_binary_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "data.csv", "name\nMorgan\n")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00bad")
    assert _found(scan(tmp_path, {"name"})) == {("Morgan", "word"), ("blob.bin", "binary")}


def test_findings_carry_path_and_line(tmp_path: Path) -> None:
    _write(tmp_path, "sub/e.md", "ok\nMorgan\n")
    [f] = scan(tmp_path, set())
    assert (f.path, f.line) == ("sub/e.md", 2)
