import Foundation

/// Reads `action-items/dismissed.md`: one `- <key>` bullet per closed Inbox or
/// Loose ends entry. Mirrors the engine's `dismissed.py`.
nonisolated enum DismissedFileLoader {
    static let fileName = "dismissed.md"

    static func parse(_ text: String) -> Set<String> {
        Set(text.components(separatedBy: .newlines).compactMap { line in
            guard line.hasPrefix("- ") else { return nil }
            let key = line.dropFirst(2).trimmingCharacters(in: .whitespacesAndNewlines)
            return key.isEmpty ? nil : key
        })
    }

    static func load(from actionItemsDirectory: URL) -> Set<String> {
        let url = actionItemsDirectory.appendingPathComponent(fileName)
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return parse(text)
    }
}
