import Foundation

nonisolated enum TopicsFileLoader {
    static let fileName = "topics.md"
    static let namesFileName = "topic-names.md"

    static func url(in actionItemsDirectory: URL) -> URL {
        actionItemsDirectory.appendingPathComponent(fileName)
    }

    /// The topics in `actionItemsDirectory/topics.md`, named from
    /// `topic-names.md` where the user renamed them. None when `topics.md` is
    /// missing or unreadable — the board then shows every task under Other.
    static func load(from actionItemsDirectory: URL) -> [TopicMeta] {
        guard let text = read(fileName, in: actionItemsDirectory) else { return [] }
        let names = read(namesFileName, in: actionItemsDirectory).map(TopicNamesParser.parse) ?? [:]
        return TopicsFileParser.parse(text).map { $0.named(names[$0.tag]) }
    }

    private static func read(_ name: String, in directory: URL) -> String? {
        try? String(contentsOf: directory.appendingPathComponent(name), encoding: .utf8)
    }
}
