"""kb lint's identity-note rule (layout v2, spec 2026-11-26 §3.2): facts never go in an identity note."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scout.kb.lint import lint_staged
from scout.kb.lint_config import load_lint_config


def _repo(tmp_path: Path) -> Path:
    v = tmp_path / "v"
    (v / "scout" / "brain").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("{}\n")
    subprocess.run(["git", "-C", str(v), "init", "-q"], check=True)
    return v


def _stage(v: Path, rel: str, text: str) -> list:
    p = v / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    subprocess.run(["git", "-C", str(v), "add", rel], check=True)
    return [f for f in lint_staged(v, load_lint_config(v)).findings if f.check == "identity-note"]


IDENTITY = (
    "---\nname: Alex\ntype: person\nemail: a@example.com\nspaces: [work]\n---\n\n"
    "# Alex\n\n- Work: [[work/people/alex-work]]\n"
)


def test_a_clean_identity_note_passes(tmp_path: Path) -> None:
    assert _stage(_repo(tmp_path), "knowledge-base/people/alex.md", IDENTITY) == []


def test_a_fact_in_an_identity_note_is_refused(tmp_path: Path) -> None:
    text = IDENTITY.replace("spaces: [work]", "role: Engineer\nspaces: [work]")
    (finding,) = _stage(_repo(tmp_path), "knowledge-base/people/alex.md", text)
    assert finding.blocking and "role" in finding.message


def test_body_text_in_an_identity_note_is_refused(tmp_path: Path) -> None:
    findings = _stage(_repo(tmp_path), "knowledge-base/people/alex.md", IDENTITY + "\nWorks on the gateway.\n")
    assert findings and findings[0].blocking


def test_a_note_the_move_did_not_split_is_free_form(tmp_path: Path) -> None:
    team = "---\nname: Team\ntype: team\nlead: Alex\n---\n\n# Team\n\nNotes.\n"
    assert _stage(_repo(tmp_path), "knowledge-base/people/team.md", team) == []


def test_a_quoted_type_is_still_an_identity_note(tmp_path: Path) -> None:
    text = IDENTITY.replace("type: person", 'type: "person"').replace(
        "spaces: [work]", "role: Engineer\nspaces: [work]"
    )
    (finding,) = _stage(_repo(tmp_path), "knowledge-base/people/alex.md", text)
    assert finding.blocking and "role" in finding.message
