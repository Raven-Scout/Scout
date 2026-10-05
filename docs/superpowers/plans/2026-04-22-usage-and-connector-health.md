# Usage Rail Card + Connector Health — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken `BudgetRailCard` with a token-based `UsageRailCard` fed by a Stop-hook tracker, and add `ConnectorHealthRailCard` + `ConnectorAlertBanner` on top of the already-wired shell telemetry pipeline.

**Architecture:** Shell side writes structured telemetry (`session-tokens.jsonl` via a new Stop hook; `connector-calls-*.jsonl` and `connector-alerts.log` already produced by existing hooks/scripts). Swift side adds two `@MainActor` services with the same file-watched pattern as `UsageTrackerService`, two rail cards, and one banner.

**Tech Stack:** Swift (SwiftUI + Swift Testing `@Suite`/`@Test`/`#expect`, `@MainActor` observable services, `FileSystemEventSource` protocol), Bash (`jq`-based aggregation), `~/Scout/.claude/settings.json` hooks.

**Companion spec:** `docs/superpowers/specs/2026-04-22-usage-and-connector-health-design.md`.

**Two tracks, independently commitable:** Tasks 1–9 implement the Usage Rail Card; Tasks 10–15 implement Connector Health. They share no code, so a subagent workflow can run them sequentially or parallel.

---

## File Structure

### Track A — Usage Rail Card (tokens)

| File | Role |
|---|---|
| **Create** `~/Scout/scripts/sum-session-tokens.sh` | Reads Stop-hook payload on stdin, sums `message.usage` across transcript turns, appends row to `.scout-logs/session-tokens.jsonl`. |
| **Modify** `~/Scout/.claude/settings.json` | Add a `Stop` entry to the existing `hooks` block. |
| **Create** `~/Scout/tests/test_sum_session_tokens.sh` | Shell test: invoke the script with a fixture transcript and a canned stdin; assert the emitted JSONL row. |
| **Create** `~/Scout/tests/fixtures/transcript-dreaming.jsonl` | Canned CC transcript with known token totals (two Opus turns + one Sonnet turn). |
| **Create** `scout-app/Scout/Models/SessionTokenEntry.swift` | Codable value type for one tracker row. |
| **Create** `scout-app/Scout/Models/TokenTotals.swift` | Aggregate value type (`sum`, per-model breakdown). |
| **Create** `scout-app/Scout/Services/SessionTokensService.swift` | `@MainActor` ObservableObject; mirrors `UsageTrackerService` shape. |
| **Create** `scout-app/Scout/ControlCenter/UsageRailCard.swift` | Replaces `BudgetRailCard`. |
| **Modify** `scout-app/Scout/ControlCenter/ControlCenterView.swift` | Swap `BudgetRailCard()` → `UsageRailCard()`; delete inline `BudgetRailCard` struct (currently ControlCenterView.swift:83-138). |
| **Modify** `scout-app/Scout/Shell/AppState.swift` | Add `sessionTokensService` property, wire `loadInitial()` into existing bootstrap `Task`. |
| **Create** `scout-app/ScoutTests/Fixtures/session-tokens.jsonl` | Canned multi-entry JSONL for parser tests. |
| **Create** `scout-app/ScoutTests/Services/SessionTokensServiceTests.swift` | Swift Testing tests for parser + totals. |
| **Create** `scout-app/ScoutTests/Models/TokenTotalsTests.swift` | Pure-function aggregation tests. |

### Track B — Connector Health

| File | Role |
|---|---|
| **Create** `scout-app/Scout/Models/ConnectorCall.swift` | Codable value for one `.scout-logs/connector-calls-*.jsonl` row. |
| **Create** `scout-app/Scout/Models/ConnectorAlert.swift` | Value type for one parsed `connector-alerts.log` entry. |
| **Create** `scout-app/Scout/Models/ConnectorHealthMatrix.swift` | Aggregation result: rows = connectors, columns = sessions. |
| **Create** `scout-app/Scout/Services/ConnectorAckStore.swift` | JSON-backed ack sidecar at `.scout-cache/connector-alerts-acked.json`. |
| **Create** `scout-app/Scout/Services/ConnectorHealthService.swift` | `@MainActor` ObservableObject: loads JSONLs + alerts log + ack store, publishes matrix + active alerts. |
| **Create** `scout-app/Scout/ControlCenter/ConnectorHealthRailCard.swift` | Rail card rendering the matrix. |
| **Create** `scout-app/Scout/ControlCenter/ConnectorAlertBanner.swift` | Top-of-window banner + ack popover. |
| **Modify** `scout-app/Scout/ControlCenter/ControlCenterView.swift` | Insert `ConnectorAlertBanner()` above `header`; insert `ConnectorHealthRailCard()` between `RepoStateRailCard()` and `SignalsRailCard()` in `rail`. |
| **Modify** `scout-app/Scout/Shell/AppState.swift` | Add `connectorHealthService` + `connectorAckStore` properties; wire into bootstrap. |
| **Create** `scout-app/ScoutTests/Fixtures/connector-calls-2026-04-22.jsonl` | Canned connector-calls fixture covering all four cell states across 3 sessions × 4 connectors. |
| **Create** `scout-app/ScoutTests/Fixtures/connector-alerts.log` | Canned alerts log with one active CRITICAL and one expired WARNING. |
| **Create** `scout-app/ScoutTests/Services/ConnectorHealthServiceTests.swift` | Parser + matrix aggregation tests. |
| **Create** `scout-app/ScoutTests/Services/ConnectorAckStoreTests.swift` | Ack/dismiss/GC lifecycle. |

---

## Task 1: Shell — Canned transcript fixture

**Files:**
- Create: `~/Scout/tests/fixtures/transcript-dreaming.jsonl`

This is the input both `sum-session-tokens.sh` and its test will consume. We build it first so the test and implementation can both reference it.

- [ ] **Step 1: Write the fixture**

Create the file with three turns — two Opus, one Sonnet. Totals: input 10+20+5=35, output 2+4+1=7, cache_read 1000+500+0=1500, cache_creation 8000+0+0=8000.

```jsonl
{"type":"assistant","message":{"model":"claude-opus-4-7","usage":{"input_tokens":10,"output_tokens":2,"cache_read_input_tokens":1000,"cache_creation_input_tokens":8000}}}
{"type":"assistant","message":{"model":"claude-opus-4-7","usage":{"input_tokens":20,"output_tokens":4,"cache_read_input_tokens":500,"cache_creation_input_tokens":0}}}
{"type":"assistant","message":{"model":"claude-sonnet-4-6","usage":{"input_tokens":5,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
{"type":"user","message":{"role":"user","content":"…"}}
{"type":"summary","summary":"done"}
```

The `user` and `summary` rows ensure the script correctly skips non-usage rows. File path: `/Users/jordanburger/Scout/tests/fixtures/transcript-dreaming.jsonl`.

- [ ] **Step 2: Verify with jq**

Run: `jq -s 'map(select(.message.usage)) | length' /Users/jordanburger/Scout/tests/fixtures/transcript-dreaming.jsonl`
Expected: `3`

- [ ] **Step 3: Commit**

```bash
cd ~/Scout && git add tests/fixtures/transcript-dreaming.jsonl
git commit -m "test: add canned CC transcript fixture for sum-session-tokens"
```

---

## Task 2: Shell — `sum-session-tokens.sh` (TDD-lite via shell harness)

**Files:**
- Create: `~/Scout/scripts/sum-session-tokens.sh`
- Create: `~/Scout/tests/test_sum_session_tokens.sh`

