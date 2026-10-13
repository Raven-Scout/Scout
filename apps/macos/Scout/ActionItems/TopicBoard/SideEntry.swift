import Foundation

/// One Inbox or Loose ends bullet, e.g.
/// `Slack · Oct 10 — [Priya asked about retention](https://…)`.
nonisolated struct SideEntry: Identifiable, Equatable, Sendable {
    let text: String
    /// The link exactly as written in the bullet.
    let link: String?
    /// Text before the ` — ` separator, when the bullet has one.
    let source: String?

    /// What `scoutctl action-items dismiss --key` records: the link as
    /// written, never `URL`'s percent-encoded form, so the engine's
    /// `side_entry_key` gets the same key.
    var key: String { link ?? text }
    var url: URL? { link.flatMap(URL.init(string:)).flatMap { LinkSchemePolicy.allowsExternalOpen($0) ? $0 : nil } }
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
            return SideEntry(text: ns.substring(with: m.range(at: 1)), link: ns.substring(with: m.range(at: 2)), source: source)
        }
        if let m = bareURLRe.firstMatch(in: rest, range: range) {
            let text = ns.replacingCharacters(in: m.range, with: "").trimmingCharacters(in: .whitespaces)
            let url = ns.substring(with: m.range)
            return SideEntry(text: text.isEmpty ? url : text, link: url, source: source)
        }
        return SideEntry(text: rest, link: nil, source: source)
    }

    /// Whether a section is the one titled `title`, ignoring case and a
    /// trailing note such as `(3)`. The engine's `is_side_section` agrees.
    static func section(_ sectionTitle: String, isTitled title: String) -> Bool {
        sectionTitle
            .replacingOccurrences(of: #"\s*\(.*?\)\s*$"#, with: "", options: .regularExpression)
            .caseInsensitiveCompare(title) == .orderedSame
    }

    /// Whether the daily file has a section titled `title`, even an empty one.
    static func hasSection(titled title: String, in sections: [ActionSection]) -> Bool {
        sections.contains { section($0.title, isTitled: title) }
    }

    /// The open bullets of the section titled `title` (Inbox, Loose ends).
    static func entries(inSectionTitled title: String, of sections: [ActionSection], dismissed: Set<String>) -> [SideEntry] {
        sections
            .filter { section($0.title, isTitled: title) }
            .flatMap(\.bullets)
            .map(parse)
            .filter { !dismissed.contains($0.key) }
    }
}
