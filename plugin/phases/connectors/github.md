---
phase: connector
name: github
slot: outbound-scan
mode: [consolidation]
requires: github
---

## GitHub Outbound Scan — What {{USER_NAME}} Did

Check recent code activity by {{USER_NAME}} using the `gh` CLI. This reveals completed work that should be reflected in action items and the KB.

The `python3 -c` time cutoffs in the GitHub scan and query commands are UTC instants (the trailing `Z`) and run the same on macOS and Linux. They are query filters only: a time you write into the vault or a DM is still converted to the display zone (see Timezone Handling).

### Recent PRs by {{USER_NAME}}

```bash
gh pr list --author @me --state all --json number,title,url,repository,state,updatedAt --limit 10
```

For each PR found:
- **Merged PRs** = completed work. Mark any corresponding action items as Done.
- **Open PRs** = work in progress. Note the PR status (draft, review requested, changes requested, approved).
- **Closed PRs** (not merged) = abandoned approach. Check if the underlying issue is still open.

### PRs {{USER_NAME}} Was Involved In

```bash
gh search prs --involves @me --updated ">=$(python3 -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 4 * 3600)))')" --json number,title,url,repository
```

This catches PRs where {{USER_NAME}} reviewed, commented, or was mentioned — broader than just authored PRs.

### PR Reviews by {{USER_NAME}}

Check if {{USER_NAME}} submitted reviews on others' PRs. A submitted review means:
- The review request action item is handled
- {{USER_NAME}} may be waiting for the author to address feedback (new watching item)

### Recent Commits

For key repos in `{{GITHUB_REPOS}}`, check recent commit activity:

```bash
gh api "/repos/{owner}/{repo}/commits?author={{GITHUB_USERNAME}}&since=$(python3 -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 3600)))')" --jq '.[].commit.message' 2>/dev/null | head -20
```

Commits outside of PRs (direct pushes) also indicate completed work.

---
phase: connector
name: github
slot: inbound-scan
mode: [consolidation, briefing]
requires: github
---

## GitHub Inbound Scan — What Happened to {{USER_NAME}}

Check for GitHub activity directed at {{USER_NAME}} — review requests, assignments, and comments.

### PR Review Requests

**Always run this search, on every briefing and consolidation run except `weekend-briefing`.** It is the only way a review request reaches the run when nothing else in the vault mentions the PR. The runner also saved its result before the session started, as `pr_review_requested` in `.scout-cache/session-context.json`; a PR in either list counts.

```bash
gh search prs --review-requested @me --state open --json number,title,url,repository,author
```

