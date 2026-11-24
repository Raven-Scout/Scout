"""PreToolUse hook — scheduled runs read the knowledge base only through ``scoutctl kb``.

``scoutctl kb`` is the one place that applies a note's privacy label (#453): it
labels private notes, denies what a reader may not see and logs non-owner
reads. A shell ``cat``/``grep``/``ls``/``find`` into ``knowledge-base/`` skips
all of that, and the phase rule saying so lost in 30 of 34 privacy eval runs
(hard-rule audit 2026-10, row 2). This gate makes it mechanical:

  - **Bash**: a command :func:`raw_kb_read` flags is denied, judged with the session's cwd and
    vault so a recursive read rooted at the vault (``rg x``, ``grep -r x .``) counts. ``scoutctl``
    segments (so ``scripts/scoutctl kb …``), the engine's scripts, ``git
    add``/``commit`` and quoted commit messages are never flagged. So is one
    that sets, unsets or blanks ``SCOUT_MODE``/``SCOUT_READER`` or clears the
    environment (``env -i``), or one that detaches (``nohup``, ``setsid``,
    ``disown``, a trailing ``&``, ``launchctl``, ``osascript``, ``open``,
    ``tmux``/``screen``, ``script``, ``at``/``batch``, ``ssh``):
    :func:`scout.kb.live_run.run_escape`, #595. The commit hook knows a run's
    commits from the process table either way; the denial makes the attempt
    fail in the open and leaves it in the transcript. The same function flags
    a git call that skips or redirects the commit hooks, which the hook can't
    see because it never runs (``--no-verify``, ``-c core.hooksPath``,
    ``commit-tree``, a write into ``.git/hooks``, #607): that deny names the
    form and records the event ``kb_read_gate.hook_skip``.
  - **Grep / Glob**: a search inside ``knowledge-base/`` or
    ``action-items/context/``, or over the whole vault (no path, or the vault
    root, with no file filter or one that reaches the KB), is denied.
  - **Read** stays allowed (Edit needs it, on any Claude Code version): the
    ``kb-read-label`` PostToolUse hook (:mod:`scout.hooks.kb_read_label`)
    tells the session the note's privacy label instead. Only a reader that
    ``scoutctl kb`` shows nothing (an ``agent:*`` reader, #453 v1) is denied a
    Read of a KB note. A ``delegate:<id>`` reader (#550 §4.1) is denied a Read
    of any vault file ``scoutctl kb`` wouldn't show it, KB note or not (trading,
    ``.scout-inbox``, ``.scout-cache``, ``.scout-logs`` included), and of a
    missing one alike; hooks.json sends every delegate Read here. ``knowledge-base/ontology/schema.yaml`` and
    ``parser.py`` are tooling, not notes, and are never gated.

Scope: only scheduled runs, by #517's rule (:func:`scout.kb.reader.is_scheduled`:
``SCOUT_MODE`` set, or a reader other than exactly ``owner``), and only in a
Scout vault (``SCOUT_DATA_DIR``, else the vault containing the session's cwd).
The owner's interactive sessions and sessions in other repos are never gated.
hooks.json checks the run and the KB words in the payload in the shell first,
so Python only starts for a run's call that may touch the KB (and every
Grep/Glob, which can search the vault with no path at all), and for a Read only
when the reader is neither ``owner`` nor ``autonomous``.

In a run this fails closed: a payload that can't be read, or a gate that
errors, denies the call. Outside a run it never denies.

The classifier (:mod:`scout.kb.raw_read`) is shared with the eval harness's ``privacy_hygiene`` check, so
what the gate denies and what the eval counts are one definition.
"""

from __future__ import annotations

import glob
import json
import os
import re
import signal
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from scout import paths
from scout.kb.live_run import _DIGIT_PARAMS, _SUBST_NAME, _VAR
from scout.kb.raw_read import KB_DIRS, filter_reaches_kb, is_kb_note_path, is_kb_path, raw_kb_read

