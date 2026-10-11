---
name: scout-connect
description: Add or remove a tool Scout reads — any connected MCP server Scout doesn't read yet (a mail suite, a chat tool, a CRM…). Drafts the definition through the engine, shows it to you, and makes it live.
---

# Scout Connect

Usage: `/scout-connect` lists the connected tools Scout doesn't read yet; `/scout-connect <server>` adds one; `/scout-connect --remove <key>` removes one.

Resolve the engine at the top of every shell block (each block is a fresh process). A custom connector is vault data, so this works however the engine was installed, Scout.app included: the `scoutctl` shim on `PATH` follows the engine pointer.

```bash
SCOUTCTL="$(command -v scoutctl)"
[ -n "$SCOUTCTL" ] || { echo "SCOUTCTL_NOT_FOUND"; exit 1; }
```

If it prints `SCOUTCTL_NOT_FOUND`, Scout isn't set up yet: tell the user to open Scout.app, or run `scoutctl setup` in a terminal (which offers every connected tool), and stop.

## Hard rule

A tool ends in exactly one of three states: **added**, **skipped because the user chose to**, or **sign in first**. For the last, say: *"Sign in through `/mcp` (or claude.ai → Settings → Connectors), then run `/scout-connect <server>` again."* Never tell the user a tool is out of Scout's reach.

## 1. Find the server

```bash
"$SCOUTCTL" connectors uncovered --json
```

- `error` is set: show it, and suggest re-running after checking `claude mcp list` in a terminal.
- With no argument: list `servers[].name` with their `status`, and ask which to add. A `needs_auth` server gets the sign-in sentence.
- With an argument: match it against `servers[].name` (case-insensitive, substring). If nothing matches, the server is either already read by Scout (say so; `"$SCOUTCTL" connectors custom list` shows the custom ones) or not connected: use the sign-in sentence.

## 2. Draft

```bash
"$SCOUTCTL" connectors custom draft --server "<server name exactly as listed>" --json
```

It takes up to a minute. Act on `status`:

- `drafted`: show each definition in three lines, from `summary`: *display name · what it scans · what it looks up*. Ask the user to confirm, or to adjust it. An adjustment may only remove tools or activities, skip a definition, or rewrite the `focus`, `when` or `notes` text; never add, rename or swap a tool, because the engine checks tool names when you add a definition but not that each tool only reads. Ask for each name in a definition's `needs_user_input`.
- `needs_auth`: the sign-in sentence. Never show the raw engine `message`.
- `no_read_tools`, `invalid`, `timeout`, `error`: "Scout couldn't find anything to read in <server> yet." Offer to retry the draft once. Never show the raw engine `message`, `issues` or stderr to the user.

## 3. Add

For each confirmed definition, pipe its JSON (with any adjustment the user asked for) to the engine, which validates it again:

```bash
"$SCOUTCTL" connectors custom add --file - --input <name>=<value> <<'EOF'
<the definition JSON>
EOF
```

Read the JSON it prints:

- `applied` / `unchanged`: the next scheduled run reads it.
- `invalid`: fix each `issues[].path` and retry; don't show raw errors unless you can't fix them.
- `probe-failed`: the check command for that tool didn't pass; say so, and offer to skip it.
- `busy`: a Scout session is running; try again in a minute.
- `deferred`: saved. If `waiting` is non-empty, those brain files have a pending review: help the user finish it, then `"$SCOUTCTL" bootstrap resolve <file>`. Otherwise it goes live at the next upgrade.
- `conflict`: saved; walk the user through merging the sidecar in `sidecars`, then `"$SCOUTCTL" bootstrap resolve <file>`.
- `error`: show `message`.

## Remove

`/scout-connect --remove <key>`: confirm with the user, then run `"$SCOUTCTL" connectors custom remove <key>` and report the status the same way. An `invalid` status with an issue at `connectors.<key>` means no custom connector has that key: run `"$SCOUTCTL" connectors custom list` and offer the real keys.
