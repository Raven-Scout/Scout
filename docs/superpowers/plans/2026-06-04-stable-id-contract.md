# Stable-ID Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `[#XXXX]` stable-ID matching the guaranteed write path for Action Items — close the ~50% prefix-coverage gap deterministically, add a one-shot app safety-net, and lock cross-language parser agreement with a contract test — closing scout-app #10.

**Architecture:** Option A (stable IDs in markdown) is already built (`scout.ids`, `id-map.json`, `--by-id`, Swift extraction). This plan adds *enforcement and proof*: (M1) a deterministic post-session backfill in the plugin runner so every line is prefixed before the app sees it; (M2) an app-side backfill-once-then-retry-`--by-id` recovery on the write path only; (M3) a golden-corpus contract test asserting the Swift and Python parsers agree byte-for-byte.

**Tech Stack:** Python 3.11 + pytest (scout-plugin), Swift + swift-testing + XCTest bundle (scout-app), bash templates, git.

**Repos & branches:**
- `scout-app` — branch `feat/stable-id-contract-issue-10` (already created; this plan + spec live here). Tasks M2, M3-app, M4.
- `scout-plugin` — **create** branch `feat/stable-id-backfill-issue-10`. Tasks M1, M3-plugin.

**Cross-repo coordination:** M3 has a canonical corpus in scout-plugin and a byte-identical copy in scout-app guarded by a checksum. Author the corpus once (Task M3.1), then copy it (Task M3.4).

**Reference spec:** `docs/superpowers/specs/2026-06-04-stable-id-contract-design.md`

---

## Milestone 1 — Deterministic session-end backfill (scout-plugin)

Only the briefing/consolidation runner (`run-scout.sh.tmpl`) produces action-items (confirmed: it is the sole template pulling `phases/core/action-items.md`). Dreaming/research write proposals/KB, not task lines, so they are out of scope. The session's own git commits happen *inside* the Claude session; the backfill therefore runs after the session and makes its own commit.

### Task M1.0: Branch

- [ ] **Step 1: Create the plugin branch**

Run:
```bash
cd /Users/jordanburger/scout-plugin && git checkout -b feat/stable-id-backfill-issue-10
```
Expected: `Switched to a new branch 'feat/stable-id-backfill-issue-10'`

### Task M1.1: Failing integration test for the post-session backfill wrapper

**Files:**
- Test: `/Users/jordanburger/scout-plugin/engine/tests/integration/test_post_session_backfill.py` (create)

The test renders the (not-yet-created) template by substituting its two `{{…}}` vars, runs it inside a temp git vault containing an action-items file with unprefixed open tasks, and asserts: prefixes get added, exactly one commit is made, and a second run is a no-op (idempotent).

- [ ] **Step 1: Write the failing test**

```python
"""Integration test for templates/scripts/post-session-backfill.sh.tmpl.

Renders the template (substituting SCOUT_DIR + SCOUTCTL_BIN), runs it against
a temp git vault, and asserts the backfill prefixes open tasks and commits
exactly once — idempotently. Mirrors the deterministic session-end guarantee
behind scout-app issue #10.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "post-session-backfill.sh.tmpl"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _render(tmpl: Path, scout_dir: Path, scoutctl_bin: str) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    text = text.replace("{{SCOUT_DIR}}", str(scout_dir)).replace("{{SCOUTCTL_BIN}}", scoutctl_bin)
    out = scout_dir / "scripts" / "post-session-backfill.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    scout_dir = tmp_path / "Scout"
    (scout_dir / "action-items").mkdir(parents=True)
    # An ET-local "today" file is what the CLI targets by default; create a
    # file for a fixed date and point the CLI at it explicitly via env below.
    daily = scout_dir / "action-items" / "action-items-2026-06-04.md"
    daily.write_text(
        "# Tuesday, June 4\n\n"
        "## 🔴 Urgent\n\n"
        "- [ ] **Unprefixed urgent task** — needs an id\n"
        "- [ ] [#AB12] **Already prefixed** — leave me\n",
        encoding="utf-8",
    )
    _git(scout_dir, "init", "-q")
    _git(scout_dir, "config", "user.email", "test@example.com")
    _git(scout_dir, "config", "user.name", "test")
    _git(scout_dir, "add", "-A")
    _git(scout_dir, "commit", "-q", "-m", "seed")
    return scout_dir


def test_backfill_adds_prefix_and_commits_once(vault: Path) -> None:
    scoutctl = shutil.which("scoutctl")
    assert scoutctl, "scoutctl must be on PATH for this integration test"
    script = _render(TEMPLATE, vault, scoutctl)

    env = {**os.environ, "SCOUT_DATA_DIR": str(vault)}
    before = _git(vault, "rev-list", "--count", "HEAD").strip()

    r1 = subprocess.run([str(script)], env=env, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr

    daily = (vault / "action-items" / "action-items-2026-06-04.md").read_text()
    # The previously-unprefixed line now carries a [#XXXX] prefix.
    import re

    assert re.search(r"- \[ \] \[#[0-9A-HJKMNP-TV-Z]{4}\] \*\*Unprefixed urgent task\*\*", daily)
    assert "[#AB12]" in daily  # untouched

    after = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert int(after) == int(before) + 1, "exactly one backfill commit expected"

    # Idempotent: a second run makes no new commit.
    r2 = subprocess.run([str(script)], env=env, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    after2 = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert after2 == after, "second run must be a no-op"
```

