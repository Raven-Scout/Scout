import Foundation

/// Rewrites GitHub PR/issue references in action-item text into markdown links
/// so they render as clickable links inline, the same way `[[wikilinks]]` do
/// (issue #17).
///
/// Two reference forms are recognized:
///
/// - **Qualified** `owner/repo#123` — always linkified; the repo is explicit.
/// - **Bare** `#123` — only linkified when the surrounding text mentions
///   *exactly one* repo (as a `owner/repo` slug, a `owner/repo#N` ref, or a
///   `github.com/owner/repo` URL). With zero or multiple candidate repos a bare
///   `#123` is ambiguous, so it's left as plain text rather than guessing.
///
/// References are emitted as `[#123](https://github.com/owner/repo/issues/123)`.
/// The `/issues/` path is canonical for both issues and PRs: GitHub redirects
/// `/issues/N` to `/pull/N` when N is a pull request, so a single form works
/// without knowing the kind up front.
///
/// Matches inside existing markdown links `[text](url)`, wikilinks `[[...]]`,
/// and inline code spans are left untouched so already-formatted content and
/// URLs aren't corrupted.
enum GitHubRefLinkifier {
    static func linkify(_ s: String) -> String {
        let ns = s as NSString
        let full = NSRange(location: 0, length: ns.length)

        let protectedRanges = self.protectedRanges(in: ns, full: full)
        let inferredRepo = self.inferredRepo(in: ns, full: full, protectedRanges: protectedRanges)

        // Single alternation so qualified refs win over the bare-ref branch on
        // the same `#N`. Group 1/2 = qualified owner/repo + number; group 3 =
        // bare number.
        guard let re = try? NSRegularExpression(
            pattern: #"(?<![\w/])([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)#(\d{1,7})\b|(?<![\w/#])#(\d{1,7})\b"#
        ) else { return s }

        var result = s
        let matches = re.matches(in: s, range: full).reversed()
        for m in matches {
            if intersectsProtected(m.range, protectedRanges) { continue }

            let repo: String
            let number: String
            if m.range(at: 1).location != NSNotFound {
                repo = ns.substring(with: m.range(at: 1))
                number = ns.substring(with: m.range(at: 2))
            } else {
                guard let inferredRepo else { continue }
                repo = inferredRepo
                number = ns.substring(with: m.range(at: 3))
            }

            let label = ns.substring(with: m.range)
            let replacement = "[\(label)](https://github.com/\(repo)/issues/\(number))"
            result = (result as NSString).replacingCharacters(in: m.range, with: replacement)
        }
        return result
    }

    // MARK: - Repo inference

    /// All distinct repo slugs referenced in the text. If exactly one, it can be
    /// used to resolve bare `#N` refs.
    private static func inferredRepo(in ns: NSString, full: NSRange, protectedRanges: [NSRange]) -> String? {
        var repos = Set<String>()

        // owner/repo#N and bare owner/repo slugs, excluding file-path-looking
        // slugs (e.g. render.py, README.md) and numeric-only segments. The
        // leading `.` exclusion keeps a domain like `github.com/acme` from
        // matching as a `com/acme` slug.
        if let re = try? NSRegularExpression(pattern: #"(?<![\w/@.])([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)"#) {
            for m in re.matches(in: ns as String, range: full) where !intersectsProtected(m.range, protectedRanges) {
                let slug = ns.substring(with: m.range(at: 1))
                if isRepoLikeSlug(slug) { repos.insert(slug) }
            }
        }
        // github.com/owner/repo URLs. The pattern stops at the second path
        // component, so the captured slug is exactly `owner/repo`.
        if let re = try? NSRegularExpression(pattern: #"github\.com/([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)"#) {
            for m in re.matches(in: ns as String, range: full) {
                repos.insert(ns.substring(with: m.range(at: 1)))
            }
        }

        return repos.count == 1 ? repos.first : nil
    }

    /// A GitHub repo slug `owner/name` where neither part is purely numeric and
    /// the name isn't a filename (no `.ext` suffix). Filters out things like
    /// `5/6`, `action-items/render.py`, `docs/README.md`.
    private static func isRepoLikeSlug(_ slug: String) -> Bool {
        let parts = slug.split(separator: "/", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return false }
        let (owner, name) = (parts[0], parts[1])
        if owner.allSatisfy(\.isNumber) || name.allSatisfy(\.isNumber) { return false }
        // Reject a filename-looking name: short trailing dotted extension.
        if let dot = name.range(of: ".", options: .backwards) {
            let ext = name[name.index(after: dot.lowerBound)...]
            if !ext.isEmpty, ext.count <= 5, ext.allSatisfy({ $0.isLetter }) { return false }
        }
        return true
    }

    // MARK: - Protected ranges

    /// Spans that must not be rewritten: markdown links, wikilinks, inline code.
    private static func protectedRanges(in ns: NSString, full: NSRange) -> [NSRange] {
        var ranges: [NSRange] = []
        let patterns = [
            #"\[\[[^\]]*\]\]"#,        // [[wikilink]] / [[target|alias]]
            #"\[[^\]]*\]\([^)]*\)"#,   // [text](url)
            #"`[^`]*`"#,                // `inline code`
        ]
        for p in patterns {
            guard let re = try? NSRegularExpression(pattern: p) else { continue }
            ranges.append(contentsOf: re.matches(in: ns as String, range: full).map(\.range))
        }
        return ranges
    }

    private static func intersectsProtected(_ range: NSRange, _ protectedRanges: [NSRange]) -> Bool {
        for p in protectedRanges where NSIntersectionRange(range, p).length > 0 { return true }
        return false
    }
}