GATE = "kb-read-gate"
MARK = f"[scout:{GATE}]"
# Claude Code's file-writing tools: a run's write into hook or agent config is denied (scout.kb.hook_paths).
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_REPLACEMENT = (
    "Read the KB through `scripts/scoutctl kb` instead: `kb search <words>`, `kb files [dir]`, "
    "`kb read <path>`, `kb resolve <name>`, `kb name-lookup <name>`, `kb delta` (in place of git log/diff). "
    "`kb read <path> --lines A:B` (or `:B`, `A:`, `-N:`) in place of sed -n, head and tail; "
    "`kb stat <path>…` or `kb stat --glob '<glob>'` (bytes, lines, words) in place of wc, ls -l and stat. "
    "`.scout-cache/session-context.json` already lists personal tasks and KB file dates."
)


# --- the gate -----------------------------------------------------------------------


def session_vault(payload: Mapping[str, Any]) -> Path | None:
    """SCOUT_DATA_DIR, else the vault containing the session's cwd (the payload's, else ours), else None."""
    env = os.environ.get("SCOUT_DATA_DIR")
    if env:
        return paths.resolve_path(env)
    cwd = payload.get("cwd")
    return paths.vault_containing(cwd if isinstance(cwd, str) and cwd else os.getcwd())


def _cwd(payload: Mapping[str, Any], vault: Path) -> Path:
    cwd = payload.get("cwd")
    return paths.resolve_path(cwd) if isinstance(cwd, str) and cwd else vault


def _rel(path: Path, vault: Path) -> str | None:
    """``path`` relative to the vault (posix), or None when it is outside."""
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return None


def _in_kb(path: Path, vault: Path) -> bool:
    rel = _rel(path, vault)
    return rel is not None and is_kb_path(rel)


def _covers_kb(path: Path, vault: Path) -> bool:
    """A search rooted here reaches the KB: it is the vault, a folder above it, or a KB parent."""
    rel = _rel(path, vault)
    if rel is None:
        return vault.is_relative_to(path)
    return rel == "." or any(d.startswith(rel + "/") for d in KB_DIRS)


def _target(raw: str, cwd: Path) -> Path:
    p = Path(raw).expanduser()
    return paths.resolve_path(p if p.is_absolute() else cwd / p)


def _deny_bash() -> str:
    return (
        f"{MARK} Scheduled runs don't read knowledge-base/ or action-items/context/ with shell commands "
        f"(cat, grep, ls, find, head, tail, sed, awk, wc, git show …), directly or through a variable, a loop, "
        f"xargs or a recursive search from the vault: that skips the privacy labels. {_REPLACEMENT} "
        "To change a note, Read it and Edit it. To search other folders, name one outside the KB "
        "(e.g. `rg x scripts/`)."
    )


def _deny_search(tool: str) -> str:
    return (
        f"{MARK} Scheduled runs don't {tool} inside knowledge-base/ or action-items/context/, or across the whole "
        f"vault: that skips the privacy labels. {_REPLACEMENT} To search other folders, give {tool} that folder "
        "as its path (or a file filter outside the KB)."
    )


def _deny_run_env() -> str:
    return (
        f"{MARK} A scheduled run doesn't set, unset or blank SCOUT_MODE or SCOUT_READER, or clear its environment: "
        "they say this session is a scheduled run and not the owner. Commit with `scripts/git-safe-commit.sh` as "
        "usual; if a check refuses the commit, follow what it says."
    )


def _deny_home() -> str:
    return (
        f"{MARK} A scheduled run doesn't set, unset or export HOME, or set a variable whose name it builds at run "
        "time (`export $v=…`, `read $v`): what `~` and `$HOME` name decides which files this gate protects. Use the "
        "paths as they are, and name the variables you set; Scout's commands find their own folders."
    )


def _deny_detach() -> str:
    return (
        f"{MARK} A scheduled run runs its commands in the foreground: it doesn't background them (a trailing &) or "
        "hand them to another process (nohup, setsid, disown, launchctl, osascript, open, tmux, screen, script, at, "
        "batch, ssh). Run the command directly."
    )


def _deny_delegate_write() -> str:
    return (
        f"{MARK} A scheduled run doesn't link, unlink or re-trust the owner's agents' mirrors, change their deploy "
        "keys (`scoutctl delegate link|unlink|key|trust-state`), or pull, apply, revert or review their "
        "inboxes (`scoutctl inbox pull|apply|revert|sync|review`): those are the owner's, or the schedule "
        "tick's own step between runs. `scoutctl delegate sync` and `status` are fine. If one of these is "
        "needed, say so in the run notes."
    )


