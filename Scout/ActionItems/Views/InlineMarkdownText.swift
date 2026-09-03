import SwiftUI
import Foundation
import AppKit

/// Optional in-app handler for `[[wikilink]]` clicks. When set and it returns
/// `true`, the click is considered handled in-app (e.g. the Knowledge Base
/// navigates to the target note); otherwise `InlineMarkdownText` falls back to
/// its default Linear/Obsidian opening. Nil everywhere except the KB, so other
/// surfaces keep their existing behavior.
private struct KBWikilinkHandlerKey: EnvironmentKey {
    static let defaultValue: ((String) -> Bool)? = nil
}

extension EnvironmentValues {
    var kbWikilinkHandler: ((String) -> Bool)? {
        get { self[KBWikilinkHandlerKey.self] }
        set { self[KBWikilinkHandlerKey.self] = newValue }
    }
}

/// Optional handler for `[#TAG]` chip clicks. Set by the Knowledge Base to run
/// a tag search; nil everywhere else, where a tag renders as a chip but stays
/// inert rather than opening something unexpected.
private struct KBTagHandlerKey: EnvironmentKey {
    static let defaultValue: ((String) -> Void)? = nil
}

extension EnvironmentValues {
    var kbTagHandler: ((String) -> Void)? {
        get { self[KBTagHandlerKey.self] }
        set { self[KBTagHandlerKey.self] = newValue }
    }
}

struct InlineMarkdownText: View {
    let raw: String
    private let attributed: AttributedString
    @Environment(\.kbWikilinkHandler) private var kbWikilinkHandler
    @Environment(\.kbTagHandler) private var kbTagHandler

    init(_ raw: String) {
        self.raw = raw
        self.attributed = Self.attributedString(for: raw)
    }

    var body: some View {
        Text(attributed)
            .environment(\.openURL, OpenURLAction { url in
                if url.scheme == "scout-wiki" {
                    return openWikilink(target: Self.target(of: url))
                }
                if url.scheme == KBTag.scheme {
                    // No handler (outside the KB) → the chip is inert rather
                    // than falling through to NSWorkspace, which would try to
                    // open a scout-tag:// URL with no registered app.
                    guard let kbTagHandler else { return .discarded }
                    kbTagHandler(Self.target(of: url))
                    return .handled
                }
                NSWorkspace.shared.open(url)
                return .handled
            })
    }

