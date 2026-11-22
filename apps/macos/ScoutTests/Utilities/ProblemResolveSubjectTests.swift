import Foundation
import Testing
@testable import Scout

/// Which problems "Resolve with Claude" can investigate: findings, connector alerts, and run
/// problems at `.error` or `.warning`. Routine `.info` skips and every other factory get none.
/// An extension of `ProblemTests`, so `-only-testing:ScoutTests/ProblemTests` runs these too.
extension ProblemTests {
    private func finding(_ id: String, severity: String = "red") -> RunHealth.Finding {
        RunHealth.Finding(id: id, severity: severity, since: nil, summary: "Something for \(id).",
                          fix: "Fix it.", source: "test", until: nil)
    }

    @Test func findingProblemHasSubject() {
        let f = finding("run.integrity_restored")
        #expect(Problem(finding: f).resolveSubject == .finding("run.integrity_restored"))
    }

    @Test func plainProblemKeepsSubject() {
        let f = finding("slot.stale:morning-briefing", severity: "yellow")
        #expect(RunOutcomeDisplay.plainProblem(f).resolveSubject == .finding("slot.stale:morning-briefing"))
    }

    @Test func subjectIsNotPartOfTheID() {
        var p = Problem(finding: finding("runs.failing"))
        let id = p.id
        p.resolveSubject = nil
        #expect(p.id == id)
    }

    @Test func connectorProblemHasSubject() {
        let alert = ConnectorAlert(ts: Date(), level: .critical, connector: "mcp:example_tracker",
                                   reason: "3 errors in a row", firstSeen: Date())
        #expect(Problem(connectorAlert: alert).resolveSubject == .connector("mcp:example_tracker"))
    }

    @Test func connectorProblemPassesTheRawKey() throws {
        // A legacy key the app shows under its canonical name: the engine matches the file's own key.
        let json = #"""
        {"schema_version": 1, "alerts": [{"connector_key": "mcp:plugin_slack_slack", "level": "WARNING",
          "name": "Slack", "reason": "slow", "ts": "2026-11-19T17:09:00Z", "first_seen": "2026-11-19T17:09:00Z"}]}
        """#
        let alert = try #require(ConnectorAlert.parse(Data(json.utf8)).first)
        #expect(alert.connector == "mcp:claude_ai_Slack")
        #expect(Problem(connectorAlert: alert).resolveSubject == .connector("mcp:plugin_slack_slack"))
    }

    @Test(arguments: ["failure", "timeout", "orphaned", "rateLimited", "skippedGates", "skippedSandbox",
                      "skippedRuntime", "skippedLease"])
    func runProblemHasSubject(_ raw: String) throws {
        let status = try #require(RunStatus(rawValue: raw))
        let problem = try #require(Problem(run: .make(status: status)))
        #expect(problem.severity >= .warning)
        #expect(problem.resolveSubject == .run(logName: "fake.log"))
    }

    @Test(arguments: ["skippedBudget", "skippedConcurrency", "skippedRedundant"])
    func infoSkipHasNoSubject(_ raw: String) throws {
        let status = try #require(RunStatus(rawValue: raw))
        let problem = try #require(Problem(run: .make(status: status)))
        #expect(problem.severity == .info)
        #expect(problem.resolveSubject == nil)
    }

    @Test func otherFactoriesHaveNoSubject() {
        #expect(Problem.engineUnavailable("Sessions", details: nil).resolveSubject == nil)
        #expect(Problem.engineTooOld("Sessions", details: nil).resolveSubject == nil)
        #expect(Problem.connectorRosterMissing(details: nil).resolveSubject == nil)
        #expect(Problem(failureClass: "oauth_expired").resolveSubject == nil)
        #expect(Problem(runStatus: "failure", runName: "Research")?.resolveSubject == nil)
    }
}