def _deny_hook_skip(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't skip or redirect git's commit hooks ({form}): their checks are what keep "
        "private and shared notes apart. Commit the normal way: `scripts/git-safe-commit.sh` in the vault, plain "
        "`git commit` in any other repo. If a hook refuses the commit, fix what it reports; if you can't, leave the "
        "file uncommitted and say so in the run notes."
    )


def _deny_agent_settings(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't write the agent's settings or hook config ({form}): those files decide "
        "which of Scout's checks run, for this run and every later one. Leave them as they are; if they need a "
        "change, say so in the run notes for the owner."
    )


def _deny_engine(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't change {form}, which start each run and run Scout's checks. If something "
        "there needs a change, say so in the run notes for the owner; engine changes go through the engine repo."
    )


def _deny_git_dir(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't write into the vault's git folder or git's own files ({form}) with a file "
        "tool or the shell: git's hooks and config live there. Use git commands, and commit with "
        "`scripts/git-safe-commit.sh`."
    )


def _deny_startup(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't write {form} (the shell's login and rc files, LaunchAgents): they run "
        "outside Scout's checks. If one needs a change, say so in the run notes for the owner."
    )


def _deny_hard_link(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't write {form}: another name for the same file may be one Scout's checks "
        "depend on. Write a new file instead."
    )


def _deny_too_many(form: str) -> str:
    return (
        f"{MARK} This shell command names more files than Scout's gate checks in one call ({form}). Split it into "
        "smaller commands."
    )


def _deny_unresolved(form: str) -> str:
    return (
        f"{MARK} In a scheduled run, a shell command writes only paths Scout's gate can read: this one writes "
        f"{form} (a command substitution, a backtick, or a variable set elsewhere). Write the path out in full, "
        "or set the variable in the same command (`d=notes; echo x > $d/a.md`)."
    )


def _deny_inline_write(form: str) -> str:
    return (
        f"{MARK} This command runs {form}: a program that writes files and names a file that decides which of "
        "Scout's checks run. Write ordinary files with the Write or Edit tool or a plain shell command; leave those "
        "files as they are and say so in the run notes if one needs a change."
    )


def _deny_secrets(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't read or change {form} (~/.scout-secrets): API keys and tokens. The "
        "scoutctl commands that need a key read it themselves; Telegram's token and chat id are the exception."
    )


def _deny_delegate_state(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't change {form} (.scout-state/delegate-mirrors.json, "
        ".scout-state/delegate-scan/, .scout-state/delegate-inbox/): they decide what each delegate's mirror "
        "holds and which of its proposals were taken. Use `scoutctl delegate` or `scoutctl inbox`, outside a run."
    )


def _deny_telegram_inbound(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't change {form} (.scout-state/telegram-inbound/): it records which of the "
        "owner's Telegram messages arrived and which were answered, and only the 5-minute tick and Scout's Telegram "
        "send (trusted code) write it, outside a run. Read the messages with `scoutctl telegram pending --json`; a "
        "reply sent with `scoutctl notify telegram --reply-to` marks them answered."
    )


def _deny_agent_text(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't read or change {form} (.scout-inbox/agents/): what the owner's agents "
        "proposed is untrusted text, which only the owner reviews (`scoutctl inbox review`). What they changed "
        "is in pre-session data's `delegate_changes`."
    )


def _deny_agent_notes(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't create, change or move {form} (knowledge-base/from-agents/): they hold "
        "the owner's agents' text exactly as the owner accepted it, and only `scoutctl inbox review` writes them. "
        "Read them (they are marked [from-agent]); put anything new in another note."
    )


def _deny_title_rewrites(form: str) -> str:
    return (
        f"{MARK} A scheduled run doesn't change {form} (.scout-state/title-rewrites.jsonl): they decide which "
        "edits keep an action item's 60-day stale clock. Only the owner's `scoutctl action-items retitle` writes them."
    )


def _deny_skip_form(form: str) -> str:
    from scout.kb import hook_paths as hp

    by_form = {
        hp.TOO_MANY: _deny_too_many,
        hp.UNRESOLVED: _deny_unresolved,
        hp.INLINE_WRITE: _deny_inline_write,
        hp.SECRETS: _deny_secrets,
        hp.DELEGATE_STATE: _deny_delegate_state,
        hp.TELEGRAM_INBOUND: _deny_telegram_inbound,
        hp.TITLE_REWRITES: _deny_title_rewrites,
        hp.AGENT_TEXT: _deny_agent_text,
        hp.AGENT_NOTES: _deny_agent_notes,
        hp.AGENT_SETTINGS: _deny_agent_settings,
        hp.ENGINE: _deny_engine,
        hp.RUNNER_SCRIPTS: _deny_engine,
        hp.STARTUP: _deny_startup,
        hp.HARD_LINK: _deny_hard_link,
        hp.GIT_DIR: _deny_git_dir,
    }
    return by_form.get(form, _deny_hook_skip)(form)


