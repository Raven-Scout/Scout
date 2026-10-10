#!/usr/bin/env bash
# kb-move.sh — move/rename a vault file and repair every inbound reference.
#
# WHY THIS EXISTS
# ---------------
# Scout vaults used to carry a blanket hard rule: "Never rename or move existing
# files/folders." It was written to protect Obsidian link integrity, and it worked
# — by making the whole capability unavailable. The cost compounds quietly: in the
# reference vault an approved reorganisation sat unexecuted for 126 days, and every
# user request to move a file was re-analysed by run after run and never acted on.
#
# So the prohibition is gone. This script is what replaces it: the rule is no
# longer "don't move" but "move through this, which cannot silently break links."
# A permission without a driver is just a new hazard.
#
# WHAT ACTUALLY BREAKS ON A MOVE (measured, not assumed)
# ------------------------------------------------------
# Obsidian resolves `[[basename]]` by *filename*, anywhere in the vault. So a
# move breaks far less than a naive string count suggests. For `ai-costs.md` the
# reorg plan cited "4,043 inbound links" and used that scale as its reason not to
# act. The real figure was 208: 6,530 of the references were bare `[[ai-costs]]`,
# which survive a move untouched. Only these three forms break:
#
#   1. path-qualified wikilinks   [[knowledge-base/ai-costs]]        → rewritten
#   2. markdown links             ](knowledge-base/ai-costs.md)      → rewritten
#   3. raw paths in prose/code    knowledge-base/ai-costs.md         → REPORTED, not rewritten
#
# Bare `[[stem]]` links are rewritten ONLY when the basename itself changes
# (a rename), because that is the only case where they stop resolving.
#
# WHY RAW PROSE PATHS ARE REPORTED AND NOT REWRITTEN (learned the hard way,
# 2026-10-10): this script's first draft rewrote them. On the ai-costs move that
# would have silently falsified the vault's own history — the file's audit trail
# contains sentences like *"`git log --follow` shows one add at 41c07939,
# knowledge-base/ai-costs.md, and no rename in 175 days"* and *"this file was born
# at knowledge-base/ai-costs.md"*. Those are true statements ABOUT the old path;
# rewriting them to the new path makes the vault lie about where the file lived.
# A path inside prose is a claim, not a link. Only a human can tell which is which,
# so they are printed for review and left alone.
#
# Path-qualified wikilinks are normalised to the BARE form `[[stem]]` whenever the
# basename is unique vault-wide (which the collision guard below already proves).
# Bare links survive every future move, so each move makes the next one cheaper —
# the opposite of the old regime, where link fragility was the argument for never
# moving anything at all.
#
# SAFETY
# ------
#   * Dry-run by default; --write is required to touch anything.
#   * Refuses a destination whose basename collides with an existing vault file
#     (that would make every bare `[[stem]]` link ambiguous).
#   * Uses `git mv`, so history follows the file and one `git revert` undoes it.
#   * Measures real dangling links before and after, and FAILS if the count rose.
#
# Usage:
#   scripts/kb-move.sh <src> <dst>              # dry run: show every edit
#   scripts/kb-move.sh --write <src> <dst>      # execute
#   scripts/kb-move.sh --write --no-verify <src> <dst>   # skip the slow post-check
#
# Exit: 0 ok · 2 bad usage/preconditions · 3 verification found new breakage
set -uo pipefail

VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRITE=0
VERIFY=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write)     WRITE=1; shift ;;
    --no-verify) VERIFY=0; shift ;;
    -h|--help)   sed -n '2,50p' "${BASH_SOURCE[0]}"; exit 0 ;;
    --) shift; break ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *)  break ;;
  esac
done

