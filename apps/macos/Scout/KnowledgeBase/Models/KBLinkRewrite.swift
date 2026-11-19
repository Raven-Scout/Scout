import Foundation

/// Rewrites the `[[wikilinks]]` that point at a note being renamed, so a
/// rename doesn't silently break every note that links to it (UI audit #75;
/// the vault's own `scripts/kb-move.sh` does the same for moves).
///
/// Only the target's name changes: a path form keeps its folders
/// (`[[people/alex]]` → `[[people/alexandra]]`), and an anchor, an explicit
/// `.md`, an alias (`|Alex`, or the `\|` form used inside table cells) are
/// kept as written. Whether a link points at the note is decided by the
/// knowledge base's own resolver, so a same-named note elsewhere, a ticket id
/// or a URL is never touched.
nonisolated enum KBLinkRewrite {

    /// A `[[target]]`, `[[target|alias]]` or `[[target\|alias]]`. Group 1 is
    /// the target as written, group 2 the alias part (with its separator).
    /// Same grammar as `KnowledgeBaseService.extractWikilinks`.
    static let linkPattern = #"\[\[([^\]|]+?)((?:\\?\|[^\]]+)?)\]\]"#

    /// Every note that links to `path`, with how many links each holds, most
    /// links first (then path), leaving out `path` itself.
    static func inboundNotes(to path: String, in index: KBIndex) -> [(path: String, links: Int)] {
        var result: [(path: String, links: Int)] = []
        for (source, resolved) in index.resolvedOut where source != path && resolved.contains(path) {
            let text = index.textByFile[source] ?? ""
            let count = links(in: text, from: source, to: path, index: index)
            result.append((source, max(count, 1)))
        }
        return result.sorted { $0.links != $1.links ? $0.links > $1.links : $0.path < $1.path }
    }

    /// How many `[[links]]` in `text` (written in `source`) resolve to `target`.
    static func links(in text: String, from source: String, to target: String, index: KBIndex) -> Int {
        matches(in: text).filter { index.resolve($0.target, from: source) == target }.count
    }

    /// `text` with every link that resolves to `oldPath` pointed at the note's
    /// new name, and how many links changed. `newStem` is the new file name
    /// without its extension; `newKBPath` is the note's new path inside the
    /// knowledge base, without extension (e.g. `people/alexandra`), used
    /// instead of the bare name when another note already has that name, so
    /// the link can't resolve to the wrong one.
    static func rewrite(_ text: String, source: String, oldPath: String,
                        newStem: String, newKBPath: String, index: KBIndex) -> (text: String, count: Int) {
        let collides = (index.stemPaths[newStem.lowercased()] ?? []).contains { $0 != oldPath }
        var out = ""
        var count = 0
        var cursor = text.startIndex
        for m in matches(in: text) {
            guard index.resolve(m.target, from: source) == oldPath,
                  let replaced = renamedTarget(m.target, newStem: newStem,
                                               newKBPath: collides ? newKBPath : nil),
                  replaced != m.target else { continue }
            out += text[cursor..<m.targetRange.lowerBound]
            out += replaced
            cursor = m.targetRange.upperBound
            count += 1
        }
        out += text[cursor...]
        return (count == 0 ? text : out, count)
    }

    /// The target with its note name replaced, or nil when it names no note
    /// (`[[#Heading]]`, a link to the note it's written in, is left alone).
    /// With `newKBPath`, a bare name becomes that path.
    static func renamedTarget(_ target: String, newStem: String, newKBPath: String?) -> String? {
        // Split off an anchor; keep surrounding spaces as written.
        let hash = target.firstIndex(of: "#") ?? target.endIndex
        var name = String(target[..<hash])
        let anchor = String(target[hash...])
        let leading = String(name.prefix { $0 == " " })
        let trailing = String(name.reversed().prefix { $0 == " " })
        name = name.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return nil }
        var ext = ""
        if name.lowercased().hasSuffix(".md") {
            ext = String(name.suffix(3))
            name.removeLast(3)
        }
        let renamed: String
        if let slash = name.lastIndex(of: "/") {
            renamed = String(name[...slash]) + newStem
        } else {
            renamed = newKBPath ?? newStem
        }
        return leading + renamed + ext + trailing + anchor
    }

    struct Match {
        let target: String
        let targetRange: Range<String.Index>
    }

    static func matches(in text: String) -> [Match] {
        guard text.contains("[["),
              let re = try? NSRegularExpression(pattern: linkPattern) else { return [] }
        let ns = NSRange(text.startIndex..., in: text)
        return re.matches(in: text, range: ns).compactMap { m in
            guard let r = Range(m.range(at: 1), in: text) else { return nil }
            return Match(target: String(text[r]), targetRange: r)
        }
    }
}

/// What a rename did to the links that pointed at the note.
nonisolated struct KBRenameLinkReport: Equatable, Sendable {
    /// Links rewritten, and in how many notes.
    var linksUpdated = 0
    var notesUpdated = 0
    /// Notes that link here but weren't changed: unsaved edits are open in
    /// them, or they changed on disk while the rename ran. Relative paths.
    var notesSkipped: [String] = []

    static let none = KBRenameLinkReport()
}