# Audit rows wait here until the decision is out (main prints the deny first): writing a log can never delay it.
_PENDING_AUDIT: list[tuple[Path, str, str | None]] = []


def _record_hook_skip(vault: Path, form: str, mode: str | None) -> None:
    """Queue the form and the run's mode, never the command: its text can quote a note."""
    _PENDING_AUDIT.append((vault, form, mode))


def flush_audit() -> None:
    """Write the queued rows, best effort: the event writer refuses anything but a regular file (scout.events)."""
    from scout.events import append_event

    while _PENDING_AUDIT:
        vault, form, mode = _PENDING_AUDIT.pop(0)
        try:
            append_event(
                paths.logs_dir(vault),
                kind="kb_read_gate.hook_skip",
                source=f"hook:{GATE}",
                payload={"form": form, "mode": mode},
            )
        except Exception:  # noqa: BLE001, S110 - the deny stands without the event
            pass


def _deny_delegate_read() -> str:
    return f"{MARK} This delegate session may not read that file. {_REPLACEMENT}"


def _deny_read() -> str:
    return f"{MARK} This session's reader may not read knowledge-base/ or action-items/context/ files. {_REPLACEMENT}"


def decide(
    payload: Mapping[str, Any], *, reader: str, mode: str | None = None, env: Mapping[str, str] | None = None
) -> str | None:
    """The deny reason for one run's tool call, or None to allow it. May raise: the caller fails closed."""
    env = os.environ if env is None else env
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        raise ValueError("not a tool-call payload")
    if tool in WRITE_TOOLS:
        return _decide_write(payload, tool, tool_input, mode, env)
    if tool not in ("Bash", "Read", "Grep", "Glob"):
        return None
    vault = session_vault(payload)
    if tool != "Bash":
        given = payload.get("cwd")
        here = _cwd(payload, vault) if vault is not None else Path(given if isinstance(given, str) and given else ".")
        secret_form = _secret_tool_read(tool, tool_input, here, env)
        if secret_form is not None:
            if vault is not None:
                _record_hook_skip(vault, secret_form, mode)
            return _deny_skip_form(secret_form)
    if vault is None:
        return None  # not a Scout vault: nothing here is the KB
    cwd = _cwd(payload, vault)
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            raise ValueError("Bash call without a command")
        from scout.kb import hook_paths
        from scout.kb.live_run import DELEGATE_FORM, HOME_FORM, expansion_escape, run_escape

        def resolve(raw: str) -> str | None:
            """The path a word names, with the environment's variables put in; None when the shell fills in
            part of it the gate can't know (a substitution, a variable neither the command nor the run sets)."""
            text = _DIGIT_PARAMS.sub("0", _VAR.sub(lambda m: _env_value(m, env), raw))
            if "$" in text or "`" in text:
                return None
            expanded = hook_paths._expand(text, env)
            return expanded if os.path.isabs(expanded) else str(cwd / expanded)

        def protect(raw: str, removes: bool) -> str | None:
            target = resolve(raw)
            if target is None:
                return hook_paths.UNRESOLVED  # round 3, must-fix 1: a run writes only paths the gate can read
            check = hook_paths.removes_protected if removes else hook_paths.protected_write
            # A glob writes (or removes) whatever it matches: judge each match, and the literal spelling.
            targets = _glob_targets(target)
            if targets is None:
                return hook_paths.TOO_MANY  # fail closed: a hook that times out allows the call
            for one in targets:
                found = check(one, vault, env)
                if found is not None:
                    return found
            return None

        def secret(raw: str) -> str | None:
            target = resolve(raw)
            if target is None:
                return None
            # A glob reads whatever it matches: judge each match, and the literal spelling (`.scout-in*/agents/*`).
            targets = _glob_targets(target)
            if targets is None:
                return hook_paths.TOO_MANY  # fail closed: a hook that times out allows the call
            for one in targets:
                found = hook_paths.secret_read(one, env) or hook_paths.agent_text_read(one, vault, env)
                if found is not None:
                    return found
            return None

        escape = expansion_escape(command) or run_escape(command, protect=protect, secret=secret)
        if escape == "env":
            return _deny_run_env()
        if escape == HOME_FORM:
            return _deny_home()
        if escape == DELEGATE_FORM:
            return _deny_delegate_write()
        if escape == "detach":
            return _deny_detach()
        if escape is not None:
            _record_hook_skip(vault, escape, mode)
            return _deny_skip_form(escape)
        flagged = raw_kb_read(command, in_kb=_in_kb(cwd, vault), cwd=str(cwd), vault=str(vault), env=env)
        return _deny_bash() if flagged else None
    if tool in ("Grep", "Glob"):
        raw = tool_input.get("path")
        root = _target(raw, cwd) if isinstance(raw, str) and raw else cwd
        if _in_kb(root, vault) or (_covers_kb(root, vault) and filter_reaches_kb(tool, tool_input)):
            return _deny_search(tool)  # the KB's own rule speaks first: it covers the copies too
        return _deny_agent_text_read(tool, tool_input, cwd, vault, env, mode)
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        raise ValueError("Read call without a file_path")
    target = _target(raw, cwd)
    agent_deny = _deny_agent_text_read(tool, tool_input, cwd, vault, env, mode)
    if agent_deny is not None:
        return agent_deny
    from scout.kb.reader import delegate_id

    if delegate_id(reader) is not None:
        return _delegate_read(vault, target, reader)
    rel = _rel(target, vault)
    if rel is None or not is_kb_note_path(rel):
        return None
    from scout.kb.privacy import FULL_READERS

    return None if reader in FULL_READERS else _deny_read()


