# Budget Settings Section — macOS App — Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Budget section to the app's Settings page so the four knobs `scoutctl budget check` enforces can be read and changed from the app, showing the gate they compute to.

**Architecture:** The app is a pure client of the `scoutctl budget show/set` contract — it parses and emits no YAML. `BudgetSettingsService` mirrors `ScheduleEditService`'s shape (injected `scoutctl` URL, `argumentsPrefix`, `ProcessRunner`), shelling out to read and write. A separate pure `BudgetGate` type derives the window budget and skip threshold locally so the readout updates as the user types; it mirrors the engine's two-step rounding exactly and is checked against the engine's own numbers on every load.

**Tech Stack:** Swift 6 / SwiftUI, swift-testing (`import Testing`, `@Test`, `#expect`), Xcode 16 project (`objectVersion = 77`) using filesystem-synchronized groups.

**Spec:** `../../../scout-plugin/docs/superpowers/specs/2026-09-08-budget-config-design.md`

**Plan 1 of 2:** `../../../scout-plugin/docs/superpowers/plans/2026-09-08-budget-config-engine.md` — **must be merged first.** Every task here depends on `scoutctl budget show`/`set` existing.

## Global Constraints

- **The app never parses or writes YAML.** All reads go through `scoutctl budget show --json`; all writes through `scoutctl budget set`. A YAML string literal appearing anywhere in this plan's output is a bug.
- **The derived-gate arithmetic is two-step and must not be collapsed:**
  ```
  windowBudgetUSD  = round(daily × hours / 24, 2)
  skipThresholdUSD = round(windowBudgetUSD × pct / 100, 2)   // rounds the ROUNDED window
  ```
  At the engine defaults (50 / 5h / 80%) the two forms disagree — `8.34` two-step versus `8.33` collapsed. A collapsed mirror would trip the parity warning on the default configuration, on first launch, for every user.
- **New files need no project edits.** The Xcode project uses `PBXFileSystemSynchronizedRootGroup`, so files added under `Scout/` and `ScoutTests/` are picked up automatically.
- **No `@AppStorage` for budget values.** They live in the vault's YAML, not in user defaults. Writes are subprocesses and therefore explicit (a Save button), never per-keystroke.
- **Design tokens only.** Use `DS.*` (`DS.Ink.p1`–`p4`, `DS.Paper.raised`/`sunk`, `DS.Rule.soft`, `DS.Status.warn`/`ok`, `DS.sans`/`mono`/`serif`) — no literal colors or system fonts.
- **Fixtures must be anonymized** per `CLAUDE.md` — this repo is public. Use `alex@example.com`, `Alex`, `/Users/alex/Scout/...` in any canned JSON.
- **Build/test commands:** `xcodebuild -scheme Scout -destination 'platform=macOS' build` and `xcodebuild test -scheme Scout -destination 'platform=macOS'`. Narrow with `-only-testing:ScoutTests/<TypeName>` — the Swift TYPE name (`BudgetGateTests`), NOT the `@Suite("...")` display name. A filter that matches nothing still reports `** TEST SUCCEEDED **`, so always confirm the run printed `Test run with N tests`.

---

### Task 1: `BudgetGate` — the derived-gate arithmetic

A pure value type, built first and alone because the intermediate rounding is the subtle part of this whole feature.

**Files:**
- Create: `Scout/Models/BudgetGate.swift`
- Create: `ScoutTests/Models/BudgetGateTests.swift`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `struct BudgetGate: Equatable, Sendable` with `let windowBudgetUSD: Double` and `let skipThresholdUSD: Double`.
  - `static func derive(dailyUSD: Double, windowHours: Int, skipAtPct: Double) -> BudgetGate`.
  - `func matches(windowBudgetUSD: Double, skipThresholdUSD: Double) -> Bool` — exact-cents comparison used for the parity check in Task 3.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/Models/BudgetGateTests.swift`:

```swift
import Testing
@testable import Scout

@Suite("BudgetGate")
struct BudgetGateTests {

    /// The case that makes the two-step rounding observable. The engine rounds
    /// the window budget to cents BEFORE applying the skip percentage, so
    /// round(round(50*5/24, 2) * 0.8, 2) == 8.34, while the algebraically
    /// equivalent round(50*5/24*0.8, 2) == 8.33. These are the engine's default
    /// knobs, so a collapsed mirror would disagree with `scoutctl budget show`
    /// on first launch for every user.
    @Test("engine defaults reproduce the two-step rounding")
    func engineDefaults() {
        let gate = BudgetGate.derive(dailyUSD: 50, windowHours: 5, skipAtPct: 80)
        #expect(gate.windowBudgetUSD == 10.42)
        #expect(gate.skipThresholdUSD == 8.34)
        #expect(gate.skipThresholdUSD != 8.33)
    }

    @Test("calibrated values where both forms happen to agree")
    func calibrated() {
        let gate = BudgetGate.derive(dailyUSD: 200, windowHours: 3, skipAtPct: 90)
        #expect(gate.windowBudgetUSD == 25.0)
        #expect(gate.skipThresholdUSD == 22.5)
    }

    @Test("a zero budget gates everything")
    func zeroBudget() {
        let gate = BudgetGate.derive(dailyUSD: 0, windowHours: 5, skipAtPct: 80)
        #expect(gate.windowBudgetUSD == 0)
        #expect(gate.skipThresholdUSD == 0)
    }

    @Test("a full-day window equals the daily budget")
    func fullDayWindow() {
        let gate = BudgetGate.derive(dailyUSD: 120, windowHours: 24, skipAtPct: 100)
        #expect(gate.windowBudgetUSD == 120.0)
        #expect(gate.skipThresholdUSD == 120.0)
    }