Shell tests use ad-hoc assertion scripts (same style as nothing currently exists — there are no shell tests today; we're establishing one). This keeps a `$?`-check harness simple.

- [ ] **Step 1: Write the failing test**

Create `~/Scout/tests/test_sum_session_tokens.sh`:

```bash
#!/usr/bin/env bash
# Test for sum-session-tokens.sh
set -eu

SCOUT_DIR="${SCOUT_DIR:-$HOME/Scout}"
SCRIPT="$SCOUT_DIR/scripts/sum-session-tokens.sh"
FIXTURE="$SCOUT_DIR/tests/fixtures/transcript-dreaming.jsonl"

TMP_TRACKER=$(mktemp -t session-tokens-XXXXX.jsonl)
trap 'rm -f "$TMP_TRACKER"' EXIT

# Fake the stdin payload Claude Code sends to a Stop hook
STDIN_PAYLOAD=$(cat <<EOF
{"session_id":"abc-123","transcript_path":"$FIXTURE","cwd":"$SCOUT_DIR"}
EOF
)

SCOUT_MODE=dreaming \
SESSION_TOKENS_TRACKER="$TMP_TRACKER" \
echo "$STDIN_PAYLOAD" | "$SCRIPT"

# Assertions: exactly one row
rows=$(wc -l < "$TMP_TRACKER" | tr -d ' ')
if [ "$rows" != "1" ]; then
  echo "FAIL: expected 1 row, got $rows"
  cat "$TMP_TRACKER"
  exit 1
fi

# Field-by-field checks
line=$(cat "$TMP_TRACKER")
for assertion in \
    ".session_id == \"abc-123\"" \
    ".scout_mode == \"dreaming\"" \
    ".primary_model == \"claude-opus-4-7\"" \
    ".input_tokens == 35" \
    ".output_tokens == 7" \
    ".cache_read_input_tokens == 1500" \
    ".cache_creation_input_tokens == 8000" \
    ".num_turns == 3" \
    ".error == null"; do
  if [ "$(echo "$line" | jq -r "$assertion")" != "true" ]; then
    echo "FAIL: $assertion"; echo "row: $line"; exit 1
  fi
done

# Cost must be > 0 and must reflect per-turn mixed-model pricing.
# Opus: (10+20)*15 + (2+4)*75 + 1500*1.5 + 8000*18.75 ...scaled by 1e-6
# Sonnet: 5*3 + 1*15 + 0 + 0 ...scaled by 1e-6
# Expected opus cost ≈ (450 + 450 + 2250 + 150000) / 1e6 = 0.15315
# Expected sonnet cost ≈ (15 + 15) / 1e6 = 0.00003
# Expected total ≈ 0.15318
expected_low=0.153
expected_high=0.154
cost=$(echo "$line" | jq -r '.cost_usd')
awk -v c="$cost" -v lo="$expected_low" -v hi="$expected_high" \
  'BEGIN { if (c >= lo && c <= hi) exit 0; else { print "FAIL: cost " c " not in [" lo ", " hi "]"; exit 1 } }'

echo "PASS"
```

Make it executable: `chmod +x ~/Scout/tests/test_sum_session_tokens.sh`.

- [ ] **Step 2: Run the test — expect failure**

Run: `~/Scout/tests/test_sum_session_tokens.sh`
Expected: FAIL because `sum-session-tokens.sh` doesn't exist yet (shell: `No such file or directory`).

- [ ] **Step 3: Write the script**

Create `~/Scout/scripts/sum-session-tokens.sh`:

```bash
#!/usr/bin/env bash
# sum-session-tokens.sh — Stop-hook-invoked. Reads a Stop-hook JSON payload
# on stdin, locates the transcript file, sums message.usage.* across turns,
# and appends one row to $SESSION_TOKENS_TRACKER (default: ~/Scout/.scout-logs/
# session-tokens.jsonl).
#
# Phase 1 of the usage-and-connector-health design. Pricing derived from
# per-turn token counts against a static table (constants below); Phase 2
# will replace dollar display with quota utilization and make this table
# irrelevant.
#
# Environment:
#   SCOUT_MODE                 — set by runner scripts (dreaming, briefing, ...).
#                                Falls back to "manual" if unset.
#   SESSION_TOKENS_TRACKER     — override tracker path (for tests).

set -euo pipefail

# PRICING: $ per 1M tokens. Verify against https://www.anthropic.com/pricing
# before shipping; these are the published rates as of 2026-04-22.
OPUS_INPUT=15.00;  OPUS_OUTPUT=75.00;  OPUS_CACHE_READ=1.50;  OPUS_CACHE_CREATE=18.75
SONNET_INPUT=3.00; SONNET_OUTPUT=15.00; SONNET_CACHE_READ=0.30; SONNET_CACHE_CREATE=3.75
HAIKU_INPUT=0.80;  HAIKU_OUTPUT=4.00;   HAIKU_CACHE_READ=0.08; HAIKU_CACHE_CREATE=1.00

SCOUT_DIR="${SCOUT_DIR:-$HOME/Scout}"
TRACKER="${SESSION_TOKENS_TRACKER:-$SCOUT_DIR/.scout-logs/session-tokens.jsonl}"
mkdir -p "$(dirname "$TRACKER")"

# Read Stop-hook payload from stdin.
PAYLOAD=$(cat)
SESSION_ID=$(echo "$PAYLOAD" | jq -r '.session_id // ""')
TRANSCRIPT_PATH=$(echo "$PAYLOAD" | jq -r '.transcript_path // ""')
CWD=$(echo "$PAYLOAD" | jq -r '.cwd // ""')
SCOUT_MODE="${SCOUT_MODE:-manual}"

TIMESTAMP_UTC=$(TZ=UTC date '+%Y-%m-%dT%H:%M:%SZ')
TIMESTAMP_ET=$(TZ=America/New_York date '+%Y-%m-%d %H:%M %Z')

# Guard: transcript missing or unreadable → emit zero-row with error flag.
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  jq -nc --arg ts "$TIMESTAMP_UTC" --arg ts_et "$TIMESTAMP_ET" \
         --arg sid "$SESSION_ID" --arg mode "$SCOUT_MODE" \
         --arg cwd "$CWD" \
    '{ts:$ts, ts_et:$ts_et, session_id:$sid, scout_mode:$mode, cwd:$cwd,
      primary_model:null, input_tokens:0, output_tokens:0,
      cache_read_input_tokens:0, cache_creation_input_tokens:0,
      cost_usd:0, num_turns:0, duration_ms:0, error:"transcript_not_found"}' \
    >> "$TRACKER"
  exit 0
fi

# Select assistant turns that carry message.usage.
USAGE_TURNS=$(jq -c 'select(.message.usage != null)' "$TRANSCRIPT_PATH")
if [ -z "$USAGE_TURNS" ]; then
  jq -nc --arg ts "$TIMESTAMP_UTC" --arg ts_et "$TIMESTAMP_ET" \
         --arg sid "$SESSION_ID" --arg mode "$SCOUT_MODE" \
         --arg cwd "$CWD" \
    '{ts:$ts, ts_et:$ts_et, session_id:$sid, scout_mode:$mode, cwd:$cwd,
      primary_model:null, input_tokens:0, output_tokens:0,
      cache_read_input_tokens:0, cache_creation_input_tokens:0,
      cost_usd:0, num_turns:0, duration_ms:0, error:"no_usage_turns"}' \
    >> "$TRACKER"
  exit 0
fi

# Sum token fields.
INPUT=$(echo "$USAGE_TURNS"  | jq -s 'map(.message.usage.input_tokens // 0) | add')
OUTPUT=$(echo "$USAGE_TURNS" | jq -s 'map(.message.usage.output_tokens // 0) | add')
CACHE_R=$(echo "$USAGE_TURNS"| jq -s 'map(.message.usage.cache_read_input_tokens // 0) | add')
CACHE_C=$(echo "$USAGE_TURNS"| jq -s 'map(.message.usage.cache_creation_input_tokens // 0) | add')
TURNS=$(echo "$USAGE_TURNS"  | jq -s 'length')

# Primary model = most-frequent model across usage turns.
PRIMARY=$(echo "$USAGE_TURNS" | jq -s -r \
  'group_by(.message.model) | max_by(length) | .[0].message.model // ""')

# Compute per-turn cost (model-aware), accumulate via awk.
COST=$(echo "$USAGE_TURNS" | jq -s \
  --argjson oi  "$OPUS_INPUT"   --argjson oo "$OPUS_OUTPUT" \
  --argjson ocr "$OPUS_CACHE_READ" --argjson occ "$OPUS_CACHE_CREATE" \
  --argjson si  "$SONNET_INPUT" --argjson so "$SONNET_OUTPUT" \
  --argjson scr "$SONNET_CACHE_READ" --argjson scc "$SONNET_CACHE_CREATE" \
  --argjson hi  "$HAIKU_INPUT"  --argjson ho "$HAIKU_OUTPUT" \
  --argjson hcr "$HAIKU_CACHE_READ" --argjson hcc "$HAIKU_CACHE_CREATE" \
  '[ .[] | . as $t
      | ($t.message.model // "") as $m
      | (if   ($m | startswith("claude-opus"))   then [$oi,$oo,$ocr,$occ]
         elif ($m | startswith("claude-sonnet")) then [$si,$so,$scr,$scc]
         elif ($m | startswith("claude-haiku"))  then [$hi,$ho,$hcr,$hcc]
         else [$oi,$oo,$ocr,$occ] end) as $r
      | (($t.message.usage.input_tokens           // 0) * $r[0]
      +  ($t.message.usage.output_tokens          // 0) * $r[1]
      +  ($t.message.usage.cache_read_input_tokens// 0) * $r[2]
      +  ($t.message.usage.cache_creation_input_tokens // 0) * $r[3]) / 1000000
    ] | add')

jq -nc --arg ts "$TIMESTAMP_UTC" --arg ts_et "$TIMESTAMP_ET" \
       --arg sid "$SESSION_ID" --arg mode "$SCOUT_MODE" --arg cwd "$CWD" \
       --arg primary "$PRIMARY" \
       --argjson input "$INPUT" --argjson output "$OUTPUT" \
       --argjson cache_r "$CACHE_R" --argjson cache_c "$CACHE_C" \
       --argjson cost "$COST" --argjson turns "$TURNS" \
  '{ts:$ts, ts_et:$ts_et, session_id:$sid, scout_mode:$mode, cwd:$cwd,
    primary_model:$primary, input_tokens:$input, output_tokens:$output,
    cache_read_input_tokens:$cache_r, cache_creation_input_tokens:$cache_c,
    cost_usd:$cost, num_turns:$turns, duration_ms:0, error:null}' \
  >> "$TRACKER"
```

Make executable: `chmod +x ~/Scout/scripts/sum-session-tokens.sh`.

- [ ] **Step 4: Run the test — expect pass**

Run: `~/Scout/tests/test_sum_session_tokens.sh`
Expected: `PASS`

If any field assertion fails, check jq version (`jq --version` — macOS default is 1.6; `startswith` and `group_by` work on 1.5+).

- [ ] **Step 5: Commit**

```bash
cd ~/Scout && git add scripts/sum-session-tokens.sh tests/test_sum_session_tokens.sh
git commit -m "feat: add sum-session-tokens.sh + test harness

Stop-hook-invoked script that reads transcript_path from stdin,
sums usage tokens across turns, and writes a row to
.scout-logs/session-tokens.jsonl. Mixed-model pricing computed
per-turn against a static table (Phase 2 will replace dollar
display with quota % and make the table irrelevant)."
```

---

## Task 3: Shell — Register Stop hook + manual end-to-end verification

**Files:**
- Modify: `~/Scout/.claude/settings.json`

- [ ] **Step 1: Add the Stop hook**

Open `~/Scout/.claude/settings.json`. The `hooks` block currently contains only `PostToolUse`. Add a sibling `Stop` entry. Final `hooks` block:

```json
"hooks": {
  "PostToolUse": [
    {
      "matcher": ".*",
      "hooks": [
        {
          "type": "command",
          "command": "$HOME/Scout/hooks/connector-log.sh",
          "timeout": 5
        }
      ]
    }
  ],
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "$HOME/Scout/scripts/sum-session-tokens.sh",
          "timeout": 10
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: End-to-end verification in a real session**

Invoke a short `claude -p` inside Scout's directory; the project-local settings auto-resolve.

Run:
```bash
cd ~/Scout && claude -p "say ok and stop" >/dev/null 2>&1
wc -l ~/Scout/.scout-logs/session-tokens.jsonl
tail -1 ~/Scout/.scout-logs/session-tokens.jsonl | jq .
```

Expected: line count incremented by 1; the last row has non-zero `input_tokens`, non-null `primary_model`, `error` is `null`, and `scout_mode` is whatever `$SCOUT_MODE` was (likely empty → `"manual"` default). This is the smoke test that proves the hook is wired.

- [ ] **Step 3: Commit**

```bash
cd ~/Scout && git add .claude/settings.json
git commit -m "feat: register Stop hook for session token tracking

Appends to .scout-logs/session-tokens.jsonl on every session exit.
Coexists with the existing PostToolUse connector-log hook."
```

---

## Task 4: Swift — `SessionTokenEntry` model

**Files:**
- Create: `scout-app/Scout/Models/SessionTokenEntry.swift`
- Create: `scout-app/ScoutTests/Fixtures/session-tokens.jsonl`
- Create: `scout-app/ScoutTests/Models/SessionTokenEntryTests.swift`

- [ ] **Step 1: Write the fixture**

Create `scout-app/ScoutTests/Fixtures/session-tokens.jsonl`:

```jsonl
{"ts":"2026-04-22T22:10:33Z","ts_et":"2026-04-22 18:10 EDT","session_id":"abc","scout_mode":"dreaming","cwd":"/Users/jordanburger/Scout","primary_model":"claude-opus-4-7","input_tokens":1200,"output_tokens":340,"cache_read_input_tokens":89000,"cache_creation_input_tokens":9200,"cost_usd":0.412,"num_turns":37,"duration_ms":512000,"error":null}
{"ts":"2026-04-22T23:05:11Z","ts_et":"2026-04-22 19:05 EDT","session_id":"def","scout_mode":"briefing","cwd":"/Users/jordanburger/Scout","primary_model":"claude-sonnet-4-6","input_tokens":300,"output_tokens":80,"cache_read_input_tokens":12000,"cache_creation_input_tokens":0,"cost_usd":0.014,"num_turns":8,"duration_ms":74000,"error":null}
not json
{"ts":"2026-04-22T23:30:00Z","ts_et":"2026-04-22 19:30 EDT","session_id":"ghi","scout_mode":"manual","cwd":"/Users/jordanburger/Scout","primary_model":null,"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0,"num_turns":0,"duration_ms":0,"error":"transcript_not_found"}
```

The corrupt row `not json` is intentional — exercises the silent-skip path.

Ensure the fixture is a test-bundle resource. In Xcode: drag it into `ScoutTests/Fixtures` group, confirm the Target Membership checkbox for `ScoutTests` is set and it appears under *Copy Bundle Resources*. If unsure, open `Scout.xcodeproj` and check.

- [ ] **Step 2: Write the failing test**

Create `scout-app/ScoutTests/Models/SessionTokenEntryTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("SessionTokenEntry")
struct SessionTokenEntryTests {
    @Test func decodesSuccessRow() throws {
        let json = #"""
        {"ts":"2026-04-22T22:10:33Z","ts_et":"2026-04-22 18:10 EDT","session_id":"abc","scout_mode":"dreaming","cwd":"/x","primary_model":"claude-opus-4-7","input_tokens":1200,"output_tokens":340,"cache_read_input_tokens":89000,"cache_creation_input_tokens":9200,"cost_usd":0.412,"num_turns":37,"duration_ms":512000,"error":null}
        """#
        let decoder = SessionTokenEntry.makeDecoder()
        let entry = try decoder.decode(SessionTokenEntry.self, from: Data(json.utf8))
        #expect(entry.sessionId == "abc")
        #expect(entry.scoutMode == "dreaming")
        #expect(entry.primaryModel == "claude-opus-4-7")
        #expect(entry.inputTokens == 1200)
        #expect(entry.costUSD == Decimal(string: "0.412"))
        #expect(entry.error == nil)
    }

    @Test func decodesErrorRow() throws {
        let json = #"""
        {"ts":"2026-04-22T23:30:00Z","ts_et":"2026-04-22 19:30 EDT","session_id":"ghi","scout_mode":"manual","cwd":"/x","primary_model":null,"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0,"num_turns":0,"duration_ms":0,"error":"transcript_not_found"}
        """#
        let decoder = SessionTokenEntry.makeDecoder()
        let entry = try decoder.decode(SessionTokenEntry.self, from: Data(json.utf8))
        #expect(entry.primaryModel == nil)
        #expect(entry.error == "transcript_not_found")
    }
}
```

- [ ] **Step 3: Run — expect fail**

Run in Xcode: ⌘U restricted to the suite, or from CLI:
```bash
cd scout-app && xcodebuild -project Scout.xcodeproj -scheme Scout -only-testing:ScoutTests/SessionTokenEntryTests test 2>&1 | tail -20
```
Expected: FAIL (`cannot find 'SessionTokenEntry' in scope`).

- [ ] **Step 4: Write the model**

Create `scout-app/Scout/Models/SessionTokenEntry.swift`:

```swift
import Foundation

/// One row of `.scout-logs/session-tokens.jsonl`, written by
/// `~/Scout/scripts/sum-session-tokens.sh` as a Stop hook.
struct SessionTokenEntry: Codable, Equatable, Hashable, Sendable {
    let ts: Date
    let tsEt: String
    let sessionId: String
    let scoutMode: String
    let cwd: String
    let primaryModel: String?
    let inputTokens: Int
    let outputTokens: Int
    let cacheReadInputTokens: Int
    let cacheCreationInputTokens: Int
    let costUSD: Decimal
    let numTurns: Int
    let durationMs: Int
    let error: String?

    private enum CodingKeys: String, CodingKey {
        case ts, tsEt = "ts_et", sessionId = "session_id", scoutMode = "scout_mode"
        case cwd, primaryModel = "primary_model"
        case inputTokens = "input_tokens", outputTokens = "output_tokens"
        case cacheReadInputTokens = "cache_read_input_tokens"
        case cacheCreationInputTokens = "cache_creation_input_tokens"
        case costUSD = "cost_usd", numTurns = "num_turns"
        case durationMs = "duration_ms", error
    }

    /// Decoder configured to accept the ISO8601 `ts` string emitted by the
    /// shell script (with or without fractional seconds — mirrors
    /// `UsageTrackerService.parseFile`).
    static func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime]
            if let d = f.date(from: s) { return d }
            f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = f.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(
                in: c,
                debugDescription: "unparseable ts: \(s)"
            )
        }
        return d
    }
}
```

- [ ] **Step 5: Run — expect pass**

Run: same `xcodebuild` command as Step 3.
Expected: PASS for both tests.

- [ ] **Step 6: Commit**

```bash
cd scout-app && git add Scout/Models/SessionTokenEntry.swift \
  ScoutTests/Fixtures/session-tokens.jsonl \
  ScoutTests/Models/SessionTokenEntryTests.swift
git commit -m "feat: add SessionTokenEntry model + fixture"
```

---

## Task 5: Swift — `TokenTotals` aggregate

**Files:**
- Create: `scout-app/Scout/Models/TokenTotals.swift`
- Create: `scout-app/ScoutTests/Models/TokenTotalsTests.swift`

Pure value type used by `SessionTokensService` and `UsageRailCard`. No side effects — easiest to unit-test early.

- [ ] **Step 1: Write the failing test**

Create `scout-app/ScoutTests/Models/TokenTotalsTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("TokenTotals")
struct TokenTotalsTests {
    @Test func sumsTokensAndCost() {
        let entries = [
            makeEntry(model: "claude-opus-4-7", input: 100, output: 20, cacheR: 1000, cacheC: 500, cost: "0.20"),
            makeEntry(model: "claude-sonnet-4-6", input: 50, output: 10, cacheR: 200, cacheC: 0, cost: "0.01")
        ]
        let totals = TokenTotals(entries: entries)

        #expect(totals.inputTokens == 150)
        #expect(totals.outputTokens == 30)
        #expect(totals.cacheReadTokens == 1200)
        #expect(totals.cacheCreationTokens == 500)
        #expect(totals.allTokens == 1880)
        #expect(totals.costUSD == Decimal(string: "0.21"))
    }

    @Test func perModelBreakdownAsFractions() {
        let entries = [
            makeEntry(model: "claude-opus-4-7", input: 90, output: 0, cacheR: 0, cacheC: 0, cost: "0"),
            makeEntry(model: "claude-opus-4-7", input: 90, output: 0, cacheR: 0, cacheC: 0, cost: "0"),
            makeEntry(model: "claude-sonnet-4-6", input: 20, output: 0, cacheR: 0, cacheC: 0, cost: "0")
        ]
        let totals = TokenTotals(entries: entries)
        // 90% opus, 10% sonnet (by all-tokens input-only in this fixture)
        #expect(totals.modelShare(startingWith: "claude-opus") == 0.9)
        #expect(totals.modelShare(startingWith: "claude-sonnet") == 0.1)
        #expect(totals.modelShare(startingWith: "claude-haiku") == 0.0)
    }

    @Test func emptyEntriesProducesZeros() {
        let totals = TokenTotals(entries: [])
        #expect(totals.inputTokens == 0)
        #expect(totals.allTokens == 0)
        #expect(totals.costUSD == 0)
        #expect(totals.modelShare(startingWith: "claude-opus") == 0.0)
    }

    @Test func nilPrimaryModelDoesNotCrashBreakdown() {
        let entries = [
            makeEntry(model: nil, input: 100, output: 0, cacheR: 0, cacheC: 0, cost: "0")
        ]
        let totals = TokenTotals(entries: entries)
        #expect(totals.modelShare(startingWith: "claude-opus") == 0.0)
    }

    // MARK: - helpers

    private func makeEntry(
        model: String?, input: Int, output: Int,
        cacheR: Int, cacheC: Int, cost: String
    ) -> SessionTokenEntry {
        SessionTokenEntry(
            ts: Date(), tsEt: "", sessionId: UUID().uuidString, scoutMode: "manual",
            cwd: "/x", primaryModel: model,
            inputTokens: input, outputTokens: output,
            cacheReadInputTokens: cacheR, cacheCreationInputTokens: cacheC,
            costUSD: Decimal(string: cost)!, numTurns: 1, durationMs: 0, error: nil
        )
    }
}
```

- [ ] **Step 2: Run — expect fail**

Run: same `xcodebuild` pattern.
Expected: FAIL (`cannot find 'TokenTotals' in scope`).

- [ ] **Step 3: Write the model**

Create `scout-app/Scout/Models/TokenTotals.swift`:

```swift
import Foundation

/// Aggregated token/cost totals over a set of `SessionTokenEntry` rows.
///
/// Used by `UsageRailCard` for the today/week rollups and the
/// "opus X% · sonnet Y%" line. All fields are computed at init time so
/// the view can `@MainActor`-safely read them without worrying about
/// sums drifting on re-render.
struct TokenTotals: Equatable, Sendable {
    let inputTokens: Int
    let outputTokens: Int
    let cacheReadTokens: Int
    let cacheCreationTokens: Int
    let costUSD: Decimal

    /// Per-primary-model totals, keyed by whatever `primary_model` string
    /// the shell emitted. Used by `modelShare(startingWith:)`.
    private let tokensByModel: [String: Int]
    private let totalAttributedTokens: Int

    init(entries: [SessionTokenEntry]) {
        var input = 0, output = 0, cacheR = 0, cacheC = 0
        var cost = Decimal(0)
        var byModel: [String: Int] = [:]
        for e in entries {
            input  += e.inputTokens
            output += e.outputTokens
            cacheR += e.cacheReadInputTokens
            cacheC += e.cacheCreationInputTokens
            cost   += e.costUSD
            if let m = e.primaryModel {
                let entryAllTokens = e.inputTokens + e.outputTokens
                    + e.cacheReadInputTokens + e.cacheCreationInputTokens
                byModel[m, default: 0] += entryAllTokens
            }
        }
        self.inputTokens = input
        self.outputTokens = output
        self.cacheReadTokens = cacheR
        self.cacheCreationTokens = cacheC
        self.costUSD = cost
        self.tokensByModel = byModel
        self.totalAttributedTokens = byModel.values.reduce(0, +)
    }

    var allTokens: Int {
        inputTokens + outputTokens + cacheReadTokens + cacheCreationTokens
    }

    /// Fraction (0.0–1.0) of attributed tokens whose `primary_model` begins
    /// with the given prefix (e.g. `"claude-opus"` or `"claude-sonnet"`).
    /// Rows with `primary_model == nil` are excluded from both numerator
    /// and denominator.
    func modelShare(startingWith prefix: String) -> Double {
        guard totalAttributedTokens > 0 else { return 0 }
        let matched = tokensByModel
            .filter { $0.key.hasPrefix(prefix) }
            .values
            .reduce(0, +)
        return Double(matched) / Double(totalAttributedTokens)
    }
}
```

- [ ] **Step 4: Run — expect pass**

Run: the xcodebuild test command.
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
cd scout-app && git add Scout/Models/TokenTotals.swift ScoutTests/Models/TokenTotalsTests.swift
git commit -m "feat: TokenTotals aggregate with per-model share"
```

---

## Task 6: Swift — `SessionTokensService`

**Files:**
- Create: `scout-app/Scout/Services/SessionTokensService.swift`
- Create: `scout-app/ScoutTests/Services/SessionTokensServiceTests.swift`

Mirrors `UsageTrackerService` shape — file-watched `@MainActor` ObservableObject.

- [ ] **Step 1: Write the failing test**

Create `scout-app/ScoutTests/Services/SessionTokensServiceTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("SessionTokensService")
struct SessionTokensServiceTests {
    @Test func loadInitialParsesFixtureAndSkipsCorrupt() async throws {
        let fixture = Bundle(for: FixtureAnchor.self)
            .resourceURL!
            .appendingPathComponent("session-tokens.jsonl")
        let svc = await SessionTokensService(trackerURL: fixture, fileEvents: NoopFS())
        let entries = try await svc.loadInitial()
        #expect(entries.count == 3, "corrupt row 'not json' must be skipped silently")
        let ids = entries.map(\.sessionId)
        #expect(ids.contains("abc"))
        #expect(ids.contains("def"))
        #expect(ids.contains("ghi"))
    }

    @Test func totalsForDateIntervalFilters() async throws {
        let body = """
        {"ts":"2026-04-22T12:00:00Z","ts_et":"","session_id":"a","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":100,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.1,"num_turns":1,"duration_ms":0,"error":null}
        {"ts":"2026-04-23T12:00:00Z","ts_et":"","session_id":"b","scout_mode":"x","cwd":"/","primary_model":"claude-opus-4-7","input_tokens":200,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0.2,"num_turns":1,"duration_ms":0,"error":null}
        """
        let tmp = try writeTemp(body)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let svc = await SessionTokensService(trackerURL: tmp, fileEvents: NoopFS())
        _ = try await svc.loadInitial()

        let start = ISO8601DateFormatter().date(from: "2026-04-23T00:00:00Z")!
        let end = ISO8601DateFormatter().date(from: "2026-04-24T00:00:00Z")!
        let totals = await svc.totals(in: start..<end)
        #expect(totals.inputTokens == 200)
    }

    @Test func handlesMissingFileAsEmpty() async throws {
        let missing = URL(fileURLWithPath: "/tmp/does-not-exist-\(UUID().uuidString).jsonl")
        let svc = await SessionTokensService(trackerURL: missing, fileEvents: NoopFS())
        let entries = try await svc.loadInitial()
        #expect(entries.isEmpty)
    }

    // MARK: - helpers

    private func writeTemp(_ s: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try s.write(to: url, atomically: true, encoding: .utf8)
        return url
    }
}
```

- [ ] **Step 2: Run — expect fail**

Run: `xcodebuild ... -only-testing:ScoutTests/SessionTokensServiceTests test`
Expected: FAIL — `SessionTokensService` not in scope.

- [ ] **Step 3: Write the service**

Create `scout-app/Scout/Services/SessionTokensService.swift`:

```swift
import Foundation
import Combine
import SwiftUI

/// Reads `.scout-logs/session-tokens.jsonl` (produced by the Stop hook via
/// `~/Scout/scripts/sum-session-tokens.sh`) and exposes it as a published
/// list of entries plus range-filtered `TokenTotals`.
///
/// Mirrors `UsageTrackerService` in lifecycle and parser tolerance —
/// corrupt lines are silently skipped.
@MainActor
final class SessionTokensService: ObservableObject {
    @Published private(set) var entries: [SessionTokenEntry] = []

    private let trackerURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    init(trackerURL: URL, fileEvents: any FileSystemEventSource) {
        self.trackerURL = trackerURL
        self.fileEvents = fileEvents
    }

    func loadInitial() async throws -> [SessionTokenEntry] {
        let parsed = Self.parseFile(trackerURL)
        entries = parsed
        startWatching()
        return parsed
    }

    /// Returns aggregated totals for entries whose `ts` falls within the
    /// half-open interval `[range.lowerBound, range.upperBound)`.
    func totals(in range: Range<Date>) -> TokenTotals {
        TokenTotals(entries: entries.filter { range.contains($0.ts) })
    }

    private func startWatching() {
        watchTask?.cancel()
        let url = trackerURL
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await _ in self.fileEvents.events(for: url) {
                let refreshed = Self.parseFile(url)
                self.entries = refreshed
            }
        }
    }

    nonisolated private static func parseFile(_ url: URL) -> [SessionTokenEntry] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = SessionTokenEntry.makeDecoder()
        var out: [SessionTokenEntry] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let d = line.data(using: .utf8) else { continue }
            if let entry = try? decoder.decode(SessionTokenEntry.self, from: d) {
                out.append(entry)
            }
            // Corrupt lines silently skipped — matches UsageTrackerService.
        }
        return out
    }
}
```

- [ ] **Step 4: Run — expect pass**

Run: `xcodebuild ... -only-testing:ScoutTests/SessionTokensServiceTests test`
Expected: PASS (three tests).

- [ ] **Step 5: Commit**

```bash
cd scout-app && git add Scout/Services/SessionTokensService.swift \
  ScoutTests/Services/SessionTokensServiceTests.swift
git commit -m "feat: SessionTokensService — file-watched tokens tracker"
```

---

## Task 7: Swift — Wire `SessionTokensService` into `AppState`

**Files:**
- Modify: `scout-app/Scout/Shell/AppState.swift:11-106`

- [ ] **Step 1: Add the property + wiring**

Open `scout-app/Scout/Shell/AppState.swift`. After the existing `let trackerService: UsageTrackerService` declaration at line 13, add:

```swift
let sessionTokensService: SessionTokensService
```

In `init()` after the existing `tracker` creation (~line 39), add:

```swift
let tokens = SessionTokensService(
    trackerURL: scoutDir.appendingPathComponent(".scout-logs/session-tokens.jsonl"),
    fileEvents: watcher
)
```

After `self.trackerService = tracker` (~line 75), add:

```swift
self.sessionTokensService = tokens
```

In the bootstrap `Task { [weak self] in ... }` block (~line 87), after `_ = try? await tracker.loadInitial()`, add:

```swift
_ = try? await tokens.loadInitial()
```

- [ ] **Step 2: Build to verify**

Run: `xcodebuild -project scout-app/Scout.xcodeproj -scheme Scout build 2>&1 | tail -20`
Expected: `BUILD SUCCEEDED`.

No unit test for this change — it's wiring; the service's own tests cover its behavior, and the rail card will render the result end-to-end once Task 9 lands.

- [ ] **Step 3: Commit**

```bash
cd scout-app && git add Scout/Shell/AppState.swift
git commit -m "chore: wire SessionTokensService into AppState bootstrap"
```

---

## Task 8: Swift — `UsageRailCard` view

**Files:**
- Create: `scout-app/Scout/ControlCenter/UsageRailCard.swift`

No unit test — it's a SwiftUI view with no extractable pure function that isn't already covered by `TokenTotals` tests. We verify visually after Task 9 wires it in.

- [ ] **Step 1: Write the view**

Create `scout-app/Scout/ControlCenter/UsageRailCard.swift`:

```swift
import SwiftUI

/// Rail card replacing the removed `BudgetRailCard`. Renders today's and
/// this-week's token totals plus a per-model share line. Dollar cost is
/// intentionally hidden — it's a misleading metric on a Claude team-plan
/// seat (quota-based; dollars only apply to overage). See the
/// 2026-04-22 design spec under docs/superpowers/specs/.
struct UsageRailCard: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            RailCardHeader(title: "Today's usage")
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(formatTokens(todayTotals.allTokens))
                    .font(DS.serif(22, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text("tokens")
                    .font(DS.mono(13))
                    .foregroundStyle(DS.Ink.p4)
            }
            Text(splitLine(todayTotals))
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p3)
                .padding(.top, 6)
                .fixedSize(horizontal: false, vertical: true)

            Divider().padding(.vertical, 10)

            Text("Week: \(formatTokens(weekTotals.allTokens)) tokens")
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p3)
            Text(modelShareLine(weekTotals))
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p4)
                .padding(.top, 2)

            Text("Quota: TBD (Phase 2)")
                .font(DS.mono(10.5))
                .foregroundStyle(DS.Ink.p4.opacity(0.7))
                .padding(.top, 10)
        }
        .editorialCard(padding: 16)
    }

    // MARK: - Totals

    private var todayTotals: TokenTotals {
        let (start, end) = etTodayRange()
        return state.sessionTokensService.totals(in: start..<end)
    }

    private var weekTotals: TokenTotals {
        let (start, end) = etCurrentWeekRange()
        return state.sessionTokensService.totals(in: start..<end)
    }

    // MARK: - Formatting

    private func formatTokens(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000     { return String(format: "%.0fK", Double(n) / 1_000) }
        return "\(n)"
    }

    private func splitLine(_ t: TokenTotals) -> String {
        "in \(formatTokens(t.inputTokens)) · out \(formatTokens(t.outputTokens)) · cache-r \(formatTokens(t.cacheReadTokens)) · cache-c \(formatTokens(t.cacheCreationTokens))"
    }

    private func modelShareLine(_ t: TokenTotals) -> String {
        let opusPct = Int((t.modelShare(startingWith: "claude-opus") * 100).rounded())
        let sonnetPct = Int((t.modelShare(startingWith: "claude-sonnet") * 100).rounded())
        return "opus \(opusPct)% · sonnet \(sonnetPct)%"
    }

    // MARK: - ET date ranges

    private func etTodayRange() -> (Date, Date) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let start = cal.startOfDay(for: Date())
        let end = cal.date(byAdding: .day, value: 1, to: start)!
        return (start, end)
    }

    private func etCurrentWeekRange() -> (Date, Date) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        cal.firstWeekday = 2 // Monday
        let now = Date()
        let comps = cal.dateComponents([.yearForWeekOfYear, .weekOfYear], from: now)
        let start = cal.date(from: comps)!
        let end = cal.date(byAdding: .day, value: 7, to: start)!
        return (start, end)
    }
}
```

`RailCardHeader`, `DS.serif`, `DS.mono`, `DS.Ink`, `editorialCard(padding:)` are all existing identifiers in the app. `AppState` is the existing environment object.

- [ ] **Step 2: Build to verify compilation**

Run: `xcodebuild -project scout-app/Scout.xcodeproj -scheme Scout build 2>&1 | tail -20`
Expected: `BUILD SUCCEEDED`.

- [ ] **Step 3: Commit**

```bash
cd scout-app && git add Scout/ControlCenter/UsageRailCard.swift
git commit -m "feat: UsageRailCard — tokens-today, week total, model share"
```

---

## Task 9: Swift — Swap `BudgetRailCard` → `UsageRailCard` in Control Center

**Files:**
- Modify: `scout-app/Scout/ControlCenter/ControlCenterView.swift:59-66` (rail VStack) and `:83-138` (delete `BudgetRailCard` struct).

- [ ] **Step 1: Modify the rail VStack**

At `scout-app/Scout/ControlCenter/ControlCenterView.swift:59-66`, change:

```swift
private var rail: some View {
    VStack(alignment: .leading, spacing: 20) {
        BudgetRailCard()
        RepoStateRailCard()
        SignalsRailCard()
        KeyboardRailCard()
    }
}
```

to:

```swift
private var rail: some View {
    VStack(alignment: .leading, spacing: 20) {
        UsageRailCard()
        RepoStateRailCard()
        SignalsRailCard()
        KeyboardRailCard()
    }
}
```

- [ ] **Step 2: Delete the `BudgetRailCard` struct**

Still in `ControlCenterView.swift`, delete the entire `struct BudgetRailCard: View { ... }` block at lines 83-138. Leave surrounding structs (`RailCardHeader`, `RepoStateRailCard` etc.) intact.

- [ ] **Step 3: Build and run**

Run: `xcodebuild -project scout-app/Scout.xcodeproj -scheme Scout build 2>&1 | tail -20`
Expected: `BUILD SUCCEEDED`.

Then launch the app from Xcode (⌘R). Manually verify:
- The rail's top card is now labeled "TODAY'S USAGE" (not "TODAY'S BUDGET").
- If `.scout-logs/session-tokens.jsonl` has rows, today's numbers render.
- If the file is empty or missing, the card shows `0 tokens` with no crash.
- "Quota: TBD (Phase 2)" appears faintly at the bottom.

If `session-tokens.jsonl` is empty (the very first time), run one `claude -p "hi"` inside `~/Scout` to get at least one entry and re-launch.

- [ ] **Step 4: Commit**

```bash
cd scout-app && git add Scout/ControlCenter/ControlCenterView.swift
git commit -m "feat: replace BudgetRailCard with UsageRailCard in rail"
```

---

## Task 10: Swift — Connector models + JSONL fixture

**Files:**
- Create: `scout-app/Scout/Models/ConnectorCall.swift`
- Create: `scout-app/Scout/Models/ConnectorAlert.swift`
- Create: `scout-app/Scout/Models/ConnectorHealthMatrix.swift`
- Create: `scout-app/ScoutTests/Fixtures/connector-calls-2026-04-22.jsonl`
- Create: `scout-app/ScoutTests/Fixtures/connector-alerts.log`
- Create: `scout-app/ScoutTests/Models/ConnectorHealthMatrixTests.swift`

One consolidated task — four tiny value types + aggregation + one test file, tightly coupled enough to commit together.

- [ ] **Step 1: Write the fixtures**

Create `scout-app/ScoutTests/Fixtures/connector-calls-2026-04-22.jsonl`:

```jsonl
{"ts":"2026-04-22T08:00:00Z","session_id":"s1","mode":"briefing","tool":"mcp__plugin_slack_slack__slack_send_message","connector":"mcp:plugin_slack_slack","error":false}
{"ts":"2026-04-22T08:00:05Z","session_id":"s1","mode":"briefing","tool":"Bash","connector":"bash:ls","error":false}
{"ts":"2026-04-22T08:00:10Z","session_id":"s1","mode":"briefing","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":false}
{"ts":"2026-04-22T12:00:00Z","session_id":"s2","mode":"consolidation","tool":"mcp__plugin_slack_slack__slack_read_channel","connector":"mcp:plugin_slack_slack","error":false}
{"ts":"2026-04-22T12:00:15Z","session_id":"s2","mode":"consolidation","tool":"mcp__claude_ai_Google_Drive__list_recent_files","connector":"mcp:claude_ai_Google_Drive","error":true,"err":"auth expired"}
{"ts":"2026-04-22T12:00:20Z","session_id":"s2","mode":"consolidation","tool":"mcp__claude_ai_Google_Drive__list_recent_files","connector":"mcp:claude_ai_Google_Drive","error":true,"err":"auth expired"}
{"ts":"2026-04-22T12:00:25Z","session_id":"s2","mode":"consolidation","tool":"mcp__claude_ai_Google_Drive__list_recent_files","connector":"mcp:claude_ai_Google_Drive","error":true,"err":"auth expired"}
{"ts":"2026-04-22T18:00:00Z","session_id":"s3","mode":"dreaming","tool":"mcp__plugin_linear_linear__list_issues","connector":"mcp:plugin_linear_linear","error":false}
{"ts":"2026-04-22T18:00:05Z","session_id":"s3","mode":"dreaming","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":true,"err":"rate-limited"}
{"ts":"2026-04-22T18:00:10Z","session_id":"s3","mode":"dreaming","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":false}
{"ts":"2026-04-22T18:00:15Z","session_id":"s3","mode":"dreaming","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":false}
```

This gives: s1 Slack✅ Gmail✅ Drive·; s2 Slack✅ Gmail· Drive❌(3/3); s3 Slack· Gmail⚠️(1/3) Linear✅.

Create `scout-app/ScoutTests/Fixtures/connector-alerts.log`:

```
2026-04-22T12:30:00Z | CRITICAL | mcp:claude_ai_Google_Drive | zero successful calls in last 3 runs | first_seen=2026-04-22T12:00:00Z
2026-04-20T08:00:00Z | WARNING  | mcp:claude_ai_Gmail | err/total 0.67 | first_seen=2026-04-20T08:00:00Z
```

- [ ] **Step 2: Write the failing test**

Create `scout-app/ScoutTests/Models/ConnectorHealthMatrixTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ConnectorHealthMatrix")
struct ConnectorHealthMatrixTests {
    @Test func aggregatesCellsAcrossSessionsAndConnectors() {
        let calls = Self.fixtureCalls()
        let matrix = ConnectorHealthMatrix(
            calls: calls,
            connectors: ["mcp:plugin_slack_slack",
                         "mcp:claude_ai_Gmail",
                         "mcp:claude_ai_Google_Drive",
                         "mcp:plugin_linear_linear"]
        )

        let sessions = matrix.sessionsNewestFirst
        #expect(sessions.map(\.id) == ["s3", "s2", "s1"])

        // Slack: s1 ok, s2 ok, s3 absent
        #expect(matrix.cell(connector: "mcp:plugin_slack_slack", sessionId: "s1") == .ok(count: 1))
        #expect(matrix.cell(connector: "mcp:plugin_slack_slack", sessionId: "s2") == .ok(count: 1))
        #expect(matrix.cell(connector: "mcp:plugin_slack_slack", sessionId: "s3") == .absent)
        // Gmail: s1 ok, s2 absent, s3 partial (2 ok, 1 err)
        #expect(matrix.cell(connector: "mcp:claude_ai_Gmail", sessionId: "s3") == .partial(ok: 2, total: 3))
        // Drive: s2 all-error
        #expect(matrix.cell(connector: "mcp:claude_ai_Google_Drive", sessionId: "s2") == .error)
    }

    @Test func sevenDayRateIgnoresAbsentSessions() {
        let calls = Self.fixtureCalls()
        let matrix = ConnectorHealthMatrix(
            calls: calls,
            connectors: ["mcp:plugin_slack_slack", "mcp:claude_ai_Google_Drive"]
        )
        // Slack: 2 ok / 2 total = 100%
        #expect(matrix.successRate(connector: "mcp:plugin_slack_slack") == 1.0)
        // Drive: 0 ok / 3 total = 0%
        #expect(matrix.successRate(connector: "mcp:claude_ai_Google_Drive") == 0.0)
    }

    private static func fixtureCalls() -> [ConnectorCall] {
        let url = Bundle(for: FixtureAnchor.self).resourceURL!
            .appendingPathComponent("connector-calls-2026-04-22.jsonl")
        return ConnectorCall.parseFile(at: url)
    }
}
```

- [ ] **Step 3: Run — expect fail**

Run: `xcodebuild ... -only-testing:ScoutTests/ConnectorHealthMatrixTests test`
Expected: FAIL — `ConnectorCall`, `ConnectorHealthMatrix` not in scope.

- [ ] **Step 4: Write the models**

Create `scout-app/Scout/Models/ConnectorCall.swift`:

```swift
import Foundation

/// One row of `~/Scout/.scout-logs/connector-calls-YYYY-MM-DD.jsonl` —
/// produced by the existing PostToolUse hook at `~/Scout/hooks/connector-log.sh`.
struct ConnectorCall: Codable, Equatable, Hashable, Sendable {
    let ts: Date
    let sessionId: String
    let mode: String
    let tool: String
    let connector: String
    let error: Bool
    let err: String?

    private enum CodingKeys: String, CodingKey {
        case ts, sessionId = "session_id", mode, tool, connector, error, err
    }

    /// Tolerant parser — skips corrupt lines silently, matching
    /// `UsageTrackerService.parseFile`.
    static func parseFile(at url: URL) -> [ConnectorCall] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime]
            if let d = f.date(from: s) { return d }
            f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = f.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(in: c, debugDescription: s)
        }
        var out: [ConnectorCall] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let d = line.data(using: .utf8) else { continue }
            if let call = try? decoder.decode(ConnectorCall.self, from: d) {
                out.append(call)
            }
        }
        return out
    }
}
```

Create `scout-app/Scout/Models/ConnectorAlert.swift`:

```swift
import Foundation

/// One parsed line from `~/Scout/.scout-logs/connector-alerts.log`.
/// The log is shell-authoritative — Swift does not duplicate the sustained-
/// state alert logic in `~/Scout/scripts/connector-health-report.sh`.
struct ConnectorAlert: Equatable, Hashable, Sendable {
    enum Level: String, Sendable { case critical = "CRITICAL", warning = "WARNING" }

    let ts: Date
    let level: Level
    let connector: String
    let reason: String
    let firstSeen: Date

    /// `connector|level|first_seen_ts` — stable across repeated entries for
    /// the same underlying alert, so acking a CRITICAL doesn't suppress a
    /// *new* CRITICAL that starts tomorrow with a fresh `first_seen_ts`.
    var fingerprint: String {
        "\(connector)|\(level.rawValue)|\(ISO8601DateFormatter().string(from: firstSeen))"
    }

    /// Parses the pipe-separated log format emitted by
    /// `connector-health-report.sh`. Format per line:
    /// `ts | LEVEL | connector | reason | first_seen=ts`
    static func parseFile(at url: URL) -> [ConnectorAlert] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        var out: [ConnectorAlert] = []
        for raw in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let parts = raw.split(separator: "|").map {
                $0.trimmingCharacters(in: .whitespaces)
            }
            guard parts.count == 5,
                  let ts = f.date(from: parts[0]),
                  let level = Level(rawValue: parts[1]),
                  parts[4].hasPrefix("first_seen="),
                  let firstSeen = f.date(from: String(parts[4].dropFirst("first_seen=".count)))
            else { continue }
            out.append(ConnectorAlert(
                ts: ts, level: level, connector: parts[2],
                reason: parts[3], firstSeen: firstSeen
            ))
        }
        return out
    }
}
```

Create `scout-app/Scout/Models/ConnectorHealthMatrix.swift`:

```swift
import Foundation

/// Aggregated connector-health view for rendering the rail card.
/// Rows = connectors (in the order passed to init). Columns = sessions
/// (newest first). Cell state is one of four buckets.
struct ConnectorHealthMatrix: Equatable, Sendable {
    struct Session: Equatable, Sendable {
        let id: String
        let mode: String
        let startedAt: Date
    }

    enum Cell: Equatable, Sendable {
        case ok(count: Int)
        case partial(ok: Int, total: Int)
        case error
        case absent
    }

    let connectors: [String]
    let sessionsNewestFirst: [Session]
    private let cells: [String: [String: Cell]]   // connector → sessionId → Cell
    private let totals: [String: (ok: Int, total: Int)]  // connector → rolled-up

    init(calls: [ConnectorCall], connectors: [String]) {
        self.connectors = connectors

        // Group calls by session → mode + startedAt (min ts).
        var bySession: [String: [ConnectorCall]] = [:]
        for c in calls { bySession[c.sessionId, default: []].append(c) }
        let sessions: [Session] = bySession.map { (sid, arr) in
            let startedAt = arr.map(\.ts).min() ?? Date.distantPast
            let mode = arr.first?.mode ?? "?"
            return Session(id: sid, mode: mode, startedAt: startedAt)
        }.sorted { $0.startedAt > $1.startedAt }
        self.sessionsNewestFirst = sessions

        // Build cells + rolled-up per-connector totals.
        var cells: [String: [String: Cell]] = [:]
        var tally: [String: (ok: Int, total: Int)] = [:]
        for connector in connectors {
            var row: [String: Cell] = [:]
            for session in sessions {
                let inSession = (bySession[session.id] ?? [])
                    .filter { $0.connector == connector }
                let total = inSession.count
                let ok = inSession.filter { !$0.error }.count
                row[session.id] = Self.bucket(ok: ok, total: total)
                tally[connector, default: (0, 0)].ok += ok
                tally[connector, default: (0, 0)].total += total
            }
            cells[connector] = row
        }
        self.cells = cells
        self.totals = tally
    }

    func cell(connector: String, sessionId: String) -> Cell {
        cells[connector]?[sessionId] ?? .absent
    }

    /// Success rate across all sessions (0.0–1.0). Returns 0 for a
    /// never-called connector.
    func successRate(connector: String) -> Double {
        guard let t = totals[connector], t.total > 0 else { return 0 }
        return Double(t.ok) / Double(t.total)
    }

    private static func bucket(ok: Int, total: Int) -> Cell {
        switch (ok, total) {
        case (0, 0):              return .absent
        case (let o, let t) where o == t: return .ok(count: t)
        case (0, _):              return .error
        default:                  return .partial(ok: ok, total: total)
        }
    }
}
```

- [ ] **Step 5: Run — expect pass**

Run: `xcodebuild ... -only-testing:ScoutTests/ConnectorHealthMatrixTests test`
Expected: PASS (two tests).

- [ ] **Step 6: Commit**

```bash
cd scout-app && git add Scout/Models/ConnectorCall.swift \
  Scout/Models/ConnectorAlert.swift \
  Scout/Models/ConnectorHealthMatrix.swift \
  ScoutTests/Fixtures/connector-calls-2026-04-22.jsonl \
  ScoutTests/Fixtures/connector-alerts.log \
  ScoutTests/Models/ConnectorHealthMatrixTests.swift
git commit -m "feat: connector-health models + matrix aggregation"
```

---

## Task 11: Swift — `ConnectorAckStore`

**Files:**
- Create: `scout-app/Scout/Services/ConnectorAckStore.swift`
- Create: `scout-app/ScoutTests/Services/ConnectorAckStoreTests.swift`

- [ ] **Step 1: Write the failing test**

Create `scout-app/ScoutTests/Services/ConnectorAckStoreTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ConnectorAckStore")
struct ConnectorAckStoreTests {
    @Test func ackPersistsAndRoundTrips() throws {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }

        let a = ConnectorAckStore(fileURL: url)
        a.ack(fingerprint: "foo")
        #expect(a.isAcked("foo"))

        // New instance reads from disk.
        let b = ConnectorAckStore(fileURL: url)
        #expect(b.isAcked("foo"))
        #expect(!b.isAcked("bar"))
    }

    @Test func gcRemovesFingerprintsNotInActiveSet() throws {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }

        let store = ConnectorAckStore(fileURL: url)
        store.ack(fingerprint: "alive")
        store.ack(fingerprint: "stale")
        store.gc(active: ["alive"])
        #expect(store.isAcked("alive"))
        #expect(!store.isAcked("stale"))
    }

    @Test func missingFileBehavesAsEmpty() {
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent("does-not-exist-\(UUID().uuidString).json")
        let store = ConnectorAckStore(fileURL: missing)
        #expect(!store.isAcked("anything"))
    }

    private func tempURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".json")
    }
}
```

- [ ] **Step 2: Run — expect fail**

Run: `xcodebuild ... -only-testing:ScoutTests/ConnectorAckStoreTests test`
Expected: FAIL — `ConnectorAckStore` not in scope.

- [ ] **Step 3: Write the store**

Create `scout-app/Scout/Services/ConnectorAckStore.swift`:

```swift
import Foundation

/// JSON-backed ack sidecar at `.scout-cache/connector-alerts-acked.json`.
///
/// Uses `ConnectorAlert.fingerprint` as the key so acking a *current*
/// alert doesn't suppress a *new* identical-looking alert with a fresh
/// `first_seen_ts`. Call `gc(active:)` once on app launch / before each
/// render to evict fingerprints whose underlying alerts are no longer
/// in the log.
final class ConnectorAckStore {
    private let fileURL: URL
    private var acks: [String: Date]   // fingerprint → acked at
    private let queue = DispatchQueue(label: "ConnectorAckStore.io")

    init(fileURL: URL) {
        self.fileURL = fileURL
        self.acks = Self.load(from: fileURL)
    }

    func isAcked(_ fingerprint: String) -> Bool {
        queue.sync { acks[fingerprint] != nil }
    }

    func ack(fingerprint: String) {
        queue.sync {
            acks[fingerprint] = Date()
            Self.persist(acks, to: fileURL)
        }
    }

    /// Remove fingerprints not present in `active`. Called from
    /// `ConnectorHealthService.refresh()` after loading the alerts log.
    func gc(active: Set<String>) {
        queue.sync {
            let before = acks.count
            acks = acks.filter { active.contains($0.key) }
            if acks.count != before { Self.persist(acks, to: fileURL) }
        }
    }

    // MARK: - Persistence

    private static func load(from url: URL) -> [String: Date] {
        guard let data = try? Data(contentsOf: url) else { return [:] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return (try? decoder.decode([String: Date].self, from: data)) ?? [:]
    }

    private static func persist(_ acks: [String: Date], to url: URL) {
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            encoder.outputFormatting = .prettyPrinted
            let data = try encoder.encode(acks)
            try data.write(to: url, options: .atomic)
        } catch {
            // Swallow — next ack retries; file inspection by user is still
            // valuable but a transient write failure shouldn't crash the app.
        }
    }
}
```

- [ ] **Step 4: Run — expect pass**

Run: `xcodebuild ... -only-testing:ScoutTests/ConnectorAckStoreTests test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd scout-app && git add Scout/Services/ConnectorAckStore.swift \
  ScoutTests/Services/ConnectorAckStoreTests.swift
git commit -m "feat: ConnectorAckStore with fingerprint-scoped GC"
```

---

## Task 12: Swift — `ConnectorHealthService` + wiring

**Files:**
- Create: `scout-app/Scout/Services/ConnectorHealthService.swift`
- Modify: `scout-app/Scout/Shell/AppState.swift`
- Create: `scout-app/ScoutTests/Services/ConnectorHealthServiceTests.swift`

- [ ] **Step 1: Write the failing test**

Create `scout-app/ScoutTests/Services/ConnectorHealthServiceTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ConnectorHealthService")
struct ConnectorHealthServiceTests {
    @Test func buildsMatrixFromFixtureAndFiltersAckedAlerts() async throws {
        let fixtures = Bundle(for: FixtureAnchor.self).resourceURL!
        let logsDir = fixtures.appendingPathComponent("Connector_fixture_\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: logsDir) }

        // Copy the two fixtures into a temp logsDir so the service sees them.
        try FileManager.default.copyItem(
            at: fixtures.appendingPathComponent("connector-calls-2026-04-22.jsonl"),
            to: logsDir.appendingPathComponent("connector-calls-2026-04-22.jsonl")
        )
        try FileManager.default.copyItem(
            at: fixtures.appendingPathComponent("connector-alerts.log"),
            to: logsDir.appendingPathComponent("connector-alerts.log")
        )

        let ackURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString)-acked.json")
        defer { try? FileManager.default.removeItem(at: ackURL) }

        let service = await ConnectorHealthService(
            logsDirectory: logsDir,
            ackStoreURL: ackURL,
            fileEvents: NoopFS(),
            connectors: ["mcp:plugin_slack_slack",
                         "mcp:claude_ai_Gmail",
                         "mcp:claude_ai_Google_Drive"]
        )
        try await service.loadInitial()
        let matrix = await service.matrix
        #expect(matrix.sessionsNewestFirst.count == 3)

        // The fixture log has one active CRITICAL (Drive).
        let active = await service.activeAlerts
        #expect(active.count == 1)
        #expect(active.first?.connector == "mcp:claude_ai_Google_Drive")

        // Acknowledge it → activeAlerts is empty.
        let fp = active.first!.fingerprint
        await service.acknowledge(fingerprint: fp)
        #expect(await service.activeAlerts.isEmpty)
    }
}
```

- [ ] **Step 2: Run — expect fail**

Run: `xcodebuild ... -only-testing:ScoutTests/ConnectorHealthServiceTests test`
Expected: FAIL.

- [ ] **Step 3: Write the service**

Create `scout-app/Scout/Services/ConnectorHealthService.swift`:

```swift
import Foundation
import Combine
import SwiftUI

/// Loads `.scout-logs/connector-calls-*.jsonl` and `connector-alerts.log`
/// from the given logs directory and publishes:
/// 1. A `ConnectorHealthMatrix` (connectors × recent sessions).
/// 2. `activeAlerts`: non-acked `ConnectorAlert`s from the log.
///
/// Shell scripts remain the source of truth for *which* alerts should fire
/// (`connector-health-report.sh` owns the 14-day sustained-state logic).
/// This service does not re-implement that logic — it only reads and
/// renders.
@MainActor
final class ConnectorHealthService: ObservableObject {
    @Published private(set) var matrix: ConnectorHealthMatrix
    @Published private(set) var activeAlerts: [ConnectorAlert] = []

    private let logsDirectory: URL
    private let ackStore: ConnectorAckStore
    private let fileEvents: any FileSystemEventSource
    private let connectors: [String]
    private var watchTask: Task<Void, Never>?

    init(
        logsDirectory: URL,
        ackStoreURL: URL,
        fileEvents: any FileSystemEventSource,
        connectors: [String] = ConnectorHealthService.defaultConnectors
    ) {
        self.logsDirectory = logsDirectory
        self.ackStore = ConnectorAckStore(fileURL: ackStoreURL)
        self.fileEvents = fileEvents
        self.connectors = connectors
        self.matrix = ConnectorHealthMatrix(calls: [], connectors: connectors)
    }

    /// The 8 connectors tracked by `~/Scout/scripts/connector-health-report.sh:29-41`.
    /// Keep in sync with the shell side (manual diff until someone pays the
    /// test-double cost of end-to-end verification).
    static let defaultConnectors: [String] = [
        "mcp:plugin_slack_slack",
        "mcp:plugin_linear_linear",
        "mcp:claude_ai_Gmail",
        "mcp:claude_ai_Google_Calendar",
        "mcp:claude_ai_Granola",
        "mcp:claude_ai_Google_Drive",
        "github",
        "mcp:claude-in-chrome"
    ]

    func loadInitial() async throws {
        await refresh()
        startWatching()
    }

    func acknowledge(fingerprint: String) {
        ackStore.ack(fingerprint: fingerprint)
        recomputeActiveAlerts(all: allAlertsFromLog())
    }

    // MARK: - Internals

    private func refresh() async {
        // 1. Matrix from all connector-calls-*.jsonl in logsDirectory within 14d window.
        let calls = await loadCallsWithinWindow()
        matrix = ConnectorHealthMatrix(calls: calls, connectors: connectors)
        // 2. Alerts from connector-alerts.log.
        let all = allAlertsFromLog()
        recomputeActiveAlerts(all: all)
        // 3. GC ack store against the live fingerprint set.
        ackStore.gc(active: Set(all.map(\.fingerprint)))
    }

    private func allAlertsFromLog() -> [ConnectorAlert] {
        let url = logsDirectory.appendingPathComponent("connector-alerts.log")
        return ConnectorAlert.parseFile(at: url)
    }

    private func recomputeActiveAlerts(all: [ConnectorAlert]) {
        // Deduplicate: keep latest ts per (connector, level).
        var latest: [String: ConnectorAlert] = [:]
        for a in all {
            let key = "\(a.connector)|\(a.level.rawValue)"
            if let existing = latest[key], existing.ts >= a.ts { continue }
            latest[key] = a
        }
        activeAlerts = latest.values
            .filter { !ackStore.isAcked($0.fingerprint) }
            .sorted { $0.ts > $1.ts }
    }

    private func loadCallsWithinWindow() async -> [ConnectorCall] {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(
            at: logsDirectory,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        ) else { return [] }
        let jsonlURLs = entries.filter {
            let name = $0.lastPathComponent
            return name.hasPrefix("connector-calls-") && name.hasSuffix(".jsonl")
        }
        let windowStart = Date().addingTimeInterval(-14 * 24 * 3600)
        let logs = logsDirectory
        return await Task.detached { () -> [ConnectorCall] in
            var out: [ConnectorCall] = []
            for url in jsonlURLs {
                out.append(contentsOf: ConnectorCall.parseFile(at: url)
                    .filter { $0.ts >= windowStart })
            }
            return out
        }.value
        _ = logs // silence capture warning
    }

    private func startWatching() {
        watchTask?.cancel()
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await event in self.fileEvents.events(for: self.logsDirectory) {
                let name = event.url.lastPathComponent
                let relevant = name.hasPrefix("connector-calls-")
                    || name == "connector-alerts.log"
                if relevant { await self.refresh() }
            }
        }
    }
}
```

- [ ] **Step 4: Run — expect pass**

Run: `xcodebuild ... -only-testing:ScoutTests/ConnectorHealthServiceTests test`
Expected: PASS.

- [ ] **Step 5: Wire into `AppState`**

In `scout-app/Scout/Shell/AppState.swift`, after the existing `let sessionTokensService: SessionTokensService` (added in Task 7), add:

```swift
let connectorHealthService: ConnectorHealthService
```

In `init()`, after the `tokens` creation, add:

```swift
let connectorHealth = ConnectorHealthService(
    logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
    ackStoreURL: scoutDir.appendingPathComponent(".scout-cache/connector-alerts-acked.json"),
    fileEvents: watcher
)
```

Assign `self.connectorHealthService = connectorHealth` with the other service assignments. In the bootstrap `Task`, after the tokens `loadInitial`, add:

```swift
_ = try? await connectorHealth.loadInitial()
```

- [ ] **Step 6: Commit**

```bash
cd scout-app && git add Scout/Services/ConnectorHealthService.swift \
  Scout/Shell/AppState.swift \
  ScoutTests/Services/ConnectorHealthServiceTests.swift
git commit -m "feat: ConnectorHealthService + AppState wiring"
```

---

## Task 13: Swift — `ConnectorHealthRailCard`

**Files:**
- Create: `scout-app/Scout/ControlCenter/ConnectorHealthRailCard.swift`

- [ ] **Step 1: Write the view**

Create `scout-app/Scout/ControlCenter/ConnectorHealthRailCard.swift`:

```swift
import SwiftUI

/// Matrix of connectors × last-5 sessions, with a 7d success-rate column.
/// Reads `AppState.connectorHealthService.matrix`.
struct ConnectorHealthRailCard: View {
    @EnvironmentObject var state: AppState

    /// Display labels for the 8 tracked connectors. Order must match
    /// `ConnectorHealthService.defaultConnectors`.
    private static let labels: [(String, String)] = [
        ("mcp:plugin_slack_slack",          "Slack"),
        ("mcp:plugin_linear_linear",        "Linear"),
        ("mcp:claude_ai_Gmail",             "Gmail"),
        ("mcp:claude_ai_Google_Calendar",   "Calendar"),
        ("mcp:claude_ai_Granola",           "Granola"),
        ("mcp:claude_ai_Google_Drive",      "Drive"),
        ("github",                          "GitHub"),
        ("mcp:claude-in-chrome",            "Chrome")
    ]

    var body: some View {
        let matrix = state.connectorHealthService.matrix
        VStack(alignment: .leading, spacing: 0) {
            RailCardHeader(title: "Connector health")
            if matrix.sessionsNewestFirst.isEmpty {
                Text("No scheduled runs have produced connector data yet.")
                    .font(DS.mono(11.5))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.vertical, 12)
            } else {
                grid(matrix: matrix)
            }
        }
        .editorialCard(padding: 16)
    }

    // MARK: - Grid

    private func grid(matrix: ConnectorHealthMatrix) -> some View {
        let cols = Array(matrix.sessionsNewestFirst.prefix(5))
        return VStack(spacing: 4) {
            HStack(spacing: 4) {
                Text("").frame(width: 70, alignment: .leading)
                ForEach(Array(cols.enumerated()), id: \.offset) { idx, _ in
                    Text("r\(idx + 1)")
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p4)
                        .frame(width: 22)
                }
                Text("7d")
                    .font(DS.mono(10))
                    .foregroundStyle(DS.Ink.p4)
                    .frame(width: 36, alignment: .trailing)
            }
            ForEach(Self.labels, id: \.0) { (key, label) in
                HStack(spacing: 4) {
                    Text(label)
                        .font(DS.mono(11.5))
                        .foregroundStyle(DS.Ink.p2)
                        .frame(width: 70, alignment: .leading)
                    ForEach(cols, id: \.id) { session in
                        cellView(matrix.cell(connector: key, sessionId: session.id))
                            .frame(width: 22)
                    }
                    Text(rateText(matrix.successRate(connector: key)))
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p3)
                        .frame(width: 36, alignment: .trailing)
                }
            }
        }
    }

    private func cellView(_ cell: ConnectorHealthMatrix.Cell) -> some View {
        switch cell {
        case .ok:      return AnyView(Text("✓").foregroundStyle(DS.Status.ok))
        case .error:   return AnyView(Text("✗").foregroundStyle(DS.Status.err))
        case .partial: return AnyView(Text("!").foregroundStyle(DS.Status.warn))
        case .absent:  return AnyView(Text("·").foregroundStyle(DS.Ink.p4))
        }
    }

    private func rateText(_ rate: Double) -> String {
        if rate == 0 && state.connectorHealthService.matrix.sessionsNewestFirst.isEmpty {
            return "—"
        }
        return "\(Int((rate * 100).rounded()))%"
    }
}
```

The `DS.Status.ok / err / warn` identifiers exist — confirm by searching `scout-app/Scout` for `DS.Status.` before writing; if the accessor is spelled differently (`DS.StatusColor.ok`, etc.), adapt. If Status colors don't exist at all, fall back to `.green`, `.red`, `.orange`.

- [ ] **Step 2: Build to verify**

Run: `xcodebuild -project scout-app/Scout.xcodeproj -scheme Scout build 2>&1 | tail -20`
Expected: `BUILD SUCCEEDED`. If `DS.Status.*` doesn't compile, replace with matching identifier or literal colors.

- [ ] **Step 3: Commit**

```bash
cd scout-app && git add Scout/ControlCenter/ConnectorHealthRailCard.swift
git commit -m "feat: ConnectorHealthRailCard — matrix view"
```

---

## Task 14: Swift — `ConnectorAlertBanner`

**Files:**
- Create: `scout-app/Scout/ControlCenter/ConnectorAlertBanner.swift`

- [ ] **Step 1: Write the view**

Create `scout-app/Scout/ControlCenter/ConnectorAlertBanner.swift`:

```swift
import SwiftUI

/// Red banner stretched across the top of `ControlCenterView`, visible
/// only when `ConnectorHealthService.activeAlerts` is non-empty. Click →
/// popover with reason text + an "Acknowledge" button. Acking dismisses
/// the banner in-app; the underlying alert still fires in scheduled-run
/// Slack DMs until the connector recovers.
struct ConnectorAlertBanner: View {
    @EnvironmentObject var state: AppState
    @State private var showPopover: Bool = false

    var body: some View {
        let alerts = state.connectorHealthService.activeAlerts
        if alerts.isEmpty {
            EmptyView()
        } else {
            bannerView(alerts: alerts)
        }
    }

    private func bannerView(alerts: [ConnectorAlert]) -> some View {
        let head = alerts[0]
        let more = alerts.count - 1
        return Button {
            showPopover = true
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("⚠").font(DS.mono(13))
                Text(summary(for: head, moreCount: more))
                    .font(DS.mono(12))
                    .lineLimit(1)
                    .foregroundStyle(.white)
                Spacer()
                Text("Details")
                    .font(DS.mono(11))
                    .foregroundStyle(.white.opacity(0.8))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Color(red: 0.75, green: 0.15, blue: 0.15))
        }
        .buttonStyle(.plain)
        .popover(isPresented: $showPopover, arrowEdge: .bottom) {
            popoverContent(alerts: alerts)
        }
    }

    private func popoverContent(alerts: [ConnectorAlert]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(alerts, id: \.fingerprint) { alert in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(alert.level.rawValue) · \(alert.connector)")
                        .font(DS.sans(12, weight: .semibold))
                        .foregroundStyle(DS.Ink.p1)
                    Text(alert.reason)
                        .font(DS.mono(11.5))
                        .foregroundStyle(DS.Ink.p3)
                    Button("Acknowledge") {
                        state.connectorHealthService.acknowledge(fingerprint: alert.fingerprint)
                    }
                    .font(DS.sans(11))
                    .padding(.top, 4)
                }
                .padding(.bottom, 8)
            }
        }
        .padding(14)
        .frame(width: 360)
    }

    private func summary(for alert: ConnectorAlert, moreCount: Int) -> String {
        let base = "\(displayName(for: alert.connector)) connector: \(alert.level.rawValue) — \(alert.reason)"
        return moreCount > 0 ? "\(base)  (+\(moreCount) more)" : base
    }

    private func displayName(for connector: String) -> String {
        // Same labels as the rail card's header row. Kept duplicated
        // locally to avoid a cross-file dependency just for 8 strings.
        switch connector {
        case "mcp:plugin_slack_slack":          return "Slack"
        case "mcp:plugin_linear_linear":        return "Linear"
        case "mcp:claude_ai_Gmail":             return "Gmail"
        case "mcp:claude_ai_Google_Calendar":   return "Calendar"
        case "mcp:claude_ai_Granola":           return "Granola"
        case "mcp:claude_ai_Google_Drive":      return "Drive"
        case "github":                          return "GitHub"
        case "mcp:claude-in-chrome":            return "Chrome"
        default:                                return connector
        }
    }
}
```

- [ ] **Step 2: Build to verify**

Run: `xcodebuild -project scout-app/Scout.xcodeproj -scheme Scout build 2>&1 | tail -20`
Expected: `BUILD SUCCEEDED`.

- [ ] **Step 3: Commit**

```bash
cd scout-app && git add Scout/ControlCenter/ConnectorAlertBanner.swift
git commit -m "feat: ConnectorAlertBanner + ack popover"
```

---

## Task 15: Swift — Insert banner + health card into `ControlCenterView`

**Files:**
- Modify: `scout-app/Scout/ControlCenter/ControlCenterView.swift`

- [ ] **Step 1: Insert the banner above the header**

In `scout-app/Scout/ControlCenter/ControlCenterView.swift`, find the main body (lines 8-29). Inside the outer `VStack(alignment: .leading, spacing: 0)` at line 11, add `ConnectorAlertBanner()` as the **first child**, before `header`:

```swift
VStack(alignment: .leading, spacing: 0) {
    ConnectorAlertBanner()
    header
    HStack(alignment: .top, spacing: 32) {
        ...
```

- [ ] **Step 2: Insert the rail card**

In the `rail` computed property (modified in Task 9 to look like):

```swift
private var rail: some View {
    VStack(alignment: .leading, spacing: 20) {
        UsageRailCard()
        RepoStateRailCard()
        SignalsRailCard()
        KeyboardRailCard()
    }
}
```

Insert `ConnectorHealthRailCard()` between `RepoStateRailCard()` and `SignalsRailCard()`:

```swift
private var rail: some View {
    VStack(alignment: .leading, spacing: 20) {
        UsageRailCard()
        RepoStateRailCard()
        ConnectorHealthRailCard()
        SignalsRailCard()
        KeyboardRailCard()
    }
}
```

- [ ] **Step 3: Build and run visually**

Run: `xcodebuild -project scout-app/Scout.xcodeproj -scheme Scout build 2>&1 | tail -20`
Expected: `BUILD SUCCEEDED`.

Launch the app (⌘R). Verify:
- Connector Health card shows the 8-connector matrix (or the empty-state message if no `connector-calls-*.jsonl` files exist yet — which is today's reality).
- No banner (no alerts on disk yet).
- Usage card still renders correctly.

To **force-test the banner**, manually create a test alert file:

```bash
mkdir -p ~/Scout/.scout-logs
cat > ~/Scout/.scout-logs/connector-alerts.log <<'EOF'
2026-04-22T12:30:00Z | CRITICAL | mcp:claude_ai_Google_Drive | zero successful calls in last 3 runs | first_seen=2026-04-22T12:00:00Z
EOF
```

Re-launch. Expected: red banner at top of Control Center reading "Drive connector: CRITICAL — zero successful calls in last 3 runs". Click → popover with Acknowledge. Click Acknowledge → banner vanishes. Re-launch app → banner stays dismissed (persisted in `.scout-cache/connector-alerts-acked.json`).

Delete the test file afterward:
```bash
rm ~/Scout/.scout-logs/connector-alerts.log
```
(The real production file will be written by `connector-health-report.sh`.)

- [ ] **Step 4: Commit**

```bash
cd scout-app && git add Scout/ControlCenter/ControlCenterView.swift
git commit -m "feat: wire ConnectorAlertBanner + ConnectorHealthRailCard into Control Center"
```

---

## Wrap-up

- [ ] **Step 1: Cross-track integration check**

Run the full test suite:

```bash
cd scout-app && xcodebuild -project Scout.xcodeproj -scheme Scout test 2>&1 | tail -40
```

Expected: all tests pass. New suites that should appear:
- `SessionTokenEntryTests` (2 tests)
- `TokenTotalsTests` (4 tests)
- `SessionTokensServiceTests` (3 tests)
- `ConnectorHealthMatrixTests` (2 tests)
- `ConnectorAckStoreTests` (3 tests)
- `ConnectorHealthServiceTests` (1 test)

- [ ] **Step 2: Smoke-test end-to-end**

```bash
cd ~/Scout && claude -p "print ok and stop" >/dev/null 2>&1
tail -1 ~/Scout/.scout-logs/session-tokens.jsonl | jq '{session_id, primary_model, input_tokens, cost_usd}'
```

Expected: a fresh row with non-zero tokens and a valid model. Launch Scout.app (⌘R); verify that the new row appears in today's usage card.

- [ ] **Step 3: Update BACKLOG.md**

Move both bullets (*Budget panel shows $0…* and *Connector health card + alert banner…*) from the *Control Center view → Soon* section to the *Shipped* section with today's date (2026-04-22). Leave the Phase 2 note in the Budget bullet pointing forward to the quota work.

```bash
cd apps/macos && git add BACKLOG.md
git commit -m "docs: mark Usage Rail Card + Connector Health as shipped in BACKLOG"
```

---

## Self-Review

**Spec coverage walk-through:**

| Spec section | Task(s) |
|---|---|
| Stop-hook-invoked `sum-session-tokens.sh` writing `session-tokens.jsonl` | 1, 2, 3 |
| Project-local `~/Scout/.claude/settings.json` Stop hook registration | 3 |
| `SessionTokenEntry` model (Swift) | 4 |
| `TokenTotals` aggregate (Swift) | 5 |
| `SessionTokensService` file-watched reader | 6 |
| AppState wiring for tokens service | 7 |
| `UsageRailCard` replaces `BudgetRailCard` | 8, 9 |
| Deletion of old `BudgetRailCard` struct | 9 |
| `ConnectorCall`, `ConnectorAlert`, `ConnectorHealthMatrix` models | 10 |
| `ConnectorAckStore` with fingerprint GC | 11 |
| `ConnectorHealthService` tying matrix + alerts + acks | 12 |
| AppState wiring for health service | 12 |
| `ConnectorHealthRailCard` view | 13 |
| `ConnectorAlertBanner` with ack popover | 14 |
| Insertion into `ControlCenterView` (banner + health card) | 15 |
| Empty-state for no-JSONL-yet | 13 (`sessionsNewestFirst.isEmpty` branch) |
| Error handling: transcript-not-found, no-usage-turns | 2 (shell script guards + explicit JSONL row) |
| Tracker untouched / dispatcher gate untouched | Non-goal — no task, by design |

No gaps.

**Placeholder scan:** no "TBD", no "implement later", no "similar to Task N". Each code-writing step carries full code. Test expectations are specific (field-by-field assertions, not "check it works").

**Type consistency check:**
- `SessionTokenEntry` fields used in Tasks 4, 5, 6 — consistent (`costUSD`, `primaryModel`, `cacheReadInputTokens`).
- `ConnectorHealthMatrix.Cell` cases used in Tasks 10 (def + test) and 13 (view) — consistent (`.ok(count:)`, `.error`, `.partial(ok:total:)`, `.absent`).
- `ConnectorAlert.fingerprint` computed in Task 10, consumed in Tasks 11, 12, 14 — consistent.
- `ConnectorHealthService` methods `loadInitial()`, `acknowledge(fingerprint:)`, `matrix`, `activeAlerts` consistent across Tasks 12, 13, 14.
- Service property names in `AppState` (`sessionTokensService`, `connectorHealthService`) consistent across Tasks 7, 12, and the views.

Plan is internally consistent; nothing to fix.
