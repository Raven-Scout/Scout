# Agent Sessions 1b — Index Speed — Design

**Date:** 2026-09-28
**Status:** Proposed (for review)
**Surfaces:** scout-plugin engine, `engine/scout/sessions/` (loaders, orchestrator, tests)
**Builds on:** the Agent Sessions design (`docs/superpowers/specs/2026-09-08-agent-sessions-design.md`, in [Raven-Scout/Scout#112](https://github.com/Raven-Scout/Scout/pull/112)) — §4.12's budget and §8's row "1b · Index performance"; plan 1 is [Raven-Scout/scout-plugin#243](https://github.com/Raven-Scout/scout-plugin/pull/243).

## 1. Context

Plan 1 builds `.scout-cache/sessions-index.json` from the Claude desktop app's per-session records, `~/.claude` transcripts and live-process files, and `gh`. It is correct but too slow for the Mac app, which (plan 3) will rebuild the index about every 2 seconds while sessions are active.

Measured on the author's machine on 2026-09-26, with `--no-gh`, from plan 1's branch after its final fix wave:

| Input | Size |
|---|---|
| Desktop session records | 257 files, 173 MB (median record ≈ 600 KB, mostly MCP configuration the index never reads) |
| Transcripts | 511 files, 864 MB (median 1.3 MB, largest 33 MB); 345 inside the 14-day window |
| Sessions indexed | 367 |

| Build | Time | Where it goes |
|---|---|---|
| Cold (empty caches) | 7.7 s | transcript parsing 5.7 s (≈114,000 rows decoded one by one), desktop records 1.1 s, `git` subprocesses 0.6 s |
| Warm, fresh process, nothing changed | 1.8 s | re-decoding every desktop record 1.0 s, 44 `git` subprocesses 0.7 s, everything else 0.1 s |
| `scoutctl session index --no-gh`, warm, end to end | 1.8–2.0 s | Python start-up and imports are only 0.07 s |

Transcript cost scales with the number of rows, not bytes: the 33 MB transcript parses in 0.15 s because most of its size is a few hundred large tool results. A running session's transcript is re-parsed in full on every build today, so each active session adds up to about 0.2 s per refresh.

## 2. Goals and non-goals

### Goals

1. **Cold build < 5 s** on the author's machine.
2. **Warm build < 1 s in the app's steady state**, measured end to end through `scoutctl session index --no-gh`. Steady state means: since the previous build, up to 5 desktop records were rewritten and up to 5 transcripts grew. A rebuild with nothing changed must also be < 1 s.
3. **Identical output.** Index schema v1 and the `cc-sessions.md` digest do not change. Every transcript's derived facts are identical to what a full parse produces.
4. **Tests that fail if the caching regresses**, independent of how fast the CI runner is.

### Non-goals

- `gh` PR refresh time. It is network-bound and already capped per run.
- A long-running engine process that keeps the index in memory. Possible later, not needed to meet the budget.
- App changes. Plan 3 must ignore the engine's own writes to `sessions-index.json` so a refresh does not trigger itself; recorded here, built there.
- New dependencies such as a faster JSON library, unless the budget cannot be met without one.
- Plan 1's deferred items unrelated to speed (`priorCliSessionIds`, tombstoned sessions reappearing as CLI-only).

## 3. Design

Each loader keeps its current contract and gains a cache keyed on file identity. The orchestrator, `derive`, `render` and the index schema are unchanged except where noted.

### 3.1 Desktop record cache

New file `.scout-cache/sessions-desktop.cache.json`:

```json
{"version": 1,
 "entries": {"<absolute record path>": {
     "size": 612345, "mtime_ns": 1788800000000000000,
     "record": { "…the DesktopRecord fields plan 1 already extracts, PR refs included…" },
     "failed": null}}}
```

For every `local_*.json`:

1. `stat` it. If `size` and `mtime_ns` match the cached entry, use the cached record without reading the file.
2. Otherwise read and decode it, extract the record as today, and replace the entry (`failed` = null).
3. If decoding fails and a cached entry exists, the file was probably caught mid-write (the desktop app rewrites a record on every turn). Serve the cached record. If `failed` already equals this file's `(size, mtime_ns)`, the file has stayed broken across builds, so also record a `SourceError`; otherwise set `failed` to `(size, mtime_ns)` and stay silent.
4. If decoding fails and no cached entry exists, record a `SourceError` and skip the file, as today.

Entries for records that no longer exist are dropped. A cached `record` whose fields fail the same type checks plan 1 applies to fresh records is treated as absent.

### 3.2 Project roots without `git`

`derive.git_toplevel` is replaced by a pure-Python `repo_root(path)` with the same contract (main repository root, or None):

1. If `path` is empty or relative, return None. Otherwise start at the nearest ancestor of `path` that exists.
2. Walk up toward `/` looking for an entry named `.git`.
3. `.git` is a directory: the folder containing it is the root.
4. `.git` is a file: read its `gitdir:` line (relative to the file's folder when not absolute). If that git dir contains a `commondir` file, resolve it relative to the git dir; when the result is named `.git`, its parent is the root (a linked worktree resolves to its main repository). In every other case — no `commondir` (a submodule), or a common dir not named `.git` — the folder containing the `.git` file is the root, which matches what `git rev-parse --show-toplevel` reports today.
5. No `.git` found: None, and the caller keeps today's worktree-stripping fallback.

Results are memoised in a dictionary owned by one build, so a long-lived process never serves a stale root. The existing test that creates a real repository plus `git worktree add` stays and now checks that the Python resolution agrees with `git`.

`is_scout_run` also catches `ValueError` from `Path.resolve()` (a NUL byte in a recorded path), which plan 1 parked as unreachable but trivially guardable.

### 3.3 Transcript checkpoints

The transcript cache moves to format version 2. Each entry keeps plan 1's published facts and adds a checkpoint:

```json
{"version": 2,
 "entries": {"<absolute transcript path>": {
     "info": { "…TranscriptInfo as today: path, first_prompt, files_touched, tool_calls, last_turn, mtime_ns…" },
     "checkpoint": {
         "dev": 16777231, "ino": 123456, "size": 1048600, "mtime_ns": 1788800000000000000,
         "offset": 1048576, "head_sha1": "…",
         "files_smallest": ["~/a.py", "…at most 10…"],
         "tool_calls": 212,
         "lines": 412,
         "first_prompt_final": true,
         "last_assistant": "tool_use",
         "pending_questions": [],
         "last_ts": "…Z or null"}}}}
```

`size` and `mtime_ns` are the file's values when the checkpoint was taken; `offset` is where the last complete line ended, so `offset ≤ size` (they differ when the file ended in a half-written line).

On each build, per transcript inside the window:

1. **Unchanged** (same device, inode, size and `mtime_ns` as the checkpoint): reuse `info`.
2. **Grown, same file** (same `dev` and `ino`, current size > the checkpoint's `size`, and the SHA-1 of the first `min(4096, offset)` bytes matches `head_sha1`): read only the bytes from `offset` to the end, process complete lines, advance `offset` past the last newline, and recompute `info` from the updated state.
3. **Anything else** (shrunk, replaced, rewritten, or no checkpoint): full parse, which produces a fresh checkpoint.

The state reproduces a full parse exactly:

- **Files touched.** The published value is the 10 alphabetically smallest distinct paths (plan 1's cap), so the checkpoint keeps only those 10 and merges new paths in. The file-path scan still runs on every line.
- **Tool calls** are additive.
- **First prompt.** It comes from the first 50 lines, which never change in an append-only file. It is fixed once 50 complete lines exist (`lines` counts the newlines consumed) or a prompt was found with no half-written line after it; until then it is re-read from the head (≤ 50 lines) after each tail parse.
- **Last turn.** Only the final assistant message matters. The checkpoint keeps that row's own kind (`tool_use` if it called a tool, else `question` or `end_turn` by whether its last text ends with `?`) and the ids of its `AskUserQuestion` calls not yet answered; the published kind is `question` while any remain. A new assistant row replaces the summary; a new user row's tool results remove answered ids. A tool result always follows its tool call, so tracking answers only after the final assistant message is exact.
- **Last-turn timestamp.** Plan 1's rule is the timestamp of the last user or assistant row, in file order, whose timestamp parses. `last_ts` in the checkpoint is exactly that value for the bytes consumed so far; how the forward pass computes it without decoding every user row is in §3.4.
- **A half-written last line** is not consumed: `offset` stops at the last newline, so the line is picked up whole on the next build instead of being skipped for good.

Old version-1 cache files are treated as empty, so the first build after upgrading is a cold build.

### 3.4 Faster full parses

Cold cost is Python work per row. The full parse changes how rows are handled, not what they produce:

- **User rows are not decoded in the forward pass.** They make up most of a transcript's bytes (tool results). A row is recognised as a user row by the compact `"type":"user"` key Claude Code writes. User rows since the most recent assistant row are held in memory undecoded and decoded only when they can matter: at the end of the pass (for answered question ids and the timestamp), or when the next assistant row has no valid timestamp (so `last_ts` falls back to them, exactly as plan 1's rule requires). Otherwise they are discarded when the next assistant row arrives. The checkpoint stores only the facts derived from them, never raw rows.
- **Assistant rows** are still decoded, since tool calls and the last-turn summary come from them.
- **Rows that are neither** are skipped before decoding when they cannot be user or assistant rows. A row that contains `"user"` or `"assistant"` in any other form is decoded as today, so an unexpected serialisation falls back to the exact slow path rather than being missed.
- **Timestamps** are parsed only for the rows that can set `last_ts`, instead of on every row.

Exactness is shown by a one-off equivalence run during development: every transcript on the author's machine is parsed by both the plan 1 code and the new code, and every `TranscriptInfo` field must match. The result is recorded in the PR. It is not a CI test.

### 3.5 Parallel full parses (conditional)

If cold builds are still ≥ 5 s after §3.4 on the author's machine, then whenever a build needs more than 50 full parses they run in a process pool (one transcript per task, workers capped at the CPU count), with the same per-file error handling; 50 or fewer stay in-process, so steady-state builds never pay the pool's start-up. It is held back because a cold build happens about once per install and the pool adds start-up cost and process handling; the plan decides from measurements.

### 3.6 Write only changed caches

The desktop, transcript and PR caches each track whether an entry was added, changed or removed during the build and are rewritten only then, through plan 1's atomic write helper. `sessions-index.json` is still written on every build because `generated_at` changes; plan 3 must ignore that write when deciding to refresh.

### 3.7 Build statistics

`build_index(opts, *, stats=None)` accepts an optional collector that the build fills in:

| Field | Meaning |
|---|---|
| `desktop_decoded` | records read and decoded this build |
| `desktop_served_last_good` | decode failures served from the cache |
| `transcripts_full_parsed` | transcripts parsed from the start |
| `transcripts_tail_parsed` | transcripts parsed from a checkpoint |
| `transcript_bytes_read` | bytes read by transcript parses (excluding the ≤ 4 KB identity check and the ≤ 50-line first-prompt head) |
| `caches_written` | names of cache files rewritten |

The collector is not part of the index, so schema v1 and its contract test are unchanged. Zero `git` subprocesses (§3.2) is not a counter: the budget tests fail if a build starts any subprocess at all.

## 4. Error handling

- A corrupt or wrong-typed cache file or entry is ignored; that source falls back to the cold path. This extends plan 1's behaviour to the new desktop cache.
- A failure anywhere in a tail parse falls back to a full parse of that transcript. If the full parse fails, the transcript gets a per-file `SourceError`, as today.
- `repo_root` never raises: unreadable `.git` files, broken `gitdir:` lines, a `gitdir:` that points at a git dir that no longer exists, and permission errors resolve as "no root found here" and the walk continues upward.
- Mid-write desktop reads are handled by §3.1's last-good rule.

## 5. Testing

- **Unit tests per part:** the desktop cache (hit, miss, mid-write fallback, persistent failure reported, pruning, bad cache entries); `repo_root` (normal repo, linked worktree, submodule-style `.git` file without `commondir`, deleted folder, no repo, the real-`git` agreement test); checkpoints (unchanged, grown, half-written last line, truncated, replaced by another file, head rewritten, a question answered in a later tail); cache writes skipped when nothing changed.
- **Exactness test in CI:** for a set of synthetic transcripts, appending rows in several steps with tail parses in between yields the same `TranscriptInfo` as one full parse of the final file.
- **Budget tests on a realistic fixture**, generated once per test session in a temporary directory: about 260 desktop records of roughly 650 KB each (padded with a large unused field, as real records are), a few hundred transcripts with the real row mix (tool results of tens of KB, attachments, assistant rows), and project folders that are real repositories with linked worktrees. Three scenarios, asserted with the §3.7 counters rather than wall-clock time:
  - cold: every record decoded, every transcript fully parsed, zero `git` subprocesses;
  - unchanged rebuild: zero records decoded, zero transcripts parsed, no cache rewritten;
  - steady state (5 records rewritten, 5 transcripts appended): exactly 5 records decoded, exactly 5 tail parses, and bytes read equal to the bytes appended (plus any half-written line left by the previous build).
  A generous wall-clock ceiling per scenario still catches a gross slowdown on slow CI runners: 60 s cold, 10 s for each warm scenario. If generating the fixture adds more than about 20 s to a CI job, the budget tests run on one matrix entry only.
- **Hard budgets on real data** are checked on the author's machine through `scoutctl session index --no-gh` — cold, unchanged rebuild, and steady state while sessions are running — and the numbers are recorded in the PR.

## 6. Rollout

- Code lands on a branch stacked on plan 1's branch while #243 is open, and is rebased onto `main` once #243 merges.
- The new desktop cache file and the version-2 transcript cache are created on first use. The first build after upgrading is cold, which goal 1 keeps under 5 s.
- The main spec's §8 row 1b points here.

## 7. Decisions

- The warm budget covers the app's steady state (records rewritten and transcripts growing), not only a rebuild with nothing changed (Jordan, 2026-09-26).
- Incremental caches inside each loader (approach A) over a whole-build snapshot or a long-running process (Jordan, 2026-09-26).
- A transient mid-write decode failure is served silently from the last good record; the same unreadable file on a later build is reported.
- Transcript output must stay identical; the cold-path speed-up is accepted only with a field-by-field equivalence run on real transcripts.
- CI asserts work done, not time taken; absolute budgets are checked on the author's machine.