GLOB_BUDGET_S = 2.0  # how long a glob in a command may take to expand; the hook itself times out at 15 s
GLOB_CAP = 256  # matches judged per glob; more refuses the call
GLOB_WILD_PARTS = 3  # wildcard path components a glob may have before it is refused unexpanded


def _glob_targets(target: str) -> list[str] | None:
    """The literal spelling and every match of ``target``, or None when it can't be judged in time: more than
    :data:`GLOB_WILD_PARTS` wildcard components (``/*/*/*/*/*/zz`` walks the whole disk without matching), more
    than :data:`GLOB_CAP` matches, or longer than :data:`GLOB_BUDGET_S` to expand. None is a refusal: a hook that
    runs past its timeout lets the call through, so the gate must never wait that long."""
    if not glob.has_magic(target):
        return [target]
    if sum(1 for part in target.split("/") if glob.has_magic(part)) > GLOB_WILD_PARTS:
        return None
    import itertools
    import threading

    found: list[str] = []
    over = threading.Event()

    def expand() -> None:
        for match in itertools.islice(glob.iglob(target), GLOB_CAP + 1):
            found.append(match)
        if len(found) > GLOB_CAP:
            over.set()

    worker = threading.Thread(target=expand, daemon=True)  # a walk that never yields can't hold the hook
    worker.start()
    worker.join(GLOB_BUDGET_S)
    if worker.is_alive() or over.is_set():
        return None
    return [target, *sorted(found)]


def _env_value(m: re.Match[str], env: Mapping[str, str]) -> str:
    """A ``$NAME`` live_run left as written: the run's environment value, when it has one."""
    name = m[1] or m[2]
    return env[name] if name != _SUBST_NAME and name in env else m[0]


def _secret_tool_read(tool: str, tool_input: Mapping[str, Any], cwd: Path, env: Mapping[str, str]) -> str | None:
    """A run's Read of one of Scout's secrets, or a Grep/Glob that searches them (the Telegram two excepted)."""
    from scout.kb import hook_paths

    def full(raw: str) -> str:
        expanded = hook_paths._expand(raw, env)
        return expanded if os.path.isabs(expanded) else str(cwd / expanded)

    if tool == "Read":
        raw = tool_input.get("file_path")
        return hook_paths.secret_read(full(raw), env) if isinstance(raw, str) and raw else None
    raw = tool_input.get("path")
    root = full(raw) if isinstance(raw, str) and raw else str(cwd)
    if hook_paths.secret_read(root, env) is not None:
        return hook_paths.SECRETS
    filters = " ".join(str(tool_input.get(k) or "") for k in ("glob", "pattern"))
    if hook_paths.holds_secrets(root, env) and "scout-secrets" in filters.casefold():
        return hook_paths.SECRETS
    return None


