# Changes Map — Plan 3: The App's Changes Tab (v1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner sees every change Scout made to itself, whether it helped, and what needs their OK, on one "Changes" tab in the macOS app, and decides there: approve or reject a governance change, undo a change, mark a verdict wrong, answer the out-of-vault consent question. The Proposals and Wishlist tabs go, and the run phases stop writing proposals.

**Architecture:** The engine gains the two documents the tab needs that don't exist yet (`scoutctl changes digest --json`, `scoutctl changes diff <id>`), derives `inline-comment` engagement from git on read, and closes the two safety gaps Plan 2 left open. The app reads three versioned JSON documents through one `ChangesService`, decides through one allow-listed `ChangesDecisions` runner (always `--surface app`), and renders with SwiftUI and Swift Charts. A final phase task retires proposals now that the tab exists. Everything lands on one branch as one PR, because the phase cutover must not ship before the tab it points at.

**Tech Stack:** Python 3 (engine, typer, pytest); Swift 5 / SwiftUI / Swift Charts, macOS 15.7 target, Swift Testing; Markdown phase fragments.

**Spec:** `docs/superpowers/specs/2026-11-17-changes-map-design.md`. Read §3, §6.3, §6.4, §7, §7a, §8, §9.1, §10 and §11 before your task. Plans 1 and 2 (`docs/superpowers/plans/2026-11-17-…plan-1…md`, `2026-11-19-…plan-2…md`) built the engine this plan reads; their code is on `main` under `plugin/engine/scout/changes/`. Plan 2's "Changed in review" note at its top supersedes parts of its body.

## The five plans

