# KB Backend Phase 2 — Gate G0 Parity & 30-Item Track R Pilot Findings

**Date:** 2026-11-27. **PRs:** #829 (Phase 1 `KBBackend` & `RelateBackend`), #830 (Phase 2 `graph_context`, graph CLI verbs, 3-arm eval axis, and Track R runner).

This note records the anonymized empirical results from running **Gate G0** (multi-commit, multi-reader parity & privacy verification) and a **30-item × 3-arm live Track R pilot** (`90` interleaved read-only Claude Code sessions on Sonnet) against a mature production Scout vault. Raw vault questions, answers, and transcripts contain personal/workspace data and remain strictly in owner-only (`0700`/`0600`) local storage outside this repository (`refuse_public_repo_destination`).

---

## 0. Headline Takeaways

1. **Gate G0 (Parity & Reader Isolation) passed with zero discrepancies:**
   - Tested `RelateBackend` (`relate-db` 1.3.0) vs `FilesBackend` across **3 historical commits** of a live production vault and **3 reader scopes** (`owner`, `elsewhere`, and a restricted `delegate:<id>` scope): **0** outside-view notes, **0** leaked hidden-note identifiers, and **0** output mismatches across `stats`, `entity`, `related`, `neighbors` (`depth=2,3`), `paths` (`150` entity pairs), `backlinks`, and `link_gaps`.
2. **In the 90-session Track R pilot, Arm B (`relate-verbs`) improved efficiency and eliminated Arm A's lone citation hallucination — driven 100% by `kb search --ranked`:**
   - **Arm A (`files`):** `93.3%` strict accuracy (`28/30`; `96.7%` / `29/30` semantic), `4.0` median tool calls (`129` total), `161,378` median tokens.
   - **Arm B (`relate-verbs`):** **`96.7%` strict accuracy (`29/30`; `100.0%` / `30/30` semantic)**, **`3.5` median tool calls (−12.5%, `124` total)**, **`151,374` median tokens (−6.2%)**, `50.0%` verb uptake (`58.3%` on `multi_hop`).
   - **Arm C (`relate-precompute`):** `96.7%` strict accuracy (`29/30`; `100.0%` / `30/30` semantic), `4.0` median tool calls (`125` total), `159,476` median tokens (−1.2%), `0.0%` verb uptake.
   - Across all `19` graph-verb invocations on Arm B, **`19/19` (`100%`) used `scoutctl kb search "<terms>" --ranked`**, with **`0` organic calls** to `kb neighbors`, `kb paths`, `kb backlinks`, or `kb link-gaps`.
3. **2-hop named-entity Q&A hits a ~97% ceiling on `FilesBackend`:**
   - Whenever a question names a starting project or person, Sonnet already solves 2-hop lookups in ~4 calls via ripgrep + `kb read`. Because `FilesBackend.ranked_search` already provides a pure-Python lexical fallback without requiring `relate-db`, adopting an optional Rust/Cypher graph engine in production runs should be gated on either **Track W scheduled consolidation** (`eval.sh compare kb-front-page --arms a,b,c`) or **unanchored 3+ hop structural queries**.
4. **Arm C (`graph_context` in `session-context.json`) is strictly a Track W mechanism:**
   - Standalone Q&A sessions read `.scout-cache/session-context.json` **`0/30` times** across all three arms because consolidation pre-flight instructions (`SKILL.md` / `DREAMING.md`) do not run during single-question Q&A probes. Evaluating Arm C requires Track W consolidation scenarios.

---

## 1. Experimental Setup

- **Runner:** `plugin/scripts/eval.sh track-r-run` (`tests/eval/harness/track_r_runner.py`).
- **Checkout & exclusion discipline:** Detached temporary git worktrees at the pinned `as_of_sha`, unlinking per-item `exclude_paths` (such as `knowledge-base/scout-mistake-audit.md` on regression items) prior to snapshot build.
- **Isolation (`claude_shim.py`):**
  - Model: Sonnet (`--model sonnet`) on subscription login (`apiKeySource: none`).
  - Zero MCP connectors (`{"mcpServers": {}}` under `--strict-mcp-config`; `guard_init` aborts on any connector leak).
  - Read-only enforcement: `Edit` and `Write` omitted from `--allowedTools`; OS Seatbelt `allowWrite` restricted to each sample's result dir plus the detached worktree's `.scout-cache` and `.scout-logs`.
  - Hooks active: `PreToolUse` `kb-read-gate` (fired `27` times on Arm A, `26` on Arm B, `30` on Arm C when the model attempted raw `cat`/`grep` on `knowledge-base/`) and `PostToolUse` `kb-read-label`.
  - Pre-warmed store: `RelateBackend` `.rl` snapshot built once per detached worktree before timing Q&A sessions so all 30 items measure steady-state warm queries.
- **Interleaving:** For each item `1..30`, sessions ran back-to-back in `Arm A → Arm B → Arm C` order.

---

## 2. Pilot Results (`N = 30` Audited Items × 3 Arms = `90` Sessions)

### 2.1 Overall Arm Comparison

