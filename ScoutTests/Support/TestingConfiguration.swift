import Foundation
@testable import Scout

extension AppState.Configuration {
    /// An inert configuration for tests: a caller-supplied vault directory, a
    /// process runner that succeeds with empty output, an event source that
    /// never fires, a private defaults suite, and no background work.
    ///
    /// Lives in the test target: nothing here ships in the app, and no
    /// `#Preview` exists to want it there. The fakes are the ones the rest of
    /// the target already uses (`StubRunner`, `NoopFS`).
    static func testing(
        scoutDirectory: URL,
        runner: any ProcessRunner = StubRunner(
            result: ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
        ),
        // A fresh suite per graph: nothing to wipe, nothing shared between
        // suites running in parallel, and no plist is ever written because
        // nothing stores a value in it.
        defaults: UserDefaults = UserDefaults(suiteName: "scout.tests.\(UUID().uuidString)")!
    ) -> AppState.Configuration {
        AppState.Configuration(
            scoutDirectory: scoutDirectory,
            runner: runner,
            fileEvents: NoopFS(),
            scoutctl: AppState.ScoutctlInvocation(
                executable: URL(fileURLWithPath: "/usr/bin/false"),
                argsPrefix: []
            ),
            defaults: defaults,
            claudeSessionsDirectory: scoutDirectory.appendingPathComponent(".claude-projects"),
            startsBackgroundWork: false
        )
    }
}
