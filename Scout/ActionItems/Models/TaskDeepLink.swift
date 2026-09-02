import Foundation

enum TaskDeepLink: Equatable, Hashable, Sendable, Identifiable {
    case linear(id: String)
    case githubPR(repo: String, number: Int, rawURL: URL)
    case slackThread(URL)
    /// A knowledge-base entity referenced from a task's `- Refs:` sub-bullet,
    /// e.g. `[[people/alex]]` or `[[people/alex|Alex]]`. `path` is the wikilink
    /// target; `label` is the optional display alias.
    case entity(path: String, label: String?)
    /// A cross-reference hashtag from the `- Refs:` sub-bullet, e.g. `#XREF` —
    /// points at another action item (by tag) in the same file. It resolves
    /// in-app rather than to an external URL, so `openURL` is `nil`.
    case crossRef(tag: String)
    /// An unrecognized `- Refs:` token, preserved verbatim so nothing is
    /// dropped. Renders as an inert plain chip; no URL.
    case plainRef(text: String)

    var id: String {
        switch self {
        case .linear(let id):               return "linear:\(id)"
        case .githubPR(let repo, let n, _): return "gh:\(repo)#\(n)"
        case .slackThread(let url):         return "slack:\(url.absoluteString)"
        case .entity(let path, _):          return "entity:\(path)"
        case .crossRef(let tag):            return "xref:\(tag)"
        case .plainRef(let text):           return "plain:\(text)"
        }
    }

    var displayLabel: String {
        switch self {
        case .linear(let id):               return "Linear \(id)"
        case .githubPR(let repo, let n, _): return "PR \(repo)#\(n)"
        case .slackThread:                  return "Slack thread"
        case .entity(let path, let label):
            return label ?? (path.split(separator: "/").last.map(String.init) ?? path)
        case .crossRef(let tag):            return "#\(tag)"
        case .plainRef(let text):           return text
        }
    }

    /// External URL to open, when one exists. `nil` for refs that resolve
    /// in-app (`crossRef`) or have no target (`plainRef`); such refs render as
    /// inert chips and are omitted from the expanded Links list.
    var openURL: URL? {
        switch self {
        case .linear(let id):
            let workspace = UserDefaults.standard.string(forKey: "linearWorkspace") ?? ""
            if workspace.isEmpty {
                return URL(string: "https://linear.app/")
            }
            return URL(string: "https://linear.app/\(workspace)/issue/\(id)")
        case .githubPR(_, _, let raw):
            return raw
        case .slackThread(let url):
            return url
        case .entity(let path, _):
            // Open the note the same way `InlineMarkdownText` opens a wikilink
            // when no in-app KB handler is present (Action Items surface).
            let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? path
            return URL(string: "obsidian://open?vault=Scout&file=\(encoded)")
        case .crossRef, .plainRef:
            return nil
        }
    }
}
