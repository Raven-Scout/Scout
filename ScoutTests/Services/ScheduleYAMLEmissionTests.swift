import Foundation
import Testing
@testable import Scout

/// `save` rewrites the canonical `schedule.yaml` from the in-memory slots.
/// These cover the emitter's optional fields and its scalar-quoting rules —
/// a runner name containing `:` or a leading `-` must round-trip as a YAML
/// string, not as a mapping or a list item.
@MainActor
@Suite("ScheduleEditService — YAML emission")
struct ScheduleYAMLEmissionTests {

    private func slot(
        key: String = "morning-briefing",
        type: SlotType = .briefing,
        runner: String = "run-scout.sh",
        firesAtLocal: String = "08:00",
        weekdays: [String] = ["Mon", "Tue"],
        budgetUsd: Double? = nil,
        tz: String? = nil,
        runtime: SlotRuntime = .local
    ) -> Slot {
        Slot(key: key, type: type, runner: runner, firesAtLocal: firesAtLocal,
             weekdays: weekdays, missedWindowHours: 4, onMiss: .fire,
             cooldownMinutes: 60, budgetUsd: budgetUsd, tz: tz, runtime: runtime)
    }

    /// Save `slots` and hand back the canonical file's new contents.
    private func emit(_ slots: [Slot]) async throws -> String {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("yaml-emit-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let canonical = dir.appendingPathComponent("schedule.yaml")
        try """
        # Header comment
        schema_version: 1

        slots:
          placeholder:
            type: briefing
        """.write(to: canonical, atomically: true, encoding: .utf8)

        let listJSON = "[]"
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: QueueProcessRunner(stdouts: [listJSON, listJSON]),
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"])

        _ = try await service.loadAll()
        try await service.save(allSlots: slots)
        return try String(contentsOf: canonical, encoding: .utf8)
    }

    // MARK: - required fields

    @Test("a plain slot emits every required field")
    func emits_requiredFields() async throws {
        let yaml = try await emit([slot()])
        #expect(yaml.contains("  morning-briefing:\n"))
        #expect(yaml.contains("    type: briefing\n"))
        #expect(yaml.contains("    runner: run-scout.sh\n"))
        #expect(yaml.contains("    fires_at_local: \"08:00\"\n"))
        #expect(yaml.contains("    weekdays: [Mon, Tue]\n"))
        #expect(yaml.contains("    missed_window_hours: 4\n"))
        #expect(yaml.contains("    on_miss: fire\n"))
        #expect(yaml.contains("    cooldown_minutes: 60\n"))
        #expect(yaml.contains("    runtime: local\n"))
    }

    @Test("the header above `slots:` is preserved")
    func emits_preservesHeader() async throws {
        let yaml = try await emit([slot()])
        #expect(yaml.contains("# Header comment"))
        #expect(yaml.contains("schema_version: 1"))
    }

    // MARK: - optional fields

    @Test("budget and tz are omitted when nil")
    func emits_omitsNilOptionalFields() async throws {
        let yaml = try await emit([slot(budgetUsd: nil, tz: nil)])
        #expect(!yaml.contains("budget_usd:"))
        #expect(!yaml.contains("tz:"))
    }

    @Test("budget is emitted when set")
    func emits_budgetWhenPresent() async throws {
        let yaml = try await emit([slot(budgetUsd: 2.5)])
        #expect(yaml.contains("    budget_usd: 2.5\n"))
    }

    @Test("tz is emitted quoted when set")
    func emits_timeZoneQuotedWhenPresent() async throws {
        let yaml = try await emit([slot(tz: "America/New_York")])
        #expect(yaml.contains("    tz: \"America/New_York\"\n"))
    }

    @Test("both optional fields can be emitted together")
    func emits_bothOptionalFields() async throws {
        let yaml = try await emit([slot(budgetUsd: 0.75, tz: "UTC")])
        #expect(yaml.contains("budget_usd: 0.75"))
        #expect(yaml.contains("tz: \"UTC\""))
    }

    // MARK: - scalar quoting

    @Test("a runner containing a colon is quoted so it isn't read as a mapping")
    func emits_quotesRunnerWithColon() async throws {
        let yaml = try await emit([slot(runner: "run:scout.sh")])
        #expect(yaml.contains("    runner: \"run:scout.sh\"\n"))
    }

    @Test("a runner starting with a dash is quoted so it isn't read as a list item")
    func emits_quotesRunnerWithLeadingDash() async throws {
        let yaml = try await emit([slot(runner: "-run-scout.sh")])
        #expect(yaml.contains("    runner: \"-run-scout.sh\"\n"))
    }

    @Test("a runner containing a comment marker is quoted")
    func emits_quotesRunnerWithHash() async throws {
        let yaml = try await emit([slot(runner: "run.sh #1")])
        #expect(yaml.contains("    runner: \"run.sh #1\"\n"))
    }

    @Test("a runner starting with a bracket or brace is quoted")
    func emits_quotesRunnerWithFlowIndicators() async throws {
        #expect(try await emit([slot(runner: "[run]")]).contains("runner: \"[run]\""))
        #expect(try await emit([slot(runner: "{run}")]).contains("runner: \"{run}\""))
    }

    @Test("an ordinary runner name is left unquoted")
    func emits_leavesPlainRunnerUnquoted() async throws {
        let yaml = try await emit([slot(runner: "run-scout.sh")])
        #expect(yaml.contains("    runner: run-scout.sh\n"))
    }

    @Test("embedded quotes and backslashes are escaped")
    func emits_escapesQuotesAndBackslashes() async throws {
        let yaml = try await emit([slot(runner: #"run:"a\b".sh"#)])
        #expect(yaml.contains(#"runner: "run:\"a\\b\".sh""#))
    }

    // MARK: - multiple slots

    @Test("every slot is emitted, in order")
    func emits_allSlotsInOrder() async throws {
        let yaml = try await emit([
            slot(key: "morning-briefing", type: .briefing),
            slot(key: "research", type: .research, runner: "run-research.sh"),
            slot(key: "dreaming-nightly", type: .dreaming, runner: "run-dreaming.sh"),
        ])
        let morning = try #require(yaml.range(of: "  morning-briefing:"))
        let research = try #require(yaml.range(of: "  research:"))
        let dreaming = try #require(yaml.range(of: "  dreaming-nightly:"))
        #expect(morning.lowerBound < research.lowerBound)
        #expect(research.lowerBound < dreaming.lowerBound)
    }

    @Test("an empty weekday list emits an empty flow sequence")
    func emits_emptyWeekdays() async throws {
        let yaml = try await emit([slot(weekdays: [])])
        #expect(yaml.contains("    weekdays: []\n"))
    }
}
