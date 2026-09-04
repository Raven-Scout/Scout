# Design — KB network-analysis stats (vault health + insight)

- **Date:** 2026-07-07
- **Status:** Approved (design); ready for implementation plan
- **Feature family:** Post-v0.9.0 Knowledge Base follow-ups (feature 3 of 3)
- **Related:**
  - Builds on the Knowledge Base tab (v0.9.0)
  - **Stacks on feature 2 — graph navigability (PR #73):** shares the degree-ranking helper and the `KBOverviewView` layout; implement after #73 merges.
  - Sibling: "Comment for Scout" (feature 1, PR #72)

## Summary

Turn the overview's single "272 notes · N connections" line into a **NETWORK** section with two parts: a **Vault Health** block that surfaces actionable problems (orphans, weakly-linked notes, dangling wikilinks, disconnected islands) as clickable lists, and an **Insight** block that summarizes the graph's shape (top hubs, degree summary, per-entity-type breakdown, connected-components summary). All metrics are computed in one pass over the existing in-memory index + edge set — no new disk I/O.

## Goals

- Answer two questions at a glance: **"what should I clean up?"** (health) and **"what's the shape of my knowledge?"** (insight).
- **Actionable:** every health item and hub clicks through to open that note.
- **Compact by default:** each list shows a count + top ~5, with a "show all N" disclosure — never a wall of 272 rows.
- Reuse existing machinery (`undirectedEdges()`, degree/adjacency, `KBEntityGroup`, feature 2's degree ranking); add no new data source.

## Non-goals

- Time-based metrics (staleness, recent activity) — needs mtime, different data; out of scope.
- Directed-graph metrics (in- vs out-degree, PageRank) — the KB graph is modeled undirected today.
- Editing/auto-fixing problems (e.g., auto-deleting orphans) — the app only surfaces + navigates; fixing is manual.
- Charts/histograms — the degree summary is a line, not a plotted distribution.

## Background (current state)

- `graphStats()` returns only `(notes, links)`; `KBOverviewView` shows `"\(notes) notes · \(links) connections"` (line 43-44).
- `undirectedEdges()` yields the resolved undirected edge set; `index.outByFile`/`index.stemToPath` give raw outgoing targets + resolution (so an unresolved target = a dangling link); `index.typeByFile` + `KBEntityGroup.of` give per-note grouping.
- Feature 2 adds `KBGraph.topHubs` / degree ranking and a `KBMapView` on the overview — feature 3's "top hubs" is the same ranking, and both render on the overview, hence the stacking.

## Design

### Engine — `KnowledgeBaseService.networkStats() -> KBNetworkStats`

A new value type computed once from the index + `undirectedEdges()`:

```
struct KBNetworkStats {
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

Computation (all over `tree.allFiles` md notes + adjacency built from `undirectedEdges()`):
- **degree(path)** = adjacency neighbour count. `orphans` = degree 0; `weaklyLinked` = degree 1.
- **dangling** = for each `(source, targets)` in `index.outByFile`, each `target` whose `stemToPath[target.lowercased()]` is nil (skip self). Reuses the same resolution `outgoingLinks` already does per-note.
- **components** = BFS/union-find over adjacency across *all* md notes (a degree-0 note is its own singleton component). Partition: the largest component is the "mainland"; `islands` = other components with size ≥ 2; singleton components are the `orphans` (already captured). `clusterCount` = count of components with size ≥ 2; `largestComponentSize` = size of the biggest.
- **topHubs** = notes sorted by degree desc (id asc tiebreak), capped at 20; `avgDegree` = 2·|edges| / |notes|; `maxDegree` = max degree.
- **byType** = count of md notes per `KBEntityGroup.of(path, type:)`, all 7 groups (0s included).

Determinism: all lists sorted (paths by degree-then-id for hubs; problem lists by path) so the UI is stable across reparses.

### Presentation — new `KBStatsView` on the overview

Lives in its own file (like `KBMapView`); `KBOverviewView` embeds it where the `"\(notes) notes · \(links) connections"` line is today, so the header keeps the note/connection totals and the NETWORK section sits above the MAP.

**Vault Health** — one row per metric, in severity order (dangling, orphans, islands, weakly-linked):
- Row = an icon + "N <label>" + the top ~5 items as clickable chips/links (`onOpen(path)`); dangling shows `source → missing-target`; islands show the island's notes (open any).
- A `DisclosureGroup` "Show all N" reveals the rest when N > 5.
- Zero-count metric renders a quiet "✓ none" (not a scary empty list).

**Insight** — compact, read-only:
- Degree line: "avg 3.1 · max 47 connections".
- Top hubs: the top ~5 clickable, "show all" expands to 20.
- Per-type breakdown: a colored count per `KBEntityGroup` (reusing `KBGraphLegend` colors), e.g. small bars or "● Projects 73".
- Components line: "1 main cluster + K islands · largest covers M notes" (from `clusterCount` / `largestComponentSize`).

### Data flow

```
overview body → service.networkStats()  (one pass; bounded work)
  → KBStatsView renders Health (top-5 + disclosure) + Insight
  → click any note/hub/island item → onOpen(path) → editor
```

## Edge cases & error handling

- **Empty vault / no edges:** all lists empty; health shows all "✓ none"; insight shows "avg 0 · max 0", byType all 0; no crash.
- **All-dangling note:** a note whose only links are unresolved has degree 0 → counts as an orphan *and* contributes dangling rows (both true; intended).
- **Self-links / duplicate links:** already normalized away by `undirectedEdges()`; dangling dedups per (source,target).
- **Large problem lists:** capped display (top-5 + "show all") keeps the section bounded; "show all" lists can be long but are opt-in.
- **Recompute cost:** `networkStats()` runs per overview `body` eval like the existing `graphStats()`; if profiling shows churn, memoize by the index identity. O(nodes+edges) either way.

## Testing

Pure-logic tests over a small on-disk fixture (neutral names, mirroring the existing `KnowledgeBaseService graph` fixture suite):
- orphan (degree 0) + weakly-linked (degree 1) detection;
- dangling detection (a `[[missing]]` target with no note);
- component/island partition (build two clusters + a singleton; assert largest is mainland, the size-2 cluster is an island, the singleton is an orphan);
- degree summary (avg/max) and per-type counts.
- The view is build + manual `/run` verification.

## Out of scope / follow-ups

- Time/staleness metrics; directed-graph centrality; auto-fix actions; plotted histograms.
- Sharing the adjacency/degree/component helpers with feature 2 is expected — the plan will extract a single degree/adjacency source rather than duplicate it.
