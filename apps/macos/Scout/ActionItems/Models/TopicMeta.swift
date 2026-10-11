import Foundation

/// One workstream from `action-items/topics.md`. Scout runs own `title`,
/// `session` and `summary`; `customTitle` is the user's rename and only
/// `scoutctl action-items rename-topic` writes it.
nonisolated struct TopicMeta: Equatable, Hashable, Sendable, Identifiable {
    let tag: String
    let title: String
    let session: String?
    let summary: String?
    let customTitle: String?

    var id: String { tag }
    var displayTitle: String { customTitle ?? title }
}

/// Parses `action-items/topics.md`:
///
///     # Topics
///
///     ## [#STRM] Make stream reattach survive a restart
///     - Session: https://claude.ai/code/session_0123
///     - Summary: Two PRs, the second needs the first
///     - Renamed: Reattach
///
/// Unknown keys and prose are ignored, so the file can grow fields later.
nonisolated enum TopicsFileParser {
    private static let headingRe = try! NSRegularExpression(
        pattern: #"^##\s+\[#((?=[A-Z0-9]{2,8}\])[A-Z0-9]*[A-Z][A-Z0-9]*)\]\s*(.*?)\s*$"#
    )
    private static let fieldRe = try! NSRegularExpression(pattern: #"^\s*-\s+([A-Za-z]+):\s*(.*?)\s*$"#)

    static func parse(_ text: String) -> [TopicMeta] {
        var topics: [TopicMeta] = []
        var current: Draft?
        for line in text.components(separatedBy: .newlines) {
            if let heading = captures(headingRe, in: line) {
                current.map { topics.append($0.meta) }
                current = Draft(tag: heading[0], title: heading[1])
            } else if line.hasPrefix("#") {
                current.map { topics.append($0.meta) }
                current = nil
            } else if let field = captures(fieldRe, in: line) {
                current?.set(key: field[0].lowercased(), value: field[1])
            }
        }
        current.map { topics.append($0.meta) }
        return topics
    }

    private struct Draft {
        let tag: String
        let title: String
        var fields: [String: String] = [:]

        mutating func set(key: String, value: String) {
            guard !value.isEmpty else { return }
            fields[key] = value
        }

        var meta: TopicMeta {
            TopicMeta(
                tag: tag,
                title: title.isEmpty ? tag : title,
                session: fields["session"],
                summary: fields["summary"],
                customTitle: fields["renamed"]
            )
        }
    }

    private static func captures(_ re: NSRegularExpression, in line: String) -> [String]? {
        let ns = line as NSString
        guard let m = re.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return (1..<m.numberOfRanges).map { ns.substring(with: m.range(at: $0)) }
    }
}
