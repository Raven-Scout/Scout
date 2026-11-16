import Testing
import Foundation
@testable import Scout

@Suite("ModelChoice")
struct ModelChoiceTests {

    @Test("a slot's model string maps onto the picker and back")
    func roundTrip() {
        let cases: [(String?, ModelChoice)] = [
            (nil, .inherit), ("", .inherit),
            ("opus", .opus), ("Sonnet", .sonnet), ("haiku", .haiku),
            ("claude-sonnet-5-5", .custom("claude-sonnet-5-5")),
        ]
        for (model, choice) in cases {
            #expect(ModelChoice(model: model) == choice)
        }
        #expect(ModelChoice.inherit.model == nil)
        #expect(ModelChoice.sonnet.model == "sonnet")
        #expect(ModelChoice.custom("claude-opus-5-5").model == "claude-opus-5-5")
    }

    @Test("the slot picker labels the inherit option with the default")
    func slotOptions() {
        let options = ModelChoice.slotOptions(defaultModel: "opus", current: .inherit)
        #expect(options.map(\.label) == ["Default (Opus)", "Opus", "Sonnet", "Haiku"])
        #expect(options.map(\.value) == [.inherit, .opus, .sonnet, .haiku])
    }

    @Test("a hand-written full ID stays selectable, so saving keeps it")
    func customIdIsOffered() {
        let current = ModelChoice(model: "claude-sonnet-5-5")
        let options = ModelChoice.slotOptions(defaultModel: "sonnet", current: current)
        #expect(options.first?.label == "Default (Sonnet)")
        #expect(options.last?.label == "claude-sonnet-5-5")
        #expect(options.last?.value == current)
    }

    @Test("the Settings default picker has no inherit option")
    func defaultOptions() {
        #expect(ModelChoice.defaultOptions(current: .opus).map(\.value) == [.opus, .sonnet, .haiku])
        #expect(ModelChoice.defaultOptions(current: .custom("claude-x-1")).count == 4)
    }

    @Test("picking a model in a slot draft makes it dirty and round-trips")
    func draftDirtiness() {
        let live = Slot(key: "midday-consolidation", type: .consolidation, runner: "run-scout.sh",
                        firesAtLocal: "13:00", weekdays: ["Mon"], missedWindowHours: 2,
                        onMiss: .collapse, cooldownMinutes: 90)
        var draft = SlotDraft(from: live)
        #expect(!draft.isDirty(against: live))
        draft.model = ModelChoice.sonnet.model
        #expect(draft.isDirty(against: live))
        #expect(draft.toSlot().model == "sonnet")
        draft.model = ModelChoice.inherit.model
        #expect(!draft.isDirty(against: live))
    }
}
