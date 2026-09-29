import Testing
import Foundation
@testable import Scout

@Suite("DoctorReport")
struct DoctorReportTests {
    @Test func parsesJsonFromDoctorDashJson() {
        let r = DoctorReport.parse(stdout: Data(#"{"severity": "yellow", "errors": [], "warnings": ["snapshot missing: x"]}"#.utf8))
        #expect(r == DoctorReport(severity: .yellow, errors: [], warnings: ["snapshot missing: x"]))
    }

    @Test func parsesLegacyTextFromOlderEngines() {
        let text = "severity: red\nwarning: runner backup present: run-scout.sh.bak.1\nerror: launchd: com.scout.schedule-tick not registered\n"
        let r = DoctorReport.parse(stdout: Data(text.utf8))
        #expect(r?.severity == .red)
        #expect(r?.errors == ["launchd: com.scout.schedule-tick not registered"])
        #expect(r?.warnings == ["runner backup present: run-scout.sh.bak.1"])
    }

    @Test func garbageIsNil() {
        #expect(DoctorReport.parse(stdout: Data("Traceback…".utf8)) == nil)
    }
}
