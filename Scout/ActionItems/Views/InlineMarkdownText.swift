import SwiftUI
import Foundation
import AppKit

struct InlineMarkdownText: View {
    let raw: String
    private let attributed: AttributedString

    init(_ raw: String) {
        self.raw = raw
        self.attributed = Self.attributedString(for: raw)
    }

    var body: some View {
        Text(attributed)
            .environment(\.openURL, OpenURLAction { url in
                if url.scheme == "scout-wiki" {
                    let host = url.host ?? ""
                    let target = host.isEmpty ? url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) : host
                    return openWikilink(target: target)
                }
                NSWorkspace.shared.open(url)
                return .handled
            })
    }

    // MARK: - Memoization

    /// Main-thread-only cache. AttributedString(markdown:) is expensive enough
    /// that rebuilding it per body evaluation visibly stalls scrolling through
    /// a full day of cards. Keys are the raw subject/body strings, which are
    /// stable across parses for the same task text.
    private static var cache: [String: AttributedString] = [:]
    private static let cacheCap = 2000

    private static func attributedString(for raw: String) -> AttributedString {
        if let hit = cache[raw] { return hit }
        // Linkify GitHub refs before wikilinks: the linkifier protects existing
        // markdown links / wikilinks, and rewriteWikilinks then leaves the
        // GitHub `[label](https://…)` links untouched.
        let rewritten = rewriteWikilinks(GitHubRefLinkifier.linkify(raw))
        let computed = (try? AttributedString(markdown: rewritten)) ?? AttributedString(rewritten)
        if cache.count >= cacheCap { cache.removeAll(keepingCapacity: true) }
        cache[raw] = computed
        return computed
    }

    /// Replace ``[[target]]`` / ``[[target|alias]]`` with ``[label](scout-wiki://target)``
    /// so AttributedString(markdown:) renders them as clickable links we intercept.
    private static func rewriteWikilinks(_ s: String) -> String {
        guard let re = try? NSRegularExpression(pattern: #"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]"#) else { return s }
        let ns = s as NSString
        var result = s
        let matches = re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed()
        for m in matches {
            let target = ns.substring(with: m.range(at: 1))
            let label  = m.range(at: 2).location == NSNotFound ? target : ns.substring(with: m.range(at: 2))
            let encoded = target.addingPercentEncoding(withAllowedCharacters: .urlHostAllowed) ?? target
            let replacement = "[\(label)](scout-wiki://\(encoded))"
            result = (result as NSString).replacingCharacters(in: m.range, with: replacement)
        }
        return result
    }

    private func openWikilink(target: String) -> OpenURLAction.Result {
        let decoded = target.removingPercentEncoding ?? target
        let linearRe = try! NSRegularExpression(pattern: #"^[A-Z]{2,10}-\d+$"#)
        if linearRe.firstMatch(in: decoded, range: NSRange(location: 0, length: (decoded as NSString).length)) != nil {
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
