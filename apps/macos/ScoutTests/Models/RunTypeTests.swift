import Testing
import Foundation
@testable import Scout

@Suite("RunType slot-key mapping")
struct RunTypeTests {
    @Test func morningBriefingMapsToMorningBriefing() {
        #expect(RunType(slotKey: "morning-briefing") == .morningBriefing)
    }

    @Test func weekendBriefingMapsToWeekendBriefing() {
        #expect(RunType(slotKey: "weekend-briefing") == .weekendBriefing)
    }

    @Test func allConsolidationSlotsMapToConsolidation() {
        #expect(RunType(slotKey: "morning-consolidation") == .consolidation)
        #expect(RunType(slotKey: "midday-consolidation") == .consolidation)
        #expect(RunType(slotKey: "afternoon-consolidation") == .consolidation)
        #expect(RunType(slotKey: "evening-consolidation") == .consolidation)
    }

    @Test func allDreamingSlotsMapToDreaming() {
        #expect(RunType(slotKey: "dreaming-evening") == .dreaming)
        #expect(RunType(slotKey: "dreaming-nightly") == .dreaming)
        #expect(RunType(slotKey: "dreaming-weekend-morning") == .dreaming)
    }

    @Test func researchMapsToResearch() {
        #expect(RunType(slotKey: "research") == .research)
    }

    @Test func unknownSlotKeyReturnsNil() {
        #expect(RunType(slotKey: "totally-made-up") == nil)
        #expect(RunType(slotKey: "") == nil)
        #expect(RunType(slotKey: "morning-briefing-extra") == nil)
    }

    @Test func displayNameCoversAllCases() {
        // All cases should produce a non-empty human-readable string.
        for type in RunType.allCases {
            #expect(!type.displayName.isEmpty)
        }
    }

    @Test func collapsedEnumHasExpectedCases() {
        // Plan 5 collapsed time-tagged variants. Lock the surface so a future
        // accidental re-introduction fails CI.
        let cases = Set(RunType.allCases.map(\.rawValue))
        #expect(cases == Set([
            "morningBriefing",
            "weekendBriefing",
            "consolidation",
            "dreaming",
            "research",
            "respond",
            "manual",
        ]))
    }
}

@Suite("UpcomingRun JSON contract decoding")
struct UpcomingRunDecodingTests {
    @Test func validSlotKeyAndUTCYieldUpcomingRun() {
        let upc = UpcomingRun(
            slotKey: "morning-briefing",
            slotType: "briefing",
            scheduledAtUTC: "2026-05-08T12:00:00Z"
        )
        #expect(upc != nil)
        #expect(upc?.type == .morningBriefing)
        #expect(upc?.slotKey == "morning-briefing")
        #expect(upc?.id == "morning-briefing-2026-05-08T12:00:00Z")
    }

    /// Only a `slot_type` this build doesn't know yields nil.
    @Test func unknownSlotTypeYieldsNil() {
        let upc = UpcomingRun(
            slotKey: "mystery-slot",
            slotType: "mystery",
            scheduledAtUTC: "2026-05-08T12:00:00Z"
        )
        #expect(upc == nil)
    }

    /// Finding #17: a slot made in the app (a key the table doesn't know)
    /// takes its type from `slot_type` instead of disappearing.
    @Test func unknownSlotKeyTakesItsTypeFromSlotType() {
        let cases: [(String, String, RunType)] = [
            ("late-research", "research", .research),
            ("focus-hour", "consolidation", .consolidation),
            ("deep-dreaming", "dreaming", .dreaming),
            ("noon-briefing", "briefing", .morningBriefing),
            ("sunday-weekend-briefing", "briefing", .weekendBriefing),
            ("on-demand", "manual", .manual),
        ]
        for (key, type, expected) in cases {
            let upc = UpcomingRun(slotKey: key, slotType: type, scheduledAtUTC: "2026-05-08T12:00:00Z")
            #expect(upc?.type == expected, "\(key)")
        }
    }

    /// A known key still wins over `slot_type`.
    @Test func knownSlotKeyWinsOverSlotType() {
        #expect(RunType(slotKey: "weekend-briefing", slotType: "briefing") == .weekendBriefing)
    }

    @Test func malformedTimestampYieldsNil() {
        let upc = UpcomingRun(
            slotKey: "morning-briefing",
            slotType: "briefing",
            scheduledAtUTC: "not-a-date"
        )
        #expect(upc == nil)
    }
}
