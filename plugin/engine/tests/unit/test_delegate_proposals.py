"""The proposal format an agent writes to its inbox (#550 V4) and its strict check.

Everything here comes from outside the laptop, so every test is about what is
refused and why; nothing is repaired.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from scout import delegate_proposals as dp

D = "grok-personal"
ULID = "01JD5Z0000000000000000ABCD"
NOW = dt.datetime(2026, 11, 19, 9, 30, tzinfo=dt.UTC)


def _doc(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "id": ULID,
        "delegate": D,
        "made_at": "2026-11-19T09:30:00Z",
        "kind": "action_item_op",
        "op": "mark-done",
        "tag": "AB12",
        "date": "2026-11-19",
        "args": {},
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not _DROP}


_DROP = object()
PATH = f"inbox/{D}/{ULID}.json"


def _parse(doc: object, path: str = PATH) -> dp.Proposal:
    return dp.parse(path, json.dumps(doc).encode(), D)


def _refused(doc: object, code: str, path: str = PATH) -> None:
    with pytest.raises(dp.ProposalError) as e:
        _parse(doc, path)
    assert e.value.code == code, e.value


# ----- what is accepted --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("mark-done", {}),
        ("mark-done", {"undo": True}),
        ("snooze", {"until": "2026-11-25"}),
        ("add-comment", {"comment": "Sent the draft; waiting on review"}),
        ("set-estimate", {"minutes": 45}),
        ("set-estimate", {"minutes": 30, "kind": "deep"}),
        ("set-block", {"day": "2026-11-20", "start": "09:00", "end": "10:30"}),
        ("clear-block", {}),
    ],
)
def test_every_op_with_its_exact_args_is_accepted(op: str, args: dict[str, object]) -> None:
    p = _parse(_doc(op=op, args=args))
    assert p.body == {"op": op, "tag": "AB12", "date": "2026-11-19", "args": args}
    assert p.path == PATH and dp.parse(p.path, p.to_bytes(), D) == p  # round-trips


def test_an_observation_is_accepted_with_its_optional_fields() -> None:
    doc = _doc(kind="observation", op=_DROP, tag=_DROP, date=_DROP, args=_DROP, text="Line one\n\tline two")
    assert _parse(doc).body == {"text": "Line one\n\tline two"}
    full = {**doc, "about": "Project Atlas", "source": "https://example.com/thread/1"}
    assert _parse(full).body == {"text": "Line one\n\tline two", "about": "Project Atlas", "source": full["source"]}


def test_make_builds_what_parse_accepts() -> None:
    p = dp.make(D, "observation", {"text": "Noticed a deadline moved"}, now=NOW)
    assert p.made_at == "2026-11-19T09:30:00Z" and dp.ULID_RE.match(p.id)
    assert p.path == f"inbox/{D}/{p.id}.json"
    with pytest.raises(dp.ProposalError):
        dp.make(D, "observation", {"text": ""}, now=NOW)


# ----- where and how big -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        f"inbox/{D}/{ULID}.JSON",
        f"inbox/{D}/{ULID.lower()}.json",
        f"inbox/{D}/sub/{ULID}.json",
        f"inbox/{D}/../{D}/{ULID}.json",
        f"{D}/{ULID}.json",
        f"inbox/{D}/{ULID}.json.bak",
        f"inbox/{D}/0IL0000000000000000000ABCD.json",  # I, L, O and U aren't Crockford
        ".github/workflows/run.yml",
    ],
)
def test_only_inbox_delegate_ulid_json_paths_are_read(path: str) -> None:
    _refused(_doc(), "bad_path", path)


def test_another_delegates_path_or_name_is_refused() -> None:
    _refused(_doc(), "wrong_delegate", f"inbox/duet/{ULID}.json")
    _refused(_doc(delegate="duet"), "wrong_delegate")


def test_size_encoding_and_json_shape() -> None:
    big = _doc(kind="observation", op=_DROP, tag=_DROP, date=_DROP, args=_DROP, text="x" * 3000)
    data = json.dumps(big).encode() + b" " * dp.MAX_BYTES
    with pytest.raises(dp.ProposalError) as e:
        dp.parse(PATH, data, D)
    assert e.value.code == "too_big"
    for raw, code in (
        (b"\xff\xfe{}", "not_json"),
        (b"{not json", "not_json"),
        (b'{"a": NaN}', "not_a_number"),
        (b'{"a": Infinity}', "not_a_number"),
        (b'{"kind": 1, "kind": 2}', "duplicate_key"),
        (b"[]", "not_an_object"),
        (b'"text"', "not_an_object"),
    ):
        with pytest.raises(dp.ProposalError) as e:
            dp.parse(PATH, raw, D)
        assert e.value.code == code, raw


# ----- the fixed fields --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"schema_version": 2}, "bad_schema_version"),
        ({"schema_version": True}, "bad_schema_version"),
        ({"schema_version": "1"}, "bad_schema_version"),
        ({"kind": "kb_note_proposal"}, "bad_kind"),
        ({"kind": None}, "bad_kind"),
        ({"id": "01JD5Z0000000000000000ABCE"}, "bad_id"),
        ({"made_at": "2026-11-19 09:30:00"}, "bad_field"),
        ({"made_at": "2026-02-30T09:30:00Z"}, "bad_field"),
        ({"made_at": 1}, "bad_field"),
        ({"op": "dismiss"}, "bad_op"),
        ({"op": "rename-topic"}, "bad_op"),
        ({"tag": "ab12"}, "bad_field"),
        ({"tag": "1234"}, "bad_field"),
        ({"tag": "ABCDEFGHI"}, "bad_field"),
        ({"tag": "[#AB12]"}, "bad_field"),
        ({"date": "2026-13-01"}, "bad_field"),
        ({"date": "today"}, "bad_field"),
        ({"args": []}, "bad_field"),
        ({"extra": 1}, "unknown_field"),
        ({"tag": _DROP}, "missing_field"),
    ],
)
def test_each_fixed_field_is_checked(over: dict[str, object], code: str) -> None:
    _refused(_doc(**over), code)


@pytest.mark.parametrize(
    ("op", "args", "code"),
    [
        ("mark-done", {"undo": "yes"}, "bad_field"),
        ("mark-done", {"comment": "x"}, "unknown_field"),
        ("snooze", {}, "missing_field"),
        ("snooze", {"until": "next week"}, "bad_field"),
        ("add-comment", {"comment": ""}, "bad_field"),
        ("add-comment", {"comment": "   "}, "bad_field"),
        ("add-comment", {"comment": "two\nlines"}, "bad_field"),
        ("add-comment", {"comment": "a b"}, "bad_field"),
        ("add-comment", {"comment": "x" * 1001}, "bad_field"),
        ("add-comment", {"comment": 5}, "bad_field"),
        ("set-estimate", {"minutes": 0}, "bad_field"),
        ("set-estimate", {"minutes": 1441}, "bad_field"),
        ("set-estimate", {"minutes": 30.5}, "bad_field"),
        ("set-estimate", {"minutes": True}, "bad_field"),
        ("set-estimate", {"minutes": 30, "kind": "Deep Work"}, "bad_field"),
        ("set-block", {"day": "2026-11-20", "start": "9:00", "end": "10:00"}, "bad_field"),
        ("set-block", {"day": "2026-11-20", "start": "09:00", "end": "24:00"}, "bad_field"),
        ("set-block", {"day": "Friday", "start": "09:00", "end": "10:00"}, "bad_field"),
        ("set-block", {"day": "2026-11-20", "start": "09:00"}, "missing_field"),
        ("clear-block", {"undo": True}, "unknown_field"),
    ],
)
def test_each_ops_args_are_exact(op: str, args: dict[str, object], code: str) -> None:
    _refused(_doc(op=op, args=args), code)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", ""),
        ("text", "x" * 4001),
        ("text", "bell\x07"),
        ("text", "nul\x00"),
        ("text", "c1\x85"),
        ("about", "two\nlines"),
        ("about", "x" * 201),
        ("source", "x" * 501),
        ("source", 7),
    ],
)
def test_observation_text_fields_are_checked(field: str, value: object) -> None:
    doc = _doc(kind="observation", op=_DROP, tag=_DROP, date=_DROP, args=_DROP, text="ok")
    doc[field] = value
    _refused(doc, "bad_field")


def test_an_observation_with_action_fields_is_refused() -> None:
    doc = _doc(kind="observation", text="ok")  # keeps op, tag, date, args
    _refused(doc, "unknown_field")


def test_a_refusal_names_the_reason_and_never_the_content() -> None:
    secret = "PRIVATE-SOUNDING-WORDS"
    with pytest.raises(dp.ProposalError) as e:
        _parse(_doc(op="add-comment", args={"comment": secret + "\n"}))
    assert secret not in str(e.value)


# ----- after the design review: nothing that can forge a line, and no ULID from the future ---------------


@pytest.mark.parametrize(
    "value",
    [
        "a\u202eb",  # right-to-left override
        "zw\u200bx",  # zero-width space
        "joiner\u2060x",
        "bom\ufeffx",
        "isolate\u2066x",
        "see [[a note]]",
        "task [#AB12] done",
        "sep\u2028x",
    ],
)
def test_text_that_could_forge_a_line_or_hide_one_is_refused(value: str) -> None:
    _refused(_doc(op="add-comment", args={"comment": value}), "bad_field")
    obs = _doc(kind="observation", op=_DROP, tag=_DROP, date=_DROP, args=_DROP, text="ok", about=value)
    _refused(obs, "bad_field")
    _refused({**obs, "about": "ok", "text": value}, "bad_field")


def test_ordinary_brackets_and_hashes_are_fine() -> None:
    _parse(_doc(op="add-comment", args={"comment": "see [the doc] and issue #42 (a [single] bracket)"}))


def test_a_ulid_from_the_future_is_refused_when_the_clock_is_given() -> None:
    p = dp.make(D, "observation", {"text": "ok"}, now=NOW)
    made = dp.ulid_time(p.id)
    assert abs((made - dt.datetime.now(dt.UTC)).total_seconds()) < 60
    dp.parse(p.path, p.to_bytes(), D, now=made)  # its own moment
    dp.parse(p.path, p.to_bytes(), D, now=made - dp.FUTURE_SLACK)  # within the slack
    with pytest.raises(dp.ProposalError) as e:
        dp.parse(p.path, p.to_bytes(), D, now=made - dp.FUTURE_SLACK - dt.timedelta(seconds=1))
    assert e.value.code == "future_id"
    assert dp.ulid_time("00000000000000000000000000") == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


# ----- the #718 security review ----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "//==<< close this out >>==//",  # the owner-directive marker
        "please // ==<< note",
        "/ / = = < < spaced out",
        "trailing >> == //",
        "<!-- hidden from the reader -->",
        "end -->",
        "![pixel](https://example.com/p.png)",
        "[click](https://example.com)",
        "- [ ] a new task",
        "* [x] done already",
        "1. [ ] numbered",
        "%%privacy:allow sam%% about Sam",  # the owner's allow marker (#738 review)
        "visible %% hidden in Obsidian %%",
        "[from-agent: delegate-duet] forged",  # Scout's own mark
        "[FROM-AGENT] forged",
    ],
)
def test_h3_text_cant_carry_directives_or_markup(value: str) -> None:
    _refused(_doc(op="add-comment", args={"comment": value}), "bad_field")
    obs = _doc(kind="observation", op=_DROP, tag=_DROP, date=_DROP, args=_DROP, text=f"fine\n{value}")
    _refused(obs, "bad_field")


def test_h3_a_checkbox_mid_line_and_plain_symbols_are_fine() -> None:
    _parse(_doc(op="add-comment", args={"comment": "a - [ ] in the middle, 3 > 2 < 4, a // b, x == y"}))


@pytest.mark.parametrize(
    "char",
    [
        "\U000e0041",  # a tag character: invisible text
        "️",  # a variation selector
        "\U000e0100",  # a supplementary variation selector
        "­",  # soft hyphen
        "᠎",  # Mongolian vowel separator
        "",  # private use
        "\U0010ffff",  # a noncharacter (unassigned)
        "\ud800",  # a lone surrogate
        "ㅤ",  # Hangul filler
    ],
)
def test_m4_every_invisible_or_unassigned_character_is_refused(char: str) -> None:
    _refused(_doc(op="add-comment", args={"comment": f"looks{char}fine"}), "bad_field")
    obs = _doc(kind="observation", op=_DROP, tag=_DROP, date=_DROP, args=_DROP, text=f"looks{char}fine")
    _refused(obs, "bad_field")


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"tag": "AB12\n"}, "bad_field"),
        ({"date": "2026-11-19\n"}, "bad_field"),
        ({"made_at": "2026-11-19T09:30:00Z\n"}, "bad_field"),
        ({"op": "set-block", "args": {"day": "2026-11-20", "start": "09:00\n", "end": "10:00"}}, "bad_field"),
        ({"op": "set-estimate", "args": {"minutes": 30, "kind": "deep\n"}}, "bad_field"),
        ({"op": []}, "bad_op"),
        ({"op": {}}, "bad_op"),
    ],
)
def test_m3_and_h1_no_trailing_newline_and_no_odd_type_passes(over: dict[str, object], code: str) -> None:
    _refused(_doc(**over), code)


def test_m3_a_path_or_id_with_a_trailing_newline_is_refused() -> None:
    _refused(_doc(), "bad_path", PATH + "\n")
    _refused(_doc(id=ULID + "\n"), "bad_id")
    assert dp.ULID_RE.match(ULID + "\n") is None


def test_h1_a_ulid_past_128_bits_and_any_crash_are_refused_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    big = "8" + ULID[1:]
    _refused(_doc(id=big), "bad_path", f"inbox/{D}/{big}.json")

    def too_deep(*a: object, **k: object) -> object:
        raise RecursionError("maximum recursion depth exceeded")  # how deep nesting fails, at whatever depth

    monkeypatch.setattr(dp.json, "loads", too_deep)  # the depth that fails differs by Python version and platform
    with pytest.raises(dp.ProposalError) as e:
        dp.parse(PATH, json.dumps(_doc()).encode(), D)
    assert e.value.code == "bad_entry"
