"""`scoutctl notify telegram --parse-mode html` and `--body-file`.

Covers the payload's ``parse_mode``, the tag-aware splitter, the plain-text
fallback on Telegram's 400 "can't parse entities", dry-run output and the CLI
wiring. All HTTP calls are mocked — no live Telegram traffic.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from typer.testing import CliRunner

from scout import cli
from scout.scripts import notify_telegram

FAKE_TOKEN = "FAKE_TOKEN_123:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
FAKE_CHAT_ID = "987654321"
PARSE_ERROR = 'Bad Request: can\'t parse entities: Unsupported start tag "foo" at byte offset 0'


def _write_secret(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o600)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    _write_secret(d / "telegram-bot-token", FAKE_TOKEN)
    _write_secret(d / "telegram-chat-id", FAKE_CHAT_ID)
    monkeypatch.setattr(notify_telegram, "secrets_dir", lambda: d)
    return d


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    resp.raise_for_status = MagicMock()
    return resp


def _bad_request(description: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 400
    resp.reason = "Bad Request"
    resp.json.return_value = {"ok": False, "error_code": 400, "description": description}
    resp.raise_for_status = MagicMock(side_effect=requests.HTTPError("400 Client Error", response=resp))
    return resp


def _payloads(mock_post: MagicMock) -> list[dict[str, object]]:
    return [call.kwargs["json"] for call in mock_post.call_args_list]


# ----- payload ---------------------------------------------------------------


def test_build_payload_omits_parse_mode_by_default() -> None:
    payload = notify_telegram._build_payload(chat_id="1", text="hi", tier="info")
    assert payload == {"chat_id": "1", "text": "hi", "disable_notification": True}


def test_build_payload_html_sets_telegram_parse_mode() -> None:
    payload = notify_telegram._build_payload(chat_id="1", text="<b>hi</b>", tier="info", parse_mode="html")
    assert payload["parse_mode"] == "HTML"


def test_send_default_is_plain_text_and_event_unchanged(secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        ev = notify_telegram.send(tier="info", body="<b>not bold</b>")
    assert "parse_mode" not in _payloads(mock_post)[0]
    assert ev.payload == {"tier": "info", "channel": "telegram", "body_chars": len("<b>not bold</b>")}


def test_send_html_posts_parse_mode_and_records_it(secrets_dir: Path) -> None:
    body = '<b>Scout</b> <a href="https://example.com">link</a>'
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        ev = notify_telegram.send(tier="info", body=body, parse_mode="html")
    [payload] = _payloads(mock_post)
    assert payload["text"] == body
    assert payload["parse_mode"] == "HTML"
    assert ev.payload["parse_mode"] == "html"
    assert "parse_fallback" not in ev.payload


def test_send_rejects_unknown_parse_mode(secrets_dir: Path) -> None:
    with pytest.raises(ValueError, match="unknown parse mode 'markdown'"):
        notify_telegram.send(tier="info", body="x", parse_mode="markdown")


# ----- plain-text fallback ---------------------------------------------------


def test_parse_error_resends_chunk_as_plain_text(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = '<b>Bold</b> &amp; <foo>broken</foo> <a href="https://example.com/?a=1&amp;b=2">site</a>'
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.side_effect = [_bad_request(PARSE_ERROR), _ok_response()]
        ev = notify_telegram.send(tier="info", body=body, parse_mode="html")

    first, second = _payloads(mock_post)
    assert first["parse_mode"] == "HTML"
    assert "parse_mode" not in second
    assert second["text"] == "Bold & broken site (https://example.com/?a=1&b=2)"
    assert ev.payload["parse_fallback"] is True
    err = capsys.readouterr().err
    assert "Telegram refused the HTML of chunk 1/1" in err
    assert "Unsupported start tag" in err
    assert FAKE_TOKEN not in err


def test_fallback_applies_per_chunk(secrets_dir: Path) -> None:
    body = "<b>" + "a" * 3000 + "</b>\n\n<foo>" + "b" * 3000 + "</foo>"
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.side_effect = [_ok_response(), _bad_request(PARSE_ERROR), _ok_response()]
        ev = notify_telegram.send(tier="info", body=body, parse_mode="html")
    texts = [p["text"] for p in _payloads(mock_post)]
    assert texts[0] == "<b>" + "a" * 3000 + "</b>"
    assert texts[2] == "b" * 3000
    assert ev.payload["parse_fallback"] is True


def test_other_400_is_not_retried(secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _bad_request("Bad Request: chat not found")
        with pytest.raises(requests.HTTPError):
            notify_telegram.send(tier="info", body="<b>x</b>", parse_mode="html")
    assert mock_post.call_count == 1


def test_plain_mode_parse_error_is_not_retried(secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _bad_request(PARSE_ERROR)
        with pytest.raises(requests.HTTPError):
            notify_telegram.send(tier="info", body="x")
    assert mock_post.call_count == 1


def test_fallback_failure_still_raises(secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.side_effect = [_bad_request(PARSE_ERROR), _bad_request("Bad Request: chat not found")]
        with pytest.raises(requests.HTTPError):
            notify_telegram.send(tier="info", body="<foo>x</foo>", parse_mode="html")


def test_html_to_plain() -> None:
    to_plain = notify_telegram._html_to_plain
    assert to_plain("<b>a</b> <i>b</i> &lt;c&gt; &amp;") == "a b <c> &"
    assert to_plain('<a href="https://e.com">label</a>') == "label (https://e.com)"
    assert to_plain("<a href='https://e.com'>https://e.com</a>") == "https://e.com"
    assert to_plain('<pre><code class="language-py">x &lt; 1</code></pre>') == "x < 1"


def test_html_to_plain_decodes_only_what_telegram_decodes() -> None:
    to_plain = notify_telegram._html_to_plain
    # A raw `&` in a query string is the likely input when the fallback runs.
    assert to_plain("see https://x.com/?a=1&copy=2&para=3") == "see https://x.com/?a=1&copy=2&para=3"
    assert to_plain("a&nbsp;b &nbsp c &eacute;") == "a&nbsp;b &nbsp c &eacute;"
    assert to_plain("&lt;&gt;&amp;&quot; &#128512;&#x1F600; &#99999999;") == '<>&" 😀😀 &#99999999;'
    assert to_plain('<a href="https://x.com/?a=1&copy=2">l</a>') == "l (https://x.com/?a=1&copy=2)"


# ----- link sanitizing -------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected", "dropped"),
    [
        ('<a href="obsidian://open?vault=x">vault</a>', "vault", 1),
        ('<a href="tg://resolve?domain=x">join</a>', "join", 1),
        ("<a href='javascript:alert(1)'>x</a>", "x", 1),
        ('<a href="java\tscript:alert(1)">x</a>', "x", 1),
        ('<a href="&#106;avascript:alert(1)">x</a>', "x", 1),
        ("<a href=tg://resolve?domain=x>unquoted</a>", "unquoted", 1),
        ('<a href="tg://x"><b>bold</b> label</a>', "<b>bold</b> label", 1),
        ('<a href="HTTPS://example.com">ok</a>', '<a href="HTTPS://example.com">ok</a>', 0),
        ('<a href="mailto:a@example.com">mail</a>', '<a href="mailto:a@example.com">mail</a>', 0),
        ("<a href=https://example.com>ok</a>", "<a href=https://example.com>ok</a>", 0),
        (
            '<a href="https://example.com">ok</a> and <a href="tg://x">bad</a>',
            '<a href="https://example.com">ok</a> and bad',
            1,
        ),
    ],
)
def test_drop_unsafe_links(body: str, expected: str, dropped: int) -> None:
    assert notify_telegram._drop_unsafe_links(body) == (expected, dropped)


def test_html_to_plain_unquoted_href() -> None:
    assert notify_telegram._html_to_plain("<a href=https://e.com>label</a>") == "label (https://e.com)"


def test_send_html_drops_unsafe_links_and_warns(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = '<a href="tg://resolve?domain=x">join</a> <a href="https://example.com">ok</a>'
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        ev = notify_telegram.send(tier="info", body=body, parse_mode="html")
    assert _payloads(mock_post)[0]["text"] == 'join <a href="https://example.com">ok</a>'
    assert "warning: dropped 1 link(s) whose href is not http(s) or mailto" in capsys.readouterr().err
    assert ev.payload["body_chars"] == len(body)


def test_send_plain_mode_keeps_links_as_typed(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = '<a href="tg://x">join</a>'
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        notify_telegram.send(tier="info", body=body)
    assert _payloads(mock_post)[0]["text"] == body
    assert "dropped" not in capsys.readouterr().err


# ----- tag-aware splitting ---------------------------------------------------


def _is_balanced(chunk: str) -> bool:
    stack: list[str] = []
    for closing, name in re.findall(r"<(/?)([a-z]+)[^<>]*>", chunk):
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


def test_split_html_without_tags_matches_plain_splitter() -> None:
    body = ("word " * 900 + "\n\n") * 3
    assert notify_telegram._split_html(body) == notify_telegram._split_message(body)


def test_split_html_skips_a_whitespace_only_chunk() -> None:
    # The one place a tag-free body splits differently: _split_message would
    # send the lone "\n" between the cuts, which Telegram refuses as empty.
    body = "x" * 10 + "\n\n\n" + "y" * 10
    assert notify_telegram._split_message(body, limit=11) == ["x" * 10, "\n", "y" * 10]
    assert notify_telegram._split_html(body, limit=11) == ["x" * 10, "y" * 10]


def test_split_html_closes_and_reopens_open_tags() -> None:
    body = '<blockquote><a href="https://example.com/x">' + "word " * 60 + "</a></blockquote> tail"
    chunks = notify_telegram._split_html(body, limit=150)
    assert len(chunks) > 2
    for chunk in chunks:
        assert len(chunk) <= 150
        assert _is_balanced(chunk), chunk
    assert chunks[1].startswith('<blockquote><a href="https://example.com/x">')
    plain = " ".join(notify_telegram._html_to_plain(c).replace(" (https://example.com/x)", "") for c in chunks)
    assert plain.split() == ["word"] * 60 + ["tail"]


def test_split_html_never_cuts_inside_a_tag_or_entity() -> None:
    body = ("x" * 90) + '<a href="https://example.com/very/long/path">l</a>' + "&amp;" * 40
    chunks = notify_telegram._split_html(body, limit=100)
    for chunk in chunks:
        assert len(chunk) <= 100
        assert not re.search(r"<[^>]*$", chunk), chunk
        assert not re.search(r"&[a-z]*$", chunk), chunk
        assert not re.match(r"^[^<]*>", chunk), chunk
    assert "".join(notify_telegram._html_to_plain(c) for c in chunks).endswith("&" * 40)


def test_split_html_does_not_cut_at_a_space_inside_a_tag() -> None:
    # The space in `<a href` counted as a word boundary. The cut moved back to
    # the `<a`, and the overflow loop then hard-cut inside `<blockquote>`:
    # `…</u><blockquote` | `><i>y</i>…`. Found by fuzzing.
    body = '<u><blockquote><a href="https://example.com/x">x</a></blockquote></u><blockquote><i>y</i><code>z</code></blockquote>'
    chunks = notify_telegram._split_html(body, limit=100)
    for chunk in chunks:
        assert len(chunk) <= 100
        assert not re.search(r"<[^>]*$", chunk), chunk
        assert not re.match(r"^[^<]*>", chunk), chunk
        assert _is_balanced(chunk), chunk


def test_split_html_deep_nesting_stays_within_the_limit() -> None:
    # Four <blockquote>s reopened plus their closers took 101 of 100 chars, and
    # the splitter then sent the text one character (and one entity) at a time.
    body = "<blockquote>" * 4 + "word &amp; " * 30 + "</blockquote>" * 4
    chunks = notify_telegram._split_html(body, limit=100)
    for chunk in chunks:
        assert len(chunk) <= 100
        assert not re.search(r"&[a-z]*$", chunk), chunk
        assert not re.match(r"^[a-z]*;", chunk), chunk
    assert len(chunks) < 20
    assert "".join(notify_telegram._html_to_plain(c) for c in chunks).count("word") == 30


def test_split_html_link_first_chunk_still_cuts_at_a_word() -> None:
    body = '<a href="https://example.com">' + "x" * 40 + " " + "y" * 40 + " " + "z" * 40 + "</a>"
    chunks = notify_telegram._split_html(body, limit=100)
    assert chunks[0] == '<a href="https://example.com">' + "x" * 40 + "</a>"
    assert all(_is_balanced(c) for c in chunks)


_FUZZ_TAGS = ["b", "i", "u", "s", "code", "blockquote", 'a href="https://example.com/?a=1&amp;b=2"']
_FUZZ_TEXT = ["word", "x" * 20, "&amp;", "&lt;", "é", "😀", "&#128512;", "\n", "\n\n", " "]


def _fuzz_body(rng: random.Random, depth: int = 0, in_link: bool = False) -> str:
    parts = []
    for _ in range(rng.randint(1, 6)):
        if depth < 4 and rng.random() < 0.35:
            tag = rng.choice([t for t in _FUZZ_TAGS if not (in_link and t.startswith("a "))])
            name = tag.split()[0]
            inner = _fuzz_body(rng, depth + 1, in_link or name == "a")
            parts.append(f"<{tag}>{inner}</{name}>")
        else:
            parts.append("".join(rng.choice(_FUZZ_TEXT) for _ in range(rng.randint(1, 8))))
    return "".join(parts)


def _visible(html_text: str) -> str:
    return re.sub(r"\s+", "", notify_telegram._unescape_telegram(notify_telegram._TAG_RE.sub("", html_text)))


@pytest.mark.parametrize("limit", [100, 150, 250, 300, 400, 600])
def test_split_html_properties(limit: int) -> None:
    # Random balanced bodies, nested up to 4 deep. Every chunk fits the limit,
    # never starts or ends inside a tag or entity, and no text is lost. From 300
    # up the carried tags never hit _carried's cap, so every chunk must also be
    # balanced on its own; below that the cap may give up on carrying them.
    rng = random.Random(465)
    for _ in range(300):
        body = _fuzz_body(rng)
        chunks = notify_telegram._split_html(body, limit=limit)
        for chunk in chunks:
            assert len(chunk) <= limit, body
            assert not re.search(r"<[^>]*$|&#?[a-zA-Z0-9]*$", chunk), (body, chunk)
            assert not re.match(r"^[^<]*>|^#?[a-zA-Z0-9]+;", chunk), (body, chunk)
            if limit >= 300:
                assert _is_balanced(chunk), (body, chunk)
        assert _visible("".join(chunks)) == _visible(body), body


@pytest.mark.parametrize(
    "body",
    [
        "<b>" + "😀é" * 150 + "</b>",  # hard cut
        "<b>" + "😀é " * 100 + "</b>",  # word cut
        "x" * 95 + "&#128512;" * 20,  # entity at the boundary
    ],
)
def test_split_html_multibyte_at_the_boundary(body: str) -> None:
    chunks = notify_telegram._split_html(body, limit=100)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 100
        assert len(chunk.encode()) <= 4 * 100
        assert not re.search(r"&#?[0-9]*$", chunk), chunk
    assert _visible("".join(chunks)) == _visible(body)


def test_send_html_long_body_sends_balanced_chunks(secrets_dir: Path) -> None:
    body = "<b>" + "bold words " * 1000 + "</b>"
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        notify_telegram.send(tier="info", body=body, parse_mode="html")
    texts = [str(p["text"]) for p in _payloads(mock_post)]
    assert len(texts) == 3
    for text in texts:
        assert len(text) <= notify_telegram.MAX_MESSAGE_LEN
        assert text.startswith("<b>") and text.endswith("</b>")


# ----- dry-run -----------------------------------------------------------------


def test_dry_run_html_prints_parse_mode(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        ev = notify_telegram.send(tier="info", body="<b>hi</b>", parse_mode="html", dry_run=True)
    assert mock_post.call_count == 0
    body_line = next(line for line in capsys.readouterr().err.splitlines() if line.startswith("[dry-run] body: "))
    assert json.loads(body_line.removeprefix("[dry-run] body: "))["parse_mode"] == "HTML"
    assert ev.payload["parse_mode"] == "html"


# ----- CLI wiring --------------------------------------------------------------


def test_cli_parse_mode_html(runner: CliRunner, secrets_dir: Path) -> None:
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "<b>hi</b>", "--parse-mode", "html", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["payload"]["parse_mode"] == "html"
    assert '"parse_mode": "HTML"' in result.stderr


def test_cli_unknown_parse_mode_exits_1(runner: CliRunner, secrets_dir: Path) -> None:
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "x", "--parse-mode", "markdownv2"])
    assert result.exit_code == 1
    assert "unknown parse mode" in result.stderr


def test_cli_body_file(runner: CliRunner, secrets_dir: Path, tmp_path: Path) -> None:
    body = "<b>Briefing</b>\n\nline with $(not run) and `backticks`"
    path = tmp_path / "briefing.html"
    path.write_text(body, encoding="utf-8")
    result = runner.invoke(
        cli.app, ["notify", "telegram", "--body-file", str(path), "--parse-mode", "html", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["payload"]["body_chars"] == len(body)
    assert json.dumps(body, ensure_ascii=False) in result.stderr


@pytest.mark.parametrize("args", [[], ["--body", "x", "--body-file", "f.txt"]])
def test_cli_needs_exactly_one_body_source(runner: CliRunner, secrets_dir: Path, args: list[str]) -> None:
    result = runner.invoke(cli.app, ["notify", "telegram", *args, "--dry-run"])
    assert result.exit_code == 1
    assert "exactly one of --body or --body-file" in result.stderr


def test_cli_missing_body_file_exits_1(runner: CliRunner, secrets_dir: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["notify", "telegram", "--body-file", str(tmp_path / "nope.txt")])
    assert result.exit_code == 1
    assert "could not read --body-file" in result.stderr


def test_main_parse_mode_html(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert notify_telegram.main(["--body", "<b>hi</b>", "--parse-mode", "html", "--dry-run"]) == 0
    assert '"parse_mode": "HTML"' in capsys.readouterr().err
