import Foundation

/// Removes vault content, personal names and secrets from diagnostics text
/// (app audit C11). The engine's `scoutctl diagnostics` scrubs its own report
/// with the same rules (`plugin/engine/scout/diagnostics.py`); the app scrubs
/// every line of the bundle again, its own logs included, so one side
/// missing a case can't leak. Keep the two in step.
///
/// Rules, in order: Unicode NFC and `\/` → `/`; credential shapes; `file://`
/// URLs and percent-encoding decoded; `git@host:owner/repo` → `git@host:<repo>`;
/// URLs lose their query and fragment, and any but the project's own become
/// `<url>`; `key=value` secrets; the vault and home folders (any spelling,
/// any case) → `<vault>` and `~`; a path after either runs to the next quote,
/// bracket or end of line and becomes `<path>` unless it is Scout's own;
/// other users' folders → `/Users/<user>`; then every note, folder and
/// identity name as a whole word; `[[wikilinks]]`; quoted spans; `*.md`
/// names; emails; long opaque strings. Long lines are cut.
nonisolated struct DiagnosticsScrubber: Sendable {
    static let maxLine = 400

    /// Absolute path prefixes → placeholder, longest first.
    let paths: [(String, String)]
    /// Names hidden as whole words anywhere.
    let names: [String: String]
    /// Short plain names hidden only as a path segment (next to a "/").
    let segments: [String: String]
    /// Names hidden even inside a word: scripts written without spaces between words (CJK).
    let loose: [String: String]
    private let pathPattern: NSRegularExpression?
    private let namePattern: NSRegularExpression?
    private let segmentPattern: NSRegularExpression?
    private let loosePattern: NSRegularExpression?

    // MARK: Structure (keep in step with diagnostics.py)

    /// The only dot-folders whose paths are kept whole. `.trash`, `.obsidian`, `.claude`… are the owner's.
    static let scoutDotDirs: Set<String> = [".scout-state", ".scout-logs", ".scout-cache", ".git"]
    static let structuralFiles: Set<String> = [
        "scout-config.yaml", "connectors.custom.yaml", "CLAUDE.md", "SKILL.md", "DREAMING.md", "RESEARCH.md",
        "knowledge-base/ontology/parser.py", "action-items/render.py",
    ]
    static let structuralTopDirs: Set<String> = ["scripts", "hooks"]
    /// Inside .git only these are Scout's business (lock and HEAD errors); refs and the rest can name the owner's work.
    static let gitFiles: Set<String> = [
        ".git", ".git/index.lock", ".git/HEAD.lock", ".git/HEAD", ".git/config", ".git/index", ".git/ORIG_HEAD",
        ".git/FETCH_HEAD", ".git/MERGE_HEAD",
    ]
    static let peopleDir = "knowledge-base/people/"
    /// Scout's own vocabulary, kept when a message quotes it ("must be one of ['mon', …]").
    static let keywords: Set<String> = [
        "mon", "tue", "wed", "thu", "fri", "sat", "sun", "fire", "skip", "collapse", "true", "false", "yes", "no",
        "on", "off", "inbound", "outbound", "lookup", "owner", "autonomous", "claude-code", "anthropic",
        "briefing", "consolidation", "dreaming", "research", "manual", "ok", "failed", "unknown", "timeout",
        "budget", "oauth_expired", "auth_race", "network", "git_lock", "rate_limited", "enforced", "unavailable", "conflict",
        "clean", "restored", "unrestored", "error", "unchecked",
    ]
    /// Report fields built from allowlists by the engine. The app checks their shape instead of scrubbing them.
    static let structuredKeys: Set<String> = [
        "id", "severity", "source", "outcome", "failure_class", "sandbox", "integrity", "mode", "slot", "key", "type",
        "status", "runtime", "provider",
    ]
    /// Every key `scoutctl diagnostics` uses (keep in step with diagnostics.REPORT_KEYS). Others are scrubbed.
    static let reportKeys: Set<String> = [
        "schema", "generated_at", "engine", "version", "python", "platform", "vault", "exists", "schedule", "config",
        "git", "custom_slots", "doctor", "severity", "errors", "warnings", "notes", "run_health", "evaluated_at",
        "last_ok_at", "findings", "id", "since", "source", "until", "summary", "fix", "has_detail", "slots", "key",
        "type", "last_fire", "last_outcome", "outcome", "finished_at", "failure_class", "sandbox", "status", "reason",
        "runtime", "provider", "gate_probe", "ok", "checked_at", "claude_version", "recent_runs", "slot", "mode",
        "started_at", "exit_code", "integrity", "commits", "redaction", "unavailable",
    ]
    static let structuralMarkdown: Set<String> = ["CLAUDE.md", "SKILL.md", "DREAMING.md", "RESEARCH.md", "README.md"]
    /// Scout's own folder names: never hidden, though what is inside them is.
    static let keepWords: Set<String> = [
        "knowledge-base", "action-items", "ontology", "wishlist", "research",
        "proposals", "archive", "scripts", "hooks",
    ]
    /// Scout's and the toolchain's own folders. Only the first path segment after one is kept; anything deeper is `<path>`.
    static let homeKeepPrefixes = [
        ".local/share/scout/", ".local/state/scout/", "Library/Application Support/Scout/", "Library/Caches/com.scout.", "Library/Caches/Scout/",
        "Library/LaunchAgents/com.scout.", "Library/Logs/Scout/",
        ".local/bin/", ".claude/plugins/", ".scout-secrets/", ".scout/",
    ]
    static let genericHomeNames: Set<String> = ["", "home", "Users", "root", "var", "tmp"]
    static let projectURLPrefixes = [
        "https://github.com/Raven-Scout/", "https://raven-scout.github.io/",
        "https://docs.anthropic.com/", "https://code.claude.com/",
    ]

    init(paths: [(String, String)] = [], names: [String: String] = [:], segments: [String: String] = [:],
         loose: [String: String] = [:]) {
        var uniquePaths: [String: String] = [:]
        for (p, ph) in paths {
            let trimmed = p.hasSuffix("/") && p.count > 1 ? String(p.dropLast()) : p
            if trimmed.count > 1 { uniquePaths[trimmed.precomposedStringWithCanonicalMapping] = ph }
        }
        self.paths = uniquePaths.sorted { $0.key.count > $1.key.count }.map { ($0.key, $0.value) }
        self.names = names
        self.segments = segments
        self.loose = loose
        loosePattern = Self.alternation(Array(loose.keys), prefix: "", suffix: "")
        let delim = Self.delimiterClass
        pathPattern = Self.alternation(self.paths.map(\.0), prefix: "", suffix: "(?![^/\\s\(delim)])", options: [.caseInsensitive])
        namePattern = Self.alternation(Array(names.keys), prefix: "(?<![\\w])", suffix: "(?![\\w])")
        let segAlts = segments.keys.sorted { $0.count > $1.count }.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
        segmentPattern = segments.isEmpty ? nil
            : try? NSRegularExpression(pattern: "(?:(?<=/)(?:\(segAlts))(?![\\w])|(?<![\\w])(?:\(segAlts))(?=/))")
    }

    private static func alternation(_ items: [String], prefix: String, suffix: String,
                                     options: NSRegularExpression.Options = []) -> NSRegularExpression? {
        guard !items.isEmpty else { return nil }
        let alts = items.sorted { $0.count > $1.count }.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
        return try? NSRegularExpression(pattern: "\(prefix)(?:\(alts))\(suffix)", options: options)
    }

    /// A scrubber for `vault`: its paths, every note and folder name under it,
    /// the owner, delegates, custom connectors, and this Mac's user.
    static func forVault(_ vault: URL, home: URL? = FileManager.default.homeDirectoryForCurrentUser,
                         maxEntries: Int = 50_000) -> DiagnosticsScrubber {
        var b = Builder()
        for variant in Set([vault.standardizedFileURL.path, vault.resolvingSymlinksInPath().path]) {
            b.addPath(variant, "<vault>")
            b.add(encoded(variant), "<vault>", anywhere: true)
        }
        let vaultName = vault.lastPathComponent
        if vaultName != "Scout" { b.add(vaultName, "<vault-name>", anywhere: true) }
        let parts = vault.standardizedFileURL.pathComponents
        if parts.count > 2, parts[1] == "Volumes" { b.add(parts[2], "<volume>", anywhere: true) }
        for h in homes(home) {
            b.addPath(h.path, "~")
            if !genericHomeNames.contains(h.lastPathComponent) { b.add(h.lastPathComponent, "<user>", anywhere: true) }
        }
        b.add(NSUserName(), "<user>", anywhere: true)
        for part in NSFullUserName().split(whereSeparator: { $0 == " " || $0 == "," }) where part.count >= 2 {
            b.add(String(part), "<user>", anywhere: true)
        }
        identityNames(vault, into: &b)
        walkNames(vault, maxEntries: maxEntries, into: &b)
        return b.build()
    }

    /// Collects paths and names, sorting each name into `names` or `segments` the way the engine does.
    struct Builder {
        var paths: [(String, String)] = []
        var names: [String: String] = [:]
        var segments: [String: String] = [:]
        var loose: [String: String] = [:]

        mutating func addPath(_ path: String, _ placeholder: String) { paths.append((path, placeholder)) }

        mutating func add(_ raw: String, _ placeholder: String, anywhere: Bool = false) {
            let value = raw.trimmingCharacters(in: .whitespacesAndNewlines).precomposedStringWithCanonicalMapping
            guard !value.isEmpty, !DiagnosticsScrubber.keepWords.contains(value) else { return }
            if value.unicodeScalars.contains(where: DiagnosticsScrubber.isWide) {
                // No spaces between words in these scripts, so no word boundary to wait for.
                if loose[value] == nil { loose[value] = placeholder }
                return
            }
            guard value.count >= 2 else { return }
            let tail = value.dropFirst()
            let distinctive = value.contains { !$0.isLetter } || tail.contains { $0.isUppercase }
            let nonASCII = value.unicodeScalars.contains { !$0.isASCII }
            if anywhere || nonASCII || (value.count >= 5 && (distinctive || value.count >= 8)) || value.contains(" ") {
                if names[value] == nil { names[value] = placeholder }
            } else if value.count >= 3 {
                if segments[value] == nil { segments[value] = placeholder }
            }
        }

        func build() -> DiagnosticsScrubber {
            DiagnosticsScrubber(paths: paths, names: names, segments: segments, loose: loose)
        }
    }

    static func isStructural(_ relative: String) -> Bool {
        var rel = relative
        while let last = rel.last, "/:,;.".contains(last) { rel.removeLast() }
        let first = rel.split(separator: "/", maxSplits: 1).first.map(String.init) ?? rel
        if first == ".git" { return gitFiles.contains(rel) }
        return scoutDotDirs.contains(first) || structuralTopDirs.contains(first) || structuralFiles.contains(rel)
    }

    /// A letter of a script written without spaces between words (CJK and the like).
    static func isWide(_ scalar: Unicode.Scalar) -> Bool {
        guard scalar.properties.isAlphabetic else { return false }
        switch scalar.value {
        case 0x1100...0x115F, 0x2E80...0xA4CF, 0xAC00...0xD7A3, 0xF900...0xFAFF, 0xFF00...0xFF60, 0x20000...0x3FFFD:
            return true
        default:
            return false
        }
    }

    /// Claude Code's folder name for a cwd: every non-alphanumeric becomes "-".
    static func encoded(_ path: String) -> String {
        String(path.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) && $0.isASCII ? Character($0) : "-" })
    }

    /// The home folder by every route: the argument, the process's, and the password database's.
    static func homes(_ home: URL?) -> [URL] {
        var out: [URL] = []
        var candidates: [URL] = [home, FileManager.default.homeDirectoryForCurrentUser].compactMap { $0 }
        if let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir {
            candidates.append(URL(fileURLWithPath: String(cString: dir), isDirectory: true))
        }
        for c in candidates where !out.contains(where: { $0.standardizedFileURL.path == c.standardizedFileURL.path }) {
            out.append(c)
        }
        return out
    }

    /// Every file and folder under `vault` that isn't Scout's (dot-folders other than Scout's own included).
    static func walkNames(_ vault: URL, maxEntries: Int, into b: inout Builder) {
        let root = vault.standardizedFileURL.resolvingSymlinksInPath()
        let rootPath = root.path.hasSuffix("/") ? root.path : root.path + "/"
        guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isDirectoryKey], options: [])
        else { return }
        var count = 0
        for case let url as URL in walker {
            count += 1
            if count > maxEntries { break }
            let full = url.standardizedFileURL.resolvingSymlinksInPath().path
            guard full.hasPrefix(rootPath) else { continue }
            let rel = String(full.dropFirst(rootPath.count))
            if [".git", "node_modules", "__pycache__"].contains(url.lastPathComponent) || isStructural(rel) {
                if (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true { walker.skipDescendants() }
                continue
            }
            b.add(rel, "<path>", anywhere: true)
            // A person's name is short and plain ("Priya"), and the most sensitive name there is: hide it anywhere.
            let person = rel.hasPrefix(peopleDir)
            b.add(url.lastPathComponent, "<name>", anywhere: person)
            let stem = url.deletingPathExtension().lastPathComponent
            if stem != url.lastPathComponent { b.add(stem, "<name>", anywhere: person) }
        }
    }

    // MARK: Identity (port of diagnostics._identity_names)

    static func identityNames(_ vault: URL, into b: inout Builder) {
        let owner = ownerIdentity(vault)
        if let name = owner.name {
            b.add(name, "<owner>", anywhere: true)
            for part in name.split(separator: " ") { b.add(String(part), "<owner>", anywhere: true) }
        }
        if let email = owner.email {
            b.add(email, "<owner>", anywhere: true)
            let local = email.split(separator: "@").first.map(String.init) ?? ""
            for part in local.split(whereSeparator: { "._+-".contains($0) }) where part.count >= 3 {
                b.add(String(part), "<owner>", anywhere: true)
            }
        }
        for key in customConnectorNames(vault) { b.add(key, "<custom-connector>", anywhere: true) }
        for key in delegateIDs(vault) { b.add(key, "<delegate>", anywhere: true) }
    }

    /// A YAML scalar as written: quotes removed, an inline `# comment` dropped.
    static func yamlScalar(_ raw: Substring) -> String {
        let s = raw.trimmingCharacters(in: .whitespaces)
        if let q = s.first, q == "\"" || q == "'" {
            let body = s.dropFirst()
            if let end = body.firstIndex(of: q) { return String(body[..<end]) }
            return String(body)
        }
        if let hash = s.range(of: " #") { return String(s[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces) }
        return s
    }

    /// `user.name` and `user.email` from `scout-config.yaml`, block or flow style.
    static func ownerIdentity(_ vault: URL) -> (name: String?, email: String?) {
        guard let text = try? String(contentsOf: vault.appendingPathComponent("scout-config.yaml"), encoding: .utf8)
        else { return (nil, nil) }
        var fields: [String: String] = [:]
        var inUser = false
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            let isTop = !(line.hasPrefix(" ") || line.hasPrefix("\t"))
            if isTop {
                inUser = false
                guard line.hasPrefix("user:") else { continue }
                let rest = line.dropFirst("user:".count).trimmingCharacters(in: .whitespaces)
                if rest.hasPrefix("{"), let close = rest.lastIndex(of: "}") {
                    // Flow style: user: {name: Ann Lee, email: ann@example.org}
                    for pair in rest[rest.index(after: rest.startIndex)..<close].split(separator: ",") {
                        let kv = pair.split(separator: ":", maxSplits: 1)
                        if kv.count == 2 { fields[kv[0].trimmingCharacters(in: .whitespaces)] = yamlScalar(kv[1]) }
                    }
                } else {
                    inUser = true
                }
                continue
            }
            guard inUser else { continue }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            for key in ["name", "email"] where trimmed.hasPrefix(key + ":") {
                fields[key] = yamlScalar(trimmed.dropFirst(key.count + 1))
            }
        }
        let name = fields["name"].flatMap { $0.isEmpty ? nil : $0 }
        let email = fields["email"].flatMap { $0.isEmpty ? nil : $0 }
        return (name, email)
    }

    /// Keys under `connectors:` in `connectors.custom.yaml` (or its top-level keys, without that block).
    static func customConnectorNames(_ vault: URL) -> [String] {
        guard let text = try? String(contentsOf: vault.appendingPathComponent("connectors.custom.yaml"), encoding: .utf8)
        else { return [] }
        var out: [String] = []
        var inBlock = false
        var childIndent: Int?
        let hasBlock = text.split(separator: "\n").contains { $0.hasPrefix("connectors:") }
        for raw in text.split(separator: "\n") {
            let line = String(raw)
            let indent = line.prefix { $0 == " " }.count
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, !trimmed.hasPrefix("#"), let colon = trimmed.firstIndex(of: ":") else { continue }
            let key = yamlScalar(trimmed[..<colon])
            if indent == 0 {
                inBlock = key == "connectors"
                childIndent = nil
                if !hasBlock, key != "schema_version" { out.append(key) }
                continue
            }
            guard inBlock else { continue }
            if childIndent == nil { childIndent = indent }
            if indent == childIndent { out.append(key) }
        }
        return out
    }

    /// Delegate ids from `.scout-state/delegate-mirrors.json` and `.scout-state/privacy-policy.json`.
    static func delegateIDs(_ vault: URL) -> [String] {
        var out: [String] = []
        for rel in [".scout-state/delegate-mirrors.json", ".scout-state/privacy-policy.json"] {
            guard let data = try? Data(contentsOf: vault.appendingPathComponent(rel)),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
            var containers: [Any?] = [obj["links"], obj["delegates"], (obj["body"] as? [String: Any])?["links"]]
            if let privacy = obj["privacy"] as? [String: Any] { containers.append(privacy["delegates"]) }
            for case let dict as [String: Any] in containers { out.append(contentsOf: dict.keys) }
        }
        return out
    }

    // MARK: - Scrubbing

    private static func regex(_ pattern: String, _ options: NSRegularExpression.Options = []) -> NSRegularExpression {
        // Literal patterns below; a failure is a programming error the tests catch.
        try! NSRegularExpression(pattern: pattern, options: options)  // swiftlint:disable:this force_try
    }

    /// The characters a path never runs past: quotes, brackets, newline.
    static let delimiterClass = "'\"`<>()\\[\\]\\n"
    private static let secretRules: [(NSRegularExpression, String)] = [
        (regex("bot\\d+(?::|%3[Aa])[A-Za-z0-9_-]+"), "bot<redacted>"),
        (regex("(?<![\\w])\\d{6,}(?::|%3[Aa])[A-Za-z0-9_-]{30,}"), "<redacted>"),
        (regex("xox[abposr]-[A-Za-z0-9-]+"), "xox-<redacted>"),
        (regex("(Bearer\\s+)[A-Za-z0-9._~+/=-]+"), "$1<redacted>"),
        (regex("sk-ant-[A-Za-z0-9_-]+"), "<redacted>"),
        (regex("\\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\\b"), "<redacted>"),
    ]
    private static let keyValueSecret = regex(
        "(?i)\\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd|client[_-]?secret)\\s*[=:]\\s*)[^\\s&'\"`,;]+")
    private static let fileURL = regex("file://([^\\s'\"`<>]+)")
    private static let scpRemote = regex("\\b[\\w.-]+@([\\w.-]+):[^\\s'\"`]+")
    private static let url = regex("https?://[^\\s'\"`<>)]+")
    private static let vaultPath = regex("<vault>/([^\(delimiterClass)]*)")
    private static let homePath = regex("(?<![\\w/])~/([^\(delimiterClass)]*)")
    private static let usersPath = regex("(?<![\\w])(/Users|/home)/(?!<user>)[^/\\s\(delimiterClass)]+((?:/[^\(delimiterClass)]*)?)")
    /// External volumes and temporary folders: everything after the root is `<path>`.
    private static let otherRoots = regex("(?<![\\w/~>])(/Volumes|/private|/tmp|/var)/(?!<path>)[^\(delimiterClass)]*")
    private static let encodedUsers = regex("-(Users|home)-(?!<user>)[A-Za-z0-9._]+(-|$)")
    private static let wikilink = regex("\\[\\[[^\\]\\n]*\\]\\]")
    private static let quoted: [(NSRegularExpression, String)] = [
        (regex("(?<![\\w])'([^'\\n]{1,300})'(?![\\w])"), "'"),
        (regex("(?<![\\w\\\\])\"([^\"\\n]{1,300})\"(?![\\w])"), "\""),
    ]
    /// A note-like file named without spaces (foo.md, dir/foo.md, <name>/foo.md).
    private static let mdToken = regex("(?<![\\w/.-])[^\\s'\"`()\\[\\]]*\\.md\\b")
    private static let email = regex("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
    private static let opaque = regex("(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")

    private static func replace(_ re: NSRegularExpression, in text: String, _ transform: (String, NSTextCheckingResult, NSString) -> String) -> String {
        let ns = text as NSString
        var out = ""
        var last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            out += transform(ns.substring(with: m.range), m, ns)
            last = m.range.location + m.range.length
        }
        out += ns.substring(from: last)
        return out
    }

    private static func group(_ m: NSTextCheckingResult, _ i: Int, _ ns: NSString) -> String {
        let r = m.range(at: i)
        return r.location == NSNotFound ? "" : ns.substring(with: r)
    }

    private static func template(_ re: NSRegularExpression, _ text: String, _ t: String) -> String {
        re.stringByReplacingMatches(in: text, range: NSRange(location: 0, length: (text as NSString).length), withTemplate: t)
    }

    private static func secrets(_ text: String) -> String {
        secretRules.reduce(text) { template($1.0, $0, $1.1) }
    }

    /// A path under a home folder: a kept prefix and its first segment stay, anything deeper is `<path>`.
    /// Text after the first segment that doesn't continue the path (a space, then words) is ordinary text.
    static func homeRest(_ rest: String) -> String {
        guard let prefix = homeKeepPrefixes.first(where: { rest.hasPrefix($0) }) else { return "<path>" }
        let tail = rest.dropFirst(prefix.count)
        let segment = tail.prefix { $0 != "/" && !$0.isWhitespace }
        let after = tail.dropFirst(segment.count)
        return after.hasPrefix("/") ? prefix + segment + "/<path>" : prefix + segment + after
    }

    func scrub(_ input: String) -> String {
        var text = input.precomposedStringWithCanonicalMapping.replacingOccurrences(of: "\\/", with: "/")
        text = Self.secrets(text)
        text = Self.replace(Self.fileURL, in: text) { _, m, ns in
            Self.group(m, 1, ns).removingPercentEncoding ?? Self.group(m, 1, ns)
        }
        if text.contains("%"), let decoded = text.removingPercentEncoding { text = Self.secrets(decoded) }
        text = Self.replace(Self.scpRemote, in: text) { _, m, ns in "git@\(Self.group(m, 1, ns)):<repo>" }
        text = Self.replace(Self.url, in: text) { match, _, _ in
            let base = match.split(whereSeparator: { $0 == "?" || $0 == "#" }).first.map(String.init) ?? match
            return Self.projectURLPrefixes.contains { match.hasPrefix($0) } ? base : "<url>"
        }
        text = Self.template(Self.keyValueSecret, text, "$1<redacted>")
        if let pathPattern {
            let lookup = Dictionary(paths.map { ($0.0.lowercased(), $0.1) }, uniquingKeysWith: { a, _ in a })
            text = Self.replace(pathPattern, in: text) { match, _, _ in lookup[match.lowercased()] ?? "<path>" }
        } else {
            for (p, ph) in paths { text = text.replacingOccurrences(of: p, with: ph, options: .caseInsensitive) }
        }
        // Path rules before names: a path runs to the next quote, bracket or end of line, so a name
        // with spaces in it can't split it; names then catch what is left in free text.
        text = Self.replace(Self.vaultPath, in: text) { match, m, ns in
            let rest = Self.group(m, 1, ns)
            if rest.isEmpty || rest.hasPrefix("<path>") { return match }
            let token = rest.split(separator: " ", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? rest
            return Self.isStructural(token) ? "<vault>/" + rest : "<vault>/<path>"
        }
        text = Self.replace(Self.homePath, in: text) { match, m, ns in
            let rest = Self.group(m, 1, ns)
            if rest.isEmpty || rest.hasPrefix("<path>") { return match }
            return "~/" + Self.homeRest(rest)
        }
        text = Self.replace(Self.usersPath, in: text) { _, m, ns in
            let root = Self.group(m, 1, ns), rest = Self.group(m, 2, ns)
            if rest.isEmpty || rest == "/" { return "\(root)/<user>\(rest)" }
            return "\(root)/<user>/" + Self.homeRest(String(rest.dropFirst()))
        }
        text = Self.template(Self.otherRoots, text, "$1/<path>")
        text = Self.template(Self.encodedUsers, text, "-$1-<user>$2")
        text = replaceNames(text)
        text = Self.template(Self.wikilink, text, "[[<note>]]")
        for (re, q) in Self.quoted {
            text = Self.replace(re, in: text) { match, m, ns in
                let inner = Self.group(m, 1, ns)
                return inner.hasPrefix("<") || Self.keywords.contains(inner) ? match : "\(q)<quoted>\(q)"
            }
        }
        text = Self.replace(Self.mdToken, in: text) { match, _, _ in
            Self.structuralMarkdown.contains(match.components(separatedBy: "/").last ?? match) ? match : "<note>.md"
        }
        text = Self.template(Self.email, text, "<email>")
        return Self.template(Self.opaque, text, "<redacted>")
    }

    private func replaceNames(_ input: String) -> String {
        var text = input
        if let loosePattern {
            text = Self.replace(loosePattern, in: text) { match, _, _ in loose[match] ?? "<name>" }
        }
        if let namePattern {
            text = Self.replace(namePattern, in: text) { match, _, _ in names[match] ?? "<name>" }
        } else {
            // The pattern couldn't be built (too many names): fall back to plain replacement, longest first.
            for name in names.keys.sorted(by: { $0.count > $1.count }) {
                text = text.replacingOccurrences(of: name, with: names[name] ?? "<name>")
            }
        }
        if let segmentPattern {
            text = Self.replace(segmentPattern, in: text) { match, _, _ in segments[match] ?? "<name>" }
        }
        return text
    }

    /// Scrub each line and cut long ones.
    func scrubLines(_ input: String) -> String {
        input.split(separator: "\n", omittingEmptySubsequences: false).map { line in
            let s = scrub(String(line))
            return s.count > Self.maxLine ? String(s.prefix(Self.maxLine)) + "…" : s
        }.joined(separator: "\n")
    }

    /// Every string value in a JSON document scrubbed (keys kept), re-encoded with sorted keys and
    /// unescaped slashes, so escapes can't hide a path from the rules. nil when it isn't JSON.
    func scrubJSON(_ data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) else { return nil }
        let cleaned = scrubTree(object)
        guard JSONSerialization.isValidJSONObject(cleaned),
              let out = try? JSONSerialization.data(withJSONObject: cleaned,
                                                    options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        else { return nil }
        return String(data: out, encoding: .utf8)
    }

    /// An allowlisted field's value as the engine builds it: lowercase words, dots, colons, `custom-N`, `<other>`.
    private static let structuredValue = regex("^[a-z0-9_.:-]*(<other>)?$")

    private func scrubTree(_ value: Any, key: String? = nil) -> Any {
        switch value {
        case let s as String:
            // Allowlisted fields keep an enum-shaped value as is (scrubbing could only corrupt it), and
            // anything else in them is scrubbed like free text.
            if let key, Self.structuredKeys.contains(key),
               Self.structuredValue.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil {
                return s
            }
            let out = scrub(s)
            return out.count > Self.maxLine ? String(out.prefix(Self.maxLine)) + "…" : out
        case let d as [String: Any]:
            var out: [String: Any] = [:]
            var unknown = 0
            for k in d.keys.sorted() {
                // Every key is a fixed name today; one that isn't could carry content, so it is replaced.
                let name: String
                if Self.reportKeys.contains(k) { name = k } else { unknown += 1; name = "<key-\(unknown)>" }
                out[name] = scrubTree(d[k] as Any, key: k)
            }
            return out
        case let a as [Any]:
            return a.map { scrubTree($0, key: key) }
        default:
            return value
        }
    }
}