| Metric | Arm A (`files`) | Arm B (`relate-verbs`) | Arm C (`relate-precompute`) |
| :--- | :---: | :---: | :---: |
| **Sessions scored / aborted** | 30 / 0 | 30 / 0 | 30 / 0 |
| **Strict deterministic accuracy** | 93.3% (28/30) | **96.7% (29/30)** | **96.7% (29/30)** |
| **True semantic accuracy*** | 96.7% (29/30) | **100.0% (30/30)** | **100.0% (30/30)** |
| **Mean Recall@5** (28 non-abstention items) | 0.458 | **0.473** | 0.432 |
| **Mean Precision@5** | 0.221 | **0.227** | 0.217 |
| **Median calls to first gold hit** | 1.0 | 1.0 | 1.0 |
| **Median tool calls per item** | 4.0 (`129` total) | **3.5 (`124` total)** | 4.0 (`125` total) |
| **Median total tokens (in+out+cache)** | 161,378 | **151,374 (−6.2%)** | 159,476 (−1.2%) |
| **Median wall duration** | 12.2s | 14.7s | **11.3s** |
| **Graph verb uptake (% of sessions)** | 0.0% (0/30) | **50.0% (15/30)** | 0.0% (0/30) |
| **Total API-equivalent cost** | $2.67 | **$2.62** | $2.69 |

*\*One `temporal` item (`pilot-temp-02`) was answered accurately by all three arms with the full month name (`"November 23, 2026"`) while strict `expected_tokens` checked abbreviated `"Nov 23"`.*

### 2.2 Per-Category Breakdown

| Category (`N`) | Arm A (`files`) | Arm B (`relate-verbs`) | Arm C (`relate-precompute`) | Arm B Verb Uptake |
| :--- | :---: | :---: | :---: | :---: |
| **`multi_hop` (12)** | 11/12 (91.7%) · 4.5 calls · 194k tok | **12/12 (100%)** · 5.0 calls · 194k tok | **12/12 (100%)** · 5.5 calls · 233k tok | **7/12 (58.3%)** |
| **`citation` (5)** | 5/5 (100%) · 4.0 calls · 152k tok | 5/5 (100%) · **3.0 calls · 134k tok** | 5/5 (100%) · 3.0 calls · 132k tok | 2/5 (40.0%) |
| **`status` (3)** | 3/3 (100%) · 4.0 calls · 131k tok | 3/3 (100%) · **3.0 calls · 129k tok** | 3/3 (100%) · 3.0 calls · 129k tok | 2/3 (66.7%) |
| **`temporal` (3)** | 2/3* · 3.0 calls · 99k tok | 2/3* · **2.0 calls · 93k tok** | 2/3* · 2.0 calls · 94k tok | 1/3 (33.3%) |
| **`single_hop` (3)** | 3/3 (100%) · 4.0 calls · 122k tok | 3/3 (100%) · **3.0 calls** · 148k tok | 3/3 (100%) · 3.0 calls · 124k tok | 1/3 (33.3%) |
| **`disambiguation` (2)** | 2/2 (100%) · 4.0 calls · 172k tok | 2/2 (100%) · **3.0 calls · 112k tok** | 2/2 (100%) · 3.0 calls · 133k tok | 1/2 (50.0%) |
| **`abstention` (2)** | 2/2 (100%) · 3.5 calls · 155k tok | 2/2 (100%) · 4.0 calls · 148k tok | 2/2 (100%) · 4.0 calls · 172k tok | 1/2 (50.0%) |

### 2.3 Discordant Item & Retrieval Diagnostics

- **`pilot-mh-05` (`multi_hop`):** Arm A identified the right entity and facts (`5` calls, `213k` tokens) but failed `citation_faithfulness` by citing a nonexistent path (`knowledge-base/action-items/archive/...` instead of `action-items/archive/...`). Arm B used two `kb search --ranked` calls (`4` total calls, `162k` tokens — **23.7% fewer tokens**) to retrieve the canonical person and project-decisions notes directly, passing `citation_faithfulness` cleanly.
- **Why `R@5` averaged `0.46–0.47` despite `97–100%` semantic accuracy:** On Turn 1, Sonnet typically ran plain `scoutctl kb search "<term>"`, which emits ripgrep matches in alphabetical path order — placing `knowledge-base/projects/<slug>/archive/*-pre-restructure.md` snapshots ahead of live front pages in the first 5 unique retrieved paths before subsequent targeted `kb read` calls fetched hop-2 and hop-3 notes.
- **Gate G1 check at pilot scale (`N = 30`):** `b_vs_a` met all non-inferiority (`status`, `temporal`, `citation`, `abstention` all `0.000 > -0.030`) and efficiency gates (`token_ratio = 0.938 <= 1.10`, `call_ratio = 0.875 <= 1.10`), with overall accuracy diff `+0.033` (`95%` cluster-bootstrap CI `[+0.000, +0.107]`) and `multi_hop` diff `+0.083` (`[+0.000, +0.250]`, McNemar `p = 1.000` at `N = 12` `multi_hop` items).

---

## 3. Implications & Next Evaluation Steps

1. **Run Track W (`eval.sh compare kb-front-page --arms a,b,c --reps 3`):** Tests Arm C (`graph_context` in `session-context.json`, which consolidation sessions actually load at startup) and Arm B on real KB-writing consolidation workflows.
2. **Isolate ranked search from graph storage:** Since `100%` of Arm B's organic verb usage was `kb search --ranked`, compare `FilesBackend.ranked_search` directly against `RelateBackend.ranked_search` before taking an optional external dependency for ranked note retrieval alone.
3. **Harder structural items & synonym grading:** Before running a 200+ item Gate G1 suite, support pipe-delimited date/token synonyms in `exact_answer_match` and add unanchored 3+ hop structural questions where ripgrep keyword lookup cannot jump straight to a named project page.
