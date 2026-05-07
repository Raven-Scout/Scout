import Testing
@testable import Scout

@Suite("SlotSummaryRow")
struct SlotSummaryRowTests {
    @Test("renders slot key, type, time, and MTWThF weekdays")
    @MainActor
    func test_renders_mtwthf_weekday_shortlist() {
        let slot = Slot(
            key: "morning-briefing",
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "08:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60
        )
        let row = SlotSummaryRow(slot: slot, hasDirtyDraft: false, isExpanded: false)
        #expect(row.summary == "morning-briefing · briefing · 08:00 MTWThF")
    }

    @Test("renders SaSu weekday shortlist for weekend slots")
    @MainActor
    func test_renders_sasu_weekday_shortlist() {
        let slot = Slot(
            key: "weekend-briefing",
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "08:30",
            weekdays: ["Sat", "Sun"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60
        )
        let row = SlotSummaryRow(slot: slot, hasDirtyDraft: false, isExpanded: false)
        #expect(row.summary == "weekend-briefing · briefing · 08:30 SaSu")
    }
}
