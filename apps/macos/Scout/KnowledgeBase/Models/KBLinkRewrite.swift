import Foundation

/// Rewrites the `[[wikilinks]]` that point at a note being renamed, so a
/// rename doesn't silently break every note that links to it (UI audit #75).
///
/// Only the target's name changes: a path form keeps its folders
/// (`[[people/alex]]` → `[[people/alexandra]]`), and an anchor, an explicit
/// `.md`, an alias (`|Alex`, or the `\|` form used inside table cells) are
/// kept as written. Whether a link points at the note is decided by the
/// knowledge base's own resolver, so a same-named note elsewhere, a ticket id
/// or a URL is never touched.
///
/// A bare `[[alex]]` is left alone when more than one note is named `alex`:
/// the resolver picks one of them by tree order, which says nothing about
/// which one the writer meant, so rewriting it could repoint a link meant for
/// the other note. Those are counted as ambiguous instead. Markdown links
/// (`[text](alex.md)`) aren't wikilinks and aren't touched.
nonisolated enum KBLinkRewrite {

    /// A `[[target]]`, `[[target|alias]]` or `[[target\|alias]]`. Group 1 is
    /// the target as written, group 2 the alias part (with its separator).
    /// Same grammar as `KnowledgeBaseService.extractWikilinks`.
    static let linkPattern = #"\[\[([^\]|]+?)((?:\\?\|[^\]]+)?)\]\]"#

    /// A note that links to the one being renamed.
    struct Inbound: Equatable, Sendable {
        let path: String
        /// Links a rename rewrites.
        let links: Int
        /// Bare links left alone because the old name isn't unique.
        var ambiguous: Int = 0
    }

    /// Every other note that links to `path`, with its links counted, most
    /// links first (then path).
    static func inboundNotes(to path: String, in index: KBIndex) -> [Inbound] {
        var result: [Inbound] = []
        for (source, resolved) in index.resolvedOut where source != path && resolved.contains(path) {
            let counts = classify(index.textByFile[source] ?? "", from: source, to: path, index: index)
            guard counts.links + counts.ambiguous > 0 else { continue }
            result.append(Inbound(path: source, links: counts.links, ambiguous: counts.ambiguous))
        }
        return result.sorted { $0.links != $1.links ? $0.links > $1.links : $0.path < $1.path }
    }

    /// How many of `path`'s links point at itself by name (`[[alex#Role]]`
    /// in alex.md). `[[#Role]]` names no note and needs no change.
    static func selfLinks(of path: String, in index: KBIndex) -> Int {
        classify(index.textByFile[path] ?? "", from: path, to: path, index: index).links
    }

    /// Links in `text` (written in `source`) that resolve to `target`: those
    /// a rename rewrites, and bare ones left alone as ambiguous.
    static func classify(_ text: String, from source: String, to target: String,
                         index: KBIndex) -> (links: Int, ambiguous: Int) {
        var links = 0, ambiguous = 0
        for m in matches(in: text) where index.resolve(m.target, from: source) == target {
            switch kind(m.target, oldPath: target, index: index) {
            case .rewrite: links += 1
            case .ambiguous: ambiguous += 1
            case .none: break
            }
        }
        return (links, ambiguous)
    }

    private enum Kind { case rewrite, ambiguous, none }

    /// What a rename does with a link that resolves to `oldPath`.
    private static func kind(_ target: String, oldPath: String, index: KBIndex) -> Kind {
        let name = linkName(target)
        guard !name.isEmpty else { return .none }   // [[#Role]]
        if !name.contains("/") && oldNameIsShared(oldPath, index: index) { return .ambiguous }
        return .rewrite
    }

    /// The name part of a link target: anchor dropped, trimmed.
    private static func linkName(_ target: String) -> String {
        let hash = target.firstIndex(of: "#") ?? target.endIndex
        return target[..<hash].trimmingCharacters(in: .whitespaces)
    }

    /// More than one note has `oldPath`'s name.
    static func oldNameIsShared(_ oldPath: String, index: KBIndex) -> Bool {
        let stem = KnowledgeBaseFileWriter.noteStem((oldPath as NSString).lastPathComponent).lowercased()
        return (index.stemPaths[stem]?.count ?? 0) > 1
    }

    /// Another note already has the new name, so existing `[[newStem]]`
    /// links may start pointing at the renamed note (and a bare link to it
    /// is written as a path).
    static func newNameCollides(_ newStem: String, oldPath: String, index: KBIndex) -> Bool {
        (index.stemPaths[newStem.lowercased()] ?? []).contains { $0 != oldPath }
    }

    /// `text` with every link that resolves to `oldPath` pointed at the note's
    /// new name; how many links changed; how many bare links were left as
    /// ambiguous. `newStem` is the new file name without `.md`; `newKBPath`
    /// is the note's new path inside the knowledge base, without `.md` (e.g.
    /// `people/alexandra`), used instead of the bare name when another note
    /// already has that name, so the link can't resolve to the wrong one.
    static func rewrite(_ text: String, source: String, oldPath: String,
                        newStem: String, newKBPath: String,
                        index: KBIndex) -> (text: String, count: Int, ambiguous: Int) {
        let collides = newNameCollides(newStem, oldPath: oldPath, index: index)
        var out = ""
        var count = 0
        var ambiguous = 0
        var cursor = text.startIndex
        for m in matches(in: text) where index.resolve(m.target, from: source) == oldPath {
            switch kind(m.target, oldPath: oldPath, index: index) {
            case .none: continue
            case .ambiguous: ambiguous += 1; continue
            case .rewrite: break
            }
            guard let replaced = renamedTarget(m.target, newStem: newStem,
                                               newKBPath: collides ? newKBPath : nil),
                  replaced != m.target else { continue }
            out += text[cursor..<m.targetRange.lowerBound]
            out += replaced
            cursor = m.targetRange.upperBound
            count += 1
        }
        out += text[cursor...]
        return (count == 0 ? text : out, count, ambiguous)
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
    /// Why a note that links here wasn't updated.
    enum SkipReason: Equatable, Sendable {
        /// Unsaved edits are open in it in the app.
        case unsavedEdits
        /// It changed on disk (or went away) while the rename ran.
        case changedMeanwhile
        /// It couldn't be read, or isn't UTF-8 text.
        case unreadable
        /// Writing it failed (permissions, a full disk).
        case writeFailed
    }

    struct Skip: Equatable, Sendable {
        let path: String
        let reason: SkipReason
    }

    /// Links rewritten, and in how many notes (the renamed note counts when
    /// its links to itself changed).
    var linksUpdated = 0
    var notesUpdated = 0
    /// Notes that link here but weren't changed, by path.
    var skipped: [Skip] = []
    /// Bare links left as written because more than one note has the old name.
    var ambiguousLinks = 0

    static let none = KBRenameLinkReport()
}
