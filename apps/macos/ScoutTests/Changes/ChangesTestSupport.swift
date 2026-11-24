import Foundation
@testable import Scout

/// A `ChangesService` already loaded with the three contract fixtures, for view tests.
@MainActor
enum LoadedChangesService {
    static func make() async throws -> ChangesService {
        let runner = RuleBasedRunner()
        func fixture(_ name: String) throws -> String {
            String(decoding: try ChangesContractTests.data(name), as: UTF8.self)
        }
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: try fixture("changes-digest"))
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: try fixture("changes"))
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: try fixture("engagement"))
        let service = ChangesService(
            scoutctl: { URL(fileURLWithPath: "/usr/bin/scoutctl") }, runner: runner,
            logsDirectory: URL(fileURLWithPath: "/tmp/nonexistent-\(UUID().uuidString)"),
            pendingDirectory: URL(fileURLWithPath: "/tmp/nonexistent-\(UUID().uuidString)"),
            fileEvents: NoopFS(), clock: FixedClock(date: Date(timeIntervalSince1970: 1_800_000_000)))
        await service.refresh()
        return service
    }

    /// A decisions object that runs nothing (no engine).
    static func decisions() -> ChangesDecisions {
        ChangesDecisions(runner: RuleBasedRunner(), scoutctl: { nil })
    }
}
