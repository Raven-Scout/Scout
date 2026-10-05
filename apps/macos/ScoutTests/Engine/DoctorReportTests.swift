import Testing
import Foundation
@testable import Scout

@Suite("DoctorReport")
struct DoctorReportTests {
    @Test func parsesJsonFromDoctorDashJson() {
        let r = DoctorReport.parse(stdout: Data(#"{"severity": "yellow", "errors": [], "warnings": ["snapshot missing: x"]}"#.utf8),
                                   stderr: Data())
        #expect(r == DoctorReport(severity: .yellow, errors: [], warnings: ["snapshot missing: x"]))
    }

    /// Engines ≤ v0.11.x print `severity:` / `warning:` (and `note:`) lines to
    /// stdout but every `error:` line to **stderr** (`typer.echo(..., err=True)`)
    /// — the fixture splits the streams exactly the way the real engine does.
    @Test func parsesLegacyTextFromOlderEngines() {
        let stdout = "severity: red\nwarning: runner backup present: run-scout.sh.bak.1\nnote: vault is a git repo\n"
        let stderr = "error: launchd: com.scout.schedule-tick not registered\nerror: vault directory missing: /Users/alex/Scout\n"
        let r = DoctorReport.parse(stdout: Data(stdout.utf8), stderr: Data(stderr.utf8))
        #expect(r?.severity == .red)
        #expect(r?.errors == ["launchd: com.scout.schedule-tick not registered", "vault directory missing: /Users/alex/Scout"])
        #expect(r?.warnings == ["runner backup present: run-scout.sh.bak.1"])
    }

    @Test func garbageIsNil() {
        #expect(DoctorReport.parse(stdout: Data("Traceback…".utf8), stderr: Data()) == nil)
    }

    /// A traceback on stderr with no `severity:` line anywhere is still not a report.
    @Test func stderrAloneWithoutSeverityIsNil() {
        #expect(DoctorReport.parse(stdout: Data(), stderr: Data("error: something broke\n".utf8)) == nil)
    }
}
