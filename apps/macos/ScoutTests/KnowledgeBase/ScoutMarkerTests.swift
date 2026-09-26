import Foundation
import Testing
@testable import Scout

@Suite("ScoutMarker")
struct ScoutMarkerTests {
    @Test func formatWraps() {
        #expect(ScoutMarker.format("scope tags to people too?")
                == "//==<< scope tags to people too? >>==//")
    }
    @Test func formatCollapsesNewlinesAndTrims() {
        #expect(ScoutMarker.format("  line one \n  line two  ")
                == "//==<< line one line two >>==//")
    }
    @Test func formatRejectsEmpty() {
        #expect(ScoutMarker.format("   ") == nil)
        #expect(ScoutMarker.format("\n\n") == nil)
    }
    @Test func detectsMarkerLine() {
        #expect(ScoutMarker.isMarkerLine("//==<< hi >>==//"))
        #expect(ScoutMarker.isMarkerLine("   //==<< hi >>==//  "))   // leading/trailing space
        #expect(!ScoutMarker.isMarkerLine("a //==<< hi >>==// b"))   // not standalone
        #expect(!ScoutMarker.isMarkerLine("//==<< unterminated"))
        #expect(!ScoutMarker.isMarkerLine("plain text"))
    }
    @Test func extractsBody() {
        #expect(ScoutMarker.body(of: "//==<< scope to people? >>==//") == "scope to people?")
        #expect(ScoutMarker.body(of: "   //==<<   padded   >>==//  ") == "padded")
        #expect(ScoutMarker.body(of: "not a marker") == nil)
    }
    @Test func roundTrips() {
        let m = ScoutMarker.format("hello world")!
        #expect(ScoutMarker.isMarkerLine(m))
        #expect(ScoutMarker.body(of: m) == "hello world")
    }
}
