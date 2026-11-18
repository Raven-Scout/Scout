// ScoutTests/ActionItems/PreambleSplitTests.swift
import Testing
@testable import Scout

@Suite("PreambleSplit")
struct PreambleSplitTests {
    @Test func aTimeInTheHeadlineIsNotASentenceEnd() {
        // UI audit R5: this read "Rewritten by the 09".
        let s = PreambleSplit.split("Rewritten by the 09:03 run: two items moved to Today.")
        #expect(s.headline == "Rewritten by the 09:03 run")
        #expect(s.body == "two items moved to Today.")
    }

    @Test func aVersionDotIsNotASentenceEnd() {
        let s = PreambleSplit.split("Upgraded to 0.18.16. Nothing else changed.")
        #expect(s.headline == "Upgraded to 0.18.16")
        #expect(s.body == "Nothing else changed.")
    }

    @Test func aLeadingBoldSpanIsTheHeadline() {
        let s = PreambleSplit.split("**This run's headline at 14:05:** three things need you — read them first.")
        #expect(s.headline == "This run's headline at 14:05:")
        #expect(s.body == "three things need you — read them first.")
        let quiet = PreambleSplit.split("**Quiet day.** Nothing new.")
        #expect(quiet.headline == "Quiet day.")
        #expect(quiet.body == "Nothing new.")
    }

    @Test func aParagraphWithNoBoundaryIsAllHeadline() {
        let none = PreambleSplit.split("Nothing new at 07:30")
        #expect(none.headline == "Nothing new at 07:30")
        #expect(none.body == "")
        let end = PreambleSplit.split("Ends here.")
        #expect(end.headline == "Ends here")
        #expect(end.body == "")
    }

    @Test func anAbbreviationOrAnEllipsisIsNotASentenceEnd() {
        let eg = PreambleSplit.split("Two items moved, e.g. the demo prep. The rest waits.")
        #expect(eg.headline == "Two items moved, e.g. the demo prep")
        #expect(eg.body == "The rest waits.")
        #expect(PreambleSplit.split("Wait... ok").headline == "Wait... ok")
        #expect(PreambleSplit.split("Wait… ok").headline == "Wait… ok")
        #expect(PreambleSplit.split("Plan vs. actual (cf. last week)").headline == "Plan vs. actual (cf. last week)")
    }

    @Test func quoteAndCalloutMarkersAreNotText() {
        #expect(PreambleSplit.split("> [!info] Rewritten at 09:03: see below").headline == "Rewritten at 09:03")
        let quoted = PreambleSplit.split("> **Moved.** Two items.")
        #expect(quoted.headline == "Moved.")
        #expect(quoted.body == "Two items.")
    }
}