⚠️ **`--review-requested @me` expands team membership**, so this list mixes genuine review asks with {{USER_NAME}}'s *own delegated work* (a Linear issue delegated to an agent like Devin → the agent opens the PR and requests {{USER_NAME}}'s *team*). It also holds requests sent only to one of {{USER_NAME}}'s teams. Classify **every** PR in the list, one at a time, team-addressed ones included, with the three-bucket rule under **PRs Requesting {{USER_NAME}}'s Review** in the briefing query slot:
1. **Own delegated work** (`devin/` branch or bot author implementing {{USER_NAME}}'s own issue, or linked issue assigned to {{USER_NAME}}) → track-to-merge, **not** review.
2. **Requested by name** (`{{GITHUB_USERNAME}}` is a `User` in `reviewRequests`) → real review action item.
3. **Requested via team only** → shared queue, any teammate can satisfy: 🟢 Watching at first (see the action-items phase).

Run `gh pr view N --repo R --json author,headRefName,reviewRequests,isDraft` for each PR to classify it. Don't classify from the title, the author or the search result alone. For buckets 2–3, note who requested it, which repo, how long it's waited (prioritize older), and PR size if available.

**No silent drops.** Every PR in the list ends the run as an action item in its bucket's section, with `owner/repo#N` in its `- Refs:` line. A PR already tracked by an item only needs that ref. If one should not be an item (it merged or closed since the search, say), write `- Skipped review request owner/repo#N — <reason>` under this run's 🪵 Run notes entry. Before committing, `scoutctl action-items check-review-requests` lists any pre-fetched request still unaccounted for (*Review Requests Never Drop* in the action-items phase).

### PR Action-Item Context

When a PR becomes an action item, the line must state **what the PR does, which project it serves, and what's blocked downstream** — never just the title and merge status. A reader seeing only "Review PR #123 — open" has to open the PR to learn whether it matters; the action item exists to spare them that. For every PR-derived item, capture:

- **What it does** — the change in one plain-English phrase, not the raw PR title.
- **Which project/area** — resolve the repo/PR against the KB so it links to the right project entity, not just a bare repo name.
- **What's blocked** — who is waiting on it, or what ships once it merges. "Review feedback needing a response" and "approval blocking a release" are different priorities; the downstream context is what distinguishes them. Before writing that a PR waits on another PR, or that it doesn't, apply **PR status claims** in the GitHub Cross-Check.
- **How long it's waited** — staleness drives ordering.

A bare title + status is not enough context to act on.

### New Issues Assigned

```bash
gh search issues --assignee @me --state open --updated ">=$(python3 -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 24 * 3600)))')" --json number,title,url,repository
```

Check for issues recently assigned to {{USER_NAME}} or recently updated issues where {{USER_NAME}} is the assignee.

### Comments on {{USER_NAME}}'s PRs

For key repos in `{{GITHUB_REPOS}}`, check recent comments:

```bash
gh api "/repos/{owner}/{repo}/issues/comments?since=$(python3 -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 4 * 3600)))')" --jq '.[] | select(.user.login != "{{GITHUB_USERNAME}}") | {body: .body[:100], user: .user.login, issue_url: .issue_url}' 2>/dev/null | head -50
```

Comments on {{USER_NAME}}'s PRs may contain:
- Review feedback needing response
- Questions about the implementation
- Approval or merge notifications
- CI/CD status updates

### Repository Activity

For repos in `{{GITHUB_REPOS}}`, check for significant activity:
- New releases or tags
- Branch protection changes
- New contributors or team changes

**Also scan for recently-created/updated repos** — repo-creation and repo-push activity is invisible to the PR scans above (a brand-new repo has no PR):

```bash
gh repo list {{GITHUB_USERNAME}} --limit 30 --json name,createdAt,updatedAt --jq 'sort_by(.updatedAt)|reverse'
```

A newly-created repo here may **close an open "stand up repo X" action item** — check it before carrying that item as "not done." Match on a fuzzy/listing basis, not an exact provisional name (an item might say `scout-ios` while the real repo is `scout-iOS-app`). Use `gh repo list`, never `gh search prs`, to verify a repo-creation item.

---
phase: connector
name: github
slot: query
mode: [briefing]
requires: github
---

## GitHub Query — Briefing Data Gathering

### Open PRs by {{USER_NAME}}

```bash
gh pr list --author @me --state open --json number,title,url,repository,reviewDecision,updatedAt,isDraft
```

For each open PR, note:
- Current review status (pending, approved, changes requested)
- Whether it's a draft
- How long it's been open
- Any CI checks failing

---
phase: connector
name: github
slot: query
mode: [consolidation, briefing]
requires: github
---

### PRs Requesting {{USER_NAME}}'s Review

```bash
gh search prs --review-requested @me --state open --json number,title,url,repository,author,createdAt
```

⚠️ **Do not blindly bucket these as "review these PRs."** `--review-requested @me` **expands team membership**, so this list mixes genuine review asks with {{USER_NAME}}'s *own delegated work* — e.g. a Linear issue {{USER_NAME}} delegated to an agent (Devin), where the agent opened the PR and requested {{USER_NAME}}'s *team*. That PR is "{{USER_NAME}}'s work to land/track to merge," **not** their queue to review. Presenting it as a review obligation creates a self-review loop.

Classify **every** returned PR, team-addressed ones included, into one of three buckets before writing any action item. The `author` field is already fetched; pull the branch + reviewer breakdown per PR:

```bash
# For each PR number N in repo R from the list above:
gh pr view N --repo R --json author,headRefName,reviewRequests,isDraft
```

1. **Your own delegated work → "track to merge," NOT review.** Any of:
   - the PR's branch is an agent-delegation branch (`headRefName` starts with `devin/`, or author is a bot like `app/devin-ai-integration`), **and** it implements an issue assigned to {{USER_NAME}}; or
   - the linked issue is assigned to {{USER_NAME}} (the PR is the implementation of {{USER_NAME}}'s own in-progress issue).
   Surface these under in-progress/track-to-merge framing, never "review requested to you." They are 🟢 Watching until the merge state is `CLEAN`, then {{USER_NAME}}'s to merge (see *🔴 and 🟡 Are {{USER_NAME}}'s Move* in the action-items phase).
2. **Requested by name (direct) → real review action item.** {{USER_NAME}}'s GitHub login (`{{GITHUB_USERNAME}}`) appears as a **User** entry in `reviewRequests`. This is a personal review ask.
3. **Requested via team only → shared queue (softer).** {{USER_NAME}} is *not* a named `reviewRequests` user but one of their **Team** entries is. Any teammate can satisfy it — present it as a team review queue item, distinct from a direct personal request, and don't escalate it as if only {{USER_NAME}} can clear it. It is 🟢 Watching until nobody on the team has picked it up for 2 working days, then 🟡 (the same rule in the action-items phase).

Only buckets 2 and 3 are review action items; bucket 1 belongs with {{USER_NAME}}'s own open work. When in doubt between a direct vs team request, check whether `{{GITHUB_USERNAME}}` is a named `reviewRequests` user. Every bucket still ends as an item with the PR's `owner/repo#N` in its `- Refs:` line, or as a skip line with a reason; see **No silent drops** in the Inbound Scan.

---
phase: connector
name: github
slot: query
mode: [briefing]
requires: github
---

### Recent Comments on Open PRs

For repos in `{{GITHUB_REPOS}}`, check for recent comment activity on open PRs:

```bash
gh api "/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=5" --jq '.[].number' 2>/dev/null | while read pr; do
  gh api "/repos/{owner}/{repo}/pulls/$pr/comments?since=$(python3 -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 24 * 3600)))')" --jq '.[] | {user: .user.login, body: .body[:80]}' 2>/dev/null
done | head -30
```

### Open Issues Assigned

```bash
gh search issues --assignee @me --state open --json number,title,url,repository,labels
```

---
phase: connector
name: github
slot: cross-check
mode: [consolidation, briefing]
requires: github
---

## GitHub Cross-Check

### Per-Claim Evidence Anchors

Every PR / merge / review / CI state claim — whether in a DM summary or an action-item row — must carry its **own** inline evidence anchor: the specific field value that proves it, not just a trailing "Source: GitHub" line. Anchor each claim to the datum:

- "merged" → cite the `mergedAt` timestamp (e.g. _merged 2026-06-01T14:22Z_)
- "approved" / "changes requested" → cite `reviewDecision` and the reviewer
- "CI green/red" → cite the `statusCheckRollup` conclusion
- "PR #N exists / open" → cite `#N` and `state`

A trailing source line covering a multi-claim sentence is insufficient — if a sentence asserts three states, it needs three anchors. This makes each claim independently verifiable and prevents one stale field from contaminating a whole row (Pattern #69 family).

Before promoting any candidate action item to To Do, verify against GitHub:

**When an action item references a specific PR, check the PR's current state:**

```bash
gh pr view {number} --repo {owner}/{repo} --json state,reviewDecision,mergedAt,closedAt,statusCheckRollup,reviewRequests,latestReviews,body,baseRefName,mergeStateStatus
```

- If the PR was merged since the action item was created, the item may be Done.
- If new commits were pushed that address review comments, update the item accordingly (e.g., "changes requested" -> "changes pushed, re-review needed").
- If CI checks are now passing that were previously failing, update the item.

**PR status claims.** Before an item says a PR "needs review", is "ready to merge" or "independent", or that {{USER_NAME}} should ask someone, check, this run, from the fields above:

1. **Whether review is what's missing.** `reviewDecision` is set only when branch protection or a ruleset requires reviews: `REVIEW_REQUIRED`, `CHANGES_REQUESTED` or `APPROVED`. Empty means no approval is required, so "needs review" is wrong. `mergeStateStatus` then says what actually stops the merge: `BLOCKED` (a required review or check), `BEHIND` (needs the base merged in), `DIRTY` (conflicts), `UNSTABLE` (a failing check), `DRAFT` (a draft, not ready for review) or `CLEAN` (ready). `UNKNOWN` means GitHub hasn't computed it yet, which is common right after a push: query once more, and if it is still `UNKNOWN`, don't call the PR ready to merge. Don't read branch protection endpoints for this: a repo on rulesets returns 404 from `/protection`, and that endpoint 404s for non-admins too.
2. **Who was asked.** `reviewRequests` lists only reviewers who haven't submitted yet. Read `latestReviews` for those who have. Name them; name a `Team` entry as the team, as in the direct-vs-team buckets above. Never write "ask X" or "no reviewer yet" without checking both.
3. **Functional dependencies.** Apply the functional-dependency rule under `Depends:` in the action-items phase's **Board Fields**, from this PR's `body` and the bodies of the PRs already fetched this run. Never call such a PR independent or suggest merging it alone: say what it waits on.
4. **Summaries are leads, not facts.** A claim from a session transcript, an earlier action item or another agent's summary is re-checked against these fields before it is repeated.

If a check can't run, write the claim as `[unverified — not queried this run]`.

**When an action item references code work, check for related PRs:**

```bash
gh search prs --author @me --repo {owner}/{repo} -- "{search terms}"
```

If a PR exists for the work described in the action item, link them and use the PR status as the source of truth.

**When an action item is about reviewing a PR, check if the review was already submitted:**

```bash
gh api "/repos/{owner}/{repo}/pulls/{number}/reviews" --jq '.[] | select(.user.login == "{{GITHUB_USERNAME}}") | {state: .state, submitted_at: .submitted_at}' 2>/dev/null
```

If {{USER_NAME}} already submitted a review, the item is Done.

---
phase: connector
name: github
slot: update
mode: [consolidation, briefing]
requires: github
---

## GitHub-Sourced KB Updates

After scanning GitHub, update the knowledge base with current code activity.

### PR Status in Project Files

For each active project in the KB, check if any open PRs are associated with that project. Update the project file with:
- PR number, title, and current status
- Review status (who reviewed, what was the decision)
- CI status if relevant
- Link to the PR

Update or remove references to PRs that have been merged or closed since the last run.

### Contributor Activity

If GitHub activity reveals new contributors to projects {{USER_NAME}} is involved in, add them to `people.md`:
- Name and GitHub username
- Context: "Contributor to {repo} — opened PR #{number}" or "Reviewed PR #{number} in {repo}"
- Role `[single-source]` based on their contribution pattern

### Issue Tracker Updates

If using GitHub Issues (instead of or alongside Linear), sync issue statuses:
- Verify open/closed status matches the KB
- Update labels and assignees
- Note any new issues filed in key repos

### Repository State

If significant repo changes occurred (new release, branch changes, CI/CD updates), note them in the relevant project files.
