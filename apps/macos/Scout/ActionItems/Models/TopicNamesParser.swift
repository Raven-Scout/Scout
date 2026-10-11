import Foundation

/// Parses `action-items/topic-names.md`, the user's own names for topics,
/// one per line:
///
///     - [#STRM] Reattach
///
/// Only `scoutctl action-items rename-topic` writes it; runs never do, so a
/// name survives every rewrite of `topics.md`. A later line for the same tag
/// wins. Other lines are ignored.
nonisolated enum TopicNamesParser {
    private static let lineRe = try! NSRegularExpression(
        pattern: #"^\s*-\s+"# + TopicsFileParser.tagPattern + #"\s+(.*?)\s*$"#
    )

    static func parse(_ text: String) -> [String: String] {
        var names: [String: String] = [:]
        for line in text.components(separatedBy: .newlines) {
            guard let match = TopicsFileParser.captures(lineRe, in: line), !match[1].isEmpty else { continue }
            names[match[0]] = match[1]
        }
        return names
    }
}