- [ ] **Step 2: Run it to confirm it fails (template missing)**

Run:
```bash
cd /Users/jordanburger/scout-plugin/engine && python -m pytest tests/integration/test_post_session_backfill.py -v
```
Expected: FAIL — `FileNotFoundError`/read error on `post-session-backfill.sh.tmpl` (template not created yet).

### Task M1.2: Create the backfill wrapper template

**Files:**
- Create: `/Users/jordanburger/scout-plugin/templates/scripts/post-session-backfill.sh.tmpl`

- [ ] **Step 1: Write the template**

```bash
#!/bin/bash
# Post-session backfill — guarantees every open action-item task line carries a
# stable [#XXXX] prefix, deterministically, regardless of whether the session
# prompt minted them. Runs AFTER the Claude session (which makes its own
# commits) and lands any newly-minted prefixes in a dedicated commit, so
# scout-app's `--by-id` write path always has a structural key to match on.
#
# Idempotent: a file already fully prefixed produces no diff and no commit.
# Background: scout-app issue #10 + docs spec 2026-06-04-stable-id-contract.
set -euo pipefail

SCOUT_DIR="{{SCOUT_DIR}}"
SCOUTCTL_BIN="{{SCOUTCTL_BIN}}"
if [[ ! -x "$SCOUTCTL_BIN" ]]; then
    if command -v scoutctl >/dev/null 2>&1; then
        SCOUTCTL_BIN="$(command -v scoutctl)"
    else
        echo "post-session-backfill.sh: scoutctl not found (looked at ${SCOUTCTL_BIN})" >&2
        exit 0   # never fail the run over a missing backfill
    fi
fi

export SCOUT_DATA_DIR="${SCOUT_DATA_DIR:-$SCOUT_DIR}"

# Backfill today's action-items file. With no PATH argument the CLI targets the
# ET-local "today" file via scout.paths — the same date logic the rest of the
# engine uses.
"$SCOUTCTL_BIN" action-items backfill-prefixes || true

# Commit only the changes the backfill introduced. The session already
# committed its own work, so a non-empty diff under action-items/ (plus the
# id-map registration) is the prefix edits.
cd "$SCOUT_DIR"
PATHS_TO_COMMIT=(action-items)
if [ -f .scout-state/id-map.json ] && ! git check-ignore -q .scout-state/id-map.json; then
    PATHS_TO_COMMIT+=(.scout-state/id-map.json)
fi
if ! git diff --quiet -- "${PATHS_TO_COMMIT[@]}" 2>/dev/null; then
    git add "${PATHS_TO_COMMIT[@]}"
    git commit -m "chore(action-items): backfill stable [#XXXX] prefixes" >/dev/null
fi
```

- [ ] **Step 2: Run the test to verify it passes**

Run:
```bash
cd /Users/jordanburger/scout-plugin/engine && python -m pytest tests/integration/test_post_session_backfill.py -v
```
Expected: PASS (both assertions: one commit added, second run no-op).

- [ ] **Step 3: Commit**

```bash
cd /Users/jordanburger/scout-plugin
git add templates/scripts/post-session-backfill.sh.tmpl engine/tests/integration/test_post_session_backfill.py
git commit -m "feat(action-items): deterministic post-session prefix backfill wrapper

Guarantees every open task line carries a [#XXXX] stable prefix after each
briefing/consolidation session, independent of prompt compliance. Refs #10."
```

### Task M1.3: Wire the wrapper into the runner template

**Files:**
- Modify: `/Users/jordanburger/scout-plugin/templates/run-scout.sh.tmpl` (insert after the "run finished" log block, before the cost tracker)

- [ ] **Step 1: Insert the post-session invocation**

Insert immediately after the `if [ $EXIT_CODE -ne 0 ]; then … fi` failure block (currently ending at line 116) and before the `# Track session cost` block:

```bash
# Post-session: guarantee stable [#XXXX] prefixes on today's action items so
# scout-app's --by-id write path always has a key (issue #10 / stable-id
# contract). Deterministic — does not depend on the session prompt complying.
POST_BACKFILL="$SCOUT_DIR/scripts/post-session-backfill.sh"
if [ -x "$POST_BACKFILL" ]; then
    "$POST_BACKFILL" >> "$LOG_FILE" 2>&1 || true
fi
```

- [ ] **Step 2: Verify the installer renders the new wrapper**

The bootstrap copies `templates/scripts/*.tmpl` → `$SCOUT_DIR/scripts/`, substituting `{{SCOUT_DIR}}` and `{{SCOUTCTL_BIN}}`. Confirm the new template is globbed and both vars are known.

Run:
```bash
cd /Users/jordanburger/scout-plugin && grep -rn "templates/scripts\|SCOUTCTL_BIN" engine/scout/ scripts/ | grep -i "render\|glob\|install\|scripts" | head
```
Expected: a render/copy step that iterates `templates/scripts/*.tmpl` (so the new file is picked up automatically). If instead each script is listed explicitly, add `post-session-backfill.sh` to that list in the same edit and note it here.

- [ ] **Step 3: Commit**

```bash
cd /Users/jordanburger/scout-plugin
git add templates/run-scout.sh.tmpl
git commit -m "feat(action-items): run post-session prefix backfill from runner

Refs #10."
```

