import Testing
@testable import Scout

@Suite("SchedulesFilterMode")
struct SchedulesFilterModeTests {

    static func slot(_ key: String, type: SlotType) -> Slot {
        Slot(
            key: key,
            type: type,
            runner: "run-scout.sh",
            firesAtLocal: "08:00",
            weekdays: ["Mon"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60
        )
    }

    static let mixed: [Slot] = [
        slot("morning-briefing", type: .briefing),
        slot("morning-consolidation", type: .consolidation),
        slot("midday-consolidation", type: .consolidation),
        slot("dreaming-evening", type: .dreaming),
        slot("research", type: .research),
    ]

    @Test(".all is passthrough")
    func test_all_passthrough() {
        let filtered = SchedulesFilterMode.all.apply(to: Self.mixed)
        #expect(filtered.count == Self.mixed.count)
        #expect(filtered.map(\.key) == Self.mixed.map(\.key))
    }

    @Test(".type filters to that type only")
    func test_type_filter() {
        let consolidations = SchedulesFilterMode.type(.consolidation).apply(to: Self.mixed)
        #expect(consolidations.count == 2)
        #expect(consolidations.allSatisfy { $0.type == .consolidation })
    }

    @Test("count returns correct count per type")
    func test_count_per_type() {
        #expect(SchedulesFilterMode.count(of: .briefing,      in: Self.mixed) == 1)
        #expect(SchedulesFilterMode.count(of: .consolidation, in: Self.mixed) == 2)
        #expect(SchedulesFilterMode.count(of: .dreaming,      in: Self.mixed) == 1)
        #expect(SchedulesFilterMode.count(of: .research,      in: Self.mixed) == 1)
        #expect(SchedulesFilterMode.count(of: .manual,        in: Self.mixed) == 0)
    }
}
