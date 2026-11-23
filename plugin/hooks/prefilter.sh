#!/bin/sh
# Scout hook pre-filters: the shell stage of every tool hook in hooks.json.
#
#   /bin/sh prefilter.sh <filter> <program> [args...]
#
# hooks.json calls this once per matching tool call. <filter> names one block of the `case` at the
# bottom; <program> [args...] is the Python stage (`.../engine/bin/scoutctl hook <name>`), which this
# script starts only when the filter cannot decide by itself. The tool call's JSON payload is on stdin.
#
# Why a shell stage at all: starting Python costs about 40 ms, and these hooks fire on every tool call
# of every Claude Code session on the machine. An owner's interactive session, and a scheduled run's
# harmless call, must leave here without starting it.
#
# What that makes this file: a security control. For a scheduled run, a call this script lets through
# is a call the Python gate never judges. So:
#   - Widening a pattern is safe (Python decides; it costs time). Narrowing one opens a hole.
#   - When the Python stage of a gate cannot run, a run's call is blocked (exit 2), never allowed.
#   - The hooks.json entry guards this script in turn: if the file is missing it starts the Python
#     stage directly, and if a gate's filter ends with anything but 0 or 2 (a syntax error, a kill)
#     it blocks the run's call. So a filter here may only ever exit 0 on purpose.
#   - tests/unit/test_hook_prefilter_differential.py pins the decisions against the inline commands
#     this file replaced. Change a pattern on purpose, and say so there.
#
# Rules for editing (tests/unit/test_relative_path_entries.py checks them):
#   - POSIX sh only: /bin/sh is dash on Linux and bash 3.2 in POSIX mode on macOS.
#   - The PATH filter below runs before any external command. Hooks run in any cwd (a cloned repo,
#     the vault), and a relative or empty PATH entry would let a planted grep, cat or tr run as the
#     user. Nothing is looked up in the working directory; no dirname (parameter expansion instead).
#   - Exit codes are the hook protocol: 0 allow or done, 2 block (PreToolUse), anything else is a
#     non-blocking error.

