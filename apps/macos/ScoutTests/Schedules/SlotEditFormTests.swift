import Testing
@testable import Scout

@Suite("SlotEditForm")
struct SlotEditFormTests {
    static let sampleSlot = Slot(
        key: "morning-briefing",
        type: .briefing,
        runner: "run-scout.sh",
        firesAtLocal: "08:00",
        weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
        missedWindowHours: 4,
        onMiss: .fire,
        cooldownMinutes: 60
    )

    @Test("validateSlotKey accepts kebab-case identifiers")
    func test_validate_slot_key_kebab_case() {
        #expect(SlotDraft.validateSlotKey("morning-briefing") == nil)
        #expect(SlotDraft.validateSlotKey("a") == nil)
        #expect(SlotDraft.validateSlotKey("a1-b2") == nil)
        #expect(SlotDraft.validateSlotKey("") != nil)
        #expect(SlotDraft.validateSlotKey("MorningBriefing") != nil)
        #expect(SlotDraft.validateSlotKey("has space") != nil)
        #expect(SlotDraft.validateSlotKey("-leading-dash") != nil)
        #expect(SlotDraft.validateSlotKey("1-leading-digit") != nil)
    }

    @Test("validateFiresAtLocal accepts HH:MM in 24-hour format")
    func test_validate_fires_at_local_HH_MM() {
        #expect(SlotDraft.validateFiresAtLocal("00:00") == nil)
        #expect(SlotDraft.validateFiresAtLocal("23:59") == nil)
        #expect(SlotDraft.validateFiresAtLocal("08:30") == nil)
        #expect(SlotDraft.validateFiresAtLocal("25:00") != nil)
        #expect(SlotDraft.validateFiresAtLocal("08:60") != nil)
        #expect(SlotDraft.validateFiresAtLocal("8:00") != nil)   // need leading zero
        #expect(SlotDraft.validateFiresAtLocal("") != nil)
        #expect(SlotDraft.validateFiresAtLocal("not a time") != nil)
    }

    @Test("validateWeekdays requires at least one day")
    func test_validate_weekdays_at_least_one() {
        #expect(SlotDraft.validateWeekdays(["Mon"]) == nil)
        #expect(SlotDraft.validateWeekdays(["Mon", "Tue", "Wed", "Thu", "Fri"]) == nil)
        #expect(SlotDraft.validateWeekdays([]) != nil)
    }

    @Test("isDirty returns true when any field differs from live")
    func test_draft_is_dirty_when_any_field_differs_from_live() {
        let live = Self.sampleSlot
        var draft = SlotDraft(from: live)
        #expect(!draft.isDirty(against: live))
        draft.cooldownMinutes = 999
        #expect(draft.isDirty(against: live))
    }

    @Test("firstError returns non-nil when validation fails")
    func test_first_error_when_validation_fails() {
        var draft = SlotDraft(from: Self.sampleSlot)
        draft.firesAtLocal = "25:00"
        #expect(draft.firstError != nil)
    }

    @Test("firstError returns nil when all fields valid")
    func test_first_error_when_all_clean() {
        let draft = SlotDraft(from: Self.sampleSlot)
        #expect(draft.firstError == nil)
    }

    @Test("requiresTypeChangeConfirmation triggers when draft.type != live.type")
    func test_typeChange_triggers_confirmation_path() {
        var draft = SlotDraft(from: Self.sampleSlot)
        #expect(SlotEditForm.requiresTypeChangeConfirmation(draft: draft, live: Self.sampleSlot) == false)
        draft.type = .consolidation
        #expect(SlotEditForm.requiresTypeChangeConfirmation(draft: draft, live: Self.sampleSlot) == true)
    }
}