    /// The payload of a `scheme://payload` URL, tolerating the parse landing in
    /// `host` or in `path` depending on the characters involved.
    private static func target(of url: URL) -> String {
        let host = url.host ?? ""
        let raw = host.isEmpty ? url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) : host
        return raw.removingPercentEncoding ?? raw
    }

    // MARK: - Memoization

    /// Main-thread-only cache. AttributedString(markdown:) is expensive enough
    /// that rebuilding it per body evaluation visibly stalls scrolling through
    /// a full day of cards. Keys are the raw subject/body strings, which are
    /// stable across parses for the same task text.
    private struct CacheEntry {
        let value: AttributedString
        /// Logical clock tick of the last read or write — the LRU ordering.
        var lastUsed: UInt64
    }

    private static var cache: [String: CacheEntry] = [:]
    private static var clock: UInt64 = 0

    /// Upper bound on cached renderings.
    ///
    /// A real day's first paint touches ~935 unique strings (~443 KB of source
    /// text), and expanding cards, searching, or switching days pushes the
    /// working set several thousand higher. `var` only so tests can shrink it;
    /// nothing in the app mutates it. MainActor-isolated like the cache itself.
    static var cacheCap = 6000

    /// Internal rather than private so tests can assert on the rendered runs —
    /// that a tag survives the markdown parse as a `scout-tag://` link and
    /// carries the chip attributes. Pure function; no other caller.
    static func attributedString(for raw: String) -> AttributedString {
        clock &+= 1
        if let hit = cache[raw] {
            cache[raw]?.lastUsed = clock
            return hit.value
        }
        // Tags first: once a tag is a `[label](scout-tag://…)` link, the GitHub
        // linkifier's protected ranges cover it. The two can't collide on the
        // same token anyway — a tag needs a letter, a GitHub ref is all digits
        // — but ordering it first also keeps a bracketed `[#TAG]` from looking
        // like a link label to the later passes. Wikilinks last: it only ever
        // touches `[[…]]`, which neither of the others emits.
        let rewritten = rewriteWikilinks(GitHubRefLinkifier.linkify(KBTag.linkify(raw)))
        // Inline-only + preserve whitespace: matches SummaryTab's call site,
        // avoids block-level parsing (fewer throws) and the whitespace collapse
        // that broke `_italic_` under the default `.full` syntax.
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        var computed = (try? AttributedString(markdown: rewritten, options: options))
            ?? AttributedString(rewritten)
        styleTagChips(&computed)
        if cache.count >= cacheCap { evictColdest() }
        cache[raw] = CacheEntry(value: computed, lastUsed: clock)
        return computed
    }

    /// Drop the coldest quarter, oldest first.
    ///
    /// The previous policy evicted `cache.keys.prefix(cap/2)` — *arbitrary*
    /// dictionary order — so the strings currently on screen were as likely to
    /// be thrown out as anything else. Past the cap that turned into a re-parse
    /// treadmill: a warm pass over a 2,398-string working set measured ~320 ms
    /// against 12 ms uncapped, a 27× cliff hit by expanding cards, searching,
    /// and switching days. Evicting by last use keeps the visible set resident,
    /// so crossing the cap degrades gradually instead of falling off an edge.
    ///
    /// A quarter at a time so the sort amortizes across many inserts rather
    /// than running on every one past the cap.
    private static func evictColdest() {
        let excess = cache.count - (cacheCap * 3 / 4)
        guard excess > 0 else { return }
        let coldest = cache
            .sorted { $0.value.lastUsed < $1.value.lastUsed }
            .prefix(excess)
        for (key, _) in coldest {
            cache.removeValue(forKey: key)
        }
    }

    // MARK: - Test seams

    static var cacheCountForTesting: Int { cache.count }
    static func cacheContainsForTesting(_ raw: String) -> Bool { cache[raw] != nil }
    static func resetCacheForTesting() {
        cache.removeAll()
        clock = 0
    }

    /// Give every `scout-tag://` run the chip treatment: accent ink on an
    /// accent wash, in a slightly smaller monospace face so a tag reads as an
    /// object rather than prose.
    ///
    /// Note: SwiftUI's inline `backgroundColor` paints a square block — `Text`
    /// has no corner-radius attribute for a run, and a real rounded capsule
    /// would mean leaving `Text` for a wrapping layout (which breaks prose
    /// flow) or baking each chip into an image (which can't carry the link).
    /// The hair spaces `KBTag` pads the label with supply the inset.
    private static func styleTagChips(_ s: inout AttributedString) {
        // Collect first, then mutate. Runs are delimited by attribute equality,
        // so writing attributes redraws the run boundaries — mutating `s` while
        // iterating `s.runs` would be walking a collection as it reshapes.
        let ranges = s.runs.filter { $0.link?.scheme == KBTag.scheme }.map(\.range)
        for range in ranges {
            s[range].font = DS.mono(11.5, weight: .medium)
            s[range].foregroundColor = DS.Accent.ink
            s[range].backgroundColor = DS.Accent.wash
            // Markdown links default to underlined accent-blue; a chip is
            // already visibly clickable, so drop the underline.
            s[range].underlineStyle = nil
        }
    }

    /// `[[target]]` / `[[target|alias]]` — compiled once; rewriteWikilinks runs
    /// for every cache miss and regex compilation dominates its cost.
    private static let wikilinkRe = try! NSRegularExpression(
        pattern: #"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]"#
    )

    /// Linear issue key, e.g. `PROJ-1234`.
    private static let linearRe = try! NSRegularExpression(pattern: #"^[A-Z]{2,10}-\d+$"#)

    /// Replace ``[[target]]`` / ``[[target|alias]]`` with ``[label](scout-wiki://target)``
    /// so AttributedString(markdown:) renders them as clickable links we intercept.
    private static func rewriteWikilinks(_ s: String) -> String {
        let ns = s as NSString
        var result = s
        let matches = wikilinkRe.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed()
        for m in matches {
            let target = ns.substring(with: m.range(at: 1))
            let label  = m.range(at: 2).location == NSNotFound ? target : ns.substring(with: m.range(at: 2))
            let encoded = target.addingPercentEncoding(withAllowedCharacters: .urlHostAllowed) ?? target
            let replacement = "[\(label)](scout-wiki://\(encoded))"
            result = (result as NSString).replacingCharacters(in: m.range, with: replacement)
        }
        return result
    }

    /// `target` arrives already percent-decoded from `target(of:)`.
    private func openWikilink(target decoded: String) -> OpenURLAction.Result {
        // In-app navigation first (Knowledge Base). If the handler resolves the
        // target it returns true and we stop; otherwise fall back to Linear/Obsidian.
        if let kbWikilinkHandler, kbWikilinkHandler(decoded) { return .handled }
        if Self.linearRe.firstMatch(in: decoded, range: NSRange(location: 0, length: (decoded as NSString).length)) != nil {
            let workspace = UserDefaults.standard.string(forKey: "linearWorkspace") ?? ""
            let urlString = workspace.isEmpty
                ? "https://linear.app/"
                : "https://linear.app/\(workspace)/issue/\(decoded)"
            if let url = URL(string: urlString) {
                NSWorkspace.shared.open(url); return .handled
            }
        }
        let obsidianTarget = decoded.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? decoded
        if let url = URL(string: "obsidian://open?vault=Scout&file=\(obsidianTarget)") {
            NSWorkspace.shared.open(url); return .handled
        }
        return .discarded
    }
}