# >>> path-filter
# Keep only absolute PATH entries. A leading `~` becomes $HOME, as the shell's own PATH search reads
# it. An empty result falls back to the system dirs. `set -f` keeps glob characters literal.
sp=; oi=$IFS; IFS=:; set -f; for pe in $PATH; do case "$pe" in "~"|"~/"*) [ -z "$HOME" ] || pe="$HOME${pe#"~"}" ;; esac; case "$pe" in /*) sp="${sp:+$sp:}$pe" ;; esac; done; set +f; IFS=$oi; PATH="${sp:-/usr/bin:/bin}"; export PATH
# <<< path-filter

# A scheduled run, by #517's rule: SCOUT_MODE set, or SCOUT_READER set to anything but exactly `owner`.
is_run() {
    [ -n "$SCOUT_MODE" ] || { [ -n "${SCOUT_READER+set}" ] && [ "$SCOUT_READER" != owner ]; }
}

# Sets $d to the vault: SCOUT_DATA_DIR, else the nearest directory at or above the cwd that holds
# scout-config.yaml, else `/`. `${d%/*}` in place of dirname.
find_vault() {
    d="$SCOUT_DATA_DIR"
    if [ -z "$d" ]; then
        d="$PWD"
        case "$d" in /*) ;; *) d=/ ;; esac
        while [ "$d" != / ] && [ ! -f "$d/scout-config.yaml" ]; do
            d="${d%/*}"
            d="${d:-/}"
        done
    fi
}

# Adds one alternative to $re.
alt() { re="${re:+$re|}$1"; }

# Sets $re: the extended regex that sends a run's Bash call to Python. It is matched against the
# payload's `"command": "..."` value as JSON spells it, so a newline or tab in the command is the two
# characters `\n` or `\t` (hence `\\[nt]` beside the other word boundaries) and a double quote is `\"`.
# One block per concern. The order of alternatives does not matter to grep -q; it is kept as it was
# in the inline command so the differential test can compare the assembled string.
bash_pattern() {
    re=
    # Word boundaries. `word`: start of the command, or after a character that cannot be part of a
    # program name. `word_or_path` also refuses `/` on either side, so a path component is not the
    # program (and a launcher called by its full path is not matched by that group).
    word='(^|[^A-Za-z0-9_.-]|\\[nt])'
    word_or_path='(^|[^A-Za-z0-9_./-]|\\[nt])'

    # -- Run identity: any mention of the variables that say "this is a run" (#595).
    alt 'SCOUT_(MODE|READER)'

    # -- A glob that could expand to a KB path without spelling it (kno*, *-base, action-items/c*).
    alt '(kno|ledge|-base|_base)[^ ;&|]*[*?[]'
    alt '[*?[][^ ;&|/]*(ledge|-base|_base)'
    alt 'action-items/[^ ;&|/]*[*?[]'

    # -- `scoutctl kb` output fed onward as a list of paths: to xargs or a read loop, ...
    alt 'scoutctl kb .*(xargs|while |mapfile|readarray)'
    alt '(for [A-Za-z_][A-Za-z0-9_]* in |mapfile|readarray).*scoutctl kb '

    # -- Recursive reads, which reach the KB from any directory above it.
    alt "$word"'(rg|find|tree|du)([^A-Za-z0-9_./-]|$)'
    alt "$word"'[ef]?grep[^;&|]* (-[A-Za-z]*[rR]|--(dereference-)?recursive|-d *recurse|--directories=recurse)'
    alt 'git[^;&|]* grep'

    # -- ... or `scoutctl kb` inside a command substitution.
    alt '(\$\(|`)[^;]*scoutctl kb '

    # -- Recursive ls.
    alt "$word"'ls[^;&|]* -[A-Za-z]*R'

    # -- Detaching from the run, or changing its environment through a launcher (#595).
    alt "$word_or_path"'(env|nohup|setsid|disown|launchctl|osascript|open|tmux|screen|script|at|batch|ssh|mosh|systemd-run)([^A-Za-z0-9_./-]|$)'
    alt '(^|[^&>|])&([^&>]|$)' # a lone & (not &&, >&, &>, |&)

    # -- Skipping or redirecting the commit hooks (#607).
    alt '--no-v'
    alt '--config-env'
    alt 'GIT_CONFIG'
    alt '[Hh][Oo][Oo][Kk][Ss][Pp][Aa][Tt][Hh]'
    alt 'commit-tree'
    alt 'update-ref'
    alt 'fast-import'
    alt 'filter-(branch|repo)'
    alt 'hash-object'
    alt '[Aa][Ll][Ii][Aa][Ss]\.'
    alt '[Ii][Nn][Cc][Ll][Uu][Dd][Ee]([Ii][Ff])?\.'
    alt '\.git/(hooks|config)'

    # -- Any output redirection (not >&): the target may be a protected path (#637).
    alt '>([^&]|$)'

    # -- Names of what decides which hooks run: agent settings, git config, launchd, shell profiles.
    alt '[Cc][Ll][Aa][Uu][Dd][Ee]'
    alt '[Gg][Ii][Tt][^;&|]*[Cc][Oo][Nn][Ff][Ii][Gg]'
    alt '[Ll][Aa][Uu][Nn][Cc][Hh][Aa][Gg][Ee][Nn][Tt][Ss]'
    alt '\.(zsh|bash|profile|mcp)'

    # -- Programs that write, move or delete files, fetch, or run an inline program (#637).
    alt '(^|[^A-Za-z0-9_.-])(tee|rm|rmdir|unlink|mv|cp|ln|install|rsync|touch|truncate|chmod|chown|chflags|shred|dd|sed|perl|curl|wget|tar|python[0-9.]*|pypy[0-9.]*|node|nodejs|ruby|php|deno|bun)([^A-Za-z0-9_.-]|$)'

    # -- More protected paths: the user's git config, agent settings and plugin store, other agent CLIs.
    alt '\.[Gg][Ii][Tt][Cc][Oo][Nn][Ff][Ii][Gg]'
    alt '\.[Cc][Oo][Nn][Ff][Ii][Gg]/[Gg][Ii][Tt]'
    alt '\.[Cc][Ll][Aa][Uu][Dd][Ee]/([Ss][Ee][Tt][Tt][Ii][Nn][Gg][Ss]|[Pp][Ll][Uu][Gg][Ii][Nn][Ss])'
    alt '\.([Cc][Oo][Dd][Ee][Xx]|[Gg][Ee][Mm][Ii][Nn][Ii]|[Aa][Gg][Ee][Nn][Tt][Ss]|[Gg][Rr][Oo][Kk]|[Cc][Uu][Rr][Ss][Oo][Rr])([^A-Za-z0-9_-]|$)'

    # -- Scout's secrets.
    alt '[Ss][Cc][Oo][Uu][Tt]-[Ss][Ee][Cc][Rr][Ee][Tt][Ss]'
    alt 'SCOUT_SECRETS'

    # -- The delegate inbox: .scout-inbox, an agents/ folder, and the inbox subcommands that write.
    alt '[Ss][Cc][Oo][Uu][Tt]-[Ii]'
    alt '[Aa][Gg][Ee][Nn][Tt][Ss]/'
    alt 'inbox([[:space:]]|\\[nt])+(pull|apply|revert|sync|review)'

    # -- A shell that runs a quoted program or its stdin.
    alt "$word"'((ba|z|da|k)?sh|eval|source)([^A-Za-z0-9_.-]|$)'

    # -- git forms that reach the hooks another way.
    alt 'cd [^;&|]*\.git'
    alt 'git[^;&|]* -c '
    alt 'git[^;&|]*(commit|am).*([^A-Za-z0-9_-]|\\[nt])-[A-Za-z]*n' # -n as --no-verify
    alt 'git +(\\")?\$'                                             # git "$var": the subcommand is hidden
}

# The payload's command value, as JSON spells it (quotes and escapes kept).
command_value() { printf '%s' "$p" | grep -oE '"command": *"([^"\\]|\\.)*"'; }

name="${1-}"
[ "$#" -gt 0 ] && shift
filter="$name"
# Every filter but the test helper needs a program to start.
[ "$#" -gt 0 ] || [ "$name" = print-bash-pattern ] || filter=

case "$filter" in

# PreToolUse, every MCP call, every session. The restrict_tools gate: Python starts only in a vault
# whose connectors.custom.yaml might set the key: the literal text, any backslash (a double-quoted
# YAML key can spell it with escapes, #590) or a NUL byte.
mcp-tool-gate)
    find_vault
    f="$d/connectors.custom.yaml"
    [ -s "$f" ] || exit 0
    LC_ALL=C grep -qsE 'restrict_tools|\\' "$f" || ! LC_ALL=C tr -d '\000' 2>/dev/null <"$f" | cmp -s - "$f" || exit 0
    exec "$@"
    ;;

# PreToolUse, MCP write tools. Skipped only for a session that is neither a run nor in a vault.
# A run is denied when the check cannot run; the owner is warned.
outbound-gate)
    run=
    is_run && run=1
    find_vault
    [ -n "$run" ] || [ -f "$d/scout-config.yaml" ] || exit 0
    "$@" && exit 0
    if [ -n "$run" ]; then
        echo "[scout:outbound-gate] Scout's outbound privacy check could not run, so this call is blocked in a scheduled run. Put the content in the daily file or the owner's Telegram chat instead, and say so in the run notes." >&2
        exit 2
    fi
    echo "[scout:outbound-gate] Warning: Scout's outbound privacy check could not run; this call was not checked for private content." >&2
    exit 1
    ;;

# PreToolUse, Grep|Glob|Bash. Owner sessions leave at once. A run's Grep or Glob, and any payload
# that spells a KB path, go to Python. A run's other Bash call goes to Python when one of these holds:
#   1. its command value matches bash_pattern;
#   2. the payload names $SCOUT_SECRETS_DIR;
#   3. its command value spells a KB path once quotes and backslashes are removed.
kb-read-gate)
    is_run || exit 0
    p=$(cat)
    case "$p" in
    *'"Grep"'* | *'"Glob"'* | *knowledge-base* | *knowledge_base* | *action-items/context*) ;;
    *)
        bash_pattern
        command_value | grep -qE "$re" ||
            { [ -n "$SCOUT_SECRETS_DIR" ] && case "$p" in *"$SCOUT_SECRETS_DIR"*) true ;; *) false ;; esac; } ||
            command_value | tr -d "'\"\\\\" | grep -qE 'knowledge-base|knowledge_base|action-items/context' ||
            exit 0
        ;;
    esac
    printf '%s' "$p" | "$@" && exit 0
    echo "[scout:kb-read-gate] Scout's KB read gate could not run, so this call is blocked in a scheduled run. Read the KB through scripts/scoutctl kb (search, files, read, resolve)." >&2
    exit 2
    ;;

# PreToolUse, Read. $r: a reader that is neither the owner nor `autonomous` (an agent:* or
# delegate:<id> reader). $s: the payload names Scout's secrets or the delegate inbox. Python starts
# for a run when $s; for a delegate reader always (#550); for another $r reader when the payload
# names a KB path.
kb-read-gate-read)
    r=
    [ -n "${SCOUT_READER+set}" ] && [ "$SCOUT_READER" != owner ] && [ "$SCOUT_READER" != autonomous ] && r=1
    [ -n "$r" ] || is_run || exit 0
    p=$(cat)
    s=
    case "$p" in *[Ss][Cc][Oo][Uu][Tt]-[Ss][Ee][Cc][Rr][Ee][Tt][Ss]*) s=1 ;; esac
    case "$p" in *[Ss][Cc][Oo][Uu][Tt]-[Ii]* | *[Aa][Gg][Ee][Nn][Tt][Ss]/*) s=1 ;; esac
    [ -n "$SCOUT_SECRETS_DIR" ] && case "$p" in *"$SCOUT_SECRETS_DIR"*) s=1 ;; esac
    if [ -z "$s" ]; then
        [ -n "$r" ] || exit 0
        case "$SCOUT_READER" in
        [Dd][Ee][Ll][Ee][Gg][Aa][Tt][Ee]:*) ;;
        *) case "$p" in *knowledge-base* | *knowledge_base* | *action-items/context*) ;; *) exit 0 ;; esac ;;
        esac
    fi
    printf '%s' "$p" | "$@" && exit 0
    echo "[scout:kb-read-gate] Scout's KB read gate could not run, so this Read is blocked for this reader." >&2
    exit 2
    ;;

# PreToolUse, Write|Edit|MultiEdit|NotebookEdit. Every run write goes to Python: a symlink can hide
# the path from the shell.
kb-write-gate)
    is_run || exit 0
    "$@" && exit 0
    echo "[scout:kb-write-gate] Scout's write gate could not run, so this write is blocked in a scheduled run." >&2
    exit 2
    ;;

# PostToolUse, Read. Labels a KB note in a run or a vault session; a note it cannot label is private.
kb-read-label)
    p=$(cat)
    case "$p" in *knowledge-base* | *knowledge_base* | *action-items/context*) ;; *) exit 0 ;; esac
    run=
    is_run && run=1
    find_vault
    [ -n "$run" ] || [ -f "$d/scout-config.yaml" ] || exit 0
    printf '%s' "$p" | "$@" && exit 0
    printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[scout:kb-label] Scout could not label the file just read, so treat it as [private]: its content must not reach any shared surface (Slack, a connector write or query, a web query, drafts/, a back-port)."}}'
    ;;

# PostToolUse, the shared-notes tools. Records what the answer showed, in a run or a vault session.
shared-read-record)
    run=
    is_run && run=1
    find_vault
    [ -n "$run" ] || [ -f "$d/scout-config.yaml" ] || exit 0
    "$@" && exit 0
    printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[scout:shared-read] Scout could not record this answer, so check-outbound will not recognise it. Notes shared with you are private to you: keep their path, title and text off every shared surface (Slack, a connector write or query, a web query, drafts/)."}}'
    ;;

# PostToolUse, a run's Write|Edit|MultiEdit. Python starts only for a file_path shaped <dir>/<x>/<x>.md
# (a project's front page). It warns, never blocks: always exit 0.
front-page-reads)
    is_run || exit 0
    p=$(cat)
    printf '%s' "$p" | grep -qE '"file_path": *"[^"]*/([^/"]+)/\1\.md"' || exit 0
    printf '%s' "$p" | "$@"
    exit 0
    ;;

# For the tests: the assembled Bash pattern, one line.
print-bash-pattern)
    bash_pattern
    printf '%s\n' "$re"
    ;;

# An unknown filter, or no program to start: hooks.json and this file disagree. Blocked in a run.
*)
    echo "[scout:prefilter] hooks/prefilter.sh was called with an unknown filter or no program (${name:-none}); hooks.json and the script are out of step." >&2
    is_run && exit 2
    exit 1
    ;;
esac
