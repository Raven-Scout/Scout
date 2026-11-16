import Foundation

/// Removes vault content, personal names and secrets from diagnostics text
/// (app audit C11). The engine's `scoutctl diagnostics` already scrubs its own
/// report with the same rules (`plugin/engine/scout/diagnostics.py`); the app
/// runs every line of the bundle through this again, its own logs included,
/// so one side missing a case can't leak.
///
/// Rules, in order: known credential shapes; URLs other than the project's
/// own; the vault path → `<vault>` and home → `~`; vault paths outside Scout's
/// hidden folders → `<vault>/<path>`; every note and folder name found under
/// the vault (whole words); `[[wikilinks]]`; `*.md` tokens; emails; long
/// opaque strings. Long lines are cut.
nonisolated struct DiagnosticsScrubber: Sendable {
    static let maxLine = 400

    let vault: URL?
    let home: URL?
    /// Exact strings to hide, each with its placeholder.
    let names: [String: String]
    private let namePattern: NSRegularExpression?

    /// Scout's own folder names: never hidden, though what is inside them is.
    static let keepWords: Set<String> = [
        "knowledge-base", "action-items", "ontology", "wishlist", "research",
        "proposals", "archive", "scripts", "hooks", "Scout",
    ]
    static let structuralFiles: Set<String> = [
        "scout-config.yaml", "connectors.custom.yaml", "CLAUDE.md", "SKILL.md", "DREAMING.md", "RESEARCH.md",
    ]
    static let structuralMarkdown: Set<String> = ["CLAUDE.md", "SKILL.md", "DREAMING.md", "RESEARCH.md", "README.md"]
    static let projectURLPrefixes = [
        "https://github.com/Raven-Scout/", "https://raven-scout.github.io/",
        "https://docs.anthropic.com/", "https://code.claude.com/",
    ]

    init(vault: URL?, home: URL? = FileManager.default.homeDirectoryForCurrentUser, names: [String: String] = [:]) {
        self.vault = vault
        self.home = home
        var kept: [String: String] = [:]
        for (value, placeholder) in names {
            let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if v.count >= 3, !Self.keepWords.contains(v) { kept[v] = placeholder }
        }
        self.names = kept
        if kept.isEmpty {
            namePattern = nil
        } else {
            let alternatives = kept.keys.sorted { $0.count > $1.count }.map(NSRegularExpression.escapedPattern(for:))
            namePattern = try? NSRegularExpression(pattern: "(?<![\\w])(?:" + alternatives.joined(separator: "|") + ")(?![\\w])")
        }
    }

    /// A scrubber that also hides every note and folder name under `vault`,
    /// plus the owner's name and email from `scout-config.yaml`.
    static func forVault(_ vault: URL, home: URL? = FileManager.default.homeDirectoryForCurrentUser,
                         maxEntries: Int = 50_000) -> DiagnosticsScrubber {
        DiagnosticsScrubber(vault: vault, home: home, names: vaultNames(vault, maxEntries: maxEntries))
    }

    static func isStructural(_ relative: String) -> Bool {
        let rel = relative.hasSuffix("/") ? String(relative.dropLast()) : relative
        let first = rel.split(separator: "/", maxSplits: 1).first.map(String.init) ?? rel
        return first.hasPrefix(".") || structuralFiles.contains(rel) || first == "scripts" || first == "hooks"
    }

    /// Every non-structural file and folder under `vault` (relative path, name and
    /// stem), and the owner's identity. Bounded by `maxEntries`.
    static func vaultNames(_ vault: URL, maxEntries: Int) -> [String: String] {
        var out: [String: String] = [:]
        let root = vault.standardizedFileURL.resolvingSymlinksInPath()
        let rootPath = root.path.hasSuffix("/") ? root.path : root.path + "/"
        let fm = FileManager.default
        if let walker = fm.enumerator(at: root, includingPropertiesForKeys: [.isDirectoryKey], options: []) {
            var count = 0
            for case let url as URL in walker {
                count += 1
                if count > maxEntries { break }
                let full = url.standardizedFileURL.resolvingSymlinksInPath().path
                guard full.hasPrefix(rootPath) else { continue }
                let rel = String(full.dropFirst(rootPath.count))
                if [".git", "node_modules"].contains(url.lastPathComponent) || isStructural(rel) {
                    if (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
                        walker.skipDescendants()
                    }
                    continue
                }
                out[rel] = out[rel] ?? "<path>"
                out[url.lastPathComponent] = out[url.lastPathComponent] ?? "<name>"
                let stem = url.deletingPathExtension().lastPathComponent
                if stem != url.lastPathComponent { out[stem] = out[stem] ?? "<name>" }
            }
        }
        for value in ownerIdentity(vault) {
            out[value] = "<owner>"
        }
        return out
    }

    /// `user.name`, its words, and `user.email` from `scout-config.yaml` (a line scan, no YAML parser).
    static func ownerIdentity(_ vault: URL) -> [String] {
        guard let text = try? String(contentsOf: vault.appendingPathComponent("scout-config.yaml"), encoding: .utf8)
        else { return [] }
        var out: [String] = []
        var inUser = false
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if !line.hasPrefix(" ") && !line.hasPrefix("\t") { inUser = line.hasPrefix("user:") ; continue }
            guard inUser else { continue }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            for key in ["name:", "email:"] where trimmed.hasPrefix(key) {
                var value = trimmed.dropFirst(key.count).trimmingCharacters(in: .whitespaces)
                value = value.trimmingCharacters(in: CharacterSet(charactersIn: "'\""))
                guard !value.isEmpty else { continue }
                out.append(value)
                out.append(contentsOf: value.split(separator: " ").map(String.init).filter { $0.count >= 4 })
            }
        }
        return out
    }

    // MARK: - Scrubbing

    private static func regex(_ pattern: String) -> NSRegularExpression {
        // Literal patterns below; a failure is a programming error caught by the tests.
        try! NSRegularExpression(pattern: pattern)  // swiftlint:disable:this force_try
    }

    private static let secretRules: [(NSRegularExpression, String)] = [
        (regex("bot\\d+(?::|%3[Aa])[A-Za-z0-9_-]+"), "bot<redacted>"),
        (regex("(?<![\\w])\\d{6,}(?::|%3[Aa])[A-Za-z0-9_-]{30,}"), "<redacted>"),
        (regex("xox[abposr]-[A-Za-z0-9-]+"), "xox-<redacted>"),
        (regex("(Bearer\\s+)[A-Za-z0-9._~+/=-]+"), "$1<redacted>"),
        (regex("sk-ant-[A-Za-z0-9_-]+"), "<redacted>"),
        (regex("\\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\\b"), "<redacted>"),
    ]
    private static let url = regex("https?://[^\\s'\"`<>)]+")
    private static let vaultPath = regex("<vault>/([^\\s'\"`,;:()]+)")
    private static let wikilink = regex("\\[\\[[^\\]\\n]*\\]\\]")
    private static let mdToken = regex("(?<![\\w/.<>-])[^\\s'\"`()<>\\[\\]]*\\.md\\b")
    private static let email = regex("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
    private static let opaque = regex("(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")

    private static func replace(_ re: NSRegularExpression, in text: String, _ transform: (String, NSTextCheckingResult) -> String) -> String {
        let ns = text as NSString
        var out = ""
        var last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            out += transform(ns.substring(with: m.range), m)
            last = m.range.location + m.range.length
        }
        out += ns.substring(from: last)
        return out
    }

    private static func pathVariants(_ url: URL) -> [String] {
        var out = [url.path]
        let resolved = url.resolvingSymlinksInPath().path
        if !out.contains(resolved) { out.append(resolved) }
        return out.map { $0.hasSuffix("/") && $0.count > 1 ? String($0.dropLast()) : $0 }
            .sorted { $0.count > $1.count }
    }

    func scrub(_ input: String) -> String {
        var text = input
        for (re, template) in Self.secretRules {
            text = re.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: template)
        }
        text = Self.replace(Self.url, in: text) { match, _ in
            Self.projectURLPrefixes.contains { match.hasPrefix($0) } ? match : "<url>"
        }
        if let vault {
            for variant in Self.pathVariants(vault) { text = text.replacingOccurrences(of: variant, with: "<vault>") }
        }
        if let home {
            for variant in Self.pathVariants(home) { text = text.replacingOccurrences(of: variant, with: "~") }
        }
        // Names first: a note or folder name can hold spaces, which end the generic path rule's match.
        if let namePattern {
            text = Self.replace(namePattern, in: text) { match, _ in names[match] ?? "<name>" }
        }
        text = Self.replace(Self.vaultPath, in: text) { match, _ in
            let rel = String(match.dropFirst("<vault>/".count))
            return Self.isStructural(rel) ? match : "<vault>/<path>"
        }
        text = Self.wikilink.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "[[<note>]]")
        text = Self.replace(Self.mdToken, in: text) { match, _ in
            Self.structuralMarkdown.contains(match.components(separatedBy: "/").last ?? match) ? match : "<note>.md"
        }
        text = Self.email.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "<email>")
        text = Self.opaque.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "<redacted>")
        return text
    }

    /// Scrub each line and cut long ones.
    func scrubLines(_ input: String) -> String {
        input.split(separator: "\n", omittingEmptySubsequences: false).map { line in
            let s = scrub(String(line))
            return s.count > Self.maxLine ? String(s.prefix(Self.maxLine)) + "…" : s
        }.joined(separator: "\n")
    }
}