[[ $# -eq 2 ]] || { echo "usage: kb-move.sh [--write] <src> <dst>" >&2; exit 2; }

SRC_IN="$1"; DST_IN="$2"

cd "$VAULT" || exit 2

# --- normalise to vault-relative paths -------------------------------------
rel() { python3 -c "
import os,sys
v=os.path.realpath(sys.argv[1]); p=os.path.abspath(sys.argv[2])
print(os.path.relpath(p, v))" "$VAULT" "$1"; }

SRC="$(rel "$SRC_IN")"
case "$SRC" in ../*) echo "❌ source is outside the vault: $SRC" >&2; exit 2 ;; esac

DST="$(rel "$DST_IN")"
# A destination that is an existing directory, or ends in /, means "into here".
if [[ -d "$DST_IN" || "$DST_IN" == */ ]]; then
  DST="$DST/$(basename "$SRC")"
fi
case "$DST" in ../*) echo "❌ destination is outside the vault: $DST" >&2; exit 2 ;; esac

# REPAIR MODE. If the source is already gone and the destination already exists,
# the move happened some other way (a hand `git mv`, or a run whose rewrite half
# was interrupted) and only the inbound links are outstanding. Fix those and skip
# the move, rather than refusing — a half-done move is exactly when this script is
# most needed, and the first version refused precisely then.
REPAIR=0
if [[ ! -f "$SRC" && -f "$DST" ]]; then
  REPAIR=1
elif [[ ! -f "$SRC" ]]; then
  echo "❌ source is not a file: $SRC" >&2; exit 2
elif [[ -e "$DST" ]]; then
  echo "❌ destination already exists: $DST" >&2; exit 2
fi

SRC_STEM="$(basename "$SRC" .md)"
DST_STEM="$(basename "$DST" .md)"
SRC_NOEXT="${SRC%.md}"
DST_NOEXT="${DST%.md}"

# --- basename-collision guard ----------------------------------------------
# Obsidian resolves bare [[stem]] by filename. Two files sharing a stem makes
# every bare link to it ambiguous, which is a silent, vault-wide corruption.
if [[ "$SRC_STEM" != "$DST_STEM" ]]; then
  COLLIDE="$(find . -name "${DST_STEM}.md" -type f \
      -not -path './.git/*' -not -path './.claude/*' -not -path './node_modules/*' \
      -not -path './.scout-cache/*' -not -path './.scout-logs/*' 2>/dev/null | head -3)"
  if [[ -n "$COLLIDE" ]]; then
    echo "❌ destination basename '${DST_STEM}.md' already exists in the vault:" >&2
    echo "$COLLIDE" >&2
    echo "   Bare [[${DST_STEM}]] links would become ambiguous. Pick another name." >&2
    exit 2
  fi
fi

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  kb-move $( ((WRITE)) && echo '— WRITE' || echo '— DRY RUN' )$( ((REPAIR)) && echo '  [REPAIR: file already moved, links only]' )"
echo "╚══════════════════════════════════════════════════════════╝"
echo "  src: $SRC"
echo "  dst: $DST"
echo

# --- baseline dangling count ------------------------------------------------
baseline=""
if ((VERIFY)) && [[ -f scripts/dangling-links.py ]]; then
  baseline="$(python3 scripts/dangling-links.py --json 2>/dev/null \
      | python3 -c 'import json,sys;print(json.load(sys.stdin).get("real_distinct",""))' 2>/dev/null)"
  [[ -n "$baseline" ]] && echo "  baseline real-dangling distinct targets: $baseline" && echo
fi

# --- rewrite inbound references --------------------------------------------
# Three breaking forms + (on rename only) bare wikilinks. Everything is applied
# by one python pass so the counts reported are the counts applied.
export KBM_SRC="$SRC" KBM_DST="$DST" KBM_SRC_NOEXT="$SRC_NOEXT" KBM_DST_NOEXT="$DST_NOEXT"
export KBM_SRC_STEM="$SRC_STEM" KBM_DST_STEM="$DST_STEM" KBM_WRITE="$WRITE"

python3 - <<'PY'
import os, re, sys

VAULT = os.getcwd()
SRC        = os.environ['KBM_SRC']
DST        = os.environ['KBM_DST']
SRC_NOEXT  = os.environ['KBM_SRC_NOEXT']
DST_NOEXT  = os.environ['KBM_DST_NOEXT']
SRC_STEM   = os.environ['KBM_SRC_STEM']
DST_STEM   = os.environ['KBM_DST_STEM']
WRITE      = os.environ['KBM_WRITE'] == '1'

# `.claude/` holds full worktree COPIES of the vault; walking it multiplies every
# count and would rewrite files that are not really ours (same trap dangling-links.py
# documents). The caches are generated and get rebuilt anyway.
SKIP = {'.git', '.claude', 'node_modules', '.scout-cache', '.scout-logs',
        '.venv', '__pycache__', '.pytest_cache', '.build'}

# 1. path-qualified wikilink: [[knowledge-base/ai-costs]] → bare [[ai-costs]].
#    Bare is safe because the collision guard above proved the stem is unique,
#    and it is immune to every future move.
wiki_path = re.compile(r'\[\[' + re.escape(SRC_NOEXT) + r'(?=[\]|])')
# 2. markdown link only:  ](knowledge-base/ai-costs.md)  — a real link, so rewrite.
md_link   = re.compile(r'\]\(/?' + re.escape(SRC) + r'(?=[)#])')
# 3. bare wikilink — ONLY on a rename, since a pure move leaves [[stem]] resolving.
bare_wiki = (re.compile(r'\[\[' + re.escape(SRC_STEM) + r'(?=[\]|])')
             if SRC_STEM != DST_STEM else None)
# 4. raw prose path — REPORTED ONLY (see the header): a path in prose is a claim
#    about where the file was, and rewriting it falsifies the vault's own history.
prose_path = re.compile(r'(?<![\w/.-])' + re.escape(SRC) + r'(?![\w])')

totals = {'wikilink-path': 0, 'markdown-link': 0, 'bare-wikilink': 0}
prose_hits = []
touched = []

for root, dirs, files in os.walk(VAULT):
    dirs[:] = [d for d in dirs if d not in SKIP and not os.path.islink(os.path.join(root, d))]
    for fn in files:
        if not fn.endswith(('.md', '.markdown')):
            continue
        p = os.path.join(root, fn)
        if os.path.islink(p):
            continue
        rel = os.path.relpath(p, VAULT)
        if rel == SRC:          # the moving file itself: self-references stay valid
            continue
        try:
            orig = open(p, encoding='utf-8').read()
        except (UnicodeDecodeError, OSError):
            continue

        new = orig
        c = {}
        new, c['wikilink-path'] = wiki_path.subn('[[' + DST_STEM, new)
        new, c['markdown-link'] = md_link.subn('](' + DST, new)
        c['bare-wikilink'] = 0
        if bare_wiki:
            new, c['bare-wikilink'] = bare_wiki.subn('[[' + DST_STEM, new)

        np = len(prose_path.findall(new))
        if np:
            prose_hits.append((rel, np))

        n = sum(c.values())
        if n:
            for k, v in c.items():
                totals[k] += v
            touched.append((rel, n, {k: v for k, v in c.items() if v}))
            if WRITE:
                open(p, 'w', encoding='utf-8').write(new)

touched.sort(key=lambda t: -t[1])
print(f"  reference rewrites: {sum(totals.values())} occurrence(s) in {len(touched)} file(s)")
for k, v in totals.items():
    if v:
        print(f"    · {k}: {v}")
if bare_wiki is None:
    print(f"    · bare [[{SRC_STEM}]] links: untouched by design "
          f"(Obsidian resolves them by basename, which is unchanged)")
print()
for rel, n, c in touched[:15]:
    print(f"    {n:>5}  {rel}  {c}")
if len(touched) > 15:
    print(f"    … and {len(touched)-15} more file(s)")

if prose_hits:
    tot = sum(n for _, n in prose_hits)
    print()
    print(f"  ⚠️  {tot} raw prose mention(s) of '{SRC}' in {len(prose_hits)} file(s) — NOT rewritten.")
    print("     A path in prose is usually a *claim about where the file was* "
          "(git-history notes, audit trails). Rewriting those makes the vault lie.")
    print("     Review by hand; rewrite only the ones that are live pointers:")
    for rel, n in sorted(prose_hits, key=lambda t: -t[1])[:10]:
        print(f"       {n:>3}  {rel}")
PY

rc=$?
[[ $rc -ne 0 ]] && { echo "❌ rewrite pass failed" >&2; exit 2; }

if ((!WRITE)); then
  echo
  echo "  DRY RUN — nothing changed. Re-run with --write to apply."
  exit 0
fi

# --- the move itself --------------------------------------------------------
if ((REPAIR)); then
  echo "  ↷ repair mode: $DST was already in place, links repaired above."
else
mkdir -p "$(dirname "$DST")"
if git ls-files --error-unmatch "$SRC" >/dev/null 2>&1; then
  git mv "$SRC" "$DST" || { echo "❌ git mv failed" >&2; exit 2; }
  echo "  ✅ git mv $SRC → $DST   (history follows the file)"
else
  mv "$SRC" "$DST" || { echo "❌ mv failed" >&2; exit 2; }
  echo "  ✅ mv $SRC → $DST   (untracked file)"
fi
fi

# --- post-move verification -------------------------------------------------
if ((VERIFY)) && [[ -n "$baseline" ]]; then
  echo
  after="$(python3 scripts/dangling-links.py --json 2>/dev/null \
      | python3 -c 'import json,sys;print(json.load(sys.stdin).get("real_distinct",""))' 2>/dev/null)"
  echo "  real-dangling distinct targets: $baseline → $after"
  if [[ -n "$after" ]] && (( after > baseline )); then
    echo "  ❌ the move introduced $((after - baseline)) new dangling target(s)." >&2
    echo "     Inspect with: python3 scripts/dangling-links.py --class real" >&2
    echo "     Undo with:    git reset && git checkout -- . && git status" >&2
    exit 3
  fi
  echo "  ✅ no new dangling links"
fi

echo
echo "  Undo: git revert <this commit>   (or: git mv $DST $SRC + revert the rewrites)"
