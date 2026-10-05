# Design — KB network-analysis stats (vault health + insight)

- **Date:** 2026-07-07
- **Status:** Approved (design); ready for implementation plan
- **Revalidated:** 2026-10-03 against `main` @ `7a037c9` — every code reference below re-checked; see "Revalidation 2026-10-03" at the end.
- **Feature family:** Post-v0.9.0 Knowledge Base follow-ups (feature 3 of 3)
- **Related:**
  - Builds on the Knowledge Base tab (v0.9.0, #69)
  - **Builds on feature 2 — graph navigability** (spec #73, implemented in #90, on `main`): shares the degree ordering (`KBGraph.topHubs`) and the `KBOverviewView` layout, which now embeds `KBMapView`.
  - Sibling: "Comment for Scout" (feature 1, spec #72, implemented in #77)

## Summary

Turn the overview's single "477 notes · N connections" line into a **NETWORK** section with two parts: a **Vault Health** block that surfaces actionable problems (orphans, weakly-linked notes, dangling wikilinks, disconnected islands) as clickable lists, and an **Insight** block that summarizes the graph's shape (top hubs, degree summary, per-entity-type breakdown, connected-components summary). All metrics are computed in one pass over the existing in-memory index + edge set — no new disk I/O.

## Goals

- Answer two questions at a glance: **"what should I clean up?"** (health) and **"what's the shape of my knowledge?"** (insight).
- **Actionable:** every health item and hub clicks through to open that note.
- **Compact by default:** each list shows a count + top ~5, with a "show all N" disclosure — never a wall of ~500 rows.
- Reuse existing machinery (`undirectedEdges()`, degree/adjacency, `KBEntityGroup`, feature 2's degree ranking); add no new data source.

## Non-goals

- Time-based metrics (staleness, recent activity) — needs mtime, different data; out of scope.
- Directed-graph metrics (in- vs out-degree, PageRank) — the KB graph is modeled undirected today.
- Editing/auto-fixing problems (e.g., auto-deleting orphans) — the app only surfaces + navigates; fixing is manual.
- Charts/histograms — the degree summary is a line, not a plotted distribution.

## Background (current state)

- `graphStats()` (`KnowledgeBaseService.swift:329`) returns only `(notes, links)`; `KBOverviewView` shows `"\(stats.notes) notes · \(stats.links) connections"` (`KBOverviewView.swift:41-42`), then QUICK ACCESS, then `KBMapView` (the MAP section, line 71).
- `undirectedEdges()` (`KnowledgeBaseService.swift:271`, `private`) yields the resolved undirected edge set; `index.outByFile`/`index.stemToPath` give raw outgoing targets + resolution (so an unresolved target = a dangling link — `outgoingLinks(for:)` already models this as `KBLink.resolved == nil`); `index.typeByFile` + `KBEntityGroup.of` give per-note grouping. `KBIndex` and `KBEntityGroup` both live in `Models/KBGraph.swift`.
- Adjacency is built today in two places: `localGraph(around:)` builds `[String: Set<String>]` and `fullGraph()` builds a degree map. Neither is shared.
- Feature 2 added `KBGraph.topHubs(maxNodes:)` (degree desc, id asc) and `KnowledgeBaseService.hubGraph(maxNodes:)` feeding `KBMapView`. Feature 3's "top hubs" uses the same ordering on paths rather than `KBGraphNode`s.

## Design

### Engine — `KnowledgeBaseService.networkStats() -> KBNetworkStats`

A new value type computed once from the index + `undirectedEdges()`:

```
struct KBNetworkStats {
    // totals (replaces the overview's separate graphStats() call — same pass)
    let noteCount: Int
    let linkCount: Int
    // health
    let orphans: [String]              // degree 0 (relative paths)
    let weaklyLinked: [String]         // degree exactly 1
    let dangling: [(source: String, target: String)]  // outgoing [[target]] that doesn't resolve
    let islands: [[String]]            // connected components of size >= 2, excluding the largest
    // insight
    let topHubs: [(path: String, degree: Int)]         // degree desc, id asc; capped (e.g. top 20)
    let avgDegree: Double
    let maxDegree: Int
    let byType: [(group: KBEntityGroup, count: Int)]   // all 7 groups, count of notes
    let clusterCount: Int              // number of components with size >= 2
    let largestComponentSize: Int
}
```

Computation (all over `tree.flatMap(\.allFiles)` md notes + adjacency built from `undirectedEdges()`):
- **adjacency** comes from one small `private static` helper, which `localGraph(around:)` also switches to. That is the "single degree/adjacency source" this spec promises, so the two paths can't drift.
- **degree(path)** = adjacency neighbour count. `orphans` = degree 0; `weaklyLinked` = degree 1.
- **dangling** = for each `(source, targets)` in `index.outByFile`, each `target` that doesn't resolve. *Superseded by "Amendment 2026-10-04": resolution goes through the shared `KBIndex.resolve`, and ticket ids and vault files outside the KB are excluded.* Reuses the same resolution `outgoingLinks` already does per-note.
- **components** = BFS/union-find over adjacency across *all* md notes (a degree-0 note is its own singleton component). Partition: the largest component is the "mainland"; `islands` = other components with size ≥ 2; singleton components are the `orphans` (already captured). `clusterCount` = count of components with size ≥ 2; `largestComponentSize` = size of the biggest.
- **topHubs** = notes sorted by degree desc (id asc tiebreak), capped at 20; `avgDegree` = 2·|edges| / |notes|; `maxDegree` = max degree.
- **byType** = count of md notes per `KBEntityGroup.of(path, type:)`, all 7 groups (0s included).
- **noteCount / linkCount** = |md notes| / |`undirectedEdges()`|, identical to `graphStats()` (which stays for its existing callers and tests).

Determinism: all lists sorted (paths by degree-then-id for hubs; problem lists by path) so the UI is stable across reparses.

Display strings (the degree line, the components line, and the "N things / ✓ none" row titles) are pure functions on `KBNetworkStats`, so they're unit-tested rather than only eyeballed.

### Presentation — new `KBStatsView` on the overview

Lives in its own file (like `KBMapView`). It is a plain view over a `KBNetworkStats` value plus an `onOpen` closure, with no service reference. `KBOverviewView` calls `service.networkStats()` once per body evaluation. The header line reads its totals from `noteCount`/`linkCount` (dropping the separate `graphStats()` pass), and `KBStatsView` sits as its own section between the header and QUICK ACCESS, so it sits above the MAP.

**Vault Health** — one row per metric, in severity order (dangling, orphans, islands, weakly-linked):
- Row = an icon + "N <label>" + the top ~5 items as clickable chips/links (`onOpen(path)`); dangling shows `source → missing-target`; islands show the island's notes (open any).
- A `DisclosureGroup` "Show all N" reveals the rest when N > 5.
- Zero-count metric renders a quiet "✓ none" (not a scary empty list).

**Insight** — compact, read-only:
- Degree line: "avg 3.1 · max 47 connections".
- Top hubs: the top ~5 clickable, "show all" expands to 20.
- Per-type breakdown: a colored count per `KBEntityGroup` (`KBEntityGroup.color`, the same colors `KBGraphLegend` in `KBLocalGraphView.swift` draws), e.g. "● Projects 73".
- Components line: "1 main cluster + K islands · largest covers M notes" (from `clusterCount` / `largestComponentSize`; K = `clusterCount − 1`). With one cluster: "1 main cluster · covers M notes". With no links at all: "No linked notes yet".

### Data flow

```
overview body → let net = service.networkStats()  (one pass; bounded work)
  → header: "\(net.noteCount) notes · \(net.linkCount) connections"
  → KBStatsView(stats: net, onOpen: onNavigate) renders Health (top-5 + disclosure) + Insight
  → click any note/hub/island item → onOpen(path) → editor
```

## Edge cases & error handling

- **Empty vault / no edges:** all lists empty; health shows all "✓ none"; insight shows "avg 0 · max 0", byType all 0; no crash.
- **All-dangling note:** a note whose only links are unresolved has degree 0 → counts as an orphan *and* contributes dangling rows (both true; intended).
- **Self-links / duplicate links:** already normalized away by `undirectedEdges()`; dangling dedups per (source,target) for free, since `extractWikilinks` already de-duplicates targets case-insensitively per note.
- **Case:** `[[Hub]]` resolves to `hub.md` (`stemToPath` is keyed lowercased), so it is not dangling.
- **Large problem lists:** capped display (top-5 + "show all") keeps the section bounded; "show all" lists can be long but are opt-in.
- **Recompute cost:** `networkStats()` replaces the overview's `graphStats()` call, so the overview does the same number of `undirectedEdges()` passes as today (the map's `hubGraph` does its own, unchanged). O(nodes+edges) on ~500 notes; memoize by index identity only if profiling shows churn.

## Testing

Pure-logic Swift Testing tests over a small on-disk fixture (`@MainActor @Suite`, `NoopFS`, `await reparseAndWait()` — mirroring the existing `KnowledgeBaseService graph` suite in `ScoutTests/KnowledgeBase/KnowledgeBaseTests.swift`). Fixture names are the repo's shared stand-ins (`alex`/`priya`/`sam`) plus generic nouns, checked at zero vault hits:
- orphan (degree 0) + weakly-linked (degree 1) detection;
- dangling detection (a `[[missing]]` target with no note), and a case-variant link that does resolve;
- component/island partition (build two clusters + a singleton; assert largest is mainland, the size-2 cluster is an island, the singleton is an orphan);
- degree summary (avg/max), hub ordering, per-type counts, totals equal to `graphStats()`;
- empty vault → `.empty`-shaped stats, no crash;
- `localGraph` keeps passing its existing tests after the adjacency extraction;
- the display-string helpers (degree line, components line, row titles incl. singular/plural/"✓ none").
- The view itself is build + manual `/run` verification. The pure helpers keep the new untested surface to SwiftUI layout only, which matters for the CI coverage floor (`scripts/check-coverage.sh`, 70.0%).

## Out of scope / follow-ups

- Time/staleness metrics; directed-graph centrality; auto-fix actions; plotted histograms.
- `fullGraph()` keeps its own degree map. Moving it onto the shared adjacency helper is a no-behavior-change cleanup this feature doesn't need.

## Amendment 2026-10-04 — real-vault findings (approved by Jordan in chat)

Running the implemented engine over the real vault (478 notes) returned **6,289 "dangling" links**, and most of them were not broken:

| Links | Form | Actually broken? |
|---:|---|---|
| 2,389 | `[[folder/note]]` path link to a note that exists | No. The resolver only knew bare stems, so these were **also missing from edges, backlinks and the map** (pre-existing gap) |
| 1,864 | `[[PROJ-1234]]` ticket ids | No. Ids linked by convention |
| 1,521 | links to vault files outside `knowledge-base/` (e.g. action-items dailies) | No. Obsidian resolves them; the KB index can't see them |
| 394 | `[[people/name]]` with no such note | **Yes** |
| 121 | `[[#heading]]`, `[[note#heading]]`, `[[note.md]]` | No |

Decisions:

1. **One shared resolver, used everywhere** (`resolveWikilink`, `outgoingLinks`, `backlinks`, `undirectedEdges`, `networkStats`). `KBIndex.resolve(_:from:)` strips a `#heading` / `#^block` anchor and a trailing `.md`. A bare `[[#heading]]` resolves to the linking note itself. `[[folder/note]]` resolves when the stem's note path ends with `/folder/note` (Obsidian's path-suffix rule). Backlink excerpts also find path-form mentions. Effect: about 2,400 previously dropped links now count as edges, so the connection total, map and backlinks all grow.
2. **Dangling = truly missing.** A link is dangling only if it (a) doesn't resolve, (b) isn't a ticket id (`^[A-Z][A-Z0-9]{1,9}-\d+$`), and (c) doesn't name a file elsewhere in the vault. For (c), `KBIndex` carries `vaultNames`: lowercased names of every non-hidden file under `~/Scout` (`.md` by stem, others by full name). It is collected during the existing off-main reparse as **a filename listing only, with no file reads**. This is a deliberate, small exception to "no new disk I/O".
3. **`networkStats()` is memoized per reparse.** The cache is keyed by a generation counter bumped whenever `tree`/`index` change. Measured on the real vault in Debug, the pass cost about 78 ms against 14 ms for `graphStats()`, on every overview body eval.

**Final-review fix pass (same day).**
- `KBIndex` keeps every note path per stem (`stemPaths`), so `[[people/alex]]` resolves even when another `alex.md` exists.
- `KBIndex` resolves each target once and builds the edge set at construction time, off the main actor. Edges, backlinks, outgoing links and stats are now lookups.
- `vaultNames` skips the KB's own `.md` notes, so a resolver miss such as a wrong-folder link shows as dangling instead of being masked.
- The default-cap stats are computed inside the detached reparse and seed the cache. The first overview read went from about 192 ms to about 1 µs (Debug, real vault).
- Backlink excerpts pick the first line whose wikilink actually resolves to the note.
- "Show all" renders at most 200 more items, followed by "…and N more".

Real vault after the fix pass: 3,927 links, 1,333 dangling, 21 orphans, 27 weakly linked, one cluster of 457.

## Revalidation 2026-10-03

Re-checked against `main` @ `7a037c9` after features 1 and 2 shipped (#77, #90). What moved and what changed here:

- **Feature 2 is merged and implemented**, so the "implement after #73 merges / base on feature-2 code" stacking caveat is gone; this branches from `main`.
- **Line refs:** the overview totals line moved from 43-44 to `KBOverviewView.swift:41-42`; `KBIndex` is now at the end of `KBGraph.swift` (137-148), after `KBGraph.topHubs`/`filtered`; `hubGraph` ends at `KnowledgeBaseService.swift:353`.
- **Vault size:** ~272 → 477 notes; examples updated.
- **Design tweaks (flagged for review):** (1) `KBNetworkStats` gains `noteCount`/`linkCount` so the overview drops its separate `graphStats()` pass; (2) `KBStatsView` takes the stats value rather than the service; (3) the adjacency extraction this section used to defer is now in the plan, because feature 2's code exists to share it with; (4) display strings move into tested pure helpers to protect the coverage floor added since (#102/#107).
