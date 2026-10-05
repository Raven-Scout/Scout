import Testing
import Foundation
@testable import Scout

@Suite("Run model")
struct RunTests {
    @Test func runIdIsDerivedFromTypeAndStartTime() {
        let started = Date(timeIntervalSince1970: 1_713_456_180)
        let run = Run.make(type: .morningBriefing, startedAt: started)
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        #expect(run.id == "morningBriefing-\(iso.string(from: started))")
    }

    @Test func runsWithSameIdAreEqual() {
        let started = Date(timeIntervalSince1970: 1_713_456_180)
        let a = Run.make(type: .morningBriefing, startedAt: started)
        let b = Run.make(type: .morningBriefing, startedAt: started)
        #expect(a == b)
    }
}
