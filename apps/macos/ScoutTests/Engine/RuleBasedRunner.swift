import Foundation
@testable import Scout

/// A `ProcessRunner` that answers by rules and records every call. Rules are
/// tried in order; the first predicate match answers. Unmatched calls throw
/// ENOENT, which is what a missing executable produces in production.
///
/// Named `RuleBasedRunner` (not `ScriptedRunner`) to avoid colliding with the
/// sequential, `init(scripted:)` double of the same brief-suggested name
/// already defined in `ScoutTests/Services/GitServiceCommitPathsTests.swift`.
final class RuleBasedRunner: ProcessRunner, @unchecked Sendable {
    typealias Responder = (URL, [String], [String: String]) async throws -> ProcessResult
    private var rules: [((URL, [String]) -> Bool, Responder)] = []
    private let lock = NSLock()
    private(set) var calls: [(executable: URL, arguments: [String], environment: [String: String])] = []

    func on(_ predicate: @escaping (URL, [String]) -> Bool, _ respond: @escaping Responder) {
        lock.withLock { rules.append((predicate, respond)) }
    }

    /// Convenience: match on the executable's last path component + a leading argument prefix.
    func on(tool: String, prefix: [String] = [], stdout: String = "", stderr: String = "", exit: Int32 = 0) {
        on({ url, args in url.lastPathComponent == tool && Array(args.prefix(prefix.count)) == prefix }) { _, _, _ in
            ProcessResult(exitCode: exit, stdout: Data(stdout.utf8), stderr: Data(stderr.utf8))
        }
    }

    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        let rule = lock.withLock { () -> Responder? in
            calls.append((executable, arguments, environment))
            return rules.first { $0.0(executable, arguments) }?.1
        }
        guard let rule else { throw NSError(domain: NSPOSIXErrorDomain, code: 2, userInfo: [NSLocalizedDescriptionKey: "ENOENT \(executable.path)"]) }
        return try await rule(executable, arguments, environment)
    }

    func calls(to tool: String) -> [[String]] { calls.filter { $0.executable.lastPathComponent == tool }.map(\.arguments) }
}
