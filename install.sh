#!/usr/bin/env bash
# Scout one-command installer.
#   curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
# Sets up the PLUGIN + ENGINE. The interactive vault is then created with /scout-setup.
#
# Flags: --check  (verify preconditions only; make no changes)
set -euo pipefail

MARKETPLACE="Raven-Scout/Scout"
PLUGIN_ID="scout@scout-plugin"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# The native Claude Code and uv installers both put binaries in ~/.local/bin,
# which a fresh login shell may not have on PATH yet.
export PATH="$HOME/.local/bin:$PATH"

have() { command -v "$1" >/dev/null 2>&1; }
fail() { echo "error: $*" >&2; exit 1; }

# Where Claude Code records added marketplaces. Overridable so the check below can
# be exercised against fixture files without touching a real Claude Code config.
KNOWN_MARKETPLACES="${SCOUT_KNOWN_MARKETPLACES:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/known_marketplaces.json}"

# Raven-Scout/Scout is the former Raven-Scout/scout-plugin repo, renamed when the
# app moved in; GitHub redirects the old name, so a `scout-plugin` marketplace
# that still says Raven-Scout/scout-plugin keeps updating and needs nothing. A
# marketplace pointing at any OTHER repo would get a failing `marketplace add`
# (the name is taken) and an `update` that never sees this repo — so detect that
# and print the re-point commands instead of reporting a success that isn't one.
# Never remove the marketplace from here: removing it uninstalls the plugin a
# user's scheduled runs depend on.
# Usage: check_marketplace_source <known_marketplaces.json>
# Returns 0 to continue (no entry, this repo under either name, a local
# directory, or an unreadable file), 1 when the user has to re-point first.
check_marketplace_source() {
  local file="$1" src repo
  local -a py
  [ -f "$file" ] || return 0
  if command -v uv >/dev/null 2>&1; then
    py=(uv run --no-project --quiet python)
  elif command -v python3 >/dev/null 2>&1; then
    py=(python3)
  else
    echo "note: no Python available to read $file; skipping the marketplace-source check." >&2
    return 0
  fi
  # Prints one line: absent | unreadable | github <owner/repo> | directory <path> | other <kind>
  src="$("${py[@]}" -c '
import json, re, sys
try:
    entry = json.load(open(sys.argv[1], encoding="utf-8")).get("scout-plugin")
except Exception:
    print("unreadable")
    sys.exit(0)
if not isinstance(entry, dict):
    print("absent")
    sys.exit(0)
src = entry.get("source") if isinstance(entry.get("source"), dict) else {}
kind = src.get("source")
if kind == "github":
    print("github " + str(src.get("repo", "")))
elif kind == "git":
    m = re.match(r"(?:https://|git@)github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", str(src.get("url", "")))
    print("github " + m.group(1) if m else "other git")
elif kind == "directory":
    print("directory " + str(src.get("path", "")))
else:
    print("other " + str(kind))
' "$file" 2>/dev/null || echo unreadable)"
  case "$src" in
    absent) return 0 ;;
    "github "*)
      repo="${src#github }"
      # GitHub owner/repo names are case-insensitive. raven-scout/scout-plugin is
      # this repo's former name, which GitHub redirects here.
      case "$(printf '%s' "$repo" | tr '[:upper:]' '[:lower:]')" in
        raven-scout/scout | raven-scout/scout-plugin) return 0 ;;
      esac
      cat >&2 <<EOF
error: your Claude Code marketplace "scout-plugin" points at $repo.
       Scout lives in Raven-Scout/Scout, and installing from another repo
       would leave you on a version that doesn't get Scout's updates.
       Re-point it, then re-run this installer:

           claude plugin marketplace remove scout-plugin
           claude plugin marketplace add Raven-Scout/Scout
           claude plugin install scout@scout-plugin

       (The remove uninstalls the plugin until the install line puts it back;
       your vault in ~/Scout is not touched.)