### Task M1.4: Backfill the live vault now (one-shot adoption)

The runner change only takes effect after the user re-renders templates (`/scout-update`). To fix today's vault immediately:

- [ ] **Step 1: Dry-run, then apply, then commit (run by the user)**

Suggest the user run in their session:
```bash
scoutctl action-items backfill-prefixes --dry-run
scoutctl action-items backfill-prefixes
cd ~/Scout && git add action-items/ .scout-state/id-map.json 2>/dev/null; git commit -m "chore(action-items): backfill stable [#XXXX] prefixes"
```
Expected: dry-run lists the ~50% unprefixed lines; apply prefixes them; commit records it. This is verification, not code — confirm coverage with:
```bash
f=~/Scout/action-items/action-items-$(TZ=America/New_York date +%F).md
echo "$(grep -cE '^\s*- \[[ xX]\] \[#' "$f") / $(grep -cE '^\s*- \[[ xX]\] ' "$f") prefixed"
```
Expected: prefixed count == total open task lines.

---

## Milestone 2 — App-side backfill-then-retry safety net (scout-app)

When the app acts on a line that has no prefix (hand-added in Obsidian between sessions, or a pre-M1 legacy line) it currently sends `--subject` and can fail with `no open task matched subject`. The safety net: on a `noMatch` for an unprefixed op, run `backfill-prefixes` once, re-read the target line's freshly-minted prefix (line numbers are stable — `backfill_prefixes` edits in place), and retry with `--by-id`. Write-path only; never on load (keeps clear of #22's file-watcher churn).

### Task M2.1: `WriteOp.withShortPrefix` helper + failing test

**Files:**
- Modify: `Scout/ActionItems/ActionItemsWriter.swift` (add a method on `WriteOp`)
- Test: `ScoutTests/ActionItems/ActionItemsWriterTests.swift` (add a test)

- [ ] **Step 1: Write the failing test**

Add to `ActionItemsWriterTests`:
```swift
@Test func withShortPrefixReplacesPrefixPreservingPayload() {
    let op = WriteOp.addComment(subject: "Subj", shortPrefix: nil, text: "hi", author: "jordan")
    let promoted = op.withShortPrefix("AB12")
    #expect(promoted.shortPrefix == "AB12")
    #expect(promoted.subject == "Subj")
    if case .addComment(_, _, let text, let author) = promoted {
        #expect(text == "hi")
        #expect(author == "jordan")
    } else {
        Issue.record("case changed unexpectedly")
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItemsWriterTests/withShortPrefixReplacesPrefixPreservingPayload 2>&1 | tail -20
```
Expected: FAIL — `value of type 'WriteOp' has no member 'withShortPrefix'`.

- [ ] **Step 3: Implement `withShortPrefix`**

Add inside `enum WriteOp`, after the `shortPrefix` computed property:
```swift
/// Return a copy of this op with its short prefix replaced. Used by the
/// writer's safety-net to promote an unprefixed op to `--by-id` after a
/// just-in-time backfill mints a prefix for the target line.
func withShortPrefix(_ prefix: String) -> WriteOp {
    switch self {
    case .addComment(let s, _, let t, let a): return .addComment(subject: s, shortPrefix: prefix, text: t, author: a)
    case .deleteComment(let s, _, let sel):   return .deleteComment(subject: s, shortPrefix: prefix, selector: sel)
    case .editComment(let s, _, let sel, let n): return .editComment(subject: s, shortPrefix: prefix, selector: sel, newText: n)
    case .markDone(let s, _):                 return .markDone(subject: s, shortPrefix: prefix)
    case .reopen(let s, _):                   return .reopen(subject: s, shortPrefix: prefix)
    case .snooze(let s, _, let u, let fk):    return .snooze(subject: s, shortPrefix: prefix, until: u, fromKind: fk)
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/jordanburger/scout-app
git add Scout/ActionItems/ActionItemsWriter.swift ScoutTests/ActionItems/ActionItemsWriterTests.swift
git commit -m "feat(action-items): WriteOp.withShortPrefix helper for safety-net retry

Refs #10."
```

### Task M2.2: Prefix-at-line reader + failing test

**Files:**
- Modify: `Scout/ActionItems/ActionItemsWriter.swift` (add a static helper)
- Test: `ScoutTests/ActionItems/ActionItemsWriterTests.swift`

- [ ] **Step 1: Write the failing test**

```swift
@Test func readsShortPrefixAtLineNumber() throws {
    let tmp = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("ai-\(UUID().uuidString).md")
    let md = """
    # Title

    ## 🔴 Urgent

    - [ ] [#AB12] **First** — body
    - [ ] **Unprefixed** — body
    """
    try md.write(to: tmp, atomically: true, encoding: .utf8)
    defer { try? FileManager.default.removeItem(at: tmp) }

    // 1-based line numbers, matching ActionTask.lineNumber.
    #expect(ActionItemsWriter.shortPrefix(inFile: tmp, atLine: 5) == "AB12")
    #expect(ActionItemsWriter.shortPrefix(inFile: tmp, atLine: 6) == nil)
    #expect(ActionItemsWriter.shortPrefix(inFile: tmp, atLine: 999) == nil)
}
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItemsWriterTests/readsShortPrefixAtLineNumber 2>&1 | tail -20
```
Expected: FAIL — no member `shortPrefix(inFile:atLine:)`.

