"""Rewriting old vault paths in text, for the layout move and ``vault move`` (spec 2026-11-26 §4.10, R3).

One function, :func:`rewrite`, driven by the exact move list of a vault (:class:`Ref` pairs). Pure: text in,
text out. What it rewrites depends on ``kind``:

- every kind: literal paths, vault-relative at a token boundary or after a vault prefix
  (``$SCOUT_DIR/``, ``${SCOUT_DIR}/``, ``$SCOUT_DATA_DIR/``, ``{{SCOUT_DIR}}/``, the vault's absolute path);
- ``brain`` and ``script``: bare brain-skill names too (``SKILL.md``), which elsewhere often mean another
  repository's skill file;
- ``note``: wikilinks and Markdown links in vault-relative or KB-relative form (``.md``, ``|alias`` and
  ``#heading`` kept);
- ``config``: a YAML list item naming a per-space folder of v1 (``knowledge-base/projects/**``) becomes one
  item per registered space; ``schema``: KB-relative globs (the ontology's ``graph.exclude``).

It is idempotent: no new path contains an old one at a token boundary (the old path is always preceded by
``/`` inside the new one), and :func:`check_refs` asserts that for any move list.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

KB = "knowledge-base"
KINDS = ("brain", "script", "config", "note", "schema", "gitignore", "plain")
# Characters that continue a path token on the left.
_LEFT = r"(?<![\w./~$-])"
_PREFIXES = (
    r"\$SCOUT_DIR/",
    r"\$\{SCOUT_DIR\}/",
    r"\$SCOUT_DATA_DIR/",
    r"\$\{SCOUT_DATA_DIR\}/",
    r"\{\{SCOUT_DIR\}\}/",
)


@dataclass(frozen=True)
class Ref:
    """One old -> new vault-relative path. ``is_dir``: everything under ``old`` moves under ``new``.

    ``bare_ok``: the old path may be rewritten with no vault prefix in every kind (a brain skill's bare name
    is rewritten bare only in ``brain`` and ``script`` text). ``per_space``: a v1 per-space folder whose
    config globs expand to every space (``new`` then holds ``{space}``).
    """

    old: str
    new: str
    is_dir: bool = False
    bare_ok: bool = True
    per_space: bool = False


def check_refs(refs: Iterable[Ref]) -> None:
    """Raise ``ValueError`` when a new path holds an old one at a token boundary (rewrite would not be idempotent)."""
    refs = list(refs)
    for r in refs:
        for o in refs:
            new = r.new.replace("{space}", "x")
            pat = re.compile(_LEFT + re.escape(o.old) + (r"(?=/)" if o.is_dir else r"(?![\w/-])"))
            if pat.search(new):
                raise ValueError(f"layout rewrite: {r.new!r} contains {o.old!r} at a token boundary")


def _sorted(refs: Iterable[Ref]) -> list[Ref]:
    return sorted(refs, key=lambda r: (-len(r.old), r.old))


def _right(is_dir: bool) -> str:
    # A directory is followed by "/" or the end of the token; a file by anything that isn't a name character
    # (a sentence's full stop is fine: ".md." still ends the token).
    return r"(?=/|[^\w./-]|$|\.(?:\s|$))" if is_dir else r"(?![\w/-])(?!\.\w)"


def _literal_pattern(refs: Sequence[Ref], vault_abs: Sequence[str], *, bare_names: bool) -> re.Pattern[str] | None:
    if not refs:
        return None
    prefixes = [*_PREFIXES, *(re.escape(v.rstrip("/") + "/") for v in vault_abs if v)]
    pre = "(?P<pre>" + "|".join(prefixes) + r"|\./)?"
    alts = []
    for i, r in enumerate(refs):
        old = re.escape(r.old)
        alts.append(f"(?P<r{i}>{old}){_right(r.is_dir)}")
    body = "(?:" + "|".join(alts) + ")"
    return re.compile(pre + body)


class _Literal:
    def __init__(self, refs: Sequence[Ref], vault_abs: Sequence[str], *, bare_names: bool, space: str) -> None:
        self.refs = _sorted(refs)
        self.bare_names = bare_names
        self.space = space
        self.pat = _literal_pattern(self.refs, vault_abs, bare_names=bare_names)

    def sub(self, text: str) -> str:
        if self.pat is None:
            return text

        def repl(m: re.Match[str]) -> str:
            pre = m.group("pre") or ""
            idx = next(int(k[1:]) for k, v in m.groupdict().items() if k.startswith("r") and v is not None)
            ref = self.refs[idx]
            start = m.start()
            if not pre or pre == "./":
                # Bare: only at a token boundary, and a brain name only where bare names are allowed.
                before = text[start - 1] if start > 0 else ""
                if before and re.match(r"[\w./~$-]", before):
                    return m.group(0)
                if not ref.bare_ok and not self.bare_names:
                    return m.group(0)
                if ref.is_dir and "/" not in ref.old and not text.startswith("/", m.end()):
                    return m.group(0)  # a bare one-word folder name alone is a word, not a path
            elif start > 0 and re.match(r"[\w.-]", text[start - 1]) and not pre.startswith(("$", "{", "/")):
                return m.group(0)
            return pre + ref.new.replace("{space}", self.space)

        return self.pat.sub(repl, text)


# A table cell escapes the alias pipe (``[[a/b\\|Label]]``): the backslash is kept outside the target.
_WIKI_RE = re.compile(r"\[\[([^\]|#\n]*[^\]|#\n\\])((?:#[^\]|\n]*?)?)(\\?(?:\|[^\]\n]*)?)\]\]")
_MDLINK_RE = re.compile(r"(\]\()([^)\s#]+)((?:#[^)\s]*)?)(\))")


def _map_target(target: str, refs: Sequence[Ref], space: str) -> str | None:
    """A link target's new form, or None when no ref moves it. Vault- or KB-relative, ``.md`` optional."""
    t = target.strip()
    if not t or "://" in t:
        return None
    lead = ""
    if t.startswith("/"):
        lead, t = "/", t[1:]
    md = t.endswith(".md")
    stem = t[:-3] if md else t
    candidates: list[tuple[str, bool]] = []
    if not stem.startswith(KB + "/"):
        candidates.append((f"{KB}/{stem}", True))  # KB-relative
    candidates.append((stem, False))
    for full, kb_rel in candidates:
        for r in refs:
            old = r.old[:-3] if r.old.endswith(".md") and not r.is_dir else r.old
            if r.is_dir:
                if full != old and not full.startswith(old + "/"):
                    continue
                new_full = r.new.replace("{space}", space) + full[len(old) :]
            else:
                if full != old:
                    continue
                new = r.new.replace("{space}", space)
                new_full = new[:-3] if new.endswith(".md") and r.old.endswith(".md") else new
            out = new_full
            # Keep the link's style: KB-relative stays KB-relative while the new path is in the KB.
            if (kb_rel or not stem.startswith(KB + "/")) and out.startswith(KB + "/") and not lead:
                out = out[len(KB) + 1 :]
            return lead + out + (".md" if md and not out.endswith(".md") else "")
    return None


