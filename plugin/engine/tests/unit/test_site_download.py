"""The website's and READMEs' Download for Mac links start the DMG download directly.

They point at the stable `Scout.dmg` asset that `scripts/release.sh` attaches to every final
release, served from /releases/latest/download/, rather than at the releases page.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DMG = "https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg"


def test_site_download_buttons_start_the_dmg_download():
    html = (REPO / "docs" / "index.html").read_text(encoding="utf-8")
    assert html.count(f'href="{DMG}"') >= 2  # the hero and the #install card
    assert "Download for Mac" in html
    assert 'href="https://github.com/Raven-Scout/Scout/releases/latest"' not in html
    assert "Scout-x.y.z.dmg" not in html


def test_readmes_link_the_dmg_directly():
    for path in (REPO / "README.md", REPO / "plugin" / "README.md"):
        text = path.read_text(encoding="utf-8")
        assert DMG in text, path
        assert "Scout-x.y.z.dmg" not in text and "Scout-*.dmg" not in text, path
