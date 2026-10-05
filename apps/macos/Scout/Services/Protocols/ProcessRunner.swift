import Foundation

struct ProcessResult: Sendable {
    let exitCode: Int32
    let stdout: Data
    let stderr: Data
}

protocol ProcessRunner: Sendable {
    /// Run a process to completion and return its result.
    func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult
}