def _rewrite_links(text: str, refs: Sequence[Ref], space: str) -> tuple[str, list[tuple[int, int]]]:
    """Links rewritten; also the spans of every link in the result, which the literal pass leaves alone."""
    spans: list[tuple[int, int]] = []

    def wiki(m: re.Match[str]) -> str:
        new = _map_target(m.group(1), refs, space)
        return m.group(0) if new is None else f"[[{new}{m.group(2)}{m.group(3)}]]"

    def md(m: re.Match[str]) -> str:
        new = _map_target(m.group(2), refs, space)
        return m.group(0) if new is None else f"{m.group(1)}{new}{m.group(3)}{m.group(4)}"

    text = _WIKI_RE.sub(wiki, text)
    text = _MDLINK_RE.sub(md, text)
    spans = [m.span() for m in _WIKI_RE.finditer(text)] + [m.span() for m in _MDLINK_RE.finditer(text)]
    return text, spans


def _outside(text: str, spans: list[tuple[int, int]], fn: Callable[[str], str]) -> str:
    if not spans:
        return fn(text)
    out, at = [], 0
    for a, b in sorted(spans):
        if a < at:
            continue
        out.append(fn(text[at:a]))
        out.append(text[a:b])
        at = b
    out.append(fn(text[at:]))
    return "".join(out)


_LIST_ITEM_RE = re.compile(r"^(\s*-\s+(?:glob:\s*)?)(['\"]?)([^'\"\n#]+?)\2(\s*(?:#.*)?)$")