| Plan | Delivers | Status |
|---|---|---|
| 1 | Ledgers, git scanner, `engagement record`, reliance + consent, JSON contracts | merged (Raven-Scout/Scout#679) |
| 2 | Verdicts, calibration, owner decisions, governance path, run phases | merged (Raven-Scout/Scout#747, v0.18.22) |
| **3 (this)** | Changes tab v1; Proposals and Wishlist tabs removed; contract copies; consent question; `app-edit` and `inline-comment` rows; **the digest generator**; the two Plan 2 safety gaps; proposals retired in the phases | 1, 2 |
| 4 | Weekly digest DM, `/scout-status` digest section (both render Plan 3's `changes digest`) | 3 |
| 5 | Feedback backfill from the DM archive; opt-in transcript backfill | 1 |

## Decisions this plan makes

- **The digest generator moves here from Plan 4.** The tab's digest card (spec §9.1 v1) needs it; Plan 4 keeps the DM and `/scout-status`, which render the same document.
- **Engine draft PRs are left out of the "Needs your OK" strip.** No local record of them is trustworthy: the engine has no GitHub connector, and a run-recorded row naming a PR would be run-writable. GitHub already notifies the owner of a draft PR. A later plan can add them once a source exists.
- **`inline-comment` rows are derived from git on read, never stored.** Plan 1 made the kind owner-only, and runs can't write the owner store. The report counts owner-authored marker lines (`//==<<`, `scout: user:`) added by commits in the `interactive` lane, once per commit (ref = the commit sha). Known limit: `scan.lane_of` treats any unprefixed subject as interactive, so a run committing with an unprefixed subject could pass as the owner. The Scout coordinator has filed that `lane_of` gap as its own card; this plan does not fix it.
- **`app-edit` rows come from one choke point.** Every app vault write commits through `GitServiceProtocol.commitPaths`. A wrapper records `scoutctl engagement record app-edit --surface app` after each successful commit. A refused record (a scheduled run is live, so the owner check fails) is dropped silently: it is one lost feedback row, never a failed write.
- **Automatic rollback stays refused.** Plan 3 closes the two gaps that made it unsafe, but spec §7a makes turning a mode on a governance change, decided after the calibration report. `AUTO_ROLLBACK_LIVE` stays `False` and the CLI keeps refusing.
- **Swift Charts** draws the trend, the sparklines and the small multiples (spec §14 leaves it to the builder). The noise line shares the bar chart's plot: it is scaled onto the bar axis and labelled in noise units on the trailing axis.
- **One window for the tab:** `changes --json --since 365d --limit 5000` and `engagement --json --since 365d`, enough for the 12-month range. Refresh on FSEvents for `.scout-logs/changes.jsonl`, `.scout-logs/engagement.jsonl` and `governance-pending/`, on a 300 s timer, and after each owner decision.
- **Sidebar:** `.changes` takes the place of `.proposals`, at position 6 (Go shortcut ⌘6, SF Symbol `chart.xyaxis.line`, id `sidebar-changes`). `.wishlist` is removed; `.research` becomes ⌘7 and `.knowledgeBase` ⌘8. The Research tab keeps the shared per-file code.
- **Existing proposals drain, then stop.** Phase text applies any `Approved` or ripe proposal still in `dreaming-proposals.md` (committing main's four paths) and writes no new ones. `dreaming-proposals/` and the archive stay frozen for Plan 5's backfill (spec §11). New installs stop getting `dreaming-proposals.md`.
- **Fixture names are unique in the test bundle** (it is flat): `changes.json`, `engagement.json`, `changes-digest.json`.

## Global Constraints

- Contracts stay `schema_version: 1`; additions are new keys. The app decodes rows lossily and shows "needs a newer app" for `schema_version` > 1, as `RunLedger.parse` does.
- The app never edits a brain file or a pending governance file, and never writes the owner store directly: every decision is a `scoutctl` call with `--surface app`. Allowed: `changes approve <gov-id> --diff-hash <64 hex>`, `changes reject <gov-id>`, `changes undo <chg-id>`, `changes wrong <chg-id>`, `engagement consent --out-of-vault yes|no`, `engagement consent --show`, `engagement record app-edit --surface app`, plus the read commands. Ids are validated before the call: `^chg-[0-9a-f]{12}$`, `^gov-[a-z0-9][a-z0-9-]{0,60}$`, hash `^[0-9a-f]{64}$`.
- No content in a row or a log line: counts, kinds, ids, shas only (spec §2 anti-goals).
- Every identifier the tab shows is glossed in plain words (spec §2 "a wall of identifiers"): a `chg-…` id is never shown alone; `pattern-288` shows as the why's `gloss`.
- Verdict colours (spec §9.1): `helped` green, `no clear effect` grey, testing amber, `hurt` and `rolled back` red, `can't tell` grey; a hollow marker when `why.inferred`. Use the design system's existing tones (`DS.PillTone`), not new colours.
- Owner-only commands exit 2 while a scheduled run of the vault is live (`store.is_owner_caller`). The app shows "Scout is running right now. Try again when the run finishes." and changes nothing.
- Engine rules from Plan 2 still hold: engine code never runs `git add|commit|reset|checkout`; git subprocesses set `GIT_OPTIONAL_LOCKS=0` and a timeout; readers never raise; `scout.changes` keeps 100 % statement and branch coverage.
- Brain size pins move only with a stated reason in `test_brain_size_budget.py`.
- Never run `release-app.sh`, `release-plugin.sh` or `scripts/release.sh`.

## Review Focus

1. **A pending file edited between the strip loading and the owner pressing Approve.** The engine refuses ("changed since you saw it"); the app must say the change was edited, reload, and record nothing. (Task 9 test `approveRefusedAfterEditReloads`.)
2. **`verified: false`** (git couldn't confirm the rows). The tab must say so above the list and disable Undo, since `due` lists no rollbacks then. (Task 8 test `unverifiedDisablesUndoAndShowsBanner`.)
3. **An empty install** (no changes, no engagement, no pending files, digest with every section empty). The tab must render a plain empty state, not blank charts or a crash on `max()` of nothing. (Task 10 test `emptyDocumentsRenderEmptyStates`; Task 1 test `test_digest_of_an_empty_vault_has_no_sections`.)
4. **An engine older than the app** (no `changes digest`, or `changes --json` missing). The tab shows "Update Scout's engine to see this page", like the run ledger's `.unsupported`, and the rest of the app keeps working. (Task 6 test `missingCommandIsUnsupported`.)
5. **The rollback race window** (Plan 2's M-2): a file changed, or a folder swapped for a symlink, between undo's check and its write. Nothing outside the vault is written, and the other edit survives. (Task 3 tests.)

---

## Part A — Engine

### Task 1: `scoutctl changes digest`

**Files:**
- Create: `plugin/engine/scout/changes/digest.py`
- Modify: `plugin/engine/scout/cli.py` (new `@changes_app.command("digest")`)
- Create: `plugin/engine/tests/unit/test_changes_digest.py`; extend `tests/unit/test_changes_contract.py` with `CANONICAL_DIGEST = FIXTURES / "changes-digest.json"`

**Interfaces:**
- Consumes: `report.changes_report(...)`, `report.engagement_report(...)` (both `dict`), `docs/wishlist/*.md` frontmatter (`title`, `status`, `priority`, `date`).
- Produces: `build_digest(vault: Path, env: Mapping[str, str], *, now: datetime, tz: ZoneInfo, owner_vault: Path | None = None) -> dict` and `render_text(doc: dict) -> str`. Document v1:

```text
schema_version: 1, generated_at, week_of: <local Monday, ISO date>, verified: bool,
engagement: {this_week, last_week, change_pct: int|null, feedback, reliance},   # feedback+reliance, this week's split
noise: {this_week, last_week},
days: [{date, engagement, noise}] × 7 (oldest first),
testing: [{id, summary, days_left}],          # state "testing", newest review first
shipped: [{id, summary}],                     # ts in the last 7 days, not in `testing`
learned: [{id, summary, verdict, suspect}],   # review_on in the last 7 days, or suspect
up_next: [{slug, title}],                     # wishlist status open|in-progress, priority high>medium>low, then date
needs_ok: int                                 # needs_ok rows with state awaiting-ok
```

"This week" is the 7 local days ending today; "last week" the 7 before. `change_pct` is null when last week is 0. Each list holds at most 3 items. `render_text` follows spec §10's layout, omits empty sections, never exceeds 12 lines, and ends a suspect line with "(Undo in the Changes tab)".

- [ ] **Step 1: Write the failing tests** in `test_changes_digest.py`:
  - `test_digest_counts_this_week_against_last` — feedback 6 + reliance 4 this week, 5 last week → `engagement == {"this_week": 10, "last_week": 5, "change_pct": 100, "feedback": 6, "reliance": 4}`.
  - `test_digest_lists_at_most_three_per_section` — 5 testing changes → `len(doc["testing"]) == 3`, ordered by `review_on` ascending.
  - `test_digest_days_left_counts_to_review_on` — `review_on` 4 local days ahead → `days_left == 4`.
  - `test_digest_up_next_reads_open_wishlist_items` — a `done`, an `open` low and an `in-progress` high item → `[w["slug"] for w in doc["up_next"]] == [<high>, <low>]`.
  - `test_digest_of_an_empty_vault_has_no_sections` — every list `[]`, `needs_ok == 0`, `render_text(doc)` has only the header and the engagement line.
  - `test_render_text_never_exceeds_twelve_lines` — every section full → `len(render_text(doc).splitlines()) <= 12`, and no line contains a bare `chg-` id.
- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/unit/test_changes_digest.py -q` from `plugin/engine`. Expected: FAIL (`scout.changes.digest` missing).
- [ ] **Step 3: Implement** `build_digest` and `render_text` in `digest.py`. Read the two reports once each, with `since` = 14 local days before today. Then add the CLI command `changes digest [--json]`: plain text by default, the document with `--json`. It resolves the vault like `cli_changes` (git from the CLI's vault, owner rows from the installed vault).
- [ ] **Step 4:** Add the contract fixture. `test_digest_contract_matches_canonical` builds the fixture vault of `test_changes_contract._build` and checks `changes-digest.json`. Regenerate it with `SCOUT_REGEN_CONTRACT=1`. Run the digest and contract tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat(changes): scoutctl changes digest, the "What Scout is working on" document`.

### Task 2: `scoutctl changes diff` and `inline-comment` rows on read

**Files:**
- Modify: `plugin/engine/scout/changes/report.py`, `plugin/engine/scout/cli.py`
- Create: `plugin/engine/scout/changes/inline.py`
- Test: `tests/unit/test_changes_diff.py`, `tests/unit/test_changes_inline.py`

**Interfaces:**
- Produces:
  - `report.change_diff(vault: Path, change_id: str) -> str | None`: the unified diff of the change's commit over its own paths (`scan.own_paths`), cut at 256 KiB with a final line `… (diff cut at 256 KB)`. None when the id isn't `CHANGE_ID_RE`, or `derive` doesn't confirm it as a self-change.
  - CLI `scoutctl changes diff <chg-id>`: prints the diff; exit 2 with `no such change` otherwise.
  - `inline.inline_comments(vault: Path, *, since: datetime, now: datetime) -> list[Signal]`: one `Signal(kind="inline-comment", surface="kb", ref=<commit sha>, ts=<commit time>)` per `interactive`-lane commit in `[since, now]` that adds at least one line containing `//==<<` or `scout: user:`. Found with `git log -G'//==<<|scout: user:' --format=… -p -U0`.
  - `changes_report` and `engagement_report` merge `inline_comments(...)` into the signals they read, once per ref.
- [ ] **Step 1: Write the failing tests:**
  - `test_change_diff_shows_only_own_paths` — a commit editing `SKILL.md` and `action-items/x.md` → the diff names `SKILL.md`, not `action-items/x.md`.
  - `test_change_diff_refuses_unknown_and_forged_ids` → `None` for `chg-ffffffffffff`, for `nonsense`, and for a ledger row whose commit isn't a self-change.
  - `test_change_diff_is_cut_at_256_kib`.
  - `test_inline_comment_counts_once_per_interactive_commit` — two marker lines in one `scout: …` commit → one signal with ref = sha; the same markers in a `dreaming [22:00]: …` commit → none.
  - `test_engagement_report_counts_inline_comments_as_feedback` → that day's `feedback == 1`, `by_surface["feedback"] == {"kb": 1}`.
  - `test_inline_comment_needs_an_added_marker` — a commit that only removes a marker line → none.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. `inline_comments` returns `[]` on any git failure (readers never raise).
- [ ] **Step 4:** Run the new tests, `test_changes_contract.py` (regenerate the fixtures if the engagement numbers move, and say why in the commit), then all of `tests/unit/test_changes_*.py`. Expected: PASS, `scout.changes` at 100 %.
- [ ] **Step 5: Commit** — `feat(changes): a change's diff for the app; inline comments counted from git`.

### Task 3: Close Plan 2's two safety gaps

**Files:**
- Modify: `plugin/engine/scout/changes/governance.py`, `plugin/engine/scout/changes/report.py`, `plugin/engine/scout/changes/actions.py`
- Test: `tests/unit/test_changes_governance.py`, `tests/unit/test_changes_actions.py`

**Interfaces:**
- Produces:
  - `governance.approved_application(vault: Path, signals: Sequence[Signal], apply_sha: str) -> bool`. True when commit `apply_sha` moves `governance-pending/<slug>.md` to `applied/<slug>.md` and an owner `approve` row matches the applied file: `item_hash` of that file at `apply_sha`, bound to the filing commit (the newest commit before `apply_sha` that added the pending file), or unbound with whole-second `ts` ≤ the apply commit's time.
  - `changes_report` sets `governance=False` on a row whose commit isn't an `approved_application`, so an unapproved move is an ordinary change: tested and undoable.
  - `_Op` gains `expect: tuple[str, bytes, int]`, what `_current` read at `path` when the undo was worked out.
  - `_write` opens each target folder from the vault root one component at a time (`os.open(..., O_RDONLY | O_DIRECTORY | O_NOFOLLOW)`, `dir_fd=` the parent's). It stages, renames and unlinks relative to that fd. Immediately before each rename or unlink it re-reads the target through the fd. A mismatch with `expect` puts back what was already done and raises `ActionRefused("edited-since")`.
- [ ] **Step 1: Write the failing tests:**
  - `test_an_unapproved_move_into_applied_is_an_ordinary_change` — a run commits the pending→applied move with no approval row → the report row has `governance is False`, `test.state == "testing"`, and `rollback_problem(...) is None`.
  - `test_an_approved_move_stays_governance` — the same with an `approve` row bound to the filing commit → `governance is True`.
  - `test_an_edit_between_check_and_write_refuses_and_keeps_the_edit` — monkeypatch `actions._sweep` (called between `_undo` and `_write`) to append a line to `SKILL.md` → `ActionRefused`, reason `edited-since`, and `SKILL.md` ends with the appended line.
  - `test_a_folder_swapped_for_a_symlink_before_the_write_writes_nothing_outside` — between check and write, replace `knowledge-base/a` with a symlink to a directory outside the vault → refused, and the outside directory is empty.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. Keep `AUTO_ROLLBACK_LIVE = False` and the CLI refusal. Update the refusal message to say the gaps are closed and turning a mode on waits for the calibration review (spec §7a).
- [ ] **Step 4:** Run `tests/unit/test_changes_*.py tests/unit/test_cli_changes.py`. Expected: PASS, 100 % on `scout.changes`.
- [ ] **Step 5: Commit** — `fix(changes): governance needs a matching approval; undo re-checks each file through folder fds`.

### Task 4: Doctor warns about leftover undo temp files

**Files:**
- Modify: `plugin/engine/scout/scripts/bootstrap_doctor.py` (new `_check_undo_leftovers`, called from `run_doctor` beside `_check_scout_tz`)
- Test: `tests/unit/test_bootstrap_doctor_checks.py`

**Interfaces:**
- Produces: `_check_undo_leftovers(*, vault: Path, now: datetime | None = None) -> list[str]`. It looks at the vault root, `scripts/`, `docs/wishlist/` and `knowledge-base/` (recursively) for regular files named `.*.scout-undo` older than 10 minutes. It returns at most one warning, naming the count and up to 5 vault-relative paths, and says to delete them. It never deletes (the doctor is read-only).
- [ ] **Step 1: Write the failing test** `test_doctor_warns_about_old_undo_temp_files` — one old and one fresh temp file → exactly one warning, which names the old one, not the fresh one. Also a test that with none, no warning is given.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement** the check and wire it in `run_doctor`.
- [ ] **Step 4:** Run `tests/unit/test_bootstrap_doctor*.py`. Expected: PASS.
- [ ] **Step 5: Commit** — `feat(doctor): warn about undo temp files a crash left behind`.

---

## Part B — App

New code lives in `apps/macos/Scout/Changes/` (the Scout target picks new files up automatically: `PBXFileSystemSynchronizedRootGroup`). Tests go in `apps/macos/ScoutTests/Changes/`. Pure types are `nonisolated` (the target defaults to MainActor). Run app tests from `apps/macos`:

```bash
xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<Suite> CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""
```

### Task 5: Decoders and contract fixtures

**Files:**
- Create: `Scout/Changes/Models/ChangesDocument.swift`, `EngagementDocument.swift`, `ChangesDigest.swift`
- Create: `ScoutTests/Fixtures/changes.json`, `engagement.json`, `changes-digest.json` (byte copies of `plugin/engine/tests/fixtures/contract/` made with `cp`); `ScoutTests/Changes/ChangesContractTests.swift`
- Modify: `.github/workflows/contract.yml` (a step shaped like step 9's run-ledger check, `cmp -s` plus the `git ls-files` guard, looping over the three names); root `CLAUDE.md` (the contract section that says these copies "land with the first client")

**Interfaces:**
- Produces (all `nonisolated struct …: Equatable, Sendable, Decodable`, each with `static let supportedSchemaVersion = 1` and `static func parse(_ data: Data) -> Result<Self, ChangesParseError>`, modelled on `RunLedger`):
  - `ChangesDocument {generatedAt: Date; verified: Bool; calibrating: Bool; autoRollback: String; changes: [ChangeRow]; needsOK: [NeedsOKItem]; skippedRows: Int}`
  - `ChangeRow {id, kind, summary, commit: String; ts: Date; why: ChangeWhy; files, targets: [String]; governance, backfilled: Bool; authorLane: String; reverts: String?; test: ChangeTest}`
  - `ChangeWhy {type, ref, gloss: String; inferred: Bool}`
  - `ChangeTest {state: String; verdict: String?; suspect, markedWrong, calibrating, engagementTargeted: Bool; reviewOn: String?; rolledBackBy: String?; overlap, reasons: [String]; baseline, after: TestWindow?; wouldHaveDone: [String: Bool]}`
  - `TestWindow {days, awayDays: Int; engagement, noise: Double; recurrences: [String: Int]}`
  - `NeedsOKItem {id, summary, why, path, state, diffHash: String; targets: [String]; governance: Bool}`
  - `EngagementDocument {days: [EngagementDay]; totals: [String: Int]; pendingDMs: Int}`, `EngagementDay {date: String; feedback, reliance, noise: Int}`
  - `ChangesDigest` mirroring Task 1's document.
  - `nonisolated enum ChangesParseError: Error, Equatable { notADocument, unsupportedSchema(Int), engine(String) }`
- [ ] **Step 1: Copy the three fixtures** (`cp plugin/engine/tests/fixtures/contract/{changes,engagement,changes-digest}.json apps/macos/ScoutTests/Fixtures/`). **Write the failing tests** in `ChangesContractTests`:
  - `changesFixtureDecodes` — 7 changes, 2 `needsOK`, `verified == false`, `needsOK[0].diffHash.count == 64`, and the kinds are exactly `{"rollback","brain-edit","driver","kb-structure","phase","upgrade","wishlist-done"}`.
  - `engagementFixtureDecodes` — 15 days, totals feedback 5 / noise 2 / reliance 3, `pendingDMs == 1`.
  - `digestFixtureDecodes` — the values Task 1's fixture holds.
  - `newerSchemaIsUnsupported` — `{"schema_version": 2}` → `.failure(.unsupportedSchema(2))` for all three.
  - `aBadRowIsSkippedNotFatal` — one change row with `ts: "nope"` → the document decodes with one row fewer.
- [ ] **Step 2:** Run `-only-testing:ScoutTests/ChangesContractTests`. Expected: FAIL (types missing).
- [ ] **Step 3: Implement** the decoders. Reuse `RunLedgerTime.parse` for dates, and decode rows lossily as `RunLedger` does.
- [ ] **Step 4:** Add the `contract.yml` byte-check step. Run the suite and `cmp` each copy against its engine file. Expected: PASS, identical.
- [ ] **Step 5: Commit** — `feat(app): decode the changes, engagement and digest documents; pin their fixtures`.

### Task 6: `ChangesService` and `ChangesDecisions`

**Files:**
- Create: `Scout/Changes/ChangesService.swift`, `Scout/Changes/ChangesDecisions.swift`
- Modify: `Scout/Shell/AppState.swift` (construct both with the engine-state closure used for `InboxReviewService`, and start the service in `startsBackgroundWork`)
- Test: `ScoutTests/Changes/ChangesServiceTests.swift`, `ScoutTests/Changes/ChangesDecisionsTests.swift`

**Interfaces:**
- Produces:
  - `@MainActor final class ChangesService: ObservableObject` with `init(scoutctl:, runner: ProcessRunner, logsDirectory: URL, pendingDirectory: URL, fileEvents:, clock:)` (`scoutctl:` takes the same engine-state closure type `InboxReviewService` takes; `fileEvents:` and `clock:` take the types `RunLedgerService.init` takes`, `@Published private(set) var state: State`, `func refresh() async` and `func start(interval: TimeInterval = 300)`.
    - `enum State: Equatable { idle, loading, loaded(ChangesSnapshot), unsupported, failed(String) }`
    - `struct ChangesSnapshot: Equatable { changes: ChangesDocument; engagement: EngagementDocument; digest: ChangesDigest }`
    - It runs `["changes","--json","--since","365d","--limit","5000"]`, `["engagement","--json","--since","365d"]` and `["changes","digest","--json"]`. A `generation` counter drops stale overlapping reads, as in `RunLedgerService`.
  - `@MainActor final class ChangesDecisions: ObservableObject`
    - `nonisolated enum Command: Equatable { approve(govID: String, diffHash: String), reject(govID: String), undo(changeID: String), wrong(changeID: String), consent(Bool), consentShow, diff(changeID: String), appEdit }`
    - `enum Outcome: Equatable { done(String), refused(String), invalid }`
    - `func perform(_ command: Command) async -> Outcome`. It validates ids and hashes (Global Constraints) before spawning anything (an invalid input gives `.invalid` and no spawn). It appends `--surface app` to `approve`, `reject`, `undo` and `wrong` (`consent` takes no surface; `appEdit` is `engagement record app-edit --surface app`). Stdout becomes `.done`. A non-zero exit becomes `.refused` with the engine's first stderr line, except that any stderr containing "a scheduled run may not" maps to "Scout is running right now. Try again when the run finishes."
    - `@Published private(set) var consent: ConsentAnswer` (`enum ConsentAnswer { unknown, notAsked, yes, no }`), refreshed by `consentShow`.
- [ ] **Step 1: Write the failing tests** (use `RuleBasedRunner`):
  - `loadsAllThreeDocuments` — the rules return the three fixtures → `.loaded`, 7 changes.
  - `missingCommandIsUnsupported` — `changes digest` exits 2 with "No such command" → `.unsupported`.
  - `approvePassesHashAndSurface` — `calls.last == ["changes","approve","gov-x","--diff-hash",<64 hex>,"--surface","app"]`.
  - `invalidIDsNeverSpawn` — `undo("chg-zz")`, `approve("gov-X", "abc")` → `.invalid`, `calls.isEmpty`.
  - `aLiveRunRefusalIsWordedForTheOwner` — stderr "…a scheduled run may not make it" → `.refused("Scout is running right now. Try again when the run finishes.")`.
  - `onlyAllowedCommandsAppearInSource` — read `ChangesDecisions.swift` and assert that every string literal passed as a scoutctl subcommand is in the allow-list (the `InboxReviewTests` 261-286 pattern).
- [ ] **Step 2:** Run both suites. Expected: FAIL.
- [ ] **Step 3: Implement** both classes and wire them into `AppState`.
- [ ] **Step 4:** Run both suites. Expected: PASS.
- [ ] **Step 5: Commit** — `feat(app): read the changes map and run owner decisions through scoutctl`.

### Task 7: The Changes tab replaces Proposals and Wishlist in the sidebar

**Files:**
- Modify: `Scout/Shell/MainWindowView.swift` (`SidebarItem` cases and `detail`), `Scout/Shell/GoCommands.swift` (title "Changes", symbol, ⌘6, id; renumber Research ⌘7 and Knowledge Base ⌘8), `Scout/Shell/SidebarView.swift` (badge: `needsOK` count awaiting OK plus suspect changes not yet undone; `badgeDescription` "N need you"), `Scout/ScoutApp.swift`
- Create: `Scout/Changes/Views/ChangesView.swift`, `Scout/Changes/Views/DigestCard.swift`
- Modify tests: `ScoutTests/Shell/AccessibilityFoundationTests.swift`, `ViewSmokeTests.swift` (`labels.count == 8`), `TabViewSmokeTests.swift` (`changesRenders` replaces `proposalsRenders` and `wishlistRenders`), `PageLayoutTests.swift`

**Interfaces:**
- Consumes: `ChangesService`, `ChangesDecisions` (Task 6).
- Produces: `struct ChangesView: View` (environment objects as above). Top to bottom it shows:
  - the banners: `verified == false` → "Scout couldn't check these changes against git just now, so they're shown unconfirmed and Undo is off."; `calibrating` → "Calibrating: verdicts are shown, but nothing acts on them yet.";
  - `DigestCard(digest:)`;
  - the strip (Task 9);
  - the trend (Task 10);
  - the list (Task 11).

  `.unsupported` shows "Update Scout's engine to see this page." and `.failed(msg)` shows the message with a Retry button. Until Tasks 9–11 land, their slots stay empty.
- [ ] **Step 1: Update the tests first:** the a11y suite expects a `.changes` row labelled "Changes", unique keys 1…8 in case order, and a valid symbol; the smoke suite expects 8 labels; `changesRenders` mounts `ChangesView` on a loaded `ChangesService` with the fixtures.
- [ ] **Step 2:** Run those suites. Expected: FAIL.
- [ ] **Step 3: Implement** the case swap and the two views. Delete nothing yet (Task 12 removes the old code).
- [ ] **Step 4:** Run `ScoutTests/AccessibilityFoundationTests`, `ViewSmokeTests`, `TabViewSmokeTests` and `PageLayoutTests`. Expected: PASS.
- [ ] **Step 5: Commit** — `feat(app): a Changes tab in place of Proposals and Wishlist`.

### Task 8: Unconfirmed and calibrating states

**Files:** `Scout/Changes/Views/ChangesView.swift`; `ScoutTests/Changes/ChangesViewStateTests.swift`

**Interfaces:**
- Produces: `nonisolated enum ChangeActions` with `static func canUndo(_ row: ChangeRow, verified: Bool) -> Bool` and `static func canMarkWrong(_ row: ChangeRow) -> Bool`.
  - `canUndo` is true when `verified`, `!row.governance`, `row.test.state != "not-tested"` and `row.test.rolledBackBy == nil`. It also requires that no `undo` is already pending; the snapshot can't show that, so the button disables after a `.done` until the next refresh shows the row rolled back.
  - `canMarkWrong` is true when `row.test.verdict != nil` and `!row.test.markedWrong`.
- [ ] **Step 1: Write the failing tests:**
  - `unverifiedDisablesUndoAndShowsBanner` — the fixture with `verified` false → `canUndo == false` for every row; the banner text is present in the accessibility tree.
  - `governanceAndUntestedNeverUndo`.
  - `rolledBackRowCannotBeUndoneAgain`.
  - `markWrongOnlyOnceAndOnlyWithAVerdict`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** — `feat(app): say when the change history is unconfirmed or calibrating`.

### Task 9: "Needs your OK" strip

**Files:** `Scout/Changes/Views/NeedsOKStrip.swift`, `Scout/Changes/Views/GovernanceReviewSheet.swift`; `ScoutTests/Changes/NeedsOKTests.swift`

**Interfaces:**
- Consumes: `NeedsOKItem`, `ChangesDecisions.perform`.
- Produces:
  - `struct NeedsOKStrip: View` shows the rows with `state == "awaiting-ok"` and is hidden when there are none. Each row's "Review" opens `GovernanceReviewSheet`.
  - The sheet shows the summary, the why, the targets, whether applying it is a governance change, and the diff block read from `<vault>/<item.path>`, read-only and monospaced (everything the approval hash covers).
  - Approve calls `.approve(govID: item.id, diffHash: item.diffHash)`; Reject calls `.reject`.
  - `nonisolated static func diffBlock(fromPendingFile text: String) -> String?` takes the one fenced `diff` block.
- [ ] **Step 1: Write the failing tests:**
  - `stripHiddenWithNothingAwaiting`.
  - `approveSendsTheHashTheOwnerSaw`.
  - `approveRefusedAfterEditReloads` — the engine refuses "changed since you saw it" → the sheet shows "This change was edited after you opened it. Review it again." and `ChangesService.refresh` ran once.
  - `diffBlockFindsTheFence`.
  - `diffBlockIsNilWithoutOne`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** — `feat(app): approve or reject governance changes from the Changes tab`.

### Task 10: Engagement trend with change markers

**Files:** `Scout/Changes/Views/EngagementTrendView.swift`, `Scout/Changes/TrendModel.swift`; `ScoutTests/Changes/TrendModelTests.swift`

**Interfaces:**
- Consumes: `EngagementDay`, `ChangeRow`, and `HeatmapRange` (reused for the 1/3/6/12-month switcher, with the same labels; spec §9.1 says "matching the Control Center heatmap"), plus `EditorialSegmentedControl`.
- Produces:
  - `nonisolated enum TrendModel`:
    - `static func weeks(_ days: [EngagementDay], range: HeatmapRange, today: Date, calendar: Calendar) -> [WeekBucket]`, with `WeekBucket {start: Date; feedback, reliance, noise: Int}` and weeks starting on the calendar's first weekday;
    - `static func markers(_ changes: [ChangeRow], range: HeatmapRange, today: Date, calendar: Calendar) -> [ChangeMarker]`, with `ChangeMarker {id: String; date: Date; tone: VerdictTone; hollow: Bool}`;
    - `static func tone(_ test: ChangeTest) -> VerdictTone`, with `enum VerdictTone { helped, neutral, testing, bad }`: `helped` → helped; `hurt` or `rolled back` → bad; `testing` with no verdict → testing; anything else → neutral.
  - `struct EngagementTrendView: View` draws stacked feedback/reliance bars per week, the noise line on the trailing axis, and markers on the time axis. Selecting a marker calls `onSelect(id)`, which opens the detail (Task 11).
- [ ] **Step 1: Write the failing tests:**
  - `weeksBucketAndSumByWeek`.
  - `rangeLimitsTheWeeks` — `.month` → at most 5 buckets, `.year` → at most 53.
  - `markersSkipChangesOutsideTheRange`.
  - `toneFollowsTheVerdict` — one case per verdict plus `testing`.
  - `hollowWhenTheWhyWasInferred`.
  - `emptyDocumentsRenderEmptyStates` — no days and no changes → `weeks == []`, and `EngagementTrendView` mounts and shows "No engagement recorded yet."
- [ ] **Step 2–4:** Run, implement (Swift Charts `BarMark`, `LineMark`, `PointMark`), run.
- [ ] **Step 5: Commit** — `feat(app): engagement trend with each change marked on it`.

### Task 11: Change list and change detail

**Files:** `Scout/Changes/Views/ChangeCard.swift`, `Scout/Changes/Views/ChangeDetailView.swift`, `Scout/Changes/Views/VerdictPill.swift`; `ScoutTests/Changes/ChangeDetailTests.swift`

**Interfaces:**
- Consumes: `ChangeActions` (Task 8); `ChangesDecisions` `.undo`, `.wrong`, `.diff`; `AppState.requestOpenKBNote(_:)`; `TrendModel.tone`.
- Produces:
  - **`ChangeCard`**, newest first: the summary, `why.gloss` (with "(guessed from the commit)" when inferred), a kind icon, a `VerdictPill`, and a before/after sparkline. The sparkline covers the 14 engagement days before `ts` and the 14 after, from `EngagementDocument.days`.
  - **`ChangeDetailView`:**
    - small multiples, baseline against after: engagement per day, noise per day, and each target's recurrences;
    - the overlap line: "N changes landed in this window; the effect is shared";
    - while `calibrating`, one line each for what `targeted` and `one-at-a-time` would have done;
    - the rollback status;
    - the diff, loaded on demand through `.diff`;
    - links: the why's pattern opens `requestOpenKBNote("knowledge-base/scout-mistake-audit.md")`, and a wishlist why opens `docs/wishlist/<slug>.md`. If `requestOpenKBNote`'s resolver doesn't take a vault-relative path, the link opens the file with `NSWorkspace.shared.open` instead;
    - buttons: Undo (confirmation: "Undo this change? The next dreaming run takes back the lines it made.") and "This verdict is wrong".
  - `nonisolated enum DetailModel` with `static func sparkline(days: [EngagementDay], around ts: Date, calendar: Calendar) -> (before: [Int], after: [Int])` and `static func wouldHaveDoneLines(_ test: ChangeTest) -> [String]`.
- [ ] **Step 1: Write the failing tests:**
  - `sparklineSplitsAtTheChange`.
  - `wouldHaveDoneLinesOnlyWhileCalibrating`.
  - `undoAsksThenSendsTheID` — confirm → `calls.last == ["changes","undo",id,"--surface","app"]`.
  - `noBareIDsInTheDetail` — the accessibility tree of a fixture row's detail has no label that matches only `^chg-[0-9a-f]{12}$`.
  - `inferredWhyIsMarked`.
  - `patternLinkOpensTheMistakeAudit` — tapping it sets `AppState.pendingKBNote` (or calls the `NSWorkspace` fallback).
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** — `feat(app): change cards and the change detail, with Undo and "verdict is wrong"`.

### Task 12: Remove the Proposals and Wishlist tabs

**Files:**
- Delete: `Scout/Proposals/` (all), `ScoutTests/Proposals/` (all)
- Modify:
  - `PerFileTabConfig.swift`: drop `static let wishlist`; `.research` stays.
  - `AppState.swift`: drop `proposalsDocumentService`, `proposalsWriterBox` and `wishlistDocumentService`, and their loads and forwards.
  - `SettingsView.swift`: drop the two folder fields and their `@AppStorage` keys.
  - `WriteFeedbackOverlay.swift`: drop the "Approve"/"Decline" mappings.
  - `WriteProblem.swift`: drop the `ProposalsWriterError` case.
  - `Glossary.swift`: drop `case proposal`; the `.dreaming` text points at the Changes tab.
  - `RawMarkupGuardTests.swift`, `BigVault.swift`, `ViewSmokeTests.swift`, `PageLayoutTests.swift`, `EngineUpgradeSheetSmokeTests.swift`, `ComponentSmokeTests.swift`: drop the Proposals and Wishlist fixtures and uses.
  - Keep `DiagnosticsScrubber`'s folder names: the folders still exist in vaults.
- [ ] **Step 1:** Run `grep -rn "Proposal\|wishlist\|Wishlist" apps/macos/Scout apps/macos/ScoutTests` and list every hit.
- [ ] **Step 2:** Remove or rewrite each hit. Afterwards, the only hits allowed are the `DiagnosticsScrubber` names and the word "wishlist" in the Glossary's description of Scout's internal queue.
- [ ] **Step 3:** Run the whole `ScoutTests` target (the CI command in Part B's header without `-only-testing:<Suite>`), then `./scripts/check-coverage.sh TestResults.xcresult`. Expected: all pass, coverage ≥ the floor in `scripts/coverage-floor.txt`.
- [ ] **Step 4: Commit** — `refactor(app): remove the Proposals and Wishlist tabs`.

### Task 13: Consent question and `app-edit` rows

**Files:**
- Create: `Scout/Changes/Views/ConsentCard.swift`, `Scout/Changes/EngagementRecordingGit.swift`
- Modify: `AppState.swift` (wrap the `GitServiceProtocol` the writers get), `SettingsView.swift` (a "Changes map" section with the consent answer and a control to change it)
- Test: `ScoutTests/Changes/ConsentTests.swift`, `ScoutTests/Changes/EngagementRecordingGitTests.swift`

**Interfaces:**
- Produces:
  - **`ConsentCard`.** It shows at the top of the Changes tab while `ChangesDecisions.consent == .notAsked`. Copy:
    - title: "Count your Claude sessions outside the vault?";
    - body: "Scout learns what's useful by noticing when you rely on it. With your OK it also notes sessions outside the vault that used Scout, only that one did, never what it read or where. You can change this in Settings.";
    - buttons: "Yes, count them" → `.consent(true)`; "No" → `.consent(false)`.

    Spec §6.3 says "asked once on every install": the answer lives in the owner store, so a new install asks again and an upgrade with an answer doesn't.
  - **`final class EngagementRecordingGit: GitServiceProtocol`** with `init(base: GitServiceProtocol, record: @escaping @Sendable () async -> Void)`. `commitPaths` awaits `base.commitPaths` and, only if it didn't throw, calls `record()`, which runs `ChangesDecisions.perform(.appEdit)` and ignores the outcome.
- [ ] **Step 1: Write the failing tests:**
  - `consentCardOnlyWhenNotAsked`.
  - `consentYesSendsTheAnswer` — `calls.last == ["engagement","consent","--out-of-vault","yes"]`.
  - `aSuccessfulCommitRecordsOneAppEdit` — `calls.last == ["engagement","record","app-edit","--surface","app"]`.
  - `aFailedCommitRecordsNothing`.
  - `aRefusedRecordNeverFailsTheWrite`.
- [ ] **Step 2–4:** Run, implement, run.
- [ ] **Step 5: Commit** — `feat(app): ask the out-of-vault question once; count app edits as feedback`.

---

## Part C — Phases

### Task 14: Retire proposals now that the tab exists

**Files:**
- Modify: `plugin/phases/modes/feedback-processing.md` (Step 1d), `plugin/phases/modes/follow-change-decisions.md`, `plugin/phases/modes/wishlist.md`, `plugin/phases/core/action-items.md`
- Modify: `plugin/engine/scout/scripts/bootstrap.py` (drop `("dreaming-proposals.md", "templates/dreaming-proposals.md.tmpl")` from `_INSTALL_ONLY_TEMPLATES`) and delete `plugin/templates/dreaming-proposals.md.tmpl`
- Test: `plugin/engine/tests/unit/test_phase_changes_map.py`, `tests/unit/test_brain_size_budget.py`, and the bootstrap install test that lists seeded files
- Add: `plugin/changelog.d/changes-map-plan-3.added.md`

**What the text says now:**
- **Step 1d:** a large, structural or uncertain brain change goes in directly too, tested and undoable like any other (spec §11). The proposals paragraph and its table row are gone.
  - **Harness fallback:** a `governance-pending/<slug>.md` file with `governance: false`, approved in the Changes tab.
  - **One "Earlier proposals" line:** apply any `Approved` or ripe proposal still in `dreaming-proposals.md`, committing `<the brain file> dreaming-proposals.md dreaming-proposals/ dreaming-proposals-archive.md`, and write no new ones.
- **Step 1e:**
  - the suspect wrap line ends "Undo in the Changes tab, or run `scoutctl changes undo <id>`";
  - a governance file's wrap line says "approve or reject it in the Changes tab";
  - the `apply` bullet says again that a `governance: false` file is deleted instead of moved.
- **`wishlist.md`** drops the proposals route. **`action-items.md`:** a governance change waiting for the owner's OK waits in the Changes tab.

- [ ] **Step 1: Update the tests first:**
  - Replace `test_step_1d_keeps_ordinary_proposals_until_the_app_can_show_changes` with `test_step_1d_writes_no_new_proposals`. It asserts `"dreaming-proposals.md"` appears in Step 1d only in the "Earlier proposals" line, together with main's four paths, and that no phase file contains `Pending (auto-apply after`.
  - Replace `test_no_phase_points_at_a_changes_tab_the_app_does_not_have_yet` with `test_owner_lines_point_at_the_changes_tab`.
  - Restore the `governance: false` assertions in the Step 1d and Step 1e tests.
  - The bootstrap test expects no `dreaming-proposals.md` in a fresh install, and an existing one left untouched by an upgrade.
- [ ] **Step 2:** Run `tests/unit/test_phase_*.py tests/unit/test_brain_*.py tests/unit/test_bootstrap_install.py tests/unit/test_bootstrap_upgrade.py`. Expected: FAIL.
- [ ] **Step 3:** Edit the phase text and bootstrap. Re-measure the brains and re-pin them, saying what moved and why.
- [ ] **Step 4:** Run the same tests, plus ruff and mypy from `plugin/engine`, and `python -m scout.scripts.changelog check --base origin/main --target-branch main --event pull_request` from `plugin/`. Expected: PASS.
- [ ] **Step 5: Commit** — `feat(phases): the Changes tab replaces proposals`.

---

## Not in this plan

- **Turning on an automatic rollback mode:** a governance change for the owner after the calibration report (spec §7a).
- **Engine draft PRs in the strip:** see Decisions.
- **Follow-up visuals in spec §9.1** (calendar heatmap, scoreboard, effort, burn-down, reliance by surface, noise detail, work queue, menu-bar glyph).
- **Plan 4** (digest DM, `/scout-status`) and **Plan 5** (backfills).
- **Deferred minors from Plans 1–2:**
  - `review_on` is null for untested changes;
  - rows with `ts > now` are dropped;
  - `--since` has no upper bound;
  - the CLI's JSON has unsorted keys;
  - `append_signal_once` has a check-then-append race.
- **The noise 0→1 threshold question,** which belongs to the calibration report.
- **The `scan.lane_of` gap for unprefixed run commits** (see Decisions), filed separately.