# What a Grep/Glob filter must not be able to match: a copy, its crash-temp, or a ledger, as paths under the
# searched folder would read. A filter is judged by matching these, never by spelling (``*.js?n`` is ``*.json``).
_AGENT_TEXT_SAMPLES = (
    ".scout-inbox/agents/d/01JD5Z0000000000000000ABCD.json",
    ".scout-inbox/agents/d/.01JD5Z0000000000000000ABCD.json.1.tmp",
    ".scout-inbox/agents/d/taken.ledger",
    "agents/d/01JD5Z0000000000000000ABCD.json",
    "d/01JD5Z0000000000000000ABCD.json",
    "01JD5Z0000000000000000ABCD.json",
    "taken.ledger",
)


def _filter_reaches_copies(pattern: str) -> bool:
    import fnmatch

    from scout.kb.lint_config import glob_match

    if re.search(r"inbox|agents", pattern, re.IGNORECASE):
        return True
    for one in _braces(pattern):  # `*.{json,ledger}`: Claude Code's Glob expands braces
        for sample in _AGENT_TEXT_SAMPLES:
            name = sample.rsplit("/", 1)[-1]
            if fnmatch.fnmatch(sample, one) or fnmatch.fnmatch(name, one) or glob_match(one, sample):
                return True
    return False


def _braces(pattern: str, limit: int = 64) -> list[str]:
    """``a{b,c}d`` as ``abd`` and ``acd``, nested braces included, at most ``limit`` alternatives (the rest are
    dropped: a filter with more than that is judged by what fits, the first ones)."""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if m is None:
        return [pattern]
    out: list[str] = []
    for alt in m[1].split(","):
        out += _braces(pattern[: m.start()] + alt + pattern[m.end() :], limit)
        if len(out) >= limit:
            break
    return out[:limit]


def _deny_agent_text_read(
    tool: str, tool_input: Mapping[str, Any], cwd: Path, vault: Path, env: Mapping[str, str], mode: str | None
) -> str | None:
    form = _agent_text_tool_read(tool, tool_input, cwd, vault, env)
    if form is None:
        return None
    _record_hook_skip(vault, form, mode)
    return _deny_skip_form(form)


def _agent_text_tool_read(
    tool: str, tool_input: Mapping[str, Any], cwd: Path, vault: Path, env: Mapping[str, str]
) -> str | None:
    """A run's Read of an agent's copy, or a Grep/Glob that searches the copies (spec §7.1b: agent text never
    reaches a run). A search from a folder above them counts when it has no filter or one that could reach them."""
    from scout.kb import hook_paths

    def full(raw: str) -> str:
        expanded = hook_paths._expand(raw, env)
        return expanded if os.path.isabs(expanded) else str(cwd / expanded)

    if tool == "Read":
        raw = tool_input.get("file_path")
        return hook_paths.agent_text_read(full(raw), vault, env) if isinstance(raw, str) and raw else None
    raw = tool_input.get("path")
    root = full(raw) if isinstance(raw, str) and raw else str(cwd)
    if hook_paths.agent_text_read(root, vault, env) is not None:
        return hook_paths.AGENT_TEXT
    if not hook_paths.holds_agent_text(root, vault, env):
        return None
    pattern = str(tool_input.get("pattern" if tool == "Glob" else "glob") or "").strip()
    if (tool == "Grep" and not pattern) or _filter_reaches_copies(pattern):
        return hook_paths.AGENT_TEXT
    return None


def _decide_write(
    payload: Mapping[str, Any], tool: str, tool_input: Mapping[str, Any], mode: str | None, env: Mapping[str, str]
) -> str | None:
    """A run's file write into what decides which hooks run (Phase 1b; scout.kb.hook_paths) is denied."""
    from scout.kb.hook_paths import _expand, protected_write

    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    raw = tool_input.get(key)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{tool} call without a {key}")
    vault = session_vault(payload)
    if vault is not None:
        cwd = _cwd(payload, vault)
    else:
        given = payload.get("cwd")
        cwd = paths.resolve_path(given if isinstance(given, str) and given else os.getcwd())
    p = Path(_expand(raw, env))
    form = protected_write(p if p.is_absolute() else cwd / p, vault, env, file_tool=True)  # resolves symlinks itself
    if form is None:
        return None
    if vault is not None:
        _record_hook_skip(vault, form, mode)
    return _deny_skip_form(form)


