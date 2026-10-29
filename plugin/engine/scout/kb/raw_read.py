"""What counts as reading the KB around ``scoutctl kb`` (#453; hard-rule audit 2026-10, row 2).

One definition for two users: the ``kb-read-gate`` PreToolUse hook
(:mod:`scout.hooks.kb_read_gate`), which denies these calls in scheduled runs,
and the eval harness's ``privacy_hygiene`` check, which counts them. Pure: it
judges strings and does no I/O, so the eval's scoring layer can import it.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from typing import Any

KB_PATH = re.compile(r"(?:^|[\s'\"=:/])(?:knowledge-base|knowledge_base|action-items/context)(?:/|\b|$)")
KB_DIRS = ("knowledge-base", "knowledge_base", "action-items/context")
_READ_COMMANDS = {
    "cat", "rg", "grep", "egrep", "fgrep", "ls", "find", "head", "tail", "less", "more", "sed", "awk", "wc",
    "tree", "bat", "nl", "strings",
}  # fmt: skip
_PREFIX_WORDS = {"xargs", "sudo", "command", "env", "nice", "time", "exec"}
_SEGMENTS = re.compile(r"&&|\|\||[;&|\n`]|\$\(|\(|\)")
_MESSAGE_ARG = re.compile(r"(\s(?:-m|--message|-F|--body|--title))(?:\s+|=)(\"(?:[^\"\\]|\\.)*\"|'[^']*')")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_READ = re.compile(r"^(?:-C\s+\S+\s+)?(?:show|diff|log\b.*\s(?:-p|--patch|-u)\b)")
# knowledge-base/ontology/schema.yaml and parser.py are tooling, not notes: reading them is fine.
ONTOLOGY_TOOLING = re.compile(r"(?:^|/)(?:knowledge-base|knowledge_base)/ontology/(?:schema\.ya?ml|parser\.py)$")
WHOLE_VAULT_GLOBS = {"*", "**", "**/*", "*.md", "**/*.md", "**/*.*"}


def _words(segment: str) -> list[str]:
    words = segment.split()
    while words and (words[0] in _PREFIX_WORDS or _ASSIGNMENT.match(words[0]) or words[0].startswith("-")):
        words.pop(0)  # `xargs`, `sudo`, `env X=1`, and xargs' own flags
    return words


def raw_kb_read(command: str, *, in_kb: bool = False) -> bool:
    """Does this shell command read ``knowledge-base/`` or ``action-items/context/`` around ``scoutctl kb``?

    Each shell segment is judged by its first word: a reading command (``/bin/cat`` too) with a KB
    path among its arguments, or run after ``cd`` into the KB (``in_kb``: the shell starts there);
    ``git show|diff|log -p`` with a KB path. ``scoutctl …`` segments and quoted commit messages
    never count.
    """
    command = _MESSAGE_ARG.sub(r"\1 ''", command)
    for segment in _SEGMENTS.split(command):
        words = _words(segment)
        if not words:
            continue
        name, rest = words[0].rsplit("/", 1)[-1], " ".join(words[1:])
        if name == "scoutctl":
            continue
        if name == "cd":
            in_kb = bool(KB_PATH.search(" " + rest))
        elif name in _READ_COMMANDS:
            if in_kb or KB_PATH.search(" " + rest):
                return True
        elif name == "git" and _GIT_READ.search(rest) and KB_PATH.search(" " + rest):
            return True
    return False


def filter_reaches_kb(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """A Grep/Glob rooted at the vault reaches the KB: Glob's pattern or Grep's ``glob`` names the KB
    or is a whole-vault pattern (``**``, ``*.md``); Grep with no ``glob`` searches everything."""
    target = str(tool_input.get("pattern" if tool_name == "Glob" else "glob") or "").strip()
    if not target:
        return tool_name == "Grep"
    return bool(KB_PATH.search(" " + target)) or target in WHOLE_VAULT_GLOBS


def search_reads_kb(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """A Grep/Glob call aimed at the KB, judged on its arguments alone: its path is in it, or it has
    no path (the vault) and :func:`filter_reaches_kb`."""
    path = str(tool_input.get("path") or "")
    if path:
        return bool(KB_PATH.search(" " + path))
    return filter_reaches_kb(tool_name, tool_input)


def is_kb_note_path(path: str) -> bool:
    """A path string in the KB that isn't ontology tooling (what a Read of the KB means)."""
    return bool(KB_PATH.search(" " + path)) and not ONTOLOGY_TOOLING.search(posixpath.normpath(path))
