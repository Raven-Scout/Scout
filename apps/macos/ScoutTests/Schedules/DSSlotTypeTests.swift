import Testing
import SwiftUI
@testable import Scout

@Suite("DS.SlotType")
struct DSSlotTypeTests {

    @Test("color(for:) returns the namespace constant for each SlotType")
    @MainActor
    func test_color_for_each_slot_type() {
        #expect(DS.SlotType.color(for: .briefing)      == DS.SlotType.briefing)
        #expect(DS.SlotType.color(for: .consolidation) == DS.SlotType.consolidation)
        #expect(DS.SlotType.color(for: .dreaming)      == DS.SlotType.dreaming)
        #expect(DS.SlotType.color(for: .research)      == DS.SlotType.research)
        #expect(DS.SlotType.color(for: .manual)        == DS.SlotType.manual)
    }

    @Test("All 5 SlotType cases have distinct colors")
    @MainActor
    func test_all_colors_distinct() {
        let all: [Color] = [
            DS.SlotType.briefing,
            DS.SlotType.consolidation,
            DS.SlotType.dreaming,
            DS.SlotType.research,
            DS.SlotType.manual,
        ]
        let unique = Set(all.map { String(describing: $0) })
        #expect(unique.count == all.count)
    }
}