def _vault_rel(vault: Path, target: Path) -> str | None:
    """``target``'s canonical vault-relative path, ``"."`` for the vault itself, None when outside it.

    Decided by file identity, not spelling: on a case-insensitive disk ``/Users/x/SCOUT/Trading/…``
    is inside ``/Users/x/Scout``, so the ancestor that is the vault is found with ``samefile``, and
    the rest takes each existing component's on-disk name (``normalize_rel``), so ``.GIT`` is ``.git``.
    """
    from scout.kb.privacy import normalize_rel

    for anc in (target, *target.parents):
        try:
            if os.path.samefile(anc, vault):
                tail = target.relative_to(anc).parts
                break
        except OSError:
            continue
    else:
        return None
    if not tail:
        return "."
    try:
        return normalize_rel(vault, vault.joinpath(*tail))
    except ValueError:
        return "."


def _delegate_read(vault: Path, target: Path, reader: str) -> str | None:
    """A delegate (#550 §4.1) Reads only a vault file ``scoutctl kb`` would show it, KB note or not.

    The vault itself, ``.git/``, a missing file and a hidden one get the same deny, so a deny
    never says whether a file exists. A path outside the vault isn't this gate's to decide.
    """
    from scout.kb.privacy import label_file, load_policy, visible_to

    rel = _vault_rel(vault, target)
    if rel is None:
        return None
    if rel == "." or ".git" in rel.split("/") or not (vault / rel).is_file():
        return _deny_delegate_read()
    policy = load_policy(vault)
    return None if visible_to(label_file(vault, rel, policy), reader, policy) else _deny_delegate_read()


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def run(
    *, stdin: IO[str] | None = None, env: Mapping[str, str] | None = None, defer_audit: bool = False
) -> dict[str, Any] | None:
    """Read one PreToolUse payload; return the deny decision, or None to allow.

    The audit rows a deny queues are written before returning, unless ``defer_audit`` (main: it prints the
    decision first, then calls :func:`flush_audit`).
    """
    try:
        return _run(stdin=stdin, env=env)
    finally:
        if not defer_audit:
            flush_audit()


def _run(*, stdin: IO[str] | None, env: Mapping[str, str] | None) -> dict[str, Any] | None:
    from scout.kb.reader import is_scheduled, resolve_reader

    env = os.environ if env is None else env
    if not is_scheduled(env):
        return None  # the owner's own session: never gated
    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        reader = resolve_reader(None, env)
        reason = decide(payload, reader=reader, mode=env.get("SCOUT_MODE") or reader, env=env)
    except Exception as exc:  # noqa: BLE001 — a run fails closed
        reason = (
            f"{MARK} Scout's KB read gate could not check this call ({type(exc).__name__}), so it is blocked in "
            f"this scheduled run. {_REPLACEMENT}"
        )
    return None if reason is None else _deny(reason)


# Below every kb-read-gate / kb-write-gate matcher's timeout in hooks.json (15 s; a test keeps it so). Claude Code
# treats a hook that times out as an allow, so a gate a run makes slow (hundreds of paths, a git that blocks on a
# FIFO) must deny before that happens, and so must a hang after the decision was printed.
WATCHDOG_S = 8


def _on_watchdog(_signum: int, _frame: object) -> None:
    os.write(2, f"{MARK} Scout's gate took too long to check this call, so it is blocked.\n".encode())
    os._exit(2)


def main() -> int:
    """CLI entry point: scoutctl hook kb-read-gate (and kb-write-gate). The decision goes out before any audit."""
    armed = hasattr(signal, "SIGALRM")
    previous = signal.signal(signal.SIGALRM, _on_watchdog) if armed else None
    if armed:
        signal.alarm(WATCHDOG_S)
    try:
        decision = run(defer_audit=True)
        if decision is not None:
            sys.stdout.write(json.dumps(decision) + "\n")
            sys.stdout.flush()
        flush_audit()  # still under the watchdog: a hang here ends in a block, not a timeout
    finally:
        if armed:  # disarmed on the way out, so a caller in the same process (a test) isn't killed later
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
    return 0