# Keys whose lists allow or widen (what syncs, what is shared by default): an entry there is never multiplied
# across spaces, which would share or sync a private space's folders.
_NARROW_KEYS = frozenset({"sync_only", "federation_defaults", "shared_files_add"})


def _expand_config(text: str, refs: Sequence[Ref], spaces: Sequence[str]) -> tuple[str, list[str]]:
    """Each YAML list item naming a per-space v1 folder becomes one item per space. Returns (text, left as written).

    A ``- glob: X`` item with continuation lines (``visibility:`` under it) is duplicated whole.
    """
    per_space = _sorted(r for r in refs if r.per_space)
    if not per_space:
        return text, []
    lines = text.split("\n")
    out: list[str] = []
    left: list[str] = []
    stack: list[tuple[int, str]] = []  # the mapping keys above the current line, by indent
    i = 0
    while i < len(lines):
        line = lines[i]
        key = re.match(r"^(\s*)([A-Za-z_][\w-]*):", line)
        if key:
            depth = len(key.group(1))
            stack = [(d, k) for d, k in stack if d < depth] + [(depth, key.group(2))]
        m = _LIST_ITEM_RE.match(line)
        hit = None
        if m and any(k in _NARROW_KEYS for _, k in stack):
            m = None  # an allowlist: a v1 entry keeps naming the default space only (the literal pass does that)
        if m:
            value = m.group(3).strip()
            for r in per_space:
                if value == r.old or value.startswith(r.old + "/"):
                    hit = (r, value)
                    break
        if hit is None:
            out.append(line)
            i += 1
            continue
        r, value = hit
        assert m is not None
        indent = len(line) - len(line.lstrip())
        block = [line]
        j = i + 1
        while (
            j < len(lines)
            and lines[j].strip()
            and (len(lines[j]) - len(lines[j].lstrip())) > indent
            and not lines[j].lstrip().startswith("- ")
        ):
            block.append(lines[j])
            j += 1
        for sp in spaces:
            new_value = r.new.replace("{space}", sp) + value[len(r.old) :]
            first = f"{m.group(1)}{m.group(2)}{new_value}{m.group(2)}{m.group(4)}"
            out.append(first)
            out.extend(block[1:])
        i = j
    return "\n".join(out), left


def unplaced_globs(text: str, refs: Sequence[Ref]) -> list[str]:
    """Config lines still naming an old path after the rewrite: what the report lists as left as written."""
    names = [r.old for r in refs]
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            continue
        if any(re.search(_LEFT + re.escape(n) + r"(?![\w-])", s) for n in names):
            out.append(s)
    return out


def rewrite(
    text: str,
    refs: Sequence[Ref],
    *,
    kind: str,
    space: str = "work",
    spaces: Sequence[str] = ("work",),
    vault_abs: Sequence[str] = (),
) -> str:
    """``text`` with every old path in ``refs`` rewritten, by ``kind`` (see the module doc).

    ``space`` is what ``{space}`` becomes in a literal path or link (the default space); ``spaces`` are all
    registered keys, for config globs. ``vault_abs``: the vault's absolute path spellings.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown rewrite kind {kind!r}")
    if kind == "plain" or not refs:
        return text
    if kind == "config":
        text, _ = _expand_config(text, refs, spaces)
    if kind == "schema":
        # KB-relative globs (graph.exclude): rewrite each ref's KB-relative form at a boundary.
        kb_refs = [
            Ref(r.old[len(KB) + 1 :], r.new[len(KB) + 1 :], r.is_dir, True)
            for r in refs
            if r.old.startswith(KB + "/") and r.new.startswith(KB + "/") and not r.per_space
        ]
        kb_refs += [
            Ref(r.old[len(KB) + 1 :], r.new[len(KB) + 1 :].replace("{space}", "*"), r.is_dir, True)
            for r in refs
            if r.old.startswith(KB + "/") and r.new.startswith(KB + "/") and r.per_space
        ]
        text = _Literal(kb_refs, (), bare_names=True, space=space).sub(text)
    literal = _Literal(refs, vault_abs, bare_names=kind in ("brain", "script"), space=space)
    if kind == "note":
        text, spans = _rewrite_links(text, _sorted(refs), space)
        return _outside(text, spans, literal.sub)
    return literal.sub(text)
