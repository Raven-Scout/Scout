import Foundation

nonisolated enum TopicsFileLoader {
    static let fileName = "topics.md"

    static func url(in actionItemsDirectory: URL) -> URL {
        actionItemsDirectory.appendingPathComponent(fileName)
    }

    /// The topics in `actionItemsDirectory/topics.md`, or none when the file
    /// is missing or unreadable — the board then shows every task under Other.
    static func load(from actionItemsDirectory: URL) -> [TopicMeta] {
        guard let text = try? String(contentsOf: url(in: actionItemsDirectory), encoding: .utf8) else { return [] }
        return TopicsFileParser.parse(text)
    }
}
