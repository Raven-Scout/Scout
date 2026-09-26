import Testing
@testable import Scout

@Suite("SchedulesView")
struct SchedulesViewTests {
    @Test("nextNewSlotKey bumps integer suffix on collision")
    func test_placeholderKey_collision_bumps_to_next_integer() {
        let existing = ["new-slot-1", "new-slot-2"]
        #expect(SchedulesView.nextNewSlotKey(existing: existing) == "new-slot-3")
    }

    @Test("nextNewSlotKey returns new-slot-1 when no collision")
    func test_placeholderKey_no_collision_starts_at_one() {
        #expect(SchedulesView.nextNewSlotKey(existing: []) == "new-slot-1")
        #expect(SchedulesView.nextNewSlotKey(existing: ["morning-briefing", "research"]) == "new-slot-1")
    }

    @Test("makeNewDraftSlot uses safe defaults")
    func test_makeNewDraftSlot_uses_safe_defaults() {
        let draft = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        #expect(draft.key == "new-slot-1")
        #expect(draft.type == .briefing)
        #expect(draft.runner == "run-scout.sh")
        #expect(draft.firesAtLocal == "09:00")
        #expect(draft.weekdays == ["Mon", "Tue", "Wed", "Thu", "Fri"])
        #expect(draft.onMiss == .fire)
        #expect(draft.cooldownMinutes == 60)
        #expect(draft.missedWindowHours == 4)
        #expect(draft.runtime == .local)
    }
}
