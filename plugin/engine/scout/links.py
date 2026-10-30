"""Which link URLs may stay clickable, shared by every surface that renders links.

The action-items dashboard (Raven-Scout/Scout#435) and ``scoutctl notify
telegram --parse-mode html`` (#465) both use it. Stdlib only, so a caller
does not pull in the dashboard renderer to check one URL.
"""

from __future__ import annotations

import html
import re

# Link schemes that become clickable anchors (Raven-Scout/Scout#435). Action
# items are written from Slack, mail, GitHub and Linear text, so a link's URL is
# attacker-reachable: any other scheme (javascript:, data:, file:, obsidian:)
# and any scheme-less URL renders as its label only.
SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
# Browsers drop ASCII control characters and spaces when they read a URL's
# scheme (`java<TAB>script:` runs), so the check ignores them too.
_URL_IGNORED_RE = re.compile(r"[\x00-\x20\x7f]")
_URL_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):")


def safe_href(url: str) -> str | None:
    """*url* (unescaped) escaped for an ``href`` attribute, or None if its scheme isn't allowed."""
    m = _URL_SCHEME_RE.match(_URL_IGNORED_RE.sub("", url))
    if m is None or m.group(1).lower() not in SAFE_LINK_SCHEMES:
        return None
    return html.escape(url, quote=True)
