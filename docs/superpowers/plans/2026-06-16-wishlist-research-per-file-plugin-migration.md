# Per-file Wishlist & Research Queue — Plugin + Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the vault's Wishlist and Research Queue from single prose-heavy files to per-file items, and update the dreaming/research session prose + scout-plugin templates to read/write the new format — so the sessions and (later) the app share one canonical per-file schema.

**Architecture:** A one-time Python migration script (TDD'd, committed to scout-plugin) splits each existing bullet/checklist item into a per-file `YYYY-MM-DD-slug.md` with YAML frontmatter, lifting the detectable status/priority/date markers and keeping the remaining text as the body. Then the vault workflow prose (`DREAMING.md` Phase 3, `RESEARCH.md` Phase 1) and the scout-plugin seed templates are updated to the per-file format. This is sub-project 2 of 3 (the app tabs are a later plan).

**Tech Stack:** Python 3 (stdlib only — `re`, `pathlib`); markdown with YAML frontmatter; three git repos — **scout-plugin** (`~/scout-plugin`, the script + templates), the **vault** (`~/Scout`, the migrated data + DREAMING.md/RESEARCH.md). Not scout-app.

**Spec:** `docs/superpowers/specs/2026-06-16-wishlist-research-queue-per-file-design.md`

**Conventions / facts for the implementer:**
- The vault `~/Scout` is a git repo; DREAMING.md / RESEARCH.md and the migrated data commit there.
- scout-plugin `~/scout-plugin` is a separate git repo; the script + templates commit there.
- Per-file schema (from the spec): frontmatter `title`, `status` (`open|in-progress|done|dropped`), `priority` (`urgent|high|medium|low`), `date` (`YYYY-MM-DD`), optional `source`, optional `area`; body below. Filenames `YYYY-MM-DD-slug.md`.
- Status mapping: wishlist `[in progress]`→`in-progress`, `[done]`/in `-done` file→`done`, else `open`; research `[x]`→`done`, `[ ]`→`open`. Priority: wishlist `HIGH`→`high`/`MEDIUM`→`medium` (default `medium`); research `🔴`/`START IMMEDIATELY`→`urgent`, `🟡`→`medium`, `🟢`→`low` (default `medium`).
- Run Python tests with: `python3 -m pytest <file> -q` (the vault already uses pytest; `~/scout-plugin` has pytest available).

---

## File Structure

- **Create** `~/scout-plugin/scripts/migrate_wishlist_research.py` — pure parsing helpers + a migration driver.
- **Create** `~/scout-plugin/scripts/test_migrate_wishlist_research.py` — unit tests for the pure helpers.
- **Modify (vault)** `~/Scout/DREAMING.md` — Phase 3 (Steps 3a, 3b, 3d) read/write per-file `docs/wishlist/`.
- **Modify (vault)** `~/Scout/RESEARCH.md` — Phase 1 reads `knowledge-base/research-queue/` + urgent preemption.
- **Generated (vault, by the script)** `~/Scout/docs/wishlist/*.md`, `~/Scout/knowledge-base/research-queue/*.md`, rewritten thin `~/Scout/knowledge-base/research-queue.md`; deleted `~/Scout/docs/Wishlist.md`, `Wishlist-in-progress.md`, `Wishlist-done.md`.
- **Modify (plugin)** `~/scout-plugin/templates/docs/Wishlist.md.tmpl` + `Wishlist-in-progress.md.tmpl` + `Wishlist-done.md.tmpl` (remove), add `~/scout-plugin/templates/docs/wishlist/.gitkeep`; reshape `~/scout-plugin/templates/knowledge-base/research-queue.md.tmpl` + add `research-queue/.gitkeep`; `~/scout-plugin/templates/run-research.sh.tmpl` (path reference); `~/scout-plugin/commands/scout-status.md` (if it reads these paths).

---

## Task 1: Wishlist bullet parser

**Files:**
- Create: `~/scout-plugin/scripts/migrate_wishlist_research.py`
- Test: `~/scout-plugin/scripts/test_migrate_wishlist_research.py`

- [ ] **Step 1: Write the failing test**

Create `~/scout-plugin/scripts/test_migrate_wishlist_research.py`:

```python
from migrate_wishlist_research import parse_wishlist_item

def test_parses_in_progress_high_with_date_and_source():
    bullet = ("**[in progress] HIGH — Upgrade the graph system Scout relies on** "
              "(2026-06-12 — Jordan Slack DM `123`). Evaluate TinkerPop + Gremlin.")
    item = parse_wishlist_item(bullet)
    assert item.status == "in-progress"
    assert item.priority == "high"
    assert item.title == "Upgrade the graph system Scout relies on"
    assert item.date == "2026-06-12"
    assert item.source == "Jordan Slack DM `123`"
    assert "Evaluate TinkerPop" in item.body

def test_defaults_open_medium_when_unmarked():
    item = parse_wishlist_item("**Some idea with no markers** and a description.")
    assert item.status == "open"
    assert item.priority == "medium"
    assert item.title == "Some idea with no markers"
    assert item.date is None

def test_done_marker_maps_to_done():
    item = parse_wishlist_item("**[done] MEDIUM — Shipped thing** delivered notes.", in_done_file=False)
    assert item.status == "done"
    assert item.priority == "medium"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: FAIL — `ImportError` / `parse_wishlist_item` undefined.

- [ ] **Step 3: Write minimal implementation**

Create `~/scout-plugin/scripts/migrate_wishlist_research.py`:

```python
"""One-time migration: split the single-file Wishlist and Research Queue into
per-file items with YAML frontmatter (see
docs/superpowers/specs/2026-06-16-wishlist-research-queue-per-file-design.md).

Pure parse helpers are unit-tested; the `migrate()` driver does the file I/O."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass
class Item:
    title: str
    status: str
    priority: str
    date: str | None
    source: str | None
    body: str
    area: str | None = None


def _strip_markers(text: str):
    """Pull leading `[in progress]`/`[done]` state + `HIGH`/`MEDIUM` priority
    off the start of a wishlist title segment. Returns (status, priority, rest)."""
    status = "open"
    priority = "medium"
    t = text.strip()
    m = re.match(r"^\[(in progress|done)\]\s*", t, re.I)
    if m:
        status = "in-progress" if m.group(1).lower() == "in progress" else "done"
        t = t[m.end():]
    m = re.match(r"^(HIGH|MEDIUM|LOW)\b\s*(—|-|–)?\s*", t)
    if m:
        priority = m.group(1).lower()
        t = t[m.end():]
    return status, priority, t.strip()


def parse_wishlist_item(bullet: str, in_done_file: bool = False) -> Item:
    """Parse one wishlist bullet (without its leading `* `). The bolded lead
    `**…**` carries state/priority/title; a trailing `(date — source)` is lifted."""
    text = bullet.strip()
    m = re.match(r"\*\*(.+?)\*\*(.*)$", text, re.S)
    lead, rest = (m.group(1), m.group(2)) if m else (text, "")
    status, priority, title_seg = _strip_markers(lead)
    # Title is up to a ` — ` or end; if the marker form was `… — Title` the
    # title is the part after the dash already consumed; otherwise the whole.
    title = title_seg.strip()
    if in_done_file:
        status = "done"
    # Source/date from a leading parenthetical in the rest, e.g. "(2026-06-12 — src)".
    date = None
    source = None
    pm = re.match(r"\s*\((.+?)\)", rest, re.S)
    if pm:
        paren = pm.group(1)
        dm = DATE_RE.search(paren)
        if dm:
            date = dm.group(1)
        src = re.sub(r"^\d{4}-\d{2}-\d{2}\s*(—|-|–)?\s*", "", paren).strip()
        source = src or None
        rest = rest[pm.end():]
    body = rest.strip()
    return Item(title=title, status=status, priority=priority,
                date=date, source=source, body=body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit (scout-plugin repo)**

```bash
cd ~/scout-plugin && git add scripts/migrate_wishlist_research.py scripts/test_migrate_wishlist_research.py
git commit -m "feat(migrate): wishlist bullet parser for per-file migration"
```

---

## Task 2: Research-queue item parser

**Files:**
- Modify: `~/scout-plugin/scripts/migrate_wishlist_research.py`
- Test: `~/scout-plugin/scripts/test_migrate_wishlist_research.py`

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
from migrate_wishlist_research import parse_research_item

def test_research_urgent_checked_done():
    line = "- [x] 🔴 **START IMMEDIATELY — Upgrade the graph system** evaluate TinkerPop."
    item = parse_research_item(line, area="graph")
    assert item.status == "done"
    assert item.priority == "urgent"
    assert item.title == "Upgrade the graph system"
    assert item.area == "graph"
    assert "evaluate TinkerPop" in item.body

def test_research_open_yellow_default():
    line = "- [ ] 🟡 **Locate the engg-general message** reconcile the date."
    item = parse_research_item(line)
    assert item.status == "open"
    assert item.priority == "medium"
    assert item.title == "Locate the engg-general message"

def test_research_green_low():
    line = "- [ ] 🟢 **G6 · CEE conference entities** create event nodes."
    item = parse_research_item(line)
    assert item.priority == "low"
    assert item.title == "G6 · CEE conference entities"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: FAIL — `parse_research_item` undefined.

- [ ] **Step 3: Write minimal implementation**

Add to `migrate_wishlist_research.py`:

```python
def parse_research_item(line: str, area: str | None = None) -> Item:
    """Parse one research-queue checklist line:
    `- [ ] 🔴 **START IMMEDIATELY — Title** body` (emoji = priority)."""
    t = line.strip()
    m = re.match(r"^[-*]\s*\[( |x|X)\]\s*", t)
    status = "open"
    if m:
        status = "done" if m.group(1).lower() == "x" else "open"
        t = t[m.end():]
    priority = "medium"
    if t.startswith("🔴"):
        priority = "urgent"
    elif t.startswith("🟢"):
        priority = "low"
    elif t.startswith("🟡"):
        priority = "medium"
    t = re.sub(r"^(🔴|🟡|🟢|🔵)\s*", "", t)
    bm = re.match(r"\*\*(.+?)\*\*(.*)$", t, re.S)
    lead, rest = (bm.group(1), bm.group(2)) if bm else (t, "")
    # Drop a leading "START IMMEDIATELY — " label that's an urgency marker,
    # not part of the title.
    title = re.sub(r"^START IMMEDIATELY\s*(—|-|–)\s*", "", lead.strip()).strip()
    date = None
    dm = DATE_RE.search(rest)
    if dm:
        date = dm.group(1)
    return Item(title=title, status=status, priority=priority,
                date=date, source=None, body=rest.strip(), area=area)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add scripts/migrate_wishlist_research.py scripts/test_migrate_wishlist_research.py
git commit -m "feat(migrate): research-queue item parser"
```

---

## Task 3: Slug + frontmatter emit

**Files:**
- Modify: `~/scout-plugin/scripts/migrate_wishlist_research.py`
- Test: `~/scout-plugin/scripts/test_migrate_wishlist_research.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from migrate_wishlist_research import slugify, render_item, filename_for

def test_slugify_basic():
    assert slugify("Upgrade the graph system Scout relies on!") == "upgrade-the-graph-system-scout-relies-on"
    assert slugify("G6 · CEE conference entities") == "g6-cee-conference-entities"

def test_filename_uses_date_then_slug():
    item = Item(title="Tighten the budget gate", status="open", priority="high",
                date="2026-06-10", source=None, body="b")
    assert filename_for(item) == "2026-06-10-tighten-the-budget-gate.md"

def test_filename_falls_back_to_default_date_when_none():
    item = Item(title="No date here", status="open", priority="medium",
                date=None, source=None, body="b")
    assert filename_for(item, default_date="2026-06-16") == "2026-06-16-no-date-here.md"

def test_render_item_emits_frontmatter_and_body():
    item = Item(title="Tighten the budget gate", status="open", priority="high",
                date="2026-06-10", source="Jordan DM", body="The gate overruns.")
    out = render_item(item)
    assert out.startswith("---\n")
    assert "title: Tighten the budget gate" in out
    assert "status: open" in out
    assert "priority: high" in out
    assert "date: 2026-06-10" in out
    assert "source: Jordan DM" in out
    assert out.rstrip().endswith("The gate overruns.")
    assert "\n# Tighten the budget gate\n" in out

def test_render_omits_absent_optional_fields():
    item = Item(title="t", status="open", priority="low", date=None, source=None, body="b")
    out = render_item(item)
    assert "source:" not in out
    assert "area:" not in out
    assert "date:" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: FAIL — `slugify` / `render_item` / `filename_for` undefined.

- [ ] **Step 3: Write minimal implementation**

Add to `migrate_wishlist_research.py`:

```python
def slugify(title: str, max_words: int = 8) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)          # drop punctuation/emoji/·
    words = [w for w in re.split(r"[\s-]+", s) if w]
    return "-".join(words[:max_words])


def filename_for(item: Item, default_date: str = "2026-06-16") -> str:
    date = item.date or default_date
    return f"{date}-{slugify(item.title)}.md"


def render_item(item: Item) -> str:
    fm = ["---", f"title: {item.title}", f"status: {item.status}",
          f"priority: {item.priority}"]
    if item.date:
        fm.append(f"date: {item.date}")
    if item.source:
        fm.append(f"source: {item.source}")
    if item.area:
        fm.append(f"area: {item.area}")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {item.title}\n\n{item.body}\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin && git add scripts/migrate_wishlist_research.py scripts/test_migrate_wishlist_research.py
git commit -m "feat(migrate): slugify + frontmatter rendering"
```

---

## Task 4: Migration driver + run against the vault

**Files:**
- Modify: `~/scout-plugin/scripts/migrate_wishlist_research.py` (add `migrate()` + `__main__`)
- Test: `~/scout-plugin/scripts/test_migrate_wishlist_research.py`

- [ ] **Step 1: Write the failing test (driver on a temp vault)**

Append:

```python
import tempfile, os
from migrate_wishlist_research import migrate_wishlist_file, split_bullets

def test_split_bullets_separates_top_level_items():
    text = "intro\n\n* **A** body a\n* **[done] B** body b\n\n## Section\n* **C** c"
    bullets = split_bullets(text)
    assert len(bullets) == 3
    assert bullets[0].startswith("**A**")

def test_migrate_wishlist_file_writes_one_file_per_bullet(tmp_path):
    src = tmp_path / "Wishlist.md"
    src.write_text("# Wishlist\n\n* **HIGH — Alpha thing** (2026-06-10 — DM) do alpha.\n"
                   "* **[in progress] MEDIUM — Beta thing** do beta.\n")
    out_dir = tmp_path / "wishlist"
    n = migrate_wishlist_file(src, out_dir, in_done_file=False, default_date="2026-06-16")
    assert n == 2
    files = sorted(p.name for p in out_dir.glob("*.md"))
    assert files == ["2026-06-10-alpha-thing.md", "2026-06-16-beta-thing.md"]
    alpha = (out_dir / "2026-06-10-alpha-thing.md").read_text()
    assert "priority: high" in alpha and "status: open" in alpha
    beta = (out_dir / "2026-06-16-beta-thing.md").read_text()
    assert "status: in-progress" in beta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: FAIL — `split_bullets` / `migrate_wishlist_file` undefined.

- [ ] **Step 3: Write minimal implementation**

Add to `migrate_wishlist_research.py`:

```python
def split_bullets(text: str) -> list[str]:
    """Return each top-level `* `/`- ` bullet as a block (continuation lines
    that are indented or blank are folded into the current bullet). Headings
    and non-bullet prose are skipped."""
    items: list[str] = []
    cur: list[str] | None = None
    for line in text.splitlines():
        if re.match(r"^[*-]\s+\S", line):
            if cur is not None:
                items.append("\n".join(cur).strip())
            cur = [re.sub(r"^[*-]\s+", "", line)]
        elif cur is not None and (line.startswith((" ", "\t")) or line.strip() == ""):
            cur.append(line)
        elif cur is not None:
            items.append("\n".join(cur).strip())
            cur = None
    if cur is not None:
        items.append("\n".join(cur).strip())
    return [i for i in items if i]


def migrate_wishlist_file(src: Path, out_dir: Path, in_done_file: bool,
                          default_date: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for bullet in split_bullets(src.read_text()):
        item = parse_wishlist_item(bullet, in_done_file=in_done_file)
        if not item.title:
            continue
        (out_dir / filename_for(item, default_date)).write_text(render_item(item))
        count += 1
    return count
```

Also add a research-queue migrator + a `migrate()` driver + CLI at the bottom:

```python
def split_research_items(text: str):
    """Yield (line, area) for each `- [ ]`/`- [x]` checklist line under `## Queue`
    and its `###` subsections. `area` is the slugified nearest `###` heading."""
    area = None
    in_queue = False
    for line in text.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h2:
            in_queue = h2.group(1).strip().lower().startswith("queue")
            area = None
            continue
        if h3:
            area = slugify(h3.group(1))
            continue
        if in_queue and re.match(r"^[-*]\s*\[( |x|X)\]", line):
            yield line, area


def migrate_research_file(src: Path, out_dir: Path, default_date: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for line, area in split_research_items(src.read_text()):
        item = parse_research_item(line, area=area)
        if not item.title:
            continue
        (out_dir / filename_for(item, default_date)).write_text(render_item(item))
        count += 1
    return count


THIN_LOG_HEADER = """# Research Queue — run log

Per-topic research items now live as files in [[research-queue/]]. This file
is the thin run log: the research session records its latest "Last verified …"
continuity note here.

---

"""


def migrate(vault: Path, default_date: str) -> dict:
    counts = {}
    wl = vault / "docs" / "wishlist"
    counts["wishlist"] = 0
    for name, done in [("Wishlist.md", False), ("Wishlist-in-progress.md", False),
                       ("Wishlist-done.md", True)]:
        src = vault / "docs" / name
        if src.exists():
            counts["wishlist"] += migrate_wishlist_file(src, wl, done, default_date)
    rq_src = vault / "knowledge-base" / "research-queue.md"
    rq_dir = vault / "knowledge-base" / "research-queue"
    counts["research"] = migrate_research_file(rq_src, rq_dir, default_date) if rq_src.exists() else 0
    return counts


if __name__ == "__main__":
    import sys
    vault = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "Scout")
    default_date = sys.argv[2] if len(sys.argv) > 2 else "2026-06-16"
    print(migrate(vault, default_date))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-plugin/scripts && python3 -m pytest test_migrate_wishlist_research.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Dry-run the migration against a COPY of the vault and verify counts**

```bash
rm -rf /tmp/vault-mig && cp -R ~/Scout /tmp/vault-mig
cd ~/scout-plugin/scripts && python3 migrate_wishlist_research.py /tmp/vault-mig 2026-06-16
echo "--- wishlist source bullets ---"; grep -cE "^\* " /tmp/vault-mig/docs/Wishlist.md /tmp/vault-mig/docs/Wishlist-in-progress.md /tmp/vault-mig/docs/Wishlist-done.md 2>/dev/null
echo "--- wishlist files written ---"; ls /tmp/vault-mig/docs/wishlist/ | wc -l
echo "--- research queue items written ---"; ls /tmp/vault-mig/knowledge-base/research-queue/ | wc -l
echo "--- spot check one ---"; head -8 "$(ls /tmp/vault-mig/docs/wishlist/*.md | head -1)"
```
Expected: written file counts ≈ source bullet/checklist counts (allow small differences for skipped non-item bullets). Spot-checked frontmatter is well-formed. If counts are wildly off or frontmatter is malformed, FIX the parser (return to Task 1–3) before proceeding — do NOT migrate the real vault on a broken parser.

- [ ] **Step 6: Run the migration against the REAL vault + clean up old files + reduce the log**

```bash
cd ~/scout-plugin/scripts && python3 migrate_wishlist_research.py ~/Scout 2026-06-16
# Reduce research-queue.md to the thin log: keep the title + the latest
# "Last verified" paragraph, drop the migrated ## Queue items.
# (Do this edit by hand/Edit tool — preserve the most recent Last-verified note.)
# Remove the now-migrated single wishlist files:
rm ~/Scout/docs/Wishlist.md ~/Scout/docs/Wishlist-in-progress.md ~/Scout/docs/Wishlist-done.md
```
Then use the Edit tool to replace `~/Scout/knowledge-base/research-queue.md`'s body with the thin-log form (title + the preserved most-recent "Last verified …" note + a pointer to `research-queue/`).

- [ ] **Step 7: Commit the vault data migration (vault repo)**

```bash
git -C ~/Scout add -A
git -C ~/Scout commit -m "migrate: wishlist + research queue to per-file items"
```

---

## Task 5: DREAMING.md Phase 3 — per-file wishlist

**Files:**
- Modify: `~/Scout/DREAMING.md` (Phase 3, Steps 3a / 3b / 3d)

No unit test (vault prose). Verified by reading the edited section back + the next dreaming run.

- [ ] **Step 1: Replace Step 3a**

Use the Edit tool. Replace the Step 3a block:

```
## Step 3a: Read the Wishlist

Read `docs/Wishlist.md`. Each item is a bullet point describing a desired feature or improvement.

**Item states:**
- Bare text (no marker) = not yet started
- `[in progress]` prefix = work has begun, may need more runs to complete
- `[done]` prefix or ~~strikethrough~~ = completed
```

with:

```
## Step 3a: Read the Wishlist

Read every `*.md` file in `docs/wishlist/`. Each file is one item: YAML
frontmatter (`title`, `status`, `priority`, `date`, optional `source`) plus a
body describing the feature/improvement.

**Item states (frontmatter `status:`):**
- `open` = not yet started
- `in-progress` = work has begun, may need more runs to complete
- `done` = completed; `dropped` = decided against
```

- [ ] **Step 2: Replace the Step 3d archive instruction**

Replace the Step 3d block (the `**Archive done items:** Move … to docs/Wishlist-done.md …` paragraph and the "Mark completed items with `[done]`" bullets) with:

```
## Step 3d: Update the Wishlist

After completing work, edit the item file's frontmatter `status:`:
- completed → `status: done` (add a brief "what was delivered" note to the body)
- partially done → `status: in-progress` (note what's left)

Do NOT move or delete done files — Scout.app and this phase filter by
`status:`, and git is the archive. For a brand-new wishlist item, create
`docs/wishlist/<YYYY-MM-DD>-<slug>.md` with the frontmatter schema above.
```

- [ ] **Step 3: Verify the edit**

Run: `sed -n '/## Step 3a/,/## Step 3e/p' ~/Scout/DREAMING.md | head -40`
Expected: the section now references `docs/wishlist/` per-file + frontmatter `status:`; no remaining `docs/Wishlist.md` / `Wishlist-done.md` references in Phase 3. Also run `grep -n "Wishlist.md\|Wishlist-done" ~/Scout/DREAMING.md` → expected: no Phase-3 hits.

- [ ] **Step 4: Commit (vault repo)**

```bash
git -C ~/Scout add DREAMING.md
git -C ~/Scout commit -m "dreaming: Phase 3 reads/writes per-file wishlist"
```

---

## Task 6: RESEARCH.md Phase 1 — per-file research queue + urgent preemption

**Files:**
- Modify: `~/Scout/RESEARCH.md` (Phase 1 queue-read section, ~lines 168–183)

- [ ] **Step 1: Replace the queue-read instruction**

Replace:

```
Read `knowledge-base/research-queue.md`. If Jordan has explicitly queued topics, those take priority.
```
…and the nearby `Checked items (`- [x]`) are done. Unchecked items are the work queue.` line, with:

```
Read every `*.md` file in `knowledge-base/research-queue/` (the thin
`knowledge-base/research-queue.md` is now just the run log). Each file is one
topic: frontmatter (`title`, `status`, `priority`, `date`, optional `area`) + body.

`status: open`/`in-progress` are the work queue; `done`/`dropped` are resolved.
**Run `priority: urgent` items first** (the START-IMMEDIATELY preemption rule),
then the rest. After researching a topic, set its frontmatter `status: done`
(or `in-progress`) and add findings to the body; write the run's "Last verified
…" continuity note to `knowledge-base/research-queue.md`.
```

- [ ] **Step 2: Verify the edit**

Run: `grep -n "research-queue/\|priority: urgent\|research-queue.md" ~/Scout/RESEARCH.md`
Expected: Phase 1 references the `research-queue/` folder + urgent-first; the only `research-queue.md` reference is the run-log note.

- [ ] **Step 3: Commit (vault repo)**

```bash
git -C ~/Scout add RESEARCH.md
git -C ~/Scout commit -m "research: Phase 1 reads per-file queue, urgent-first"
```

---

## Task 7: scout-plugin templates (fresh-install seeds + runner refs)

**Files:**
- Delete: `~/scout-plugin/templates/docs/Wishlist.md.tmpl`, `Wishlist-in-progress.md.tmpl`, `Wishlist-done.md.tmpl`
- Create: `~/scout-plugin/templates/docs/wishlist/.gitkeep`, `~/scout-plugin/templates/knowledge-base/research-queue/.gitkeep`
- Modify: `~/scout-plugin/templates/knowledge-base/research-queue.md.tmpl`, `~/scout-plugin/templates/run-research.sh.tmpl`, `~/scout-plugin/commands/scout-status.md`

- [ ] **Step 1: Replace the wishlist templates with a seeded directory**

```bash
cd ~/scout-plugin
git rm templates/docs/Wishlist.md.tmpl templates/docs/Wishlist-in-progress.md.tmpl templates/docs/Wishlist-done.md.tmpl
mkdir -p templates/docs/wishlist && touch templates/docs/wishlist/.gitkeep
mkdir -p templates/knowledge-base/research-queue && touch templates/knowledge-base/research-queue/.gitkeep
```

- [ ] **Step 2: Reshape `research-queue.md.tmpl` into the thin-log template**

Use the Edit tool to set `~/scout-plugin/templates/knowledge-base/research-queue.md.tmpl` to:

```
# Research Queue — run log

Per-topic research items live as files in [[research-queue/]]. This file is the
thin run log: the research session records its latest "Last verified …"
continuity note here.

---

_No runs yet._
```

- [ ] **Step 3: Fix the runner + any path references**

In `~/scout-plugin/templates/run-research.sh.tmpl`, change the Phase-1 prompt line "check research-queue.md first" to "check the `research-queue/` folder first".
Then audit `~/scout-plugin/commands/scout-status.md`:
Run: `grep -n "Wishlist\|research-queue" ~/scout-plugin/commands/scout-status.md`
For each hit that reads/globs the old single files, update it to the new dirs (`docs/wishlist/*.md`, `knowledge-base/research-queue/*.md`). If a hit is only descriptive prose, update the wording. (Show the diffs in the report.)

- [ ] **Step 4: Sanity-check no stale references remain in the plugin**

Run: `grep -rn "Wishlist.md\|Wishlist-done\|Wishlist-in-progress" ~/scout-plugin --include=*.tmpl --include=*.md --include=*.sh | grep -v "/.git/"`
Expected: no hits (or only historical changelog/docs entries, which are fine — note them).

- [ ] **Step 5: Commit (scout-plugin repo)**

```bash
cd ~/scout-plugin && git add -A
git commit -m "feat(templates): seed per-file wishlist + research-queue dirs; thin research log"
```

---

## Task 8: End-to-end verification

**Files:** none (verification).

- [ ] **Step 1: Schema conformance of the migrated vault data**

```bash
python3 - <<'PY'
import pathlib, re
for d in ["~/Scout/docs/wishlist", "~/Scout/knowledge-base/research-queue"]:
    p = pathlib.Path(d).expanduser()
    files = list(p.glob("*.md"))
    bad = [f.name for f in files if not f.read_text().startswith("---\n")]
    statuses = {}
    for f in files:
        m = re.search(r"^status:\s*(\S+)", f.read_text(), re.M)
        statuses[m.group(1) if m else "MISSING"] = statuses.get(m.group(1) if m else "MISSING", 0) + 1
    print(d, "files:", len(files), "no-frontmatter:", bad, "statuses:", statuses)
PY
```
Expected: every file starts with frontmatter (no-frontmatter list empty); statuses are only `open|in-progress|done|dropped`.

- [ ] **Step 2: Confirm the prose + templates point at the new layout**

```bash
grep -rn "docs/wishlist/\|research-queue/" ~/Scout/DREAMING.md ~/Scout/RESEARCH.md
grep -rn "Wishlist.md" ~/Scout/DREAMING.md ~/Scout/RESEARCH.md   # expect: none in the phases
```
Expected: DREAMING.md Phase 3 and RESEARCH.md Phase 1 reference the per-file dirs; no lingering single-file reads.

- [ ] **Step 3: Report**

Summarize: item counts migrated (wishlist / research), status distribution, the thin research-queue.md log preserved its latest "Last verified" note, plugin templates reshaped, and both repos committed. Note that the next scheduled dreaming/research run is the live confirmation, and that **sub-project 3 (the app tabs)** is the follow-on plan.

---

## Self-review notes (for the implementer)

- The migration parser is deliberately *shallow* on the body — it lifts obvious markers (status/priority/date/source) and keeps the rest verbatim, which is robust to the messy real prose. The Task-4 dry-run-against-a-copy gate is where real-data edge cases surface; fix the parser there, not by hand-editing migrated files.
- Three repos: script + templates → **scout-plugin**; migrated data + DREAMING/RESEARCH → **vault** (`~/Scout`). Nothing here touches scout-app.
- `default_date` (2026-06-16) is only used for items with no detectable date; pass a different value if re-run later.
- The app sub-project (Wishlist + Research tabs) is a separate plan that reuses the Proposals feature shape against `docs/wishlist/` and `knowledge-base/research-queue/`.
