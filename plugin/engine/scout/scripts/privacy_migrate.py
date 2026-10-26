"""Per-note privacy: the vault migration and the link step (#453, spec §3.1, §4.3, §5.1, §10 P5, D6, D10).

Three things an existing vault needs once ``visibility`` means something:

(a) **Legacy ``visibility:`` values.** Notes whose ``visibility:`` value is
    present but isn't ``normal``/``private`` (``PRIVATE while drafting``,
    ``secret``). They already count as private (fail-closed, §3.1);
    :func:`set_private` rewrites the value to ``private`` with the owner's OK,
    one note at a time, changing only that line. Notes without the key are never
    touched: Scout never writes defaults into frontmatter (D4).
(b) **Private links in shared files.** Lines of files classed ``shared``
    (``drafts/**``, a declared ``federation_scope``) that link or name a private
    note, reported as file + line numbers only (:func:`private_links_in_shared`).
(c) **The link step.** Trading/brokerage folders, which stay on the laptop
    (D6), proposed by :func:`local_only_proposals` and written to
    ``scout-config.yaml → privacy.local_only_add`` by :func:`add_local_only`
    with the owner present.

:func:`report` is read-only. The two writers refuse a headless caller
(:func:`headless_reason`); ``/scout-update`` asks the owner per note and per
path and then calls them through ``scoutctl bootstrap privacy-migrate``. The
upgrade pipeline only prints the report (``bootstrap._stage_privacy_report``).

Once the owner has answered every proposal, :func:`accept` records it in
``.scout-state/privacy-migrate.json`` (local-only). Hosts such as Agnes check
that marker, and that it is still current (:func:`acceptance`), before linking
the vault.

Nothing here prints a private note's title, name or content: (a) lists paths,
(b) lists shared files with line numbers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from scout import paths
from scout.kb.frontmatter import FENCE
from scout.kb.lint_config import glob_match
from scout.kb.outbound import PrivateTerms, find_mentions, private_terms
from scout.kb.privacy import (
    VISIBILITIES,
    classifier_block,
    load_frontmatter_yaml,
    load_policy,
    normalize_rel,
)
from scout.kb.read import all_labels
from scout.kb.reader import resolve_reader

Outcome = Literal["rewritten", "unchanged", "refused"]

# Folder-name words that mark trading/brokerage data (D6). A folder matches when
# one of its name's words (split on `-`, `_`, `.`, spaces) is one of these.
TRADING_WORDS = frozenset(
    {
        "trading",
        "brokerage",
        "brokerages",
        "broker",
        "brokers",
        "investment",
        "investments",
        "investing",
        "portfolio",
        "portfolios",
    }
)
_WORD_SPLIT = re.compile(r"[-_.\s]+")
_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
_VISIBILITY_LINE = re.compile(r"^(visibility[ \t]*:[ \t]*)(.*?)(\r?\n)?$", re.DOTALL)
_TRAILING_COMMENT = re.compile(r"[ \t]+#.*$")


# ----- who may write --------------------------------------------------------------


def headless_reason(env: Mapping[str, str]) -> str | None:
    """Why the caller isn't the owner at a keyboard, or None when it is.

    A scheduled run sets ``SCOUT_MODE``; an engine-launched one sets
    ``SCOUT_READER=autonomous``. Not a TTY check: ``/scout-update`` runs
    ``scoutctl`` from Claude Code without one, with the owner in the conversation.
    """
    if env.get("SCOUT_MODE"):
        return "SCOUT_MODE is set (a scheduled run)"
    reader = resolve_reader(None, env)
    if reader != "owner":
        return f"the reader is {reader}, not owner"
    return None


# ----- (a) legacy visibility values ----------------------------------------------------


def _standard(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in VISIBILITIES


def _frontmatter(text: str) -> dict[str, Any] | None:
    block = classifier_block(text)
    if block is None:
        return None
    try:
        data = load_frontmatter_yaml("\n".join(block))
    except (yaml.YAMLError, RecursionError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def odd_visibility_notes(vault: Path, *, persist: bool = True) -> list[str]:
    """Vault-relative paths of notes whose ``visibility:`` is set to anything but ``normal``/``private``.

    Sorted. A note whose frontmatter doesn't parse isn't listed (kb-lint
    reports it); neither is one without the key.
    """
    root = vault.resolve()
    out: list[str] = []
    for rel, label in sorted(all_labels(root, load_policy(root), persist=persist).items()):
        if not rel.lower().endswith(".md") or "visibility-value" not in label.warnings:
            continue
        try:
            text = (root / rel).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        fm = _frontmatter(text)
        if fm is not None and "visibility" in fm and not _standard(fm["visibility"]):
            out.append(rel)
    return out


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_private(vault: Path, path: str | Path) -> tuple[Outcome, str]:
    """Rewrite one note's legacy ``visibility:`` value to ``private``.

    Only that line changes (its key spelling, spacing, a trailing comment on an
    unquoted value and its line ending are kept). Returns ``(outcome, reason)``:
    ``unchanged`` when the value is already ``normal``/``private``, ``refused``
    when there is no ``visibility:`` key (Scout never adds one), the value spans
    several lines, or the rewrite wouldn't parse back as exactly that change.
    ``ValueError`` for a path outside the vault.
    """
    root = vault.resolve()
    rel = normalize_rel(root, path)
    target = root / rel
    if not rel.lower().endswith(".md") or not target.is_file():
        return "refused", "not a markdown note"
    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "refused", "not UTF-8"
    fm = _frontmatter(text)
    if fm is None:
        return "refused", "no frontmatter that parses"
    if "visibility" not in fm:
        return "refused", "no visibility: key (Scout never adds one)"
    if _standard(fm["visibility"]):
        return "unchanged", f"already {fm['visibility'].strip().lower()}"

    lines = text.splitlines(keepends=True)
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.rstrip() == FENCE), None)
    if end is None:  # pragma: no cover - _frontmatter() above already needs the closing fence
        return "refused", "no closing frontmatter fence"
    hits = [i for i in range(1, end) if _VISIBILITY_LINE.match(lines[i]) and lines[i].startswith("visibility")]
    if len(hits) != 1:
        return "refused", "the visibility: line can't be found unambiguously; edit it by hand"
    i = hits[0]
    m = _VISIBILITY_LINE.match(lines[i])
    assert m is not None
    value = m.group(2)
    if not value.strip():
        return "refused", "the value starts on the next line (empty after visibility:); edit it by hand"
    if value.lstrip().startswith(("|", ">")):
        return "refused", "the value is a block scalar over several lines; edit it by hand"
    comment = ""
    if not value.lstrip().startswith(("'", '"', "[", "{")):
        cm = _TRAILING_COMMENT.search(value)
        comment = cm.group(0) if cm else ""
    lines[i] = f"{m.group(1)}private{comment}{m.group(3) or ''}"
    new_text = "".join(lines)

    want = dict(fm)
    want["visibility"] = "private"
    if _frontmatter(new_text) != want:
        return "refused", "the rewritten frontmatter didn't parse back as just this change; edit it by hand"
    _atomic_write_bytes(target, new_text.encode("utf-8"))
    return "rewritten", ""


# ----- (b) private links in shared files ---------------------------------------------


def private_links_in_shared(vault: Path, *, persist: bool = True) -> list[tuple[str, list[int]]]:
    """``(shared file, 1-based line numbers)`` for every shared file that links or names a private note.

    Same matcher as kb-lint (``outbound.find_mentions``, ``%%privacy:allow%%``
    markers honoured). A shared file that is itself private isn't matched
    against its own name.
    """
    root = vault.resolve()
    policy = load_policy(root)
    labels = all_labels(root, policy, persist=persist)
    shared = sorted(rel for rel, label in labels.items() if label.shared_file)
    if not shared:
        return []
    terms = private_terms(root, policy, persist=persist)
    out: list[tuple[str, list[int]]] = []
    for rel in shared:
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue  # binary
        own = terms
        if labels[rel].visibility == "private":
            # any_term is only a pre-check, so a superset of the remaining notes' terms is fine.
            own = PrivateTerms(notes=tuple(n for n in terms.notes if n.path != rel), any_term=terms.any_term)
        lines = find_mentions(data.decode("utf-8", errors="replace"), own, allow_markers=True)
        if lines:
            out.append((rel, lines))
    return out


# ----- (c) the link step ------------------------------------------------------------------


def _trading_name(name: str) -> bool:
    return any(w in TRADING_WORDS for w in _WORD_SPLIT.split(name.casefold()) if w)


def local_only_proposals(vault: Path) -> list[str]:
    """``<folder>/**`` globs for trading/brokerage folders not already local-only. Sorted.

    Hidden folders (machine plumbing, already local-only or deliberately synced)
    and symlinks aren't walked; a matching folder's subfolders aren't proposed
    on their own.
    """
    root = vault.resolve()
    policy = load_policy(root)
    covered = (*policy.local_only, *policy.local_only_always)
    out: list[str] = []
    for dirpath, dirnames, _files in os.walk(root, followlinks=False):
        base = Path(dirpath)
        keep: list[str] = []
        for d in sorted(dirnames):
            if d.startswith(".") or d in _SKIP_DIRS or (base / d).is_symlink():
                continue
            rel = (base / d).relative_to(root).as_posix()
            if _trading_name(d):
                if not any(glob_match(g, f"{rel}/x") for g in covered):
                    out.append(f"{rel}/**")
                continue
            keep.append(d)
        dirnames[:] = keep
    return sorted(out)


def _check_glob(glob: str) -> str:
    g = glob.strip()
    if not g or "\n" in g or "\r" in g:
        raise ValueError(f"not a path glob: {glob!r}")
    if g.startswith("/") or ".." in g.split("/"):
        raise ValueError(f"a local-only glob must be vault-relative: {glob!r}")
    return g


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _filler(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _insert_globs(text: str, new: list[str]) -> str | None:
    """``text`` with ``new`` appended to ``privacy.local_only_add``, edited in place; None when the
    layout isn't one this handles (the caller then falls back to a block re-dump)."""
    eol = _eol(text)
    lines = text.splitlines(keepends=True)
    top = re.compile(r"^privacy[ \t]*:[ \t]*(#.*)?\r?\n?$")
    p = next((i for i, ln in enumerate(lines) if top.match(ln)), None)
    if p is None:
        if any(ln.startswith("privacy") for ln in lines):
            return None  # `privacy: {...}` and friends
        head = text if not text or text.endswith(("\n", "\r")) else text + eol
        items = "".join(f"    - {json.dumps(g)}{eol}" for g in new)
        return f"{head}privacy:{eol}  local_only_add:{eol}{items}"
    end = p + 1
    while end < len(lines) and (_filler(lines[end]) or _indent(lines[end]) > 0):
        end += 1
    body = [i for i in range(p + 1, end) if not _filler(lines[i])]
    if not body:
        return None
    child = _indent(lines[body[0]])
    any_key = re.compile(" " * child + r"local_only_add[ \t]*:")
    key = re.compile(" " * child + r"local_only_add[ \t]*:[ \t]*(#.*)?\r?\n?$")
    found = [i for i in body if any_key.match(lines[i])]
    if len(found) > 1 or (found and not key.match(lines[found[0]])):
        return None  # an inline value (`[]`, `~`, a flow list) or a duplicate: re-dump the block
    q = found[0] if found else None
    if q is None:
        last = body[-1]
        if not lines[last].endswith(("\n", "\r")):
            lines[last] += eol
        items = "".join(f"{' ' * (child + 2)}- {json.dumps(g)}{eol}" for g in new)
        lines.insert(last + 1, f"{' ' * child}local_only_add:{eol}{items}")
        return "".join(lines)
    # The key's own lines: the run right after it that is deeper, or a `- ` item at its indent.
    run: list[int] = []
    for i in (i for i in body if i > q):
        ln = lines[i]
        if _indent(ln) > child or (_indent(ln) == child and ln.lstrip().startswith("-")):
            run.append(i)
        else:
            break
    dashes = [i for i in run if lines[i].lstrip().startswith("- ")]
    indent = _indent(lines[dashes[0]]) if dashes else child + 2
    after = run[-1] if run else q
    if not lines[after].endswith(("\n", "\r")):
        lines[after] += eol
    lines.insert(after + 1, "".join(f"{' ' * indent}- {json.dumps(g)}{eol}" for g in new))
    return "".join(lines)


def add_local_only(vault: Path, globs: Iterable[str]) -> list[str]:
    """Append ``globs`` to ``scout-config.yaml → privacy.local_only_add``; return the ones added.

    Idempotent (a glob already listed is skipped) and append-only: it never
    removes or reorders an entry. Comments and every other line are kept: the
    list is edited in place, with a fallback to the comment-keeping block
    re-dump the bootstrap uses for odd layouts. ``ValueError`` for a bad glob or
    a ``scout-config.yaml`` / ``privacy:`` block that isn't a mapping.
    """
    from scout.scripts.bootstrap import _dump_keeping_comments  # bootstrap imports this module

    wanted = list(dict.fromkeys(_check_glob(g) for g in globs))
    root = vault.resolve()
    config = paths.config_path(root)
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    data = yaml.safe_load(text) if text.strip() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("scout-config.yaml isn't a mapping; fix it by hand first")
    privacy = data.get("privacy")
    if privacy is None:
        privacy = {}
    if not isinstance(privacy, dict):
        raise ValueError("scout-config.yaml → privacy isn't a mapping; fix it by hand first")
    existing = privacy.get("local_only_add")
    if existing is None:
        existing = []
    if not isinstance(existing, list) or not all(isinstance(g, str) for g in existing):
        raise ValueError("scout-config.yaml → privacy.local_only_add isn't a list of globs; fix it by hand first")
    new = [g for g in wanted if g not in existing]
    if not new:
        return []
    want = dict(data)
    want["privacy"] = {**privacy, "local_only_add": [*existing, *new]}

    result = _insert_globs(text, new)
    if result is None or _load(result) != want:
        print(
            "warning: scout-config.yaml → privacy.local_only_add couldn't be edited in place; "
            "the privacy: block was rewritten and comments inside it may be lost",
            file=sys.stderr,
        )
        result = _dump_keeping_comments(text, want)
    if _load(result) != want:
        raise RuntimeError("could not write scout-config.yaml → privacy.local_only_add")
    if config.exists():
        _atomic_write_bytes(config, result.encode("utf-8"))
    else:
        config.write_text(result, encoding="utf-8")
    return new


class _StrictLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses a mapping with a duplicate key (PyYAML keeps the last silently)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:
                continue  # an unhashable key: the base class reports it
            if duplicate:
                raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _load(text: str) -> Any:
    """``yaml.safe_load`` that rejects duplicate keys; None when it doesn't parse."""
    try:
        return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError:
        return None


# ----- the report ----------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationReport:
    visibility_values: list[str] = field(default_factory=list)
    private_links: list[tuple[str, list[int]]] = field(default_factory=list)
    local_only_proposals: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.visibility_values or self.private_links or self.local_only_proposals)

    def to_json(self) -> dict[str, Any]:
        return {
            "visibility_values": list(self.visibility_values),
            "private_links": [{"path": p, "lines": list(ls)} for p, ls in self.private_links],
            "local_only_proposals": list(self.local_only_proposals),
        }

    def lines(self) -> list[str]:
        """The human report. Paths and line numbers only, never note content."""
        if not self:
            return ["privacy migration: nothing to do"]
        out: list[str] = []
        if self.visibility_values:
            out.append(
                f"visibility: {len(self.visibility_values)} note(s) set visibility: to something other than "
                "normal/private; they already count as private:"
            )
            out += [f"  {p}" for p in self.visibility_values]
            out.append("  with the owner's OK, per note: scoutctl bootstrap privacy-migrate --set-private <path>")
        if self.private_links:
            out.append(f"private links: {len(self.private_links)} shared file(s) link or name a private note:")
            for p, ls in self.private_links:
                out.append(f"  {p}: line{'s' if len(ls) > 1 else ''} {', '.join(str(n) for n in ls)}")
            out.append("  remove the reference, or allow one with %%privacy:allow <slug>%% on that line")
        if self.local_only_proposals:
            out.append("local-only: trading/brokerage folders that would stay on this computer (not written):")
            out += [f"  {g}" for g in self.local_only_proposals]
            out.append("  with the owner's OK: scoutctl bootstrap privacy-migrate --local-only '<glob>'")
        return out


def report(vault: Path, *, persist: bool = True) -> MigrationReport:
    """(a), (b) and (c) for ``vault``. Read-only for notes and config.

    ``persist=False`` also leaves the privacy index (``.scout-cache/``) alone,
    so the pass changes no file at all (the upgrade stage passes it).
    """
    return MigrationReport(
        visibility_values=odd_visibility_notes(vault, persist=persist),
        private_links=private_links_in_shared(vault, persist=persist),
        local_only_proposals=local_only_proposals(vault),
    )


# ----- the owner's consent marker -------------------------------------------------------

MARKER_NAME = "privacy-migrate.json"
MARKER_SCHEMA = 1
_MARKER_STRINGS = ("engine_version", "accepted_at", "report_sha256")


def marker_path(vault: Path) -> Path:
    """``.scout-state/privacy-migrate.json``. Local-only: ``.scout-state/**`` minus ``sync_allow``."""
    return paths.state_dir(vault.resolve()) / MARKER_NAME


def decision_sha256(rep: MigrationReport) -> str:
    """sha256 of what the owner decided on: the outstanding legacy-visibility notes and local-only proposals.

    The hashed value is the canonical JSON (sorted keys, no whitespace) of
    ``{"local_only_proposals": [globs], "visibility_values": [paths]}``, each
    list sorted. ``private_links`` is left out on purpose: it is informational
    (nothing to approve), and it changes whenever a draft is edited, which must
    not invalidate the owner's consent. A new legacy-visibility note or a new
    trading folder changes the hash, so the owner is asked again.
    """
    decided = {
        "local_only_proposals": sorted(rep.local_only_proposals),
        "visibility_values": sorted(rep.visibility_values),
    }
    canonical = json.dumps(decided, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_private(path: Path, data: bytes) -> None:
    """Atomic write of a new or existing file, mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def accept(vault: Path) -> dict[str, Any]:
    """Record that the owner has been through the P5 migration; return the marker written.

    Computes the report with ``persist=False`` (no privacy-index write) before
    touching anything, so a report that fails writes nothing. The caller checks
    :func:`headless_reason` first: this records the owner's consent.

    A declined proposal is the owner's decision, so accepting after a "no" is
    valid. The decline is recorded rather than silent: ``declined_local_only``
    lists the local-only proposals still outstanding at acceptance (the ones the
    owner said no to). A host (e.g. Agnes R1) may require
    ``declined_local_only == []`` before linking the vault.
    """
    from scout import __version__

    rep = report(vault, persist=False)
    marker = {
        "schema": MARKER_SCHEMA,
        "engine_version": __version__,
        "accepted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report_sha256": decision_sha256(rep),
        "declined_local_only": sorted(rep.local_only_proposals),
    }
    _write_private(marker_path(vault), (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return marker


def read_marker(vault: Path) -> dict[str, Any] | None:
    """The consent marker, or None when it is missing, unreadable or not a schema-1 marker."""
    try:
        data = json.loads(marker_path(vault).read_bytes().decode("utf-8"))
    except (OSError, ValueError, RecursionError):  # ValueError covers UnicodeDecodeError and JSONDecodeError
        return None
    if not isinstance(data, dict) or data.get("schema") != MARKER_SCHEMA:
        return None
    if not all(isinstance(data.get(k), str) for k in _MARKER_STRINGS):
        return None
    declined = data.get("declined_local_only")
    if not isinstance(declined, list) or not all(isinstance(g, str) for g in declined):
        return None
    return data


def acceptance(vault: Path, rep: MigrationReport) -> dict[str, Any] | None:
    """The report's ``accepted`` field: None without a readable marker, else the marker plus ``current``.

    ``current`` is true only when the marker's hash equals :func:`decision_sha256`
    of ``rep`` (the report as it is now). False means the owner must be asked again.
    ``declined_local_only`` is what the owner declined at acceptance;
    ``outstanding_local_only`` is the live proposal list. A host may require
    ``declined_local_only == []`` before linking.
    """
    marker = read_marker(vault)
    if marker is None:
        return None
    out: dict[str, Any] = {k: marker[k] for k in _MARKER_STRINGS}
    out["current"] = marker["report_sha256"] == decision_sha256(rep)
    out["declined_local_only"] = list(marker["declined_local_only"])
    out["outstanding_local_only"] = list(rep.local_only_proposals)
    return out


def acceptance_line(accepted: Mapping[str, Any] | None) -> str:
    """One line for the text report."""
    if accepted is None:
        return "privacy consent: not accepted; run /scout-update with the owner present"
    n = len(accepted["declined_local_only"])
    declined = f"{n} local-only proposal{'' if n == 1 else 's'} declined"
    if not accepted["current"]:
        return (
            f"privacy consent: out of date (accepted at {accepted['accepted_at']}, {declined}, "
            "the report has changed since); the owner must be asked again: run /scout-update"
        )
    return f"privacy consent: accepted at {accepted['accepted_at']}; {declined}"
