import Foundation

/// Abstraction over `/bin/launchctl`. Tests swap in a fake.
protocol LaunchctlClient: Sendable {
    /// Equivalent to `launchctl bootout gui/<uid> <path>`.
    /// Returns the raw exit code; callers decide whether a non-zero code
    /// is recoverable (exit 3 "not loaded" is typically swallowed).
    func bootout(userUid: uid_t, plistPath: URL) async throws -> Int32

    /// Equivalent to `launchctl bootstrap gui/<uid> <path>`.
    /// Throws `LaunchctlError.bootstrapFailed` on non-zero exit.
    func bootstrap(userUid: uid_t, plistPath: URL) async throws
}

enum LaunchctlError: Error, Equatable {
    case bootstrapFailed(exitCode: Int32, stderr: String)
}
