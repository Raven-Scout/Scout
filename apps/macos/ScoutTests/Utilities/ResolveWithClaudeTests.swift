import Foundation
import Testing
@testable import Scout

/// "Resolve with Claude" on a problem banner: when it shows, what it says, and where a failed
/// prompt goes.
@Suite("Resolve with Claude")
@MainActor
struct ResolveWithClaudeTests {
    private func service() -> ResolvePromptService {
        ResolvePromptService(scoutctl: URL(fileURLWithPath: "/opt/scout/bin/scoutctl"), runner: RuleBasedRunner(),
                             environment: [:], scoutDirectory: URL(fileURLWithPath: "/Users/alex/Scout"))
    }

    @Test func titleIsResolveWithClaude() {
        #expect(ResolveWithClaudeButton.title == "Resolve with Claude")
    }

    @Test func asyncPromptFailureSetsLaunchError() {
        #expect(LaunchClaudeMenu.launchErrorText(for: ResolvePromptError.wrongFolder)
                == ResolvePromptError.wrongFolder.errorDescription)
        #expect(LaunchClaudeMenu.launchErrorText(for: ResolvePromptError.engineFailed("no such run log"))
                == "Couldn't build the prompt: no such run log")
    }

    @Test func problemViewShowsButtonOnlyWithSubjectAndService() {
        let finding = RunHealth.Finding(id: "runs.failing", severity: "red", since: nil, summary: "Runs fail.",
                                        fix: "", source: "test", until: nil)
        let withSubject = Problem(finding: finding)
        let without = Problem.engineUnavailable("Sessions", details: nil)
        #expect(ProblemView.showsResolve(problem: withSubject, service: service()))
        #expect(!ProblemView.showsResolve(problem: without, service: service()))
        #expect(!ProblemView.showsResolve(problem: withSubject, service: nil))
    }
}