EOF
      return 1 ;;
    "directory "*)
      echo "note: your \"scout-plugin\" marketplace is a local directory (${src#directory }) — using it as is."
      echo "      If that is a pre-monorepo scout-plugin checkout, \`git pull\` it: the repo is now Raven-Scout/Scout"
      echo "      (the old URL redirects), and the pull brings it to the current layout."
      return 0 ;;
    unreadable)
      echo "note: could not read $file; skipping the marketplace-source check." >&2
      return 0 ;;
    *)
      echo "note: your \"scout-plugin\" marketplace has an unrecognised source ($src) — leaving it as is." >&2
      return 0 ;;
  esac
}

# --- preconditions ---
# On a fresh Mac, /usr/bin/git and /usr/bin/python3 exist but are stubs that pop
# the Command Line Tools installer when first run — so `command -v git` passes and
# the dialog would interrupt us mid-install. Check the real thing up front.
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
  [ "$CHECK_ONLY" = 1 ] || xcode-select --install >/dev/null 2>&1 || true
  fail "Apple's Command Line Tools are required (they provide git).
       A system dialog should have opened — click Install, wait for it to finish,
       then re-run this installer."
fi
have claude || fail "Claude Code not found. Install it first: https://docs.claude.com/claude-code
       then run \`claude\` once to sign in, and re-run this installer."
have git    || fail "git is required."
if ! have uv; then
  echo "uv not found — installing (https://docs.astral.sh/uv)…"
  [ "$CHECK_ONLY" = 1 ] || curl -fsSL https://astral.sh/uv/install.sh | sh
fi

check_marketplace_source "$KNOWN_MARKETPLACES" || exit 1

if [ "$CHECK_ONLY" = 1 ]; then
  echo "preconditions OK (claude, git present; uv $(have uv && echo present || echo 'will-install'))"
  exit 0
fi
have uv || fail "uv install did not land on PATH (expected ~/.local/bin/uv). Open a new terminal and re-run."

# --- plugin + engine ---
echo "Adding the Scout marketplace…"
claude plugin marketplace add "$MARKETPLACE" 2>/dev/null || claude plugin marketplace update scout-plugin
echo "Installing the Scout plugin…"
claude plugin install "$PLUGIN_ID"

# Resolve the installed plugin root. `claude plugin list --json` has emitted both a
# top-level list and a {"plugins": {...}} map across versions — accept either.
# uv provides the interpreter, so this works with no system Python.
ROOT="$(claude plugin list --json 2>/dev/null | uv run --no-project --quiet python -c '
import json, sys
data = json.load(sys.stdin)
entries = data if isinstance(data, list) else [p for ps in data.get("plugins", {}).values() for p in ps]
paths = [p["installPath"] for p in entries if p.get("id") == sys.argv[1] or "/scout-plugin/" in p.get("installPath", "")]
print(paths[0] if paths else "")
' "$PLUGIN_ID" 2>/dev/null || true)"
# Fallback: the marketplace cache layout (~/.claude/plugins/cache/scout-plugin/scout/<version>).
if [ -z "$ROOT" ]; then
  ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1 || true)"
  ROOT="${ROOT%/}"
fi
[ -n "$ROOT" ] && [ -f "$ROOT/scripts/install-venv.sh" ] \
  || fail "the Scout plugin installed, but its folder could not be located.
       Run \`claude plugin list\` to confirm it is installed, then re-run this installer."

echo "Setting up the engine (one-time, ~1 minute)…"
bash "$ROOT/scripts/install-venv.sh" \
  || fail "engine setup failed (see the output above). Retry with:
       bash \"$ROOT/scripts/install-venv.sh\""
"$ROOT/.venv/bin/scoutctl" --help >/dev/null 2>&1 \
  || fail "engine installed but scoutctl does not run. Retry with:
       bash \"$ROOT/scripts/install-venv.sh\""

cat <<'DONE'

✅ Scout plugin + engine installed.

Next step — create your vault (interactive: detects your connectors, collects
your details, sets the schedule):

    Open Claude Code and run:  /scout-setup

DONE
