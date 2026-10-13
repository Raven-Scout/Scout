"""Markdown links in the action-items HTML renderers (Raven-Scout/Scout#435).

Action items are written by the model from Slack, mail, GitHub and Linear
text, so a link's URL is attacker-reachable. Both renderers — the engine's
``scout.action_items.render`` and the vault template
``templates/action-items/render.py`` — must attribute-escape the ``href`` and
turn only ``http``/``https``/``mailto`` links into anchors. Anything else
renders as plain text.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scout.action_items import render as engine_render

_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "action-items" / "render.py"


def _load_template_inline() -> Callable[[str], str]:
    spec = importlib.util.spec_from_file_location("scout_template_action_items_render", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module.inline


RENDERERS = {
    "engine": engine_render._inline,
    "template": _load_template_inline(),
}


@pytest.fixture(params=sorted(RENDERERS))
def inline(request: pytest.FixtureRequest) -> Callable[[str], str]:
    return RENDERERS[request.param]


def _hrefs(out: str) -> list[str]:
    return re.findall(r'<a href="([^"]*)"', out)


# ---------------------------------------------------------------------------
# Legitimate links still render
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "href"),
    [
        ("[the PR](https://github.com/example-org/widgets/pull/42)", "https://github.com/example-org/widgets/pull/42"),
        (
            "[PROJ-1234](https://linear.app/your-workspace/issue/PROJ-1234)",
            "https://linear.app/your-workspace/issue/PROJ-1234",
        ),
        (
            "[thread](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)",
            "https://acme-co.slack.com/archives/C0123456789/p1700000000000000",
        ),
        ("[docs](http://example.com/a)", "http://example.com/a"),
        ("[mail Alex](mailto:alex@example.com)", "mailto:alex@example.com"),
        ("[upper](HTTPS://example.com/)", "HTTPS://example.com/"),
    ],
)
def test_allowed_schemes_render_as_links(inline: Callable[[str], str], md: str, href: str) -> None:
    assert _hrefs(inline(md)) == [href]


def test_query_ampersand_is_escaped_once(inline: Callable[[str], str]) -> None:
    out = inline("[search](https://example.com/?a=1&b=2)")
    assert _hrefs(out) == ["https://example.com/?a=1&amp;b=2"]


def test_wikilinks_still_render_as_pills(inline: Callable[[str], str]) -> None:
    out = inline("Ping [[people/alex|Alex]] about [[PROJ-1234]]")
    assert '<span class="wiki">Alex</span>' in out
    assert '<span class="wiki">PROJ-1234</span>' in out


# ---------------------------------------------------------------------------
# Hostile links
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "&#106;avascript:alert(1)",
        "&#x6A;avascript:alert(1)",
        "javascript&colon;alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "obsidian://new?content=x",
        "//example.com/x",
        "relative/path",
        "\x01javascript:alert(1)",
    ],
)
def test_disallowed_schemes_render_as_plain_text(inline: Callable[[str], str], url: str) -> None:
    out = inline(f"[click me]({url})")
    assert "<a " not in out
    assert "click me" in out
    assert "javascript:" not in out.lower().replace("\t", "").replace("\n", "")


def test_quote_in_url_cannot_break_out_of_href(inline: Callable[[str], str]) -> None:
    out = inline('[x](https://example.com/" onmouseover="alert(1))')
    assert _hrefs(out) == ["https://example.com/&quot; onmouseover=&quot;alert(1"]
    assert ' onmouseover="' not in out


def test_single_quote_and_angle_brackets_are_escaped(inline: Callable[[str], str]) -> None:
    out = inline("[x](https://example.com/'<b>)")
    (href,) = _hrefs(out)
    assert "<" not in href and ">" not in href and "'" not in href
    assert "<b>" not in out


def test_label_markup_stays_escaped(inline: Callable[[str], str]) -> None:
    out = inline("[<img src=x onerror=alert(1)>](https://example.com/)")
    assert "<img" not in out
    assert "&lt;img" in out
