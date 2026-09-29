"""The new full parse is plan 1's parse, only cheaper (1b spec §3.4).

Every synthetic case must give exactly what the frozen plan 1 parser gives. The pass must
also skip decoding user rows unless they can still change a fact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scout.sessions import transcript as tr
from tests.unit.sessions_reference_parse import reference_parse
from tests.unit.sessions_transcript_cases import (
    HOME,
    REPO,
    call,
    cases,
    jsonl,
    prompt,
    result,
    say,
    without_timestamp,
)


@pytest.mark.parametrize("spaced", [False, True], ids=["compact", "spaced"])
@pytest.mark.parametrize("name", sorted(cases(spaced=False)))
def test_full_parse_matches_the_plan_1_parser(tmp_path: Path, name: str, spaced: bool) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(cases(spaced=spaced)[name])
    assert tr.parse_transcript(p, home=HOME) == reference_parse(p, home=HOME)


def test_user_rows_are_decoded_only_when_they_can_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, Any]] = [prompt("go", 0)]
    for n in range(1, 21):
        rows += [call(f"t{n}", "Read", 2 * n, file_path=f"{REPO}/f{n}.py"), result(f"t{n}", 2 * n + 1)]
    rows += [say("done", 50), prompt("thanks", 51)]
    p = tmp_path / "s.jsonl"
    p.write_bytes(jsonl(*rows))
    decoded: list[str] = []
    real = tr._decode

    def counting(line: bytes) -> dict[str, Any] | None:
        obj = real(line)
        decoded.append(str((obj or {}).get("type")))
        return obj

    monkeypatch.setattr(tr, "_decode", counting)
    info = tr.parse_transcript(p, home=HOME)
    assert decoded == ["assistant"] * 21 + ["user"]  # tool results before the last assistant row are never decoded
    assert info == reference_parse(p, home=HOME)


def test_an_assistant_row_without_a_timestamp_takes_the_last_user_row_timestamp(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(
        jsonl(
            prompt("go", 0),
            call("t1", "Read", 1, file_path=f"{REPO}/a.py"),
            result("t1", 2),
            without_timestamp(say("done", 3)),
        )
    )
    assert tr.parse_transcript(p, home=HOME).last_turn.at == "2026-09-08T10:00:02Z"
