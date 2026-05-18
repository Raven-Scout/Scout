import Testing
@testable import Scout

@Suite("SchedulesViewMode")
struct SchedulesViewModeTests {
    @Test("rawValue round-trip")
    func test_raw_value_round_trip() {
        for mode in SchedulesViewMode.allCases {
            #expect(SchedulesViewMode(rawValue: mode.rawValue) == mode)
        }
    }

    @Test("default is .table")
    func test_default_is_table() {
        #expect(SchedulesViewMode.default == .table)
    }

    @Test("allCases includes all three modes")
    func test_all_cases() {
        #expect(SchedulesViewMode.allCases.count == 3)
        #expect(SchedulesViewMode.allCases.contains(.table))
        #expect(SchedulesViewMode.allCases.contains(.cards))
        #expect(SchedulesViewMode.allCases.contains(.timeline))
    }

    @Test("isAvailable — all three modes are available after Scout.html parity revamp")
    func test_is_available() {
        #expect(SchedulesViewMode.table.isAvailable == true)
        #expect(SchedulesViewMode.cards.isAvailable == true)
        #expect(SchedulesViewMode.timeline.isAvailable == true)
    }
}
