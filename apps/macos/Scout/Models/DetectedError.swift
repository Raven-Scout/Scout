import Foundation

/// One error `RunLogErrorScanner` found in a run's log: a single line, or a
/// whole multi-line block (a Python traceback, a command's error box).
nonisolated struct DetectedError: Equatable, Hashable, Sendable, Codable {
    enum Kind: String, Equatable, Hashable, Sendable, Codable {
        /// A usage limit, overload or other API error line.
        case api
        /// A Python traceback, from its header to the exception line.
        case traceback
        /// A line that says it's an error (`ERROR …`, `Error: …`,
        /// `ValueError: …`, a shell's `command not found`, a CLI error box).
        case error
        /// The runner's own `=== … failed … ===` note about a step.
        case runner

        /// The kind in the owner's words, as the Errors tab labels it.
        var displayName: String {
            switch self {
            case .api:       return "Usage limit or API error"
            case .traceback: return "Python error"
            case .error:     return "Error"
            case .runner:    return "Run setup"
            }
        }
    }

    /// The 1-based log line the error starts on.
    let line: Int
    /// Which rule matched. For `.api` it is the old pattern text (`429`,
    /// `rate.?limit`, …), which the Control Center's rate-limit signal reads.
    let pattern: String
    /// One line that says what went wrong: the exception line of a traceback,
    /// otherwise the matching line. At most 200 characters.
    let snippet: String
    var kind: Kind = .api
    /// Every line of a multi-line error, in order; empty for a one-line one.
    var block: [String] = []
}

nonisolated extension DetectedError {
    private enum CodingKeys: String, CodingKey { case line, pattern, snippet, kind, block }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        line = try c.decode(Int.self, forKey: .line)
        pattern = try c.decode(String.self, forKey: .pattern)
        snippet = try c.decode(String.self, forKey: .snippet)
        kind = (try? c.decodeIfPresent(Kind.self, forKey: .kind)) ?? .api
        block = (try? c.decodeIfPresent([String].self, forKey: .block)) ?? []
    }
}
