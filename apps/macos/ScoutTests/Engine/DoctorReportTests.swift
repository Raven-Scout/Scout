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

    // MARK: run_health (P3)

    private final class FixtureAnchor {}

    private static func sharedFixture() throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "doctor-run-health", withExtension: "json"))
        return try Data(contentsOf: url)
    }

    @Test func decodesRunHealthFromSharedFixture() throws {
        let r = DoctorReport.parse(stdout: try Self.sharedFixture(), stderr: Data())
        #expect(r?.severity == .yellow)                       // install severity is independent
        #expect(r?.runHealth?.severity == .red)
        #expect(r?.runHealth?.findings.first?.id == "auth.expired")
        #expect(r?.runHealth?.findings.count == 4)
        #expect(r?.runHealth?.findings.last?.id == "update.last")
        #expect(r?.runHealth?.findings.last?.severity == "note")
        #expect(r?.runHealth?.slots.count == 2)
        #expect(r?.runHealth?.slots.first?.lastFire == "2026-10-09T06:00:58Z")
        #expect(r?.runHealth?.slots.last?.lastFire == "2026-10-07T14:00:00Z")
        #expect(r?.runHealth?.lastOkAt == "2026-10-07T08:01:00Z")
        #expect(r?.runHealth?.evaluatedAt == "2026-10-11T12:00:00Z")
        #expect(r?.runHealth?.firstActionable?.id == "auth.expired")
        // The failing run's own error line and its log (#445).
        #expect(r?.runHealth?.firstActionable?.detail == "OAuth token has expired")
        #expect(r?.runHealth?.firstActionable?.log == "/Users/alex/Scout/.scout-logs/scout-2026-10-09_06-00.log")
        #expect(r?.runHealth?.firstActionable?.until == nil)
        #expect(r?.runHealth?.findings.last?.detail == nil)
    }

    @Test func decodesAFindingFromAnEngineOlderThanItsErrorLine() {
        let json = #"""
        {"severity": "green", "errors": [], "warnings": [], "run_health": {"severity": "yellow",
         "evaluated_at": "2026-10-11T12:00:00Z", "last_ok_at": null, "slots": [],
         "findings": [{"id": "runs.failing", "severity": "yellow", "since": null, "summary": "s", "fix": "f", "source": "ledger"}]}}
        """#
        let f = DoctorReport.parse(stdout: Data(json.utf8), stderr: Data())?.runHealth?.firstActionable
        #expect(f?.id == "runs.failing")
        #expect(f?.detail == nil && f?.log == nil && f?.until == nil)
    }

    @Test func decodesWithoutRunHealth() {
        let r = DoctorReport.parse(stdout: Data(#"{"severity": "green", "errors": [], "warnings": []}"#.utf8), stderr: Data())
        #expect(r?.severity == .green)
        #expect(r?.runHealth == nil)
    }

    @Test func legacyTextHasNoRunHealth() {
        let r = DoctorReport.parse(stdout: Data("severity: yellow\nwarning: w\n".utf8), stderr: Data())
        #expect(r?.severity == .yellow)
        #expect(r?.runHealth == nil)
    }

    /// A future engine may send a severity this app has never heard of; that must
    /// cost only the run-health block, never the whole install report.
    @Test func unknownRunHealthSeverityKeepsInstallHealth() {
        let json = #"{"severity": "red", "errors": ["e"], "warnings": [], "run_health": {"severity": "purple", "evaluated_at": "x", "last_ok_at": null, "findings": [], "slots": []}}"#
        let r = DoctorReport.parse(stdout: Data(json.utf8), stderr: Data())
        #expect(r?.severity == .red)
        #expect(r?.errors == ["e"])
        #expect(r?.runHealth == nil)
    }

    /// A run_health whose shape changed (an object where an array belongs) costs
    /// only the run-health block; the install report decodes as before.
    @Test func structurallyBrokenRunHealthKeepsInstallHealth() {
        let json = #"{"severity": "yellow", "errors": [], "warnings": ["w"], "run_health": {"severity": "red", "evaluated_at": "2026-10-11T12:00:00Z", "last_ok_at": null, "findings": {}, "slots": []}}"#
        let r = DoctorReport.parse(stdout: Data(json.utf8), stderr: Data())
        #expect(r?.severity == .yellow)
        #expect(r?.errors == [])
        #expect(r?.warnings == ["w"])
        #expect(r?.runHealth == nil)
    }

    @Test func unknownKeysAndNullsAreTolerated() {
        let json = #"{"severity": "green", "errors": [], "warnings": [], "future": 1, "run_health": {"severity": "green", "evaluated_at": "2026-10-11T12:00:00Z", "last_ok_at": null, "new_key": {}, "findings": [{"id": "n", "severity": "note", "since": null, "summary": "s", "fix": "f", "source": "x", "extra": 1}], "slots": [{"key": "k", "type": "t", "last_fire": null, "last_outcome": null}]}}"#
        let r = DoctorReport.parse(stdout: Data(json.utf8), stderr: Data())
        #expect(r?.runHealth?.severity == .green)
        #expect(r?.runHealth?.lastOkAt == nil)
        #expect(r?.runHealth?.slots.first?.lastFire == nil)
        #expect(r?.runHealth?.firstActionable == nil)   // a note is never actionable
    }
}
