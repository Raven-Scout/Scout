{ # Scout hook pre-filters: the shell stage of every tool hook in hooks.json. POSIX sh.
#
#   /bin/sh prefilter.sh <filter> <program> [args...]
#
# Lint it with `shellcheck -s sh` (CI does): a shell directive would have to come before the `{`.
#
# The whole file is one `{ ... }` group, opened on line 1 and closed on the last line, and it has no
# shebang (it is only ever run as `/bin/sh <path>`). The shell reads a group whole before running any of
# it, so a copy cut off anywhere (a partial write during a plugin update) is a syntax error that runs
# nothing. With a shebang first, a copy cut to that one line would be a comment-only script.
#
# THE VERDICT PROTOCOL. A syntax error exits 2, which is also a hook's "block"; an empty, comment-only,
# NUL-filled or no-op copy exits 0, which is "allow". So neither code can be trusted from this script.
# Every filter ends with `verdict <code>`, which exits 200 + <code>; the hooks.json entry trusts only an
# exit of 200 or more, and hands Claude Code <exit> - 200. Anything else means this copy did not do its
# job, and the entry starts the Python stage itself, as when the file is missing. A copy killed by a
# signal (128 + n) may have read the payload already, so for a gate a run's call is blocked instead.
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
#   - The hooks.json entry guards this script in turn (the verdict protocol above): a copy that is
#     missing, empty or broken is bypassed for the Python stage, and one that is killed blocks a run's
#     gated call. So a filter here ends only through `verdict`, and `verdict 0` only on purpose.
#   - A tool that fails (cat, grep, tr or cmp missing or erroring: any status but 0 or 1) means
#     "cannot decide": the call goes to Python, or, when the payload itself could not be read, a run's
#     call is blocked.
#   - tests/unit/test_hook_prefilter_differential.py pins the decisions against the inline commands
#     this file replaced. Change a pattern on purpose, and say so there.
#
# Rules for editing (tests/unit/test_relative_path_entries.py checks them):
#   - POSIX sh only: /bin/sh is dash on Linux and bash 3.2 in POSIX mode on macOS.
#   - The PATH filter below runs before any external command. Hooks run in any cwd (a cloned repo,
#     the vault), and a relative or empty PATH entry would let a planted grep, cat or tr run as the
#     user. Nothing is looked up in the working directory; no dirname (parameter expansion instead).
#     The filters' own tools come from /usr/bin or /bin first (see `tool`), so a PATH without them
#     (PATH=/opt/homebrew/bin) neither breaks the filter nor changes which grep decides.
#   - Hook codes, as `verdict` takes them: 0 allow or done, 2 block (PreToolUse), anything else is a
#     non-blocking error. Never `exit` or `exec` directly.

# >>> path-filter
# Keep only absolute PATH entries. A leading `~` becomes $HOME, as the shell's own PATH search reads
# it. An empty result falls back to the system dirs. `set -f` keeps glob characters literal.
sp=; oi=$IFS; IFS=:; set -f; for pe in $PATH; do case "$pe" in \~|\~/*) [ -z "$HOME" ] || pe="$HOME${pe#"~"}" ;; esac; case "$pe" in /*) sp="${sp:+$sp:}$pe" ;; esac; done; set +f; IFS=$oi; PATH="${sp:-/usr/bin:/bin}"; export PATH
# <<< path-filter

# Ends the filter with hook exit code $1, under the verdict protocol (see the top). A code above 55
# (the Python stage killed, say) is reported as 55: still neither "allow" nor "block".
verdict() {
    if [ "$1" -le 55 ]; then exit $((200 + $1)); fi
    exit 255
}

# A scheduled run, by #517's rule: SCOUT_MODE set, or SCOUT_READER set to anything but exactly `owner`.
is_run() {
    [ -n "$SCOUT_MODE" ] || { [ -n "${SCOUT_READER+set}" ] && [ "$SCOUT_READER" != owner ]; }
}

# Sets $d to the vault: SCOUT_DATA_DIR, else the nearest directory at or above this process's cwd
# that holds scout-config.yaml, else `/`. `${d%/*}` in place of dirname. Only for a payload that could
# not be read: the Python hooks go by the payload's cwd (each_vault), which need not be this one.
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
    alt "$word"'(rg|find|tree|du|ag|ack|fd|ugrep)([^A-Za-z0-9_./-]|$)'
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

# Sets $t to program $1 by absolute path: /usr/bin or /bin first, else the first in the (filtered,
# all-absolute) PATH, else empty. Running an empty $t fails with 127, which callers read as "cannot
# decide". No subshell on the common path: [ -x ] is a builtin.
tool() {
    for t in "/usr/bin/$1" "/bin/$1"; do
        [ -x "$t" ] && return 0
    done
    t=$(command -v "$1") || t=
}

# Reads the payload into $p. Fails (non-zero) when it could not be read. With fd 0 closed, the pipe
# that $(...) opens would take fd 0 and cat would read its own output pipe forever, so a closed
# stdin is refused first. Duplicating fd 0 to fd 9 fails when it is closed (`<&0` onto itself is a
# no-op that always succeeds); `true`, not `:`, since a redirection error on a special builtin ends
# the shell.
read_payload() {
    { true 9<&0; } 2>/dev/null || return 1
    tool cat
    p=$("$t")
}

# Exit status of `grep -qE -e <pattern>` over $v: 0 match, 1 no match, anything else an error.
matches() {
    printf '%s' "$v" | "$GREP" -qE -e "$1"
}

# Sets $v to the payload's command value, as JSON spells it (quotes and escapes kept). Returns grep's
# status: 1 (and an empty $v) when the payload has no command, above 1 when grep failed.
command_value() {
    v=$(printf '%s' "$p" | "$GREP" -oE '"command": *"([^"\\]|\\.)*"')
}

# Returns 0 when a run's Bash call must go to Python. Any tool error answers yes.
bash_needs_python() {
    tool grep
    GREP=$t
    command_value
    [ "$?" -gt 1 ] && return 0
    bash_pattern
    matches "$re"
    [ "$?" -ne 1 ] && return 0
    if [ -n "$SCOUT_SECRETS_DIR" ]; then
        case "$p" in *"$SCOUT_SECRETS_DIR"*) return 0 ;; esac
    fi
    tool tr
    # shellcheck disable=SC1003 # the set is ', " and \ (one backslash after the shell's escaping)
    v=$(printf '%s' "$v" | "$t" -d "'\"\\\\") || return 0
    matches 'knowledge-base|knowledge_base|action-items/context'
    [ "$?" -ne 1 ]
}

# Sets $w to the nearest directory at or above $1 that holds scout-config.yaml; fails when there is
# none. `${w%/*}` in place of dirname.
walk_up() {
    w=$1
    while [ ! -f "$w/scout-config.yaml" ]; do
        [ "$w" = / ] && return 1
        w="${w%/*}"
        w="${w:-/}"
    done
}

# Runs `$1 <vault>` for each vault the session of payload $p may be in, by the rule every Python hook
# goes by (session_vault in each, _may_be_in_a_vault in engine/scout/hooks/__main__.py): SCOUT_DATA_DIR,
# else the nearest scout-config.yaml at or above the payload's cwd, never this process's own cwd.
# Returns 0 ("start Python") as soon as $1 does, or when the shell cannot tell for certain; 1 when no
# vault needs Python.
#
# What the shell cannot tell for certain (unknown stays closed): a SCOUT_DATA_DIR that is not absolute;
# no top-level "cwd" key (Python then goes by its own working directory), or one the shell cannot prove
# is top-level; a cwd that is not a string, is empty or relative (`~` included), holds a JSON escape,
# is longer than 4096 bytes, or names a directory the shell cannot enter; a grep that fails.
#
# Cost: this runs on every MCP call on the machine, nearly all outside any vault, so the common case
# starts no process beyond read_payload's cat: Claude Code's own payload shape is read by builtins
# (claude_code_cwd). Anything else goes to the grep below, a fork and an exec more.
#
# The shell does not parse JSON, so one grep finds two things. Its first expression matches only from
# the payload's start: `{`, then top-level keys with scalar values, then "cwd" and its value. That line
# comes first when it is there (Claude Code puts cwd before tool_input), and proves a top-level cwd.
# The second matches any other "cwd" key: the filter checks them all (a tool input may have its own),
# each as written and with its symlinks resolved. Everything after the grep is builtins on short lines:
# bash 3.2's ${x#*pat} is quadratic, so it never runs over the payload itself. A symlink is resolved by
# `cd -P` in this shell and back, not in a subshell: this runs on every MCP call on the machine.
each_vault() {
    if [ -n "$SCOUT_DATA_DIR" ]; then
        case "$SCOUT_DATA_DIR" in /*) ;; *) return 0 ;; esac
        "$1" "$SCOUT_DATA_DIR"
        return
    fi
    if claude_code_cwd; then
        cwd_vault "$1"
        return
    fi
    tool grep
    js='"([^"\\]|\\.)*"'
    jw='[[:space:]]*'
    cws=$(printf '%s' "$p" | LC_ALL=C "$t" -oE \
        -e '^'"$jw"'[{]('"$jw$js$jw:$jw"'('"$js"'|[-+.0-9eE]+|true|false|null)'"$jw"',)*'"$jw"'"cwd"'"$jw:$jw($js|.)" \
        -e '"cwd"'"$jw:$jw($js|.)") || return 0
    top=
    while IFS= read -r cw; do
        [ "${#cw}" -le 4096 ] || return 0
        case "$cw" in
        \"*) [ -n "$top" ] || return 0 ;; # the first line is not the top-level one: no top-level cwd
        *) [ -z "$top" ] || return 0 ;;   # cannot happen in JSON
        esac
        top=1
        cw="${cw##*\"cwd\"}"
        cw="${cw#*:}"
        cw="${cw#"${cw%%[![:space:]]*}"}"
        case "$cw" in \"*\") ;; *) return 0 ;; esac
        cw="${cw#\"}"
        cw="${cw%\"}"
        cwd_vault "$1" && return 0
    done <<EOF
$cws
EOF
    return 1
}

# The fast path of each_vault, all builtins: sets $cw to the payload's top-level cwd when the payload is
# one line that starts as Claude Code writes it, `{"session_id":"…","transcript_path":"…","cwd":"…"` (any
# two string-valued keys before cwd, with or without spaces after `:` and `,`), and holds no other
# "cwd" key. Fails, for the grep above to decide, on anything else. A backslash in a key or value
# before cwd may hide an escaped quote, so it fails too. `read` from a here-document splits the line on
# `"`; nothing scans the payload with a pattern removal (quadratic in bash 3.2), and each `case` on it
# is linear.
claude_code_cwd() {
    nl='
'
    case "$p" in *"$nl"*) return 1 ;; esac
    # shellcheck disable=SC2034 # c1 and c5 are the first two key names: any name will do
    IFS='"' read -r c0 c1 c2 c3 c4 c5 c6 c7 c8 c9 c10 cw c12 <<EOF
$p
EOF
    [ "$c0" = "{" ] && [ "$c9" = cwd ] || return 1
    for c in "$c2" "$c6" "$c10"; do case "$c" in : | ': ') ;; *) return 1 ;; esac; done
    for c in "$c4" "$c8"; do case "$c" in , | ', ') ;; *) return 1 ;; esac; done
    case "$c1$c3$c5$c7" in *\\*) return 1 ;; esac
    case "$c12" in *'"cwd"'*) return 1 ;; esac # another cwd key: the grep checks them all
}

# Runs `$1 <vault>` for the vault of cwd $cw, by each_vault's rule (0: start Python). The gate goes by
# the resolved path, so that is checked first; the path as written is checked too when it differs.
# The symlinks are resolved by `cd -P` in this shell and back, not in `$(...)`: no fork.
cwd_vault() {
    case "$cw" in /*) ;; *) return 0 ;; esac
    case "$cw" in *\\*) return 0 ;; esac
    [ "${#cw}" -le 4096 ] || return 0
    o=$PWD
    cd -P -- "$cw" 2>/dev/null || return 0
    r=$PWD
    # Back where we were. If that is gone, staying here is harmless: with a top-level cwd in the
    # payload, no Python hook looks at its own working directory.
    cd -- "$o" 2>/dev/null || true
    if walk_up "$r"; then "$1" "$w" && return 0; fi
    [ "$r" = "$cw" ] && return 1
    if walk_up "$cw"; then "$1" "$w" && return 0; fi
    return 1
}

# Whether the session of payload $p may be in a vault (each_vault's rule; unknown counts as yes).
any_vault() { true; }
may_be_in_a_vault() { each_vault any_vault; }

# Whether vault $1's connectors.custom.yaml might set restrict_tools, so the gate must start: the
# literal text, any backslash (a double-quoted YAML key can spell it with escapes, #590) or a NUL byte.
# A grep, tr or cmp that fails answers yes.
restricts() {
    f="$1/connectors.custom.yaml"
    [ -s "$f" ] || return 1
    tool grep
    # shellcheck disable=SC1003 # the pattern ends in an escaped backslash: any backslash in the file
    LC_ALL=C "$t" -qsE -e 'restrict_tools|\\' "$f"
    [ "$?" -eq 1 ] || return 0 # the key, or grep failed
    # No key: Python only if the file holds a NUL byte (tr drops them; cmp then finds a difference).
    tool tr
    tr=$t
    tool cmp
    # shellcheck disable=SC2094 # both only read the file
    LC_ALL=C "$tr" -d '\000' 2>/dev/null <"$f" | "$t" -s - "$f" && return 1
    return 0
}

# Runs the restrict_tools gate ($@) on payload $p and ends the filter. The gate always exits 0 (its
# deny is JSON on stdout), so any other exit means it did not run: 126 for a shim naming a missing
# interpreter, 127 for no scoutctl, 1 for a crash before the gate, 2 for a Python or typer usage
# error, 128 + n for a signal. Claude Code lets a call through on every exit but 0 and 2, so passing
# that on would lift every restriction without a word. The call is blocked instead when it is an MCP
# call in a session that may be in a vault; anything else gets a non-blocking 1, since hooks.json is
# plugin-wide and a broken engine must not block MCP outside a vault.
mcp_gate() {
    printf '%s' "$p" | "$@" && verdict 0
    s=$?
    tool grep
    n=$(printf '%s' "$p" | LC_ALL=C "$t" -oE -e '"tool_name"[[:space:]]*:[[:space:]]*"mcp__[A-Za-z0-9_.-]*')
    case "$?" in
    0)
        n="mcp__${n#*\"mcp__}"
        n="${n%%[!A-Za-z0-9_.-]*}"
        ;;
    1) verdict 1 ;; # not an MCP call: nothing to gate
    *) n="this MCP call" ;;
    esac
    may_be_in_a_vault || verdict 1
    echo "[scout:mcp-tool-gate] Scout's MCP tool gate could not start (status $s), so $n is blocked in this vault's sessions: its restrict_tools limits can't be checked. To fix it, run /scout-update or \`scoutctl bootstrap doctor\`." >&2
    verdict 2
}

name="${1-}"
[ "$#" -gt 0 ] && shift
filter="$name"
# Every filter but the test helper needs a program to start.
[ "$#" -gt 0 ] || [ "$name" = print-bash-pattern ] || filter=

case "$filter" in

# PreToolUse, every MCP call, every session. The restrict_tools gate: Python starts only when the
# session's vault, found from the payload's cwd as the gate finds it (each_vault), has a
# connectors.custom.yaml that might set the key (restricts), or when the shell can't tell. When it
# cannot start, the call is blocked in a session that may be in a vault (mcp_gate). With no payload
# there is no cwd to go by, so this process's own directory decides.
mcp-tool-gate)
    if ! read_payload; then
        find_vault
        restricts "$d" || verdict 0
        echo "[scout:mcp-tool-gate] Scout's MCP tool gate could not read this call, so it is blocked in this vault's sessions." >&2
        verdict 2
    fi
    each_vault restricts || verdict 0
    mcp_gate "$@"
    ;;

# PreToolUse, MCP write tools. Skipped only for a session that is neither a run nor in a vault (by
# the payload's cwd, as the gate decides). A run is denied when the check cannot run; the owner is warned.
outbound-gate)
    run=
    is_run && run=1
    if [ -n "$run" ]; then
        "$@" && verdict 0
    elif read_payload; then
        may_be_in_a_vault || verdict 0
        printf '%s' "$p" | "$@" && verdict 0
    fi
    if [ -n "$run" ]; then
        echo "[scout:outbound-gate] Scout's outbound privacy check could not run, so this call is blocked in a scheduled run. Put the content in the daily file or the owner's Telegram chat instead, and say so in the run notes." >&2
        verdict 2
    fi
    echo "[scout:outbound-gate] Warning: Scout's outbound privacy check could not run; this call was not checked for private content." >&2
    verdict 1
    ;;

# PreToolUse, Grep|Glob|Bash. Owner sessions leave at once. A run's Grep or Glob, and any payload
# that spells a KB path, go to Python. A run's other Bash call goes to Python when one of these holds:
#   1. its command value matches bash_pattern;
#   2. the payload names $SCOUT_SECRETS_DIR;
#   3. its command value spells a KB path once quotes and backslashes are removed.
kb-read-gate)
    is_run || verdict 0
    if ! read_payload; then
        echo "[scout:kb-read-gate] Scout's KB read gate could not read this call, so it is blocked in a scheduled run." >&2
        verdict 2
    fi
    case "$p" in
    *'"Grep"'* | *'"Glob"'* | *knowledge-base* | *knowledge_base* | *action-items/context*) ;;
    *) bash_needs_python || verdict 0 ;;
    esac
    printf '%s' "$p" | "$@" && verdict 0
    echo "[scout:kb-read-gate] Scout's KB read gate could not run, so this call is blocked in a scheduled run. Read the KB through scripts/scoutctl kb (search, files, read, resolve)." >&2
    verdict 2
    ;;

# PreToolUse, Read. $r: a reader that is neither the owner nor `autonomous` (an agent:* or
# delegate:<id> reader). $s: the payload names Scout's secrets or the delegate inbox. Python starts
# for a run when $s; for a delegate reader always (#550); for another $r reader when the payload
# names a KB path.
kb-read-gate-read)
    r=
    [ -n "${SCOUT_READER+set}" ] && [ "$SCOUT_READER" != owner ] && [ "$SCOUT_READER" != autonomous ] && r=1
    [ -n "$r" ] || is_run || verdict 0
    if ! read_payload; then
        echo "[scout:kb-read-gate] Scout's KB read gate could not read this Read, so it is blocked for this reader." >&2
        verdict 2
    fi
    s=
    case "$p" in *[Ss][Cc][Oo][Uu][Tt]-[Ss][Ee][Cc][Rr][Ee][Tt][Ss]*) s=1 ;; esac
    case "$p" in *[Ss][Cc][Oo][Uu][Tt]-[Ii]* | *[Aa][Gg][Ee][Nn][Tt][Ss]/*) s=1 ;; esac
    [ -n "$SCOUT_SECRETS_DIR" ] && case "$p" in *"$SCOUT_SECRETS_DIR"*) s=1 ;; esac
    if [ -z "$s" ]; then
        [ -n "$r" ] || verdict 0
        case "$SCOUT_READER" in
        [Dd][Ee][Ll][Ee][Gg][Aa][Tt][Ee]:*) ;;
        *) case "$p" in *knowledge-base* | *knowledge_base* | *action-items/context*) ;; *) verdict 0 ;; esac ;;
        esac
    fi
    printf '%s' "$p" | "$@" && verdict 0
    echo "[scout:kb-read-gate] Scout's KB read gate could not run, so this Read is blocked for this reader." >&2
    verdict 2
    ;;

# PreToolUse, Write|Edit|MultiEdit|NotebookEdit. Every run write goes to Python: a symlink can hide
# the path from the shell.
kb-write-gate)
    is_run || verdict 0
    "$@" && verdict 0
    echo "[scout:kb-write-gate] Scout's write gate could not run, so this write is blocked in a scheduled run." >&2
    verdict 2
    ;;

# PostToolUse, Read. Labels a KB note in a run or a vault session (by the payload's cwd); a note it
# cannot label is private.
kb-read-label)
    if read_payload; then
        case "$p" in *knowledge-base* | *knowledge_base* | *action-items/context*) ;; *) verdict 0 ;; esac
    else
        p=
    fi
    is_run || may_be_in_a_vault || verdict 0
    printf '%s' "$p" | "$@" && verdict 0
    printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[scout:kb-label] Scout could not label the file just read, so treat it as [private]: its content must not reach any shared surface (Slack, a connector write or query, a web query, drafts/, a back-port)."}}'
    verdict 0
    ;;

# PostToolUse, the shared-notes tools. Records what the answer showed, in a run or a vault session
# (by the payload's cwd).
shared-read-record)
    if is_run; then
        "$@" && verdict 0
    else
        read_payload || p=
        may_be_in_a_vault || verdict 0
        printf '%s' "$p" | "$@" && verdict 0
    fi
    printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[scout:shared-read] Scout could not record this answer, so check-outbound will not recognise it. Notes shared with you are private to you: keep their path, title and text off every shared surface (Slack, a connector write or query, a web query, drafts/)."}}'
    verdict 0
    ;;

# PostToolUse, a run's Write|Edit|MultiEdit. Python starts only for a file_path shaped <dir>/<x>/<x>.md
# (a project's front page). It warns, never blocks: always verdict 0.
front-page-reads)
    is_run || verdict 0
    read_payload || verdict 0 # it only ever warns
    tool grep
    printf '%s' "$p" | "$t" -qE -e '"file_path": *"[^"]*/([^/"]+)/\1\.md"'
    [ "$?" -eq 1 ] && verdict 0
    printf '%s' "$p" | "$@"
    verdict 0
    ;;

# For the tests: the assembled Bash pattern, one line.
print-bash-pattern)
    bash_pattern
    printf '%s\n' "$re"
    ;;

# An unknown filter, or no program to start: hooks.json and this file disagree. Not a verdict, so the
# entry bypasses this script for the Python stage.
*)
    echo "[scout:prefilter] hooks/prefilter.sh was called with an unknown filter or no program (${name:-none}); hooks.json and the script are out of step." >&2
    exit 1 # not a verdict: the entry starts the Python stage itself
    ;;
esac
}
