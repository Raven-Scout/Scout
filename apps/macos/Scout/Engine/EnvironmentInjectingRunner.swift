import Foundation

/// Adds a fixed environment to every process the app spawns — today that is
/// `SCOUT_DATA_DIR`, so a non-default vault root reaches every `scoutctl`
/// call without touching each call site (spec §4.4). Call-site values win.
nonisolated struct EnvironmentInjectingRunner: ProcessRunner {
    let base: any ProcessRunner
    let extra: [String: String]

    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        try await base.run(executable: executable, arguments: arguments,
                           environment: extra.merging(environment) { _, callSite in callSite },
                           workingDirectory: workingDirectory)
    }
}
