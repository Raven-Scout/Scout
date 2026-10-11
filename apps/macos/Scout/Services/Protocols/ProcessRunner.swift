import Foundation

struct ProcessResult: Sendable {
    let exitCode: Int32
    let stdout: Data
    let stderr: Data
}

/// `nonisolated` so actors can conform: under the app target's default MainActor
/// isolation, Swift 6.4 (Xcode 27) otherwise infers the protocol as MainActor.
/// A `nonisolated` protocol also makes its conformers and their witnesses infer
/// `nonisolated` (SE-0449), so a witness that relied on the default MainActor
/// must now say `@MainActor` itself; see `SystemProcessRunner.run`.
nonisolated protocol ProcessRunner: Sendable {
    /// Run a process to completion and return its result.
    func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult
}
