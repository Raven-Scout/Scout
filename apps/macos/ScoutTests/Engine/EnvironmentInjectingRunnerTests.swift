import Testing
import Foundation
@testable import Scout

@Suite("EnvironmentInjectingRunner")
struct EnvironmentInjectingRunnerTests {
    @Test func injectsExtraAndLetsCallSiteWin() async throws {
        let inner = RuleBasedRunner()
        inner.on({ _, _ in true }) { _, _, _ in ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()) }
        let runner = EnvironmentInjectingRunner(base: inner, extra: ["SCOUT_DATA_DIR": "/vault", "A": "base"])
        _ = try await runner.run(executable: URL(fileURLWithPath: "/bin/true"), arguments: [], environment: ["A": "call"], workingDirectory: nil)
        #expect(inner.calls[0].environment == ["SCOUT_DATA_DIR": "/vault", "A": "call"])
    }
}