- [ ] **Step 3: Implement the reader**

Add to `actor ActionItemsWriter` as a `static` method (place above `perform`):
```swift
/// Read the `[#XXXX]` prefix on a specific 1-based line of an action-items
/// file, or nil if that line has no prefix / doesn't exist. Used by the
/// safety-net after a just-in-time backfill — line numbers are stable
/// because `backfill_prefixes` edits lines in place (no insert/remove).
static func shortPrefix(inFile url: URL, atLine line: Int) -> String? {
    guard line >= 1,
          let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
    let lines = text.components(separatedBy: "\n")
    guard line <= lines.count else { return nil }
    let target = lines[line - 1]
    guard let re = try? NSRegularExpression(
        pattern: #"^\s*- \[[ xX]\] \[#([0-9A-HJKMNP-TV-Z]{4})\]"#
    ) else { return nil }
    let range = NSRange(target.startIndex..., in: target)
    guard let m = re.firstMatch(in: target, range: range),
          let r = Range(m.range(at: 1), in: target) else { return nil }
    return String(target[r])
}
```

- [ ] **Step 4: Run to verify it passes**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/jordanburger/scout-app
git add Scout/ActionItems/ActionItemsWriter.swift ScoutTests/ActionItems/ActionItemsWriterTests.swift
git commit -m "feat(action-items): read [#XXXX] prefix at a given line for retry

Refs #10."
```

### Task M2.3: Wire recovery into `submit`/`perform` + failing test

**Files:**
- Modify: `Scout/ActionItems/ActionItemsWriter.swift` (`submit`, `perform`)
- Test: `ScoutTests/ActionItems/ActionItemsWriterTests.swift`

The recovery needs the target line number and the ability to run a backfill subprocess. `RecordingRunner` (the test mock) must be able to (a) fail the first call with `noMatch` exit code 2, (b) record the backfill call, (c) succeed the retry. Confirm/extend the mock to support scripted per-call results.

- [ ] **Step 1: Inspect the existing mock**

Run:
```bash
cd /Users/jordanburger/scout-app && grep -n "RecordingRunner\|struct.*ProcessRunner\|func run" ScoutTests/ActionItems/*.swift Scout/**/*.swift | grep -i "runner\|ProcessResult\|func run" | head
```
Expected: locate `RecordingRunner` and the `ProcessRunner` protocol + `ProcessResult` shape. If `RecordingRunner` returns a single canned success, extend it (Step 2) to dequeue scripted results.

- [ ] **Step 2: Write the failing test (and extend the mock if needed)**

If `RecordingRunner` lacks scripted results, add a queue to it (in the test file where it's defined):
```swift
// Append to RecordingRunner: a FIFO of results; falls back to success.
// (Add `var scripted: [ProcessResult] = []` and pop it at the front of run().)
```
Then add the test:
```swift
@Test func backfillsThenRetriesByIdOnNoMatchForUnprefixedOp() async throws {
    // Arrange a real temp file the writer can re-read after the "backfill".
    let dir = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("Scout-\(UUID().uuidString)")
    let ai = dir.appendingPathComponent("action-items")
    try FileManager.default.createDirectory(at: ai, withIntermediateDirectories: true)
    let date = Calendar(identifier: .iso8601).date(from: DateComponents(
        timeZone: TimeZone(identifier: "America/New_York"), year: 2026, month: 4, day: 20))!
    let daily = ai.appendingPathComponent("action-items-2026-04-20.md")
    // Line 1 is the unprefixed task; the "backfill" run will rewrite it WITH a prefix.
    try "- [ ] **Ship it** — now".write(to: daily, atomically: true, encoding: .utf8)
    defer { try? FileManager.default.removeItem(at: dir) }

    let recorder = RecordingRunner()
    // 1st call (the --subject markDone) fails noMatch; the backfill call
    // succeeds AND mutates the file; the retry (--by-id) succeeds.
    recorder.scripted = [
        ProcessResult(exitCode: 2, stdout: Data(), stderr: Data("no open task matched subject".utf8)),
        ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // backfill
        ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // retry
    ]
    recorder.onCall = { call in
        if call.arguments.contains("backfill-prefixes") {
            try? "- [ ] [#QW34] **Ship it** — now".write(to: daily, atomically: true, encoding: .utf8)
        }
    }

    let writer = ActionItemsWriter(
        scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        actionItemsDirectory: ai, scoutDirectory: dir, runner: recorder, gitService: nil)

    _ = try await writer.submit(
        .markDone(subject: "Ship it", shortPrefix: nil),
        displayedDate: date, recoveryLineNumber: 1)

    let calls = await recorder.calls
    #expect(calls.count == 3)
    #expect(calls[1].arguments.contains("backfill-prefixes"))
    #expect(calls[2].arguments.contains("--by-id"))
    #expect(calls[2].arguments.contains("QW34"))
}
```
> Note: `ProcessResult`/`onCall` field names above must match the real types found in Step 1. Adjust names to the actual `ProcessResult` initializer and add `var onCall: ((Call) -> Void)?` to `RecordingRunner` if absent.

- [ ] **Step 3: Run to verify it fails**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItemsWriterTests/backfillsThenRetriesByIdOnNoMatchForUnprefixedOp 2>&1 | tail -25
```
Expected: FAIL — `submit` has no `recoveryLineNumber:` parameter.

- [ ] **Step 4: Implement the recovery**

In `submit`, add the parameter and forward it:
```swift
@discardableResult
func submit(_ op: WriteOp, displayedDate: Date, recoveryLineNumber: Int? = nil) async throws -> WriteResult {
    let previous = tail
    let task = Task { [scoutctl, argumentsPrefix, runner, actionItemsDirectory, scoutDirectory, gitService] in
        _ = await previous?.value
        return try await Self.perform(
            op: op, displayedDate: displayedDate, recoveryLineNumber: recoveryLineNumber,
            scoutctl: scoutctl, argumentsPrefix: argumentsPrefix,
            actionItemsDirectory: actionItemsDirectory, scoutDirectory: scoutDirectory,
            runner: runner, gitService: gitService)
    }
    tail = Task { _ = try? await task.value }
    return try await task.value
}
```

In `perform`, add `recoveryLineNumber: Int?` to the signature, and wrap the non-zero-exit branch so a `noMatch` on an unprefixed op triggers one backfill + retry. Replace the existing `if result.exitCode != 0 { throw … }` block with:
```swift
if result.exitCode != 0 {
    let cls = Self.classify(exitCode: result.exitCode, stderr: stderr)
    // Safety net: an unprefixed op missed on --subject. Mint prefixes via a
    // one-shot backfill, then retry by stable id. One attempt only.
    if cls == .noMatch, op.shortPrefix == nil, let line = recoveryLineNumber {
        _ = try? await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["action-items", "backfill-prefixes", dailyFile.path],
            environment: [:], workingDirectory: scoutDirectory)
        if let prefix = Self.shortPrefix(inFile: dailyFile, atLine: line) {
            let retryOp = op.withShortPrefix(prefix)
            let retry: ProcessResult
            do {
                retry = try await runner.run(
                    executable: scoutctl,
                    arguments: argumentsPrefix + retryOp.scoutctlArguments(dailyFilePath: dailyFile),
                    environment: [:], workingDirectory: scoutDirectory)
            } catch { throw ActionItemsWriterError.processFailed(error) }
            let retryStderr = String(data: retry.stderr, encoding: .utf8) ?? ""
            if retry.exitCode == 0 {
                let slug = Self.slugify(op.subject)
                try? await gitService?.commitAll(message: "action-items: \(op.verb) \(slug)")
                return WriteResult(stderr: retryStderr)
            }
            throw ActionItemsWriterError.cliNonZeroExit(
                exitCode: retry.exitCode, stderr: retryStderr,
                classification: Self.classify(exitCode: retry.exitCode, stderr: retryStderr))
        }
    }
    throw ActionItemsWriterError.cliNonZeroExit(exitCode: result.exitCode, stderr: stderr, classification: cls)
}
```
> `scoutctlArguments` is `fileprivate`; it's already in the same file, so `retryOp.scoutctlArguments(dailyFilePath:)` is accessible from `perform`.

- [ ] **Step 5: Run to verify it passes (and the existing suite still passes)**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItemsWriterTests 2>&1 | tail -25
```
Expected: PASS for the new test and all existing `ActionItemsWriterTests`.

- [ ] **Step 6: Commit**

```bash
cd /Users/jordanburger/scout-app
git add Scout/ActionItems/ActionItemsWriter.swift ScoutTests/ActionItems/ActionItemsWriterTests.swift
git commit -m "feat(action-items): one-shot backfill+by-id retry on noMatch

When an unprefixed op misses on --subject, mint prefixes via backfill and
retry by stable id. Write-path only; no extra load-time churn. Refs #10."
```

### Task M2.4: Thread the recovery line number from the UI

**Files:**
- Modify: `Scout/ActionItems/ActionItemsView.swift` (`handleOp`, the `onOp: handleOp` wiring)
- Modify: `Scout/ActionItems/Views/SectionView.swift` (`onOp` type + forwarding)
- Modify: `Scout/ActionItems/Views/TaskCardView.swift` (`onOp` type, `runOp`, call sites, TaskActionsView wiring)
- Modify: `Scout/ActionItems/Views/TaskActionsView.swift` (`onOp` type + call sites)

Change the `onOp` closure to carry the originating task's line number so `handleOp` can pass it as `recoveryLineNumber`.

- [ ] **Step 1: Update `handleOp` to accept and forward the line number**

In `ActionItemsView.swift` replace `handleOp`:
```swift
private func handleOp(_ op: WriteOp, lineNumber: Int?) async throws {
    do {
        _ = try await writerBox.writer.submit(op, displayedDate: displayedDate, recoveryLineNumber: lineNumber)
        await MainActor.run { docService.reparseCurrent() }
    } catch let err as ActionItemsWriterError {
        if case .cliNonZeroExit(_, _, let kind) = err, kind == .environment {
            await MainActor.run { setToast("Environment problem — check python3 install.") }
        }
        throw err
    }
}
```
And update the wiring (currently `onOp: handleOp`, ~line 123) to match the new closure shape — if passed as a bare function reference it continues to work once the closure type below matches `(WriteOp, Int?) async throws -> Void`.

- [ ] **Step 2: Update `SectionView` closure type + forwarding**

In `SectionView.swift` change:
```swift
let onOp: (WriteOp, Int?) async throws -> Void
```
and pass it through unchanged to `TaskCardView(onOp: onOp …)`.

- [ ] **Step 3: Update `TaskCardView`**

Change the stored closure + initializer parameter type to `(WriteOp, Int?) async throws -> Void`. Update `runOp` and its callers:
```swift
private func runOp(_ op: WriteOp) async {
    do {
        try await onOp(op, task.lineNumber)
        await MainActor.run { inlineError = nil }
    } catch let err as ActionItemsWriterError {
        await MainActor.run { inlineError = describe(err) }
    } catch {
        await MainActor.run { inlineError = error.localizedDescription }
    }
}
```
The `.markDone` call at line ~171 already routes through `runOp`, so it needs no per-call change. For the `TaskActionsView(onOp:)` it constructs (line ~264), pass an adapter (Step 4).

- [ ] **Step 4: Update `TaskActionsView`**

Change its closure type to `(WriteOp, Int?) async -> Void` and update each call site to pass the task's line number, e.g.:
```swift
Task { await onOp(.markDone(subject: task.matchableSubject, shortPrefix: task.shortPrefix), task.lineNumber) }
```
(do the same for the `.reopen` and `.snooze` call sites). In `TaskCardView`, wire `TaskActionsView(onOp: { op, line in await runOpRaw(op, line) } …)` or, simplest, pass `{ op, _ in await runOp(op) }` since `runOp` already reads `task.lineNumber` — choose the form that compiles cleanly with the existing `TaskActionsView` ownership of `task`.

- [ ] **Step 5: Build to verify everything compiles**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild build -project Scout.xcodeproj -scheme Scout 2>&1 | tail -15
```
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 6: Run the full Action Items test suite**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ActionItems 2>&1 | tail -20
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/jordanburger/scout-app
git add Scout/ActionItems/ActionItemsView.swift Scout/ActionItems/Views/SectionView.swift Scout/ActionItems/Views/TaskCardView.swift Scout/ActionItems/Views/TaskActionsView.swift
git commit -m "feat(action-items): thread task line number into write ops for retry

Refs #10."
```

---

## Milestone 3 — Cross-language parser contract test

A golden corpus of raw task lines + expected `{short_prefix, subject, plain_subject, body}`. Canonical in scout-plugin; copied to scout-app; a checksum guard fails on drift. Seeded with #10's historical failure modes.

### Task M3.1: Author the golden corpus (canonical, scout-plugin)

**Files:**
- Create: `/Users/jordanburger/scout-plugin/engine/tests/fixtures/contract/parser-corpus.json`

- [ ] **Step 1: Write the corpus**

```json
{
  "_doc": "Cross-language parser contract. Each entry is a raw action-item task line; both the Swift (scout-app) and Python (scout-plugin) parsers MUST reproduce expected exactly. Seeded with scout-app #10 failure modes. Canonical copy lives here; scout-app/ScoutTests/Fixtures/parser-corpus.json must be byte-identical (checksum-guarded).",
  "entries": [
    {
      "name": "prefixed-emoji-bold",
      "line": "- [ ] [#AI30] **🔴 Validate kai-agent LangSmith tracing — [[AI-3026]]** _(carries 6/2→6/4)_ — overnight progress",
      "expected": {
        "short_prefix": "AI30",
        "subject": "🔴 Validate kai-agent LangSmith tracing — [[AI-3026]]",
        "plain_subject": "🔴 Validate kai-agent LangSmith tracing — AI-3026",
        "body": "_(carries 6/2→6/4)_ — overnight progress"
      }
    },
    {
      "name": "unprefixed-the-issue10-line",
      "line": "- [ ] **🔥 🆕 Update kai-pricing-calculator-app with per-client conversion levers** _(net-new from Kai's pricing meeting)_",
      "expected": {
        "short_prefix": null,
        "subject": "🔥 🆕 Update kai-pricing-calculator-app with per-client conversion levers",
        "plain_subject": "🔥 🆕 Update kai-pricing-calculator-app with per-client conversion levers",
        "body": "_(net-new from Kai's pricing meeting)_"
      }
    },
    {
      "name": "title-case-scout-no-separator",
      "line": "- [ ] [#SC01] Review Scout backlog",
      "expected": { "short_prefix": "SC01", "subject": "Review Scout backlog", "plain_subject": "Review Scout backlog", "body": "" }
    },
    {
      "name": "done-with-github-pr-link",
      "line": "- [x] [#PR99] **Merge the fix** — see https://github.com/keboola/keboola_com/pull/301",
      "expected": {
        "short_prefix": "PR99",
        "subject": "Merge the fix",
        "plain_subject": "Merge the fix",
        "body": "see https://github.com/keboola/keboola_com/pull/301"
      }
    },
    {
      "name": "snooze-suffix",
      "line": "- [ ] [#SNZ1] **Ping Devin** — nudge — 🛌 Snoozed until 2026-06-10",
      "expected": {
        "short_prefix": "SNZ1",
        "subject": "Ping Devin",
        "plain_subject": "Ping Devin",
        "body": "nudge — 🛌 Snoozed until 2026-06-10"
      }
    },
    {
      "name": "carry-in-was-kind",
      "line": "- [ ] [#CRY2] **Reschedule Groupon** _(carried in from 2026-06-03, was urgent)_",
      "expected": {
        "short_prefix": "CRY2",
        "subject": "Reschedule Groupon",
        "plain_subject": "Reschedule Groupon",
        "body": "_(carried in from 2026-06-03, was urgent)_"
      }
    },
    {
      "name": "wikilink-alias-and-code",
      "line": "- [ ] [#WK01] **Check [[AI-2619|the cost ruling]]** — run `scoutctl status`",
      "expected": {
        "short_prefix": "WK01",
        "subject": "Check [[AI-2619|the cost ruling]]",
        "plain_subject": "Check AI-2619",
        "body": "run `scoutctl status`"
      }
    }
  ]
}
```
> The `expected` values above encode the intended contract. During Step 2 you will run both parsers against the corpus; if a parser disagrees, that is either (a) a real cross-language bug to fix in the parser, or (b) an incorrect expectation to correct here. Resolve each disagreement explicitly — do not loosen the test to paper over a real drift.

### Task M3.2: Python contract test (scout-plugin)

**Files:**
- Test: `/Users/jordanburger/scout-plugin/engine/tests/unit/test_parser_contract.py` (create)

- [ ] **Step 1: Write the test**

```python
"""Cross-language parser contract — Python side.

Asserts scout-plugin's parser reproduces parser-corpus.json exactly. The same
corpus is asserted by scout-app's Swift ParserContractTests; the two corpus
copies are checksum-guarded so they cannot drift. See scout-app issue #10.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.action_items.parser import parse_lines  # adjust to the real entry point

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "parser-corpus.json"


def _load() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["entries"]


@pytest.mark.parametrize("entry", _load(), ids=lambda e: e["name"])
def test_parser_matches_contract(entry: dict) -> None:
    exp = entry["expected"]
    # Parse the single task line through the plugin's real parser. Wrap in a
    # minimal section so the line is recognized as a task.
    text = "# T\n\n## 🔴 Urgent\n\n" + entry["line"] + "\n"
    items = parse_lines(text)  # adjust call to the actual parser signature
    assert len(items) == 1, f"expected one item for {entry['name']}"
    item = items[0]
    assert (item.short_prefix or None) == exp["short_prefix"]
    assert item.title == exp["subject"]
    assert item.plain_subject == exp["plain_subject"]   # adjust attr name to plugin's
    assert item.body == exp["body"]                     # adjust attr name to plugin's
```
> Step 1a: confirm the real parser entry point + attribute names. Run:
> ```bash
> cd /Users/jordanburger/scout-plugin && grep -n "def parse\|short_prefix\|plain\|title\|body" engine/scout/action_items/parser.py | head -30
> ```
> Map `parse_lines`/`item.plain_subject`/`item.body` to the actual names (e.g. the parser may expose `parse_file` only — if so, write the corpus line to a temp file and parse that, or factor a `parse_text` helper). Adjust the test to the real API before running.

- [ ] **Step 2: Run the test**

Run:
```bash
cd /Users/jordanburger/scout-plugin/engine && python -m pytest tests/unit/test_parser_contract.py -v
```
Expected: all parametrized cases PASS. For any failure, decide per the Step-1 note in M3.1 (fix parser vs. fix expectation) and re-run.

- [ ] **Step 3: Commit**

```bash
cd /Users/jordanburger/scout-plugin
git add engine/tests/fixtures/contract/parser-corpus.json engine/tests/unit/test_parser_contract.py
git commit -m "test(action-items): cross-language parser contract corpus + Python side

Refs #10."
```

### Task M3.3: Compute the canonical checksum

- [ ] **Step 1: Record the corpus SHA-256**

Run:
```bash
shasum -a 256 /Users/jordanburger/scout-plugin/engine/tests/fixtures/contract/parser-corpus.json | awk '{print $1}'
```
Expected: a 64-char hex digest. Note it — it is embedded in both the app copy guard (M3.4) and used to prove the copies match.

### Task M3.4: Copy corpus into scout-app + Swift contract test + checksum guard

**Files:**
- Create: `/Users/jordanburger/scout-app/ScoutTests/Fixtures/parser-corpus.json` (byte-identical copy)
- Test: `/Users/jordanburger/scout-app/ScoutTests/ActionItems/ParserContractTests.swift` (create)

- [ ] **Step 1: Copy the corpus byte-for-byte**

Run:
```bash
cp /Users/jordanburger/scout-plugin/engine/tests/fixtures/contract/parser-corpus.json \
   /Users/jordanburger/scout-app/ScoutTests/Fixtures/parser-corpus.json
shasum -a 256 /Users/jordanburger/scout-app/ScoutTests/Fixtures/parser-corpus.json | awk '{print $1}'
```
Expected: digest identical to M3.3. (`ScoutTests/Fixtures/` is a file-system-synchronized group, so the file auto-bundles — no pbxproj edit.)

- [ ] **Step 2: Write the Swift contract test**

```swift
import Testing
import Foundation
import CryptoKit
@testable import Scout

@Suite("Parser contract — Swift side")
struct ParserContractTests {
    static let bundle = Bundle(for: ActionItemsFixtureAnchor.self)

    /// Must equal the canonical scout-plugin corpus digest (Task M3.3).
    /// If this fails, the two corpus copies have drifted — re-copy from the
    /// plugin, do not edit only one side.
    static let canonicalSHA256 = "PASTE_DIGEST_FROM_M3.3"

    struct Entry: Decodable {
        struct Expected: Decodable {
            let short_prefix: String?
            let subject: String
            let plain_subject: String
            let body: String
        }
        let name: String
        let line: String
        let expected: Expected
    }

    private static func corpusURL() throws -> URL {
        guard let url = bundle.url(forResource: "parser-corpus", withExtension: "json")
                ?? bundle.resourceURL?.appendingPathComponent("parser-corpus.json") else {
            Issue.record("parser-corpus.json not in test bundle")
            throw CocoaError(.fileReadNoSuchFile)
        }
        return url
    }

    @Test func corpusMatchesCanonicalChecksum() throws {
        let data = try Data(contentsOf: try Self.corpusURL())
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        #expect(digest == Self.canonicalSHA256,
                "Corpus drift: scout-app copy != scout-plugin canonical. Re-copy, don't edit one side.")
    }

    @Test func parserMatchesContract() throws {
        let data = try Data(contentsOf: try Self.corpusURL())
        let entries = try JSONDecoder().decode([String: [Entry]].self, from: data)["entries"] ?? []
        #expect(!entries.isEmpty)
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-04-20.md")
        for e in entries {
            let text = "# T\n\n## 🔴 Urgent\n\n\(e.line)\n"
            let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
            let tasks = doc.sections.flatMap { $0.tasks }
            guard let t = tasks.first, tasks.count == 1 else {
                Issue.record("\(e.name): expected exactly one task, got \(tasks.count)")
                continue
            }
            #expect(t.shortPrefix == e.expected.short_prefix, "\(e.name): short_prefix")
            #expect(t.subject == e.expected.subject, "\(e.name): subject")
            #expect(t.plainSubject == e.expected.plain_subject, "\(e.name): plain_subject")
            #expect(t.body == e.expected.body, "\(e.name): body")
        }
    }
}
```

- [ ] **Step 3: Paste the digest**

Replace `PASTE_DIGEST_FROM_M3.3` with the digest from Task M3.3.

- [ ] **Step 4: Run the Swift contract test**

Run:
```bash
cd /Users/jordanburger/scout-app && xcodebuild test -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/ParserContractTests 2>&1 | tail -25
```
Expected: both tests PASS. Any `parserMatchesContract` failure is a genuine Swift↔Python drift — fix the Swift parser (or correct the expectation in the canonical corpus and re-copy + re-checksum) per the M3.1 note.

- [ ] **Step 5: Commit**

```bash
cd /Users/jordanburger/scout-app
git add ScoutTests/Fixtures/parser-corpus.json ScoutTests/ActionItems/ParserContractTests.swift
git commit -m "test(action-items): cross-language parser contract — Swift side + checksum guard

Refs #10."
```

---

## Milestone 4 — Close #10

### Task M4.1: Cross-link spec and close the issue

- [ ] **Step 1: Verify the spec already enumerates all 7 acceptance items**

Run:
```bash
grep -n "acceptance\|### [1-7]\." /Users/jordanburger/scout-app/docs/superpowers/specs/2026-06-04-stable-id-contract-design.md | head
```
Expected: the seven items are present (contract, write protocol, read protocol, migration, contract test, hand-edit, fallback). They are — this step is a sanity check.

- [ ] **Step 2: Post a closing summary on #10 (after M1–M3 PRs merge)**

Run (adjust PR numbers once opened):
```bash
gh issue comment 10 --repo jordanrburger/Scout --body "Resolved via Option A (stable [#XXXX] IDs), which was ~80% already built. Closing the real gaps:
- Design doc: docs/superpowers/specs/2026-06-04-stable-id-contract-design.md (covers all 7 acceptance items).
- M1 (scout-plugin): deterministic post-session backfill → ~100% prefix coverage, not prompt-dependent.
- M2 (scout-app): one-shot backfill+by-id retry on the write path.
- M3: cross-language parser contract test (golden corpus, checksum-guarded).
The --subject substring path is now a last-resort fallback only."
gh issue close 10 --repo jordanrburger/Scout
```
Expected: comment posted, issue closed. **Only run after the implementing PRs are merged.**

---

## Self-Review

**Spec coverage:** All seven #10 acceptance items map to tasks — contract/write/read protocol are ratified by M2 + the doc; migration = M1.4 + backfill; contract test = M3; hand-edit + fallback are documented in the spec and exercised by M2/M3. ✓ Coverage gap (#10's live symptom) = M1 + M2. ✓

**Placeholder scan:** Two intentional, clearly-flagged lookups remain — the Python parser entry-point/attribute names (M3.2 Step 1a) and the `RecordingRunner` result/`ProcessResult` field names (M2.3 Step 1). Both are "confirm the real symbol then adjust" steps with the exact grep to run, not hidden TODOs, because those symbols live in files this plan should verify rather than guess. The `canonicalSHA256` and PR numbers are deferred-by-design (computed/known only at execution).

**Type consistency:** `withShortPrefix`, `shortPrefix(inFile:atLine:)`, `submit(_:displayedDate:recoveryLineNumber:)`, and the `(WriteOp, Int?)` closure type are used consistently across M2.1–M2.4. Swift `ActionTask` fields referenced (`shortPrefix`, `subject`, `plainSubject`, `body`, `lineNumber`) match `ActionTask.swift`. Corpus JSON keys (`short_prefix`/`subject`/`plain_subject`/`body`) are consistent between M3.1, M3.2, and the Swift `Entry.Expected` decoder in M3.4.
