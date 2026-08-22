# Design — KB graph navigability (focus + context map)

- **Date:** 2026-07-06
- **Status:** Approved (design); ready for implementation plan
- **Feature family:** Post-v0.9.0 Knowledge Base follow-ups (feature 2 of 3)
- **Related:**
  - Builds on the Knowledge Base tab (v0.9.0)
  - Sibling specs: "Comment for Scout" (feature 1, PR #72), network-analysis stats (feature 3)

## Summary

The global KB map currently renders `fullGraph()` — **every one of the ~272 vault notes and all their edges, unfiltered and uncapped** — which is an unreadable hairball. Replace it with a **focus + context** map: a bounded default view (top hubs), **re-root on focus** (tap a node → it becomes the center, showing its neighborhood), name search to jump anywhere, and filters (entity type, hide orphans, minimum degree). The view is bounded by construction, so it never re-crowds.

## Goals

- The overview map is **readable at all times** — never more than ~40 nodes on screen.
- **Navigable:** search a note by name → focus it; tap any node → re-root on it; ‹ back / ⌂ hubs to retrace.
- **Filterable:** toggle entity types, hide orphans, raise a minimum-degree floor.
- Reuse the existing, tested graph engine (`localGraph`, `undirectedEdges`, `KBGraphCanvas`, `KBEntityGroup`) — minimal new traversal code.
- **No silent truncation:** when a view is capped, say so ("showing 40 of 272").

## Non-goals

- **Network-analysis statistics** (degree distribution, components, centrality) — feature 3, separate spec. This feature only *uses* per-node degree for hub seeding/filtering.
- Saved/named views, multi-select, graph editing, 3D, or physics-parameter tuning UI.
- Changing the **per-note local graph** in the right panel (`KBRightPanel`) — it's already capped at 26 and works; untouched.

## Background (current state)

- `KBOverviewView` renders `service.fullGraph()` — all notes + all edges, `labelMinDegree: 3` the only lever (hides *labels*, not nodes). Source of the crowding.
- `localGraph(around:depth:maxNodes:)` already does bounded BFS (depth 2, cap 26, keeps center + highest-degree neighbours) — the re-root engine we reuse with a larger cap.
- `KBGraphCanvas` (Grape) already supports tap/drag/pan/pinch, zoom-aware sizing, and `labelMinDegree`.
- `KBEntityGroup.of(_:type:)` classifies each note into 7 groups (people/projects/issues/channels/ontology/research/other) and drives node colour + legend.

## Design

### Engine additions — `KnowledgeBaseService` (all pure, over the existing in-memory index/edges)

1. **`hubGraph(maxNodes: Int = 40) -> KBGraph`** — the default seed. Rank all notes by degree over `undirectedEdges()`, take the top `maxNodes`, and keep only edges whose *both* endpoints are in that set. Nodes tagged with group + degree as in `fullGraph()`. No `isCenter`.
2. **Re-root reuses `localGraph(around:depth:maxNodes:)`** — the overview calls it with `maxNodes: 40` (vs the right panel's 26) when `focusPath != nil`. No new traversal.
3. **`filter(_ graph: KBGraph, types: Set<KBEntityGroup>, hideOrphans: Bool, minDegree: Int) -> KBGraph`** — a pure post-filter applied to whichever graph is showing: keep nodes whose group ∈ `types`, whose degree ≥ `minDegree`, and (if `hideOrphans`) degree > 0; then drop edges with a dropped endpoint. The center node (if any) is always kept so re-root never yields an empty focus.

### UI — upgrade the map region of `KBOverviewView`

State:
- `@State focusPath: String?` — `nil` = hub seed; otherwise the re-root center.
- `@State history: [String]` — focus stack for ‹ back.
- `@State query: String` — node-name search.
- `@State activeTypes: Set<KBEntityGroup>` — default all 7 on.
- `@State hideOrphans: Bool` — default off.
- `@State minDegree: Int` — default 0.

Layout (above/around the existing `KBGraphCanvas`):
- **Search field** — matches note display names (via the existing index); Enter/pick re-roots (`focusPath = match; history.append`).
- **Filter bar** — 7 entity-type toggle chips (reusing legend colours) + a "Hide orphans" toggle + a compact "min degree" stepper/slider.
- **Breadcrumb row** — `⌂ hubs` chip (clears focus + history) and `‹ back` (pops `history`); shows the current center's name when focused.
- **Cap caption** — "showing N of M notes" whenever the rendered set is capped or filtered.

Graph source per render:
```
base = focusPath == nil ? hubGraph(maxNodes: 40)
                        : localGraph(around: focusPath!, depth: 2, maxNodes: 40)
shown = filter(base, types: activeTypes, hideOrphans: hideOrphans, minDegree: minDegree)
```

Gestures (in `KBGraphCanvas`):
- **Single tap a node → re-root** (`focusPath = id; history.append(previous)`).
- **Double-click a node → open** the note in the editor (the current `onNavigate`, moved from single-tap to double-click).
- Drag/pan/pinch unchanged. `.id` the canvas on `(focusPath, filters)` so Grape re-lays-out on focus/filter change.

### Data flow

```
launch/overview → focusPath=nil → hubGraph(40) → filter → canvas (top hubs)
tap node X → focusPath=X, history+=[prev] → localGraph(X,2,40) → filter → canvas
double-click X → onNavigate(X) → editor opens the note
search "bigquery" → pick → focusPath=match → …
toggle filter / min-degree → re-filter current base → canvas
‹ back → focusPath = history.pop() ;  ⌂ hubs → focusPath=nil, history=[]
```

## Edge cases & error handling

- **Empty vault / no edges:** `hubGraph` returns nodes with no edges (or empty) → existing empty-state; caption "showing 0 of 0".
- **Everything filtered out:** show an inline "No notes match these filters" with a "reset filters" affordance (never a blank hairless canvas with no explanation).
- **Focus node filtered out by an active filter:** the center is always retained by `filter` (see engine #3), so a re-rooted view is never empty; its neighbours may be filtered.
- **Focus on an orphan:** `localGraph` returns just the center; caption reflects "1 of 272"; fine.
- **Back history across filter changes:** history stores focus paths only; filters are independent and persist across re-roots.
- **Deleted/renamed note while focused:** on reparse, if `focusPath` no longer resolves, fall back to `nil` (hub seed) rather than error.
- **Bounded performance:** every rendered set ≤ 40 nodes regardless of vault size — no full-vault force simulation.

## Testing

Pure-logic tests (new suite, neutral fixtures, mirroring the existing graph tests):
- `hubGraph`: picks the highest-degree notes; includes only edges internal to the selected set; respects `maxNodes`.
- `filter`: drops nodes by type / by `minDegree` / orphans; drops edges with a removed endpoint; **always retains the center**.
- Re-root cap: `localGraph(around:maxNodes:40)` never exceeds 40 and always includes the center.
- Orphan detection consistent with degree over `undirectedEdges()`.
- Interactive view (gestures, search, breadcrumb) = build + manual `/run` verification.

## Out of scope / follow-ups

- **Feature 3 — network-analysis stats** will add aggregate metrics (degree distribution, hubs, orphan list, connected components) to the overview; this feature deliberately computes only what it needs for seeding/filtering. Sharing the degree/adjacency helpers between the two is expected and noted for that spec.
