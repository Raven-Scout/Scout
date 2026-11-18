import Foundation

/// Finds the errors in a run's log for the run detail's Errors tab.
///
/// It used to match only usage-limit and API patterns, one line at a time,
/// so a run that died on a Python traceback said "No errors detected."
/// (finding #24). It now also finds:
/// - Python tracebacks, header to exception line, chained ones
///   ("During handling of the above exception…") kept as one error;
/// - a command's error box (`╭─ Error ─╮ … ╰─╯`, what `scoutctl` prints for
///   a bad option);
/// - lines that say they're errors (`ERROR …`, `Error: …`, `fatal: …`,
///   `ValueError: …`, a shell's `line 12: …` or `command not found`), with
///   the indented lines that follow them (a stack, a detail list);
/// - the runner's own `=== … failed … ===` notes about a step.
///
/// Every rule is anchored to the start of a line except the API patterns,
/// which match anywhere, as they always did, so a sentence in the session's
/// output that merely mentions an error doesn't count.
nonisolated enum RunLogErrorScanner {
    /// A log with more errors than this shows the first ones only.
    static let maxErrors = 200
    /// A longer block keeps its first lines and its last ones.
    static let maxBlockLines = 60
    /// Indented lines taken after a one-line error.
    static let maxContinuationLines = 20

    /// Usage-limit and API errors, matched anywhere in a line. The ids are the
    /// patterns the scanner always used; the Control Center's rate-limit
    /// signal reads them from `DetectedError.pattern`.
    static let apiPatterns: [(id: String, regex: NSRegularExpression)] = [
        "429", "rate.?limit", "overloaded", "throttle", "too many requests",
        "insufficient_quota", "context_length_exceeded", "internal server error",
    ].map { ($0, regex($0, caseInsensitive: true)) }

    private static let tracebackHeader = regex(#"^\s*(Exception Group )?Traceback \(most recent call last\):\s*$"#)
    private static let chainLine = regex(
        #"^(During handling of the above exception, another exception occurred:|The above exception was the direct cause of the following exception:)\s*$"#
    )
    private static let errorBoxTop = regex(#"^\s*╭─+ ?Error\b"#)
    private static let errorBoxBottom = regex(#"^\s*╰"#)
    private static let runnerLine = regex(#"^=== .*\b(failed|missing|refused|error)\b.* ===\s*$"#, caseInsensitive: true)
    private static let errorLines: [(id: String, regex: NSRegularExpression)] = [
        ("exception", regex(#"^\s*[A-Za-z_][\w.]*(Error|Exception): \S"#)),
        ("error", regex(#"^\s*(ERROR|FATAL|CRITICAL)\b"#)),
        ("error", regex(#"^\s*(error|Error|fatal|Fatal)(\[\w+\])?: \S"#)),
        ("shell", regex(#"^\S+: line \d+: \S"#)),
        ("shell", regex(#": command not found\s*$"#)),
    ]

    static func scan(_ text: String) -> [DetectedError] {
        let lines = text.components(separatedBy: "\n").map { $0.hasSuffix("\r") ? String($0.dropLast()) : $0 }
        var out: [DetectedError] = []
        var i = 0
        while i < lines.count, out.count < maxErrors {
            let line = lines[i]
            if line.trimmingCharacters(in: .whitespaces).isEmpty { i += 1; continue }

            if matches(tracebackHeader, line) {
                let (block, exception, next) = traceback(lines, from: i)
                out.append(DetectedError(
                    line: i + 1,
                    pattern: apiPattern(in: block) ?? "traceback",
                    snippet: headline(exception ?? line),
                    kind: .traceback,
                    block: capped(block)
                ))
                i = next
                continue
            }
            if matches(errorBoxTop, line) {
                var end = i
                while end + 1 < lines.count, end - i < maxBlockLines, !matches(errorBoxBottom, lines[end]) { end += 1 }
                let block = Array(lines[i...end])
                let message = block.dropFirst().map(boxContent).first { !$0.isEmpty } ?? "Error"
                out.append(DetectedError(
                    line: i + 1, pattern: apiPattern(in: block) ?? "error-box",
                    snippet: headline(message), kind: .error, block: capped(block)
                ))
                i = end + 1
                continue
            }
            if let id = apiPatterns.first(where: { matches($0.regex, line) })?.id {
                out.append(DetectedError(line: i + 1, pattern: id, snippet: headline(line), kind: .api))
                i += 1
                continue
            }
            if matches(runnerLine, line) {
                out.append(DetectedError(line: i + 1, pattern: "runner", snippet: headline(line), kind: .runner))
                i += 1
                continue
            }
            if let id = errorLines.first(where: { matches($0.regex, line) })?.id {
                var end = i
                while end + 1 < lines.count, end - i < maxContinuationLines, isIndented(lines[end + 1]) { end += 1 }
                let block = end > i ? Array(lines[i...end]) : []
                out.append(DetectedError(
                    line: i + 1, pattern: id, snippet: headline(line), kind: .error, block: capped(block)
                ))
                i = end + 1
                continue
            }
            i += 1
        }
        return out
    }

    /// The traceback starting at `start`: its lines, its last exception line
    /// (the one that ended the run), and the index after it.
    private static func traceback(_ lines: [String], from start: Int) -> ([String], String?, Int) {
        var block = [lines[start]]
        var exception: String? = nil
        // The current traceback's exception line has been read; only a
        // chained traceback (or the exception's own indented detail) goes on.
        var closed = false
        var i = start + 1
        while i < lines.count {
            let line = lines[i]
            if matches(tracebackHeader, line) || matches(chainLine, line) {
                block.append(line); closed = false; i += 1; continue
            }
            if isIndented(line) {
                block.append(line); i += 1; continue
            }
            if line.trimmingCharacters(in: .whitespaces).isEmpty {
                // A blank line ends it, unless a chained traceback follows.
                var next = i + 1
                while next < lines.count, lines[next].trimmingCharacters(in: .whitespaces).isEmpty { next += 1 }
                guard next < lines.count,
                      matches(chainLine, lines[next]) || matches(tracebackHeader, lines[next])
                else { break }
                block.append(contentsOf: lines[i..<next]); i = next; continue
            }
            // Past the exception line, a plain line is the log moving on.
            if closed { break }
            // The exception line: `ValueError: …`.
            block.append(line); exception = line; closed = true; i += 1
        }
        return (block, exception, i)
    }

    private static func apiPattern(in block: [String]) -> String? {
        for line in block {
            if let id = apiPatterns.first(where: { matches($0.regex, line) })?.id { return id }
        }
        return nil
    }

    private static func capped(_ block: [String]) -> [String] {
        guard block.count > maxBlockLines else { return block }
        let head = 5
        let tail = maxBlockLines - head - 1
        return Array(block.prefix(head)) + ["… \(block.count - head - tail) more lines …"] + Array(block.suffix(tail))
    }

    private static func headline(_ line: String) -> String {
        String(line.trimmingCharacters(in: .whitespaces).prefix(200))
    }

    private static func boxContent(_ line: String) -> String {
        line.trimmingCharacters(in: CharacterSet(charactersIn: "│╭╮╰╯─ \t"))
    }

    private static func isIndented(_ line: String) -> Bool {
        guard let first = line.first, first == " " || first == "\t" else { return false }
        return !line.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private static func matches(_ regex: NSRegularExpression, _ line: String) -> Bool {
        regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    private static func regex(_ pattern: String, caseInsensitive: Bool = false) -> NSRegularExpression {
        // The patterns are literals, so a bad one is a programming error.
        try! NSRegularExpression(pattern: pattern, options: caseInsensitive ? [.caseInsensitive] : [])
    }
}
