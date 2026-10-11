import Foundation

/// One Inbox or Loose ends bullet, e.g.
/// `Slack · Oct 10 — [Priya asked about retention](https://…)`.
nonisolated struct SideEntry: Identifiable, Equatable, Sendable {
    let text: String
    let url: URL?
    /// Text before the ` — ` separator, when the bullet has one.
    let source: String?

    /// What `scoutctl action-items dismiss --key` records.
    var key: String { url?.absoluteString ?? text }
    var id: String { key }

    private static let linkRe = try! NSRegularExpression(pattern: #"\[([^\]]+)\]\((https?://[^)\s]+)\)"#)
    private static let bareURLRe = try! NSRegularExpression(pattern: #"https?://\S+"#)

    static func parse(_ bullet: String) -> SideEntry {
        let parts = bullet.components(separatedBy: " — ")
        let source = parts.count > 1 ? parts[0].trimmingCharacters(in: .whitespaces) : nil
        let rest = (parts.count > 1 ? parts.dropFirst().joined(separator: " — ") : bullet).trimmingCharacters(in: .whitespaces)
        let ns = rest as NSString
        let range = NSRange(location: 0, length: ns.length)
        if let m = linkRe.firstMatch(in: rest, range: range) {
            return SideEntry(text: ns.substring(with: m.range(at: 1)), url: URL(string: ns.substring(with: m.range(at: 2))), source: source)
        }
        if let m = bareURLRe.firstMatch(in: rest, range: range) {
            let text = ns.replacingCharacters(in: m.range, with: "").trimmingCharacters(in: .whitespaces)
            let url = ns.substring(with: m.range)
            return SideEntry(text: text.isEmpty ? url : text, url: URL(string: url), source: source)
        }
        return SideEntry(text: rest, url: nil, source: source)
    }

    /// The open bullets of the section titled `title` (Inbox, Loose ends).
    static func entries(inSectionTitled title: String, of sections: [ActionSection], dismissed: Set<String>) -> [SideEntry] {
        sections
            .filter { $0.title.caseInsensitiveCompare(title) == .orderedSame }
            .flatMap(\.bullets)
            .map(parse)
            .filter { !dismissed.contains($0.key) }
    }
}
