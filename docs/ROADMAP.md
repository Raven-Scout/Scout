# Scout.app — Roadmap

Forward plan from the **2026-06-22 app audit** (feature/UX, code-health, and architecture/robustness passes). Ordered to the project priority: **correctness / data-layer first, polish second.** Each item links to its tracking issue.

State of the app: Control Center, Action Items, Schedules, and Proposals are solid; the Wishlist/Research tabs (shipped in v0.8.1) work but are thin. The highest-leverage work is data-layer correctness (the app and the scout-plugin both write `~/Scout` concurrently) and finishing the new tabs.

---

## Phase 1 — Data-layer & correctness hardening (do first)

Real bugs, mostly small. Recommended as a single "correctness hardening" PR for the S-effort items.

| Issue | Severity | What |
|---|---|---|
| [#44](https://github.com/Raven-Scout/Scout/issues/44) | **Critical** | `ActionItemsWriter` commits with `git add -A` (`commitAll`) — can sweep an in-flight plugin session's uncommitted vault changes into an app commit. Scope it to the file written (`commitPaths`), like the other writers. |
| [#45](https://github.com/Raven-Scout/Scout/issues/45) | High | `fireNow` hardcodes `"scoutctl"` argv[0] instead of the resolved path prefix (wrong invocation when scoutctl is absolute) and swallows the failure with `try?`. |
| [#46](https://github.com/Raven-Scout/Scout/issues/46) | High | Action Items hardcodes `America/New_York` — non-ET users open the wrong daily file. |
| [#47](https://github.com/Raven-Scout/Scout/issues/47) | High | Action Items load/reparse errors are swallowed by `try?` → the view sticks on `.loading`. Surface the existing `.failed` state. |
| [#48](https://github.com/Raven-Scout/Scout/issues/48) | High | Concurrent app↔plugin git writes: handle `.git/index.lock` collisions (retry + surface), and the per-file read-modify-write race in `PerFileItemWriter.performResolve`. |

## Phase 2 — Performance & robustness *(roadmap-tracked; file issues when scheduled)*

- **Offload file I/O off the main thread.** `PerFileDocumentService.reparse`, `ActionItemsDocumentService.reparse`, and `UsageTrackerService` read/parse synchronously on `@MainActor` per FSEvent → beachball on large vaults. `SessionLogService` already uses `Task.detached` — match it.
- **Cache regexes & date formatters.** `ActionItemsParser` recompiles ~7 `NSRegularExpression` + allocates `DateFormatter` per parse, on every FSEvent. Make them `static let`.
- **Verify/pin CI action versions.** The audit flagged `actions/checkout@v6` / `upload-artifact@v7` as suspect; CI has been passing, so confirm + pin, and consider gating release tags on tests (`scripts/release.sh` tags without a test gate).
- **Configurable vault root.** `~/Scout` is hardcoded (`AppState.swift:50`) while sub-paths are overridable; folded into onboarding ([#51](https://github.com/Raven-Scout/Scout/issues/51)).

## Phase 3 — Wishlist / Research v2 (finish the new tabs)

The tabs are currently write-once. Make them workable:

| Issue | What |
|---|---|
| [#41](https://github.com/Raven-Scout/Scout/issues/41) | User-changeable **priority** (and mark **in-progress**) from the card. Infra ~80% ready. |
| [#42](https://github.com/Raven-Scout/Scout/issues/42) | **"Do now"** — launch a focused dreaming/research session on a specific item (the biggest gap vs Action Items' "Launch Claude"; cross-repo). |
| [#43](https://github.com/Raven-Scout/Scout/issues/43) | **Resolved-item outcomes** — track a resolved item to the run that did it; what changed. |
| — | **Reopen** resolved items (today the resolved closure is a no-op); **search + priority/status filter** (Action Items' `FilterChipsView` is the template); distinct status-vs-priority pill colors; clearer missing-dir onboarding. |

## Phase 4 — Proposals depth & upstreaming

| Issue | What |
|---|---|
| [#50](https://github.com/Raven-Scout/Scout/issues/50) | Rich detail for **implemented** proposals — diff/commits, files changed, plain-language explanation, link to the run. (Pairs with #43.) |
| [#49](https://github.com/Raven-Scout/Scout/issues/49) | **"Auto-apply"** a locally-proven change *upstream* into the engine (`scout-plugin`) — for Proposals + Wishlist. Cross-repo; design the promotion unit + gating. |

## Phase 5 — Onboarding & distribution

| Issue | What |
|---|---|
| [#51](https://github.com/Raven-Scout/Scout/issues/51) | **New-user onboarding (large).** Hermes-style (Nous Research): install the Mac app first; if the engine/vault is missing, the app guides install + verify. Detect engine presence, guided `/scout-setup`, configurable vault root, route empty states into onboarding instead of silent blanks. |
| — | **Notarization** *(roadmap only).* Ad-hoc signing forces the right-click-to-open dance on every machine; Apple Developer enrollment + notarization removes it. Defer unless distributing widely. |

## Phase 6 — Tech debt / maintainability

- **Extract a shared `FrontmatterParser`** — 5 byte-identical parser fns + 2 writer helpers are duplicated across `Proposals/` ↔ `PerFileItems/`; a frontmatter fix in one silently misses the other. (Don't unify the model/view layers — that duplication is justified.)
- **Give `ActionItemsWriter` a `GitServiceProtocol` seam** (last writer without one → untestable git path).
- **Stale UI placeholders showing wrong/fake data:** hardcoded `$8.00` budget + "Quota: TBD" (`NowStripView`/`UsageRailCard`), static `repo ~/Scout` / `branch main` / "Daemon: healthy" labels, and the retry slot-key `TODO(plan-6)` that fires the wrong slot for custom keys.
- **`AppState`** isn't a god-object yet but `init` grows per feature + has zero tests; a `ServiceContainer` is the eventual refactor.
- **Action Items rendering** ([#52](https://github.com/Raven-Scout/Scout/issues/52)) — fix rendering rough edges (orphaned comment-composer styling, board card missing Launch-Claude, no comments on done tasks) + add customization (density/layout/fields/sort). Relates to [#15](https://github.com/Raven-Scout/Scout/issues/15) (Kanban board).

---

## Related existing issues
- [#15](https://github.com/Raven-Scout/Scout/issues/15) Kanban board view for Action Items
- [#18](https://github.com/Raven-Scout/Scout/issues/18) In-app conversational assistant scoped to all of Scout
- [#30](https://github.com/Raven-Scout/Scout/issues/30) Spec: embedding architecture for live Claude sessions
- [#31](https://github.com/Raven-Scout/Scout/issues/31) Concurrency-skip logs parse as phantom `.running` sessions

## Recommended sequencing
1. **Phase 1 hardening PR** (#44–48) — highest value, lowest risk, matches the correctness-first priority. Start with #44 (the one real data-loss path).
2. **Wishlist/Research v2** (#41, #42, #43 + reopen/search) — make the new tabs actually workable.
3. **Onboarding** (#51) — the path to new users; large, its own spec→plan→build.
4. Performance (Phase 2), Proposals depth (#50/#49), and tech debt as they fit.
