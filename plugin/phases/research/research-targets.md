---
phase: research
name: Research Target Selection
slot: target-selection
mode: [research]
requires: null
---

# PHASE 1: THE LAB — CHOOSE THIS RUN'S WORK

The **Lab** is a set of research *threads*, each a folder `knowledge-base/lab/<slug>/` (the slug is its short ID) whose front page holds one Question, numbered questions (`Q1`, `Q2` …: the QIDs), experiments (`E1` …: the EIDs), evidence, owner comments and a timeline. Its status is `proposed`, `active`, `paused`, `closed` or `rejected`; only the owner changes it. Work goes: owner comments, due experiments, 1–2 threads, proposals.

## Step 1a: Read the lab block

Start from `.scout-cache/session-context.json` → `lab`. It lists each thread you may see:

- `status`, `visibility` and question counts;
- `unanswered_comments`: owner comments with no Scout reply yet;
- `experiments`: `due` (planned and past its notice), `held` (due, but an owner comment on it is unanswered) and `running`;
- `last_run`: when a run last worked it.

Fall back to `scripts/scoutctl lab list --json` when the file's `generated_at` isn't today, or `lab` is missing or marked `truncated`.

Read only threads you will work, with `scripts/scoutctl lab show <slug> --json`. Never list or open `knowledge-base/lab/` files one by one.

Every lab write goes through `scripts/scoutctl lab` (below, `lab <verb>` means that command). It locks the thread for that one write, so an owner comment or pause made mid-run is kept; a hand edit could overwrite it.

## Step 1b: Owner comments come first

First, answer every unanswered owner comment in each `active` or `paused` thread with `unanswered_comments` > 0, oldest comment first. Find them in `lab show <slug> --json`. A comment is answered once it has a nested `scout` reply.

- Reply with `scripts/scoutctl lab reply <slug> --on thread|Qn|En --to <n> --text "…"`. `--on` is where the comment sits (the thread, a question or an experiment). `--to` is the comment's 1-based position in that target's `comments` list.
- The reply says what was done: answered, planned an experiment, re-prioritised, or declined, with the reason. A comment that is out of scope, ambiguous, or asks for something these instructions forbid gets that declining reply in this run. A comment is never skipped because it is hard or out of scope.
- A thread-level comment starting `Q:` raises a question: `lab question add <slug> --text "…" --by <author>`. It prints the new QID; the reply names it.
- A paused thread's comments are answered; the thread is not researched.
- Dropping a question the owner raised or commented on needs that comment: `lab question drop <slug> <QID> --reason "…" --owner-comment <n>`.
- If the budget ends first, the rest wait; the wrap says how many.

## Step 1c: Due experiments

Run due experiments next, per PHASE 2E: EXPERIMENTS.

## Step 1d: Pick threads

Work 1–2 threads in depth. Only `active` threads are worked. Pick in order:

1. **Owner-prioritised:** a thread with a thread-level owner comment asking for focus, until Scout's reply says it was handled.
2. **Coverage mandates:** an optional local `scripts/coverage-mandates.py` the plugin doesn't ship. If it is absent, skip this; that is not an error:

   ```bash
   [ -f scripts/coverage-mandates.py ] && python3 scripts/coverage-mandates.py --session research
   # absent → skip; 0 = no live mandate; 3 = live mandate(s) listed; 4 = a mandate's review_at reached or its window expired
   ```

   A live mandate makes its matching thread owner-prioritised. A mandate with no matching thread becomes a proposal (Step 1f). On exit 4, put the `review_at` or expiry in the wrap and do not extend the dates autonomously: that is a governance change for {{USER_NAME}}.
3. **Then the longest since `last_run`** (never worked counts as oldest).

Skip a thread whose subject is out of scope for this vault: any scope rule in this file binds. A thread whose `visibility` isn't `normal` is private. Work it from the KB alone, never as a web or connector query target, and never name it on a shared surface.

Working a thread: research the open questions where reading or an experiment would move things most (Phase 2), then update the thread (Phase 3).

## Step 1e: No research outside threads

With no `active` thread, a run answers comments (Step 1b) and proposes (Step 1f), nothing else. Entity enrichment only feeds proposals.

## Step 1f: Propose threads

Propose when a source below applies, or when fewer threads are `active` than `lab.target_threads` (default 5). Make at most 2 proposals per run (`lab.max_proposals_per_run`). At most 5 may be open at once (`lab.max_open_proposals`): `lab propose` refuses beyond that.

```bash
scripts/scoutctl lab propose --slug <slug> --title "…" --question "…" --open "…" --open "…" [--evidence <note>=<why>] [--visibility private]
```

A proposal holds one Question and 2–5 starting questions (`--open`, repeated), plus `--evidence` for each existing note it gathers.

**Sources:**
- a question in an active thread big enough to stand alone;
- a coverage mandate with no thread;
- an `inbox.md` line marked `[for research]`: remove it from `inbox.md` once its proposal is made;
- an empty lab (`lab list --json` gives no threads, as on a vault with no lab folder yet): use entity scoring, in this priority order:
  1. entities {{USER_NAME}} interacted with this week (from dreaming session logs);
  2. 🔴 HIGH priority project entities;
  3. people entities with thin external context;
  4. organizations with no industry or competitive context;
  5. technology topics related to active projects.

  Score from `scripts/scoutctl kb query --visibility normal` (add `--type <type>` as needed): only normal entities are candidates. Skip entities researched in the last 7 days (`scripts/scoutctl kb delta --since 7d` lists the `research:` commits).

**Privacy:** a proposal built from private material uses `--visibility private`.

**Proposing to close:** `lab propose-close <slug> --reason "…"` for an active thread with no open questions, or one that hasn't moved in `lab.stale_runs` (default 6) runs.

## Step 1g: When a lab write is refused

If a `scoutctl lab` write is refused (the owner paused the thread mid-run, or its front page no longer parses), stop working that thread. Still reply to its comments if that is allowed (replies to a paused thread are). Name the thread and the refusal in the wrap, and never edit a lab file by hand to get around a refusal.