    @Test("matches() accepts the engine's own numbers")
    func matchesEngineValues() {
        let gate = BudgetGate.derive(dailyUSD: 50, windowHours: 5, skipAtPct: 80)
        #expect(gate.matches(windowBudgetUSD: 10.42, skipThresholdUSD: 8.34))
    }

    @Test("matches() rejects the collapsed-rounding value")
    func rejectsCollapsedValue() {
        let gate = BudgetGate.derive(dailyUSD: 50, windowHours: 5, skipAtPct: 80)
        #expect(!gate.matches(windowBudgetUSD: 10.42, skipThresholdUSD: 8.33))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/BudgetGateTests`

Expected: FAIL — compile error, `cannot find 'BudgetGate' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/Models/BudgetGate.swift`:

```swift
import Foundation

/// The gate `scoutctl budget check` computes from the four budget knobs: how
/// much of the daily budget a rolling window is allowed, and the spend at which
/// a scheduled session is skipped.
///
/// This mirrors `BudgetConfig.window_budget_usd` / `skip_threshold_usd` in the
/// engine (`scout/scripts/budget_check.py`) so the Settings readout can update
/// as the user types, before anything is written. `BudgetSettingsService` checks
/// every derived value against the engine's own numbers on load, so a drift
/// between the two implementations surfaces instead of misinforming quietly.
///
/// The two-step rounding is load-bearing and must NOT be collapsed into one
/// expression: the engine rounds the window budget to cents first, then applies
/// the percentage to that rounded figure. At the engine defaults the two forms
/// disagree — 8.34 two-step, 8.33 collapsed.
struct BudgetGate: Equatable, Sendable {
    let windowBudgetUSD: Double
    let skipThresholdUSD: Double

    static func derive(dailyUSD: Double, windowHours: Int, skipAtPct: Double) -> BudgetGate {
        let windowBudget = roundedToCents(dailyUSD * Double(windowHours) / 24)
        let skipThreshold = roundedToCents(windowBudget * skipAtPct / 100)
        return BudgetGate(windowBudgetUSD: windowBudget, skipThresholdUSD: skipThreshold)
    }

    /// True when the engine's reported figures equal what we derived, to the
    /// cent. Compared as integer cents rather than with a Double tolerance —
    /// the values are currency, and the 8.34-vs-8.33 disagreement this guards
    /// against is exactly one cent wide.
    func matches(windowBudgetUSD: Double, skipThresholdUSD: Double) -> Bool {
        cents(self.windowBudgetUSD) == cents(windowBudgetUSD)
            && cents(self.skipThresholdUSD) == cents(skipThresholdUSD)
    }

    private static func roundedToCents(_ value: Double) -> Double {
        (value * 100).rounded() / 100
    }

    private func cents(_ value: Double) -> Int {
        Int((value * 100).rounded())
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/BudgetGateTests`

Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add Scout/Models/BudgetGate.swift ScoutTests/Models/BudgetGateTests.swift
git commit -m "feat(budget): BudgetGate mirrors the engine's derived gate

Two-step rounding, not the algebraically equivalent single expression:
the engine rounds the window budget to cents before applying the skip
percentage, so at the default knobs it gates at \$8.34 where a collapsed
form yields \$8.33. Tests pin the disagreeing case.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Extract the Settings building blocks

A pure refactor, taken first so the new section lands in its own file rather than growing a 416-line view. No behavior change.

**Files:**
- Create: `Scout/Shell/SettingsComponents.swift`
- Modify: `Scout/Shell/SettingsView.swift:216-416` (remove the four private structs)

**Interfaces:**
- Consumes: `DS` from `Scout/Utilities/DesignSystem.swift`, `.plainHit` from `Scout/Utilities/PlainHitButtonStyle.swift`.
- Produces, all `internal` (no access modifier), unchanged in behavior and signature:
  - `SettingsCard<Content: View>` — `var padding: CGFloat = 0`, `@ViewBuilder var content: () -> Content`
  - `SettingsRow<Trailing: View>` — `let title: String`, `let help: String`, `@ViewBuilder var trailing: () -> Trailing`
  - `SettingsField<Input: View>` — `let label: String`, `let help: String`, `@ViewBuilder var input: () -> Input`
  - `SettingsInput` — `@Binding var text: String`, `let placeholder: String`
  - `SettingsToggle` — `@Binding var isOn: Bool`

- [ ] **Step 1: Move the five structs verbatim**

Cut everything from the `// MARK: - Building blocks` comment to the end of `Scout/Shell/SettingsView.swift` and paste it into a new `Scout/Shell/SettingsComponents.swift`, prefixed with:

```swift
import SwiftUI

// Extracted from SettingsView.swift so sections can live in their own files.
// `internal` rather than `private` for exactly that reason — BudgetSettingsSection
// composes the same atoms. Behavior is unchanged from the original.
```

Then delete the `private` modifier from each of the five struct declarations, so they read:

```swift
struct SettingsCard<Content: View>: View {
struct SettingsRow<Trailing: View>: View {
struct SettingsField<Input: View>: View {
struct SettingsInput: View {
struct SettingsToggle: View {
```

Change nothing else — not the bodies, not the doc comments, not `parseHelp`.

- [ ] **Step 2: Build to verify the refactor is behavior-neutral**

Run: `xcodebuild -scheme Scout -destination 'platform=macOS' build`

Expected: BUILD SUCCEEDED. A `'X' is inaccessible due to 'private' protection level` error means a `private` was left in place; an "invalid redeclaration" means the originals were not fully removed from `SettingsView.swift`.

- [ ] **Step 3: Run the existing test suite**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS'`

Expected: PASS — the same set of tests that passed before this task. This is a move; nothing should change.

- [ ] **Step 4: Commit**

```bash
git add Scout/Shell/SettingsComponents.swift Scout/Shell/SettingsView.swift
git commit -m "refactor(settings): extract the card/row/field atoms

Moved verbatim out of a 416-line SettingsView.swift and made internal so
a section can live in its own file. No behavior change.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `BudgetSettingsService`

The client of the CLI contract: decode `budget show --json`, build `budget set` arguments, surface the parity check.

**Files:**
- Create: `Scout/Services/BudgetSettingsService.swift`
- Create: `ScoutTests/Services/BudgetSettingsServiceTests.swift`

**Interfaces:**
- Consumes: `ProcessRunner` / `ProcessResult` from `Scout/Services/Protocols/ProcessRunner.swift`; `BudgetGate` from Task 1.
- Produces:
  - `struct BudgetSettings: Codable, Equatable, Sendable` — `configPath: String`, `source: BudgetSource`, `dailyUSD: Double`, `windowHours: Int`, `skipAtPct: Double`, `failureBackoffMinutes: Int`, `windowBudgetUSD: Double`, `skipThresholdUSD: Double`. `CodingKeys` map to the engine's snake_case payload keys.
  - `enum BudgetSource: String, Codable, Sendable { case vault, legacy, defaults }` — with `init(from:)` decoding an unrecognized value to `.defaults` rather than throwing.
  - `@MainActor final class BudgetSettingsService: ObservableObject` — `@Published private(set) var settings: BudgetSettings?`, `@Published private(set) var parityWarning: String?`, `func load() async throws`, `func save(dailyUSD: Double, windowHours: Int, skipAtPct: Double, failureBackoffMinutes: Int) async throws`.
  - `static func setArguments(dailyUSD:windowHours:skipAtPct:failureBackoffMinutes:) -> [String]`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Services/BudgetSettingsServiceTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

/// Canned `scoutctl budget show --json` output. Mirrors the payload asserted by
/// the engine's `test_budget_show_json_emits_the_payload` — if these key names
/// drift apart, the app silently shows defaults.
private let defaultsJSON = """
{
  "config_path": "/Users/alex/Scout/scout-config.yaml",
  "source": "defaults",
  "daily_usd": 50.0,
  "window_hours": 5,
  "skip_at_pct": 80.0,
  "failure_backoff_minutes": 60,
  "window_budget_usd": 10.42,
  "skip_threshold_usd": 8.34
}
"""

private let vaultJSON = """
{
  "config_path": "/Users/alex/Scout/scout-config.yaml",
  "source": "vault",
  "daily_usd": 200.0,
  "window_hours": 3,
  "skip_at_pct": 90.0,
  "failure_backoff_minutes": 30,
  "window_budget_usd": 25.0,
  "skip_threshold_usd": 22.5
}
"""

/// Stub runner returning a queued stdout per call, reusing the last entry when
/// the queue is exhausted, and recording every invocation for assertion.
private actor StubBudgetRunner: ProcessRunner {
    struct Call: Sendable {
        let arguments: [String]
    }

    private(set) var calls: [Call] = []
    private var stdouts: [String]
    private let exitCode: Int32
    private let stderr: String

    init(stdouts: [String], exitCode: Int32 = 0, stderr: String = "") {
        self.stdouts = stdouts
        self.exitCode = exitCode
        self.stderr = stderr
    }

    func recordedCalls() -> [Call] { calls }

    nonisolated func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult {
        await record(arguments: arguments)
        let out = await nextStdout()
        return ProcessResult(
            exitCode: exitCode,
            stdout: Data(out.utf8),
            stderr: Data(stderr.utf8)
        )
    }

    private func record(arguments: [String]) {
        calls.append(Call(arguments: arguments))
    }

    private func nextStdout() -> String {
        if stdouts.count > 1 { return stdouts.removeFirst() }
        return stdouts.first ?? ""
    }
}

@MainActor
private func makeService(
    stdouts: [String],
    exitCode: Int32 = 0,
    stderr: String = "",
    argumentsPrefix: [String] = []
) -> (BudgetSettingsService, StubBudgetRunner) {
    let runner = StubBudgetRunner(stdouts: stdouts, exitCode: exitCode, stderr: stderr)
    let service = BudgetSettingsService(
        scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        runner: runner,
        argumentsPrefix: argumentsPrefix
    )
    return (service, runner)
}

@Suite("BudgetSettingsService")
struct BudgetSettingsServiceTests {

    @Test("load decodes the engine payload")
    @MainActor
    func loadDecodes() async throws {
        let (service, _) = makeService(stdouts: [vaultJSON])
        try await service.load()
        let settings = try #require(service.settings)
        #expect(settings.source == .vault)
        #expect(settings.dailyUSD == 200)
        #expect(settings.windowHours == 3)
        #expect(settings.skipAtPct == 90)
        #expect(settings.failureBackoffMinutes == 30)
        #expect(settings.windowBudgetUSD == 25.0)
        #expect(settings.skipThresholdUSD == 22.5)
        #expect(settings.configPath == "/Users/alex/Scout/scout-config.yaml")
    }

    @Test("load reads the defaults source")
    @MainActor
    func loadDefaultsSource() async throws {
        let (service, _) = makeService(stdouts: [defaultsJSON])
        try await service.load()
        #expect(try #require(service.settings).source == .defaults)
    }

    @Test("load asks scoutctl for JSON")
    @MainActor
    func loadArguments() async throws {
        let (service, runner) = makeService(stdouts: [vaultJSON])
        try await service.load()
        let calls = await runner.recordedCalls()
        #expect(calls.count == 1)
        #expect(calls[0].arguments == ["budget", "show", "--json"])
    }

    @Test("load honours the argumentsPrefix used for the env fallback")
    @MainActor
    func loadArgumentsPrefix() async throws {
        let (service, runner) = makeService(stdouts: [vaultJSON], argumentsPrefix: ["scoutctl"])
        try await service.load()
        let calls = await runner.recordedCalls()
        #expect(calls[0].arguments == ["scoutctl", "budget", "show", "--json"])
    }

    /// The engine's numbers and ours must agree. At the DEFAULT knobs the
    /// two-step and collapsed roundings differ by a cent, so this is the case
    /// that catches a collapsed mirror.
    @Test("no parity warning when our arithmetic matches the engine")
    @MainActor
    func parityClean() async throws {
        let (service, _) = makeService(stdouts: [defaultsJSON])
        try await service.load()
        #expect(service.parityWarning == nil)
    }

    @Test("parity warning when the engine reports different figures")
    @MainActor
    func parityDrift() async throws {
        let drifted = defaultsJSON.replacingOccurrences(
            of: "\"skip_threshold_usd\": 8.34",
            with: "\"skip_threshold_usd\": 8.33"
        )
        let (service, _) = makeService(stdouts: [drifted])
        try await service.load()
        #expect(service.parityWarning != nil)
        // Still publishes the engine's values — they are authoritative.
        #expect(try #require(service.settings).skipThresholdUSD == 8.33)
    }

    @Test("unrecognized source decodes to defaults rather than throwing")
    @MainActor
    func unknownSource() async throws {
        let odd = vaultJSON.replacingOccurrences(of: "\"vault\"", with: "\"something-new\"")
        let (service, _) = makeService(stdouts: [odd])
        try await service.load()
        #expect(try #require(service.settings).source == .defaults)
    }

    @Test("load surfaces a non-zero exit with the CLI's stderr")
    @MainActor
    func loadFailure() async throws {
        let (service, _) = makeService(stdouts: [""], exitCode: 1, stderr: "boom: no vault")
        await #expect(throws: (any Error).self) { try await service.load() }
        #expect(service.settings == nil)
    }

    @Test("load surfaces undecodable stdout")
    @MainActor
    func loadBadJSON() async throws {
        let (service, _) = makeService(stdouts: ["not json at all"])
        await #expect(throws: (any Error).self) { try await service.load() }
    }

    @Test("setArguments passes every knob")
    func setArguments() {
        let args = BudgetSettingsService.setArguments(
            dailyUSD: 200,
            windowHours: 3,
            skipAtPct: 90,
            failureBackoffMinutes: 30
        )
        #expect(args == [
            "budget", "set",
            "--daily-usd", "200",
            "--window-hours", "3",
            "--skip-at-pct", "90",
            "--failure-backoff-minutes", "30",
            "--json",
        ])
    }

    @Test("setArguments keeps a fractional dollar amount")
    func setArgumentsFractional() {
        let args = BudgetSettingsService.setArguments(
            dailyUSD: 12.5,
            windowHours: 3,
            skipAtPct: 90,
            failureBackoffMinutes: 30
        )
        #expect(args.contains("12.5"))
    }

    @Test("save writes then republishes from the set payload")
    @MainActor
    func saveRepublishes() async throws {
        let (service, runner) = makeService(stdouts: [defaultsJSON, vaultJSON])
        try await service.load()
        try await service.save(dailyUSD: 200, windowHours: 3, skipAtPct: 90, failureBackoffMinutes: 30)

        let settings = try #require(service.settings)
        #expect(settings.source == .vault)
        #expect(settings.dailyUSD == 200)

        let calls = await runner.recordedCalls()
        #expect(calls.count == 2)
        #expect(calls[1].arguments.contains("set"))
        #expect(calls[1].arguments.contains("--daily-usd"))
    }

    @Test("a rejected save leaves the previous settings in place")
    @MainActor
    func saveFailureKeepsSettings() async throws {
        let (service, _) = makeService(
            stdouts: [defaultsJSON, ""],
            exitCode: 1,
            stderr: "error: skip_at_pct=500 is outside the usable range [0.0, 100.0]"
        )
        // The stub applies its exit code to every call, so load must happen
        // against a fresh clean service.
        let (loader, _) = makeService(stdouts: [defaultsJSON])
        try await loader.load()

        await #expect(throws: (any Error).self) {
            try await service.save(dailyUSD: 1, windowHours: 1, skipAtPct: 500, failureBackoffMinutes: 1)
        }
        #expect(service.settings == nil)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/BudgetSettingsServiceTests`

Expected: FAIL — compile error, `cannot find 'BudgetSettingsService' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/Services/BudgetSettingsService.swift`:

```swift
import Foundation
import Combine

/// Which layer supplied the budget values the engine is using.
///
/// Coarse by design — the engine reports where the values came from, canonical
/// block winning over the legacy `plan:`/`thresholds:` spellings. Used only to
/// pick an advisory line in the UI, never to decide what the values are.
enum BudgetSource: String, Codable, Sendable {
    case vault
    case legacy
    case defaults

    /// An unrecognized value decodes to `.defaults` rather than throwing: a
    /// newer engine adding a source must not stop the pane from loading.
    init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = BudgetSource(rawValue: raw) ?? .defaults
    }
}

/// The `scoutctl budget show --json` payload. Key names are the engine's
/// canonical YAML spellings; see the engine's `budget_config.show_payload`,
/// whose test asserts this exact key set.
struct BudgetSettings: Codable, Equatable, Sendable {
    let configPath: String
    let source: BudgetSource
    let dailyUSD: Double
    let windowHours: Int
    let skipAtPct: Double
    let failureBackoffMinutes: Int
    let windowBudgetUSD: Double
    let skipThresholdUSD: Double

    enum CodingKeys: String, CodingKey {
        case configPath = "config_path"
        case source
        case dailyUSD = "daily_usd"
        case windowHours = "window_hours"
        case skipAtPct = "skip_at_pct"
        case failureBackoffMinutes = "failure_backoff_minutes"
        case windowBudgetUSD = "window_budget_usd"
        case skipThresholdUSD = "skip_threshold_usd"
    }
}

/// Reads and writes the vault's budget configuration by shelling out to
/// `scoutctl budget show/set`.
///
/// The app deliberately holds no YAML knowledge: `scout-config.yaml` doubles as
/// bootstrap state written by several producers, so composing it here would put
/// the app in a position to drop a subtree bootstrap wrote. The engine does a
/// line-surgical in-place update instead. Shape mirrors `ScheduleEditService`.
@MainActor
final class BudgetSettingsService: ObservableObject {
    @Published private(set) var settings: BudgetSettings?

    /// Set when our locally derived gate disagrees with the figures the engine
    /// reported — meaning `BudgetGate` and the engine's `BudgetConfig` have
    /// drifted apart. The engine's values stay authoritative; this only tells
    /// the user the live readout may mislead while they type.
    @Published private(set) var parityWarning: String?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]

    init(scoutctl: URL, runner: any ProcessRunner, argumentsPrefix: [String] = []) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
    }

    func load() async throws {
        let payload = try await invoke(["budget", "show", "--json"])
        publish(payload)
    }

    func save(
        dailyUSD: Double,
        windowHours: Int,
        skipAtPct: Double,
        failureBackoffMinutes: Int
    ) async throws {
        let payload = try await invoke(Self.setArguments(
            dailyUSD: dailyUSD,
            windowHours: windowHours,
            skipAtPct: skipAtPct,
            failureBackoffMinutes: failureBackoffMinutes
        ))
        publish(payload)
    }

    /// `budget set` echoes the resulting config with `--json`, so a save needs
    /// no follow-up read.
    static func setArguments(
        dailyUSD: Double,
        windowHours: Int,
        skipAtPct: Double,
        failureBackoffMinutes: Int
    ) -> [String] {
        [
            "budget", "set",
            "--daily-usd", numeric(dailyUSD),
            "--window-hours", "\(windowHours)",
            "--skip-at-pct", numeric(skipAtPct),
            "--failure-backoff-minutes", "\(failureBackoffMinutes)",
            "--json",
        ]
    }

    // MARK: - Internals

    private func invoke(_ arguments: [String]) async throws -> BudgetSettings {
        let result = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + arguments,
            environment: [:],
            workingDirectory: nil
        )
        guard result.exitCode == 0 else {
            let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
            let detail = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(
                domain: "BudgetSettingsService",
                code: Int(result.exitCode),
                userInfo: [NSLocalizedDescriptionKey: detail.isEmpty
                    ? "scoutctl \(arguments.joined(separator: " ")) exited \(result.exitCode)"
                    : detail]
            )
        }
        do {
            return try JSONDecoder().decode(BudgetSettings.self, from: result.stdout)
        } catch {
            let stdout = String(data: result.stdout, encoding: .utf8) ?? ""
            let snippet = String(stdout.prefix(200))
            throw NSError(
                domain: "BudgetSettingsService",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey:
                    "could not decode scoutctl budget output: \(error.localizedDescription)\n\(snippet)"]
            )
        }
    }

    private func publish(_ payload: BudgetSettings) {
        settings = payload
        let derived = BudgetGate.derive(
            dailyUSD: payload.dailyUSD,
            windowHours: payload.windowHours,
            skipAtPct: payload.skipAtPct
        )
        if derived.matches(
            windowBudgetUSD: payload.windowBudgetUSD,
            skipThresholdUSD: payload.skipThresholdUSD
        ) {
            parityWarning = nil
        } else {
            parityWarning = String(
                format: "Scout computes this gate as $%.2f (skip at $%.2f); the engine reports "
                    + "$%.2f (skip at $%.2f). The engine's values are the ones in effect.",
                derived.windowBudgetUSD, derived.skipThresholdUSD,
                payload.windowBudgetUSD, payload.skipThresholdUSD
            )
        }
    }

    /// Emit a number the way a person writes it, so the YAML the engine writes
    /// reads as `90` rather than `90.0`.
    private static func numeric(_ value: Double) -> String {
        value == value.rounded() ? "\(Int(value))" : "\(value)"
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/BudgetSettingsServiceTests`

Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/BudgetSettingsService.swift ScoutTests/Services/BudgetSettingsServiceTests.swift
git commit -m "feat(budget): BudgetSettingsService over the scoutctl contract

Reads scoutctl budget show --json and writes via budget set. The app
holds no YAML knowledge — scout-config.yaml has several producers, so
the engine does the line-surgical update.

Checks the engine's reported gate against BudgetGate on every load and
publishes a warning on drift, keeping the engine authoritative.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The Budget section in Settings

Four fields, an explicit Save, the live derived readout, and the advisory that would have made the tight default gate obvious.

**Files:**
- Create: `Scout/Shell/BudgetSettingsSection.swift`
- Modify: `Scout/Shell/AppState.swift:26` (property), `:120-125` (construction), `:192` (assignment)
- Modify: `Scout/Shell/SettingsView.swift:63-110` (insert the section after "Claude Code")
- Create: `ScoutTests/Shell/BudgetSettingsSectionTests.swift`

**Interfaces:**
- Consumes: `BudgetSettingsService`, `BudgetSettings`, `BudgetSource` (Task 3); `BudgetGate` (Task 1); `SettingsCard`, `SettingsField`, `SettingsInput` (Task 2); `DS`, `.plainHit`.
- Produces:
  - `struct BudgetSettingsSection: View` — takes no parameters, reads `@EnvironmentObject var service: BudgetSettingsService`.
  - `struct BudgetDraft: Equatable` — `dailyUSD: String`, `windowHours: String`, `skipAtPct: String`, `failureBackoffMinutes: String`, plus `init(from: BudgetSettings)`, `var parsed: (daily: Double, hours: Int, pct: Double, backoff: Int)?`, `var validationMessage: String?`.
  - `AppState.budgetSettingsService: BudgetSettingsService`.

- [ ] **Step 1: Write the failing tests for the draft/validation logic**

The parsing and validation rules are the testable part of this task; SwiftUI layout is verified by eye in Step 7. Create `ScoutTests/Shell/BudgetSettingsSectionTests.swift`:

```swift
import Testing
@testable import Scout

private let sample = BudgetSettings(
    configPath: "/Users/alex/Scout/scout-config.yaml",
    source: .vault,
    dailyUSD: 200,
    windowHours: 3,
    skipAtPct: 90,
    failureBackoffMinutes: 30,
    windowBudgetUSD: 25.0,
    skipThresholdUSD: 22.5
)

@Suite("BudgetDraft")
struct BudgetDraftTests {

    @Test("seeds from settings without trailing zeros")
    func seeds() {
        let draft = BudgetDraft(from: sample)
        #expect(draft.dailyUSD == "200")
        #expect(draft.windowHours == "3")
        #expect(draft.skipAtPct == "90")
        #expect(draft.failureBackoffMinutes == "30")
    }

    @Test("parses a complete valid draft")
    func parsesValid() throws {
        let parsed = try #require(BudgetDraft(from: sample).parsed)
        #expect(parsed.daily == 200)
        #expect(parsed.hours == 3)
        #expect(parsed.pct == 90)
        #expect(parsed.backoff == 30)
    }

    @Test("keeps a fractional dollar amount")
    func parsesFraction() throws {
        var draft = BudgetDraft(from: sample)
        draft.dailyUSD = "12.50"
        #expect(try #require(draft.parsed).daily == 12.5)
        #expect(draft.validationMessage == nil)
    }

    @Test("non-numeric input does not parse")
    func rejectsNonNumeric() {
        var draft = BudgetDraft(from: sample)
        draft.dailyUSD = "lots"
        #expect(draft.parsed == nil)
        #expect(draft.validationMessage != nil)
    }

    @Test("an empty field does not parse")
    func rejectsEmpty() {
        var draft = BudgetDraft(from: sample)
        draft.windowHours = ""
        #expect(draft.parsed == nil)
    }

    /// The engine rejects these on `budget set`, so catching them here keeps a
    /// pointless subprocess and a raw stderr string away from the user.
    @Test("mirrors the engine's bounds")
    func mirrorsEngineBounds() throws {
        var draft = BudgetDraft(from: sample)
        draft.windowHours = "0"
        #expect(draft.parsed == nil)
        #expect(try #require(draft.validationMessage).contains("window"))

        draft = BudgetDraft(from: sample)
        draft.skipAtPct = "101"
        #expect(draft.parsed == nil)

        draft = BudgetDraft(from: sample)
        draft.dailyUSD = "-1"
        #expect(draft.parsed == nil)

        draft = BudgetDraft(from: sample)
        draft.failureBackoffMinutes = "-1"
        #expect(draft.parsed == nil)
    }

    @Test("a zero daily budget is a real choice, not an error")
    func zeroDailyAllowed() {
        var draft = BudgetDraft(from: sample)
        draft.dailyUSD = "0"
        #expect(draft.parsed != nil)
        #expect(draft.validationMessage == nil)
    }

    @Test("100 percent and a 24h window are in range")
    func upperBoundsAllowed() {
        var draft = BudgetDraft(from: sample)
        draft.skipAtPct = "100"
        draft.windowHours = "24"
        #expect(draft.parsed != nil)
    }

    @Test("a draft equal to the loaded settings is not dirty")
    func notDirtyWhenUnchanged() {
        let draft = BudgetDraft(from: sample)
        #expect(draft == BudgetDraft(from: sample))
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/BudgetDraftTests`

Expected: FAIL — compile error, `cannot find 'BudgetDraft' in scope`.

- [ ] **Step 3: Write the section**

Create `Scout/Shell/BudgetSettingsSection.swift`:

```swift
import SwiftUI

/// Editable form state for the four budget knobs.
///
/// Held as strings because the fields are free-text `SettingsInput`s and a
/// half-typed value ("2", on the way to "200") must not be coerced into a
/// number mid-keystroke. Bounds mirror the engine's `CONFIG_BOUNDS` so an
/// out-of-range value is caught here rather than costing a subprocess and
/// surfacing as raw stderr.
struct BudgetDraft: Equatable {
    var dailyUSD: String
    var windowHours: String
    var skipAtPct: String
    var failureBackoffMinutes: String

    init(from settings: BudgetSettings) {
        self.dailyUSD = Self.display(settings.dailyUSD)
        self.windowHours = "\(settings.windowHours)"
        self.skipAtPct = Self.display(settings.skipAtPct)
        self.failureBackoffMinutes = "\(settings.failureBackoffMinutes)"
    }

    /// The four values, or nil when any field is empty, non-numeric, or out of
    /// the range the engine will accept.
    var parsed: (daily: Double, hours: Int, pct: Double, backoff: Int)? {
        guard let daily = Double(dailyUSD.trimmed), daily >= 0,
              let hours = Int(windowHours.trimmed), hours >= 1,
              let pct = Double(skipAtPct.trimmed), (0...100).contains(pct),
              let backoff = Int(failureBackoffMinutes.trimmed), backoff >= 0
        else { return nil }
        return (daily, hours, pct, backoff)
    }

    /// A specific reason the draft cannot be saved, or nil when it can.
    var validationMessage: String? {
        guard parsed == nil else { return nil }
        if let daily = Double(dailyUSD.trimmed) {
            if daily < 0 { return "Daily budget cannot be negative." }
        } else {
            return "Daily budget must be a number."
        }
        if let hours = Int(windowHours.trimmed) {
            if hours < 1 { return "The rolling window must be at least 1 hour." }
        } else {
            return "The rolling window must be a whole number of hours."
        }
        if let pct = Double(skipAtPct.trimmed) {
            if !(0...100).contains(pct) { return "The skip threshold must be between 0 and 100 percent." }
        } else {
            return "The skip threshold must be a number."
        }
        if let backoff = Int(failureBackoffMinutes.trimmed) {
            if backoff < 0 { return "The failure backoff cannot be negative." }
        } else {
            return "The failure backoff must be a whole number of minutes."
        }
        return "Enter a value in every field."
    }

    /// `200`, not `200.0` — these round-trip through YAML a person reads.
    private static func display(_ value: Double) -> String {
        value == value.rounded() ? "\(Int(value))" : "\(value)"
    }
}

private extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}

/// The Budget section of Settings: the four knobs `scoutctl budget check`
/// enforces, plus the gate they compute to.
///
/// Configuration only — no spend figures. `UsageRailCard` deliberately omits
/// dollar cost as misleading on a quota-based plan seat, and this section keeps
/// that line.
struct BudgetSettingsSection: View {
    @EnvironmentObject var service: BudgetSettingsService

    @State private var draft: BudgetDraft?
    @State private var loadError: String?
    @State private var saveError: String?
    @State private var isSaving = false

    var body: some View {
        SettingsCard {
            if let draft {
                fields
                footer(draft)
            } else if let loadError {
                message(loadError, color: DS.Status.warn)
            } else {
                message("Reading budget configuration…", color: DS.Ink.p4)
            }
        }
        .task { await load() }
    }

    // MARK: - Fields

    /// The four inputs. Reads and writes through `binding`, so it needs no
    /// draft parameter — SwiftUI redraws it when `@State draft` changes.
    @ViewBuilder
    private var fields: some View {
        SettingsField(
            label: "Daily budget",
            help: "USD per day the scheduled runs may spend. Prorated to the rolling window below."
        ) {
            SettingsInput(text: binding(\.dailyUSD), placeholder: "50")
        }
        SettingsField(
            label: "Rolling window",
            help: "Hours of spend history the gate sums. A shorter window means a tighter effective cap."
        ) {
            SettingsInput(text: binding(\.windowHours), placeholder: "5")
        }
        SettingsField(
            label: "Skip threshold",
            help: "Percent of the window budget at which a scheduled session is skipped."
        ) {
            SettingsInput(text: binding(\.skipAtPct), placeholder: "80")
        }
        SettingsField(
            label: "Failure backoff",
            help: "Minutes to wait after a failed run. Rate-limit events back off for twice this long."
        ) {
            SettingsInput(text: binding(\.failureBackoffMinutes), placeholder: "60")
        }
    }

    private func binding(_ key: WritableKeyPath<BudgetDraft, String>) -> Binding<String> {
        Binding(
            get: { draft?[keyPath: key] ?? "" },
            set: { newValue in
                guard var updated = draft else { return }
                updated[keyPath: key] = newValue
                draft = updated
                saveError = nil
            }
        )
    }

    // MARK: - Footer: derived gate, advisories, Save

    @ViewBuilder
    private func footer(_ current: BudgetDraft) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            gateLine(current)
            if let message = current.validationMessage {
                advisory(message, color: DS.Status.warn)
            }
            if let settings = service.settings {
                sourceAdvisory(settings)
            }
            if let parity = service.parityWarning {
                advisory(parity, color: DS.Status.warn)
            }
            if let saveError {
                advisory(saveError, color: DS.Status.warn)
            }
            saveButton(current)
        }
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private func gateLine(_ current: BudgetDraft) -> some View {
        if let values = current.parsed {
            let gate = BudgetGate.derive(
                dailyUSD: values.daily,
                windowHours: values.hours,
                skipAtPct: values.pct
            )
            Text(String(
                format: "%dh window → $%.2f budget · sessions skip at $%.2f",
                values.hours, gate.windowBudgetUSD, gate.skipThresholdUSD
            ))
            .font(DS.mono(11.5))
            .foregroundStyle(DS.Ink.p2)
        }
    }

    /// The line that makes the invisible case visible. A vault with no budget
    /// block gates at $8.34 per 5h window on the engine's defaults, which is
    /// roughly two sessions — and nothing in the app said so before this.
    @ViewBuilder
    private func sourceAdvisory(_ settings: BudgetSettings) -> some View {
        switch settings.source {
        case .defaults:
            advisory(
                String(format: "No budget block in scout-config.yaml — running on engine defaults, "
                       + "so sessions skip at $%.2f. Save to write the values above.",
                       settings.skipThresholdUSD),
                color: DS.Status.warn
            )
        case .legacy:
            advisory(
                "These values come from the older plan:/thresholds: keys. Saving rewrites them "
                    + "as the canonical budget: block.",
                color: DS.Ink.p3
            )
        case .vault:
            Text(settings.configPath)
                .font(DS.mono(10.5))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    @ViewBuilder
    private func saveButton(_ current: BudgetDraft) -> some View {
        let unchanged = service.settings.map { BudgetDraft(from: $0) == current } ?? false
        let disabled = isSaving || current.parsed == nil || unchanged
        Button {
            Task { await save(current) }
        } label: {
            Text(isSaving ? "Saving…" : "Save")
                .font(DS.sans(12.5, weight: .medium))
                .foregroundStyle(disabled ? DS.Ink.p4 : .white)
                .padding(.horizontal, 14)
                .frame(height: 28)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(disabled ? DS.Paper.sunk : DS.Accent.fill)
                        .overlay(RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                )
        }
        .buttonStyle(.plainHit)
        .disabled(disabled)
        .padding(.top, 2)
    }

    private func advisory(_ text: String, color: Color) -> some View {
        Text(text)
            .font(DS.sans(11.5))
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func message(_ text: String, color: Color) -> some View {
        Text(text)
            .font(DS.sans(12.5))
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, 14)
    }

    // MARK: - Actions

    private func load() async {
        guard draft == nil else { return }
        do {
            try await service.load()
            loadError = nil
            if let settings = service.settings {
                draft = BudgetDraft(from: settings)
            }
        } catch {
            loadError = "Could not read the budget configuration: \(error.localizedDescription)"
        }
    }

    private func save(_ current: BudgetDraft) async {
        guard let values = current.parsed else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            try await service.save(
                dailyUSD: values.daily,
                windowHours: values.hours,
                skipAtPct: values.pct,
                failureBackoffMinutes: values.backoff
            )
            saveError = nil
            if let settings = service.settings {
                draft = BudgetDraft(from: settings)
            }
        } catch {
            saveError = error.localizedDescription
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/BudgetDraftTests`

Expected: PASS, 9 tests.

- [ ] **Step 5: Wire the service into `AppState`**

In `Scout/Shell/AppState.swift`:

Add the property after line 26 (`let scheduleEditService: ScheduleEditService`):

```swift
    let budgetSettingsService: BudgetSettingsService
```

Construct it after the `scheduleEditService` block (line 125), reusing the already-resolved scoutctl trio:

```swift
        let budgetSettings = BudgetSettingsService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
```

Assign it after line 192 (`self.scheduleEditService = scheduleEditService`):

```swift
        self.budgetSettingsService = budgetSettings
```

- [ ] **Step 6: Insert the section into `SettingsView`**

In `Scout/Shell/SettingsView.swift`, immediately after the closing brace of the `section(label: "Claude Code")` block and before `section(label: "Proposals")`, add:

```swift
                section(label: "Budget") {
                    BudgetSettingsSection()
                        .environmentObject(appState.budgetSettingsService)
                }
```

`SettingsView` has no `appState` yet, so add the environment object near the other property wrappers at the top of the struct:

```swift
    @EnvironmentObject var appState: AppState
```

`MainWindowView` already injects `AppState` into the whole detail hierarchy, so `SettingsView()` needs no change at its call site.

- [ ] **Step 7: Build, run the full suite, and verify in the real app**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS'`

Expected: PASS, whole suite.

Then launch the app and open Settings → Budget. Against the current vault expect: the four fields populated with `50` / `5` / `80` / `60`, the readout `5h window → $10.42 budget · sessions skip at $8.34`, and the amber advisory that no budget block exists. Confirm Save is disabled until a value changes, that entering `0` in the window field shows the range message and keeps Save disabled, and that no parity warning appears (it would mean `BudgetGate` disagrees with the engine).

Take a screenshot of the section for the PR.

- [ ] **Step 8: Commit**

```bash
git add Scout/Shell/BudgetSettingsSection.swift Scout/Shell/SettingsView.swift Scout/Shell/AppState.swift ScoutTests/Shell/BudgetSettingsSectionTests.swift
git commit -m "feat(settings): Budget section

Four knobs, an explicit Save (writes are subprocesses, not AppStorage),
and a live derived readout of the gate they compute to. Bounds mirror the
engine's so an out-of-range value never costs a subprocess.

Says out loud when a vault has no budget block and is therefore gating on
engine defaults — the state that had sessions skipping at \$8.34 per 5h
window with nothing in the UI to show it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Plan Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §5 `BudgetSettingsService` from AppState's scoutctl trio | Task 3, Task 4 Step 5 |
| §5 `load()` / `save()` over `budget show`/`set` | Task 3 |
| §5 app parses no YAML | Task 3 (Global Constraints; no YAML anywhere in this plan) |
| §5 Budget section between Claude Code and Proposals | Task 4 Step 6 |
| §5 four numeric fields | Task 4 |
| §5 explicit Save, not `@AppStorage` | Task 4 (`saveButton`) |
| §5 derived readout recomputed as you type | Task 4 (`gateLine`) + Task 1 |
| §5 `defaults` warning line, `legacy` note | Task 4 (`sourceAdvisory`) |
| §5 non-zero exit surfaces stderr | Task 3 (`invoke`), Task 4 (`saveError`) |
| §5 two-step rounding, parity check, disagreeing test case | Task 1, Task 3 (`publish`) |
| §5 extract the settings atoms | Task 2 |
| Testing: decode each source, save args, partial behavior, error surfacing | Task 3 |
| No spend figures anywhere | Task 4 (configuration only, by construction) |

**Type consistency:** `BudgetSettings` property names are used identically in Tasks 3 and 4 (`dailyUSD`, `windowHours`, `skipAtPct`, `failureBackoffMinutes`, `windowBudgetUSD`, `skipThresholdUSD`, `configPath`, `source`); the `CodingKeys` snake_case values match the exact key set the engine plan's `test_budget_show_json_emits_the_payload` asserts. `BudgetGate.derive(dailyUSD:windowHours:skipAtPct:)` and `matches(windowBudgetUSD:skipThresholdUSD:)` are called with those labels in both Task 3's `publish` and Task 4's `gateLine`. `BudgetDraft.parsed` returns the labelled tuple `(daily, hours, pct, backoff)`, consumed under those labels in `gateLine` and `save`.

**Cross-plan dependency:** every `scoutctl budget` invocation here requires Plan 1 merged. Running Task 3's tests does not (the runner is stubbed), but Task 4 Step 7's real-app verification does.

**Deliberate divergence from `ScheduleEditService`:** no mtime stale-check on the app side. The engine's `write_budget` owns that guard, because it is the only writer of the `budget:` block and holds the read-modify-write window. Duplicating it here would guard a window the app never has.
