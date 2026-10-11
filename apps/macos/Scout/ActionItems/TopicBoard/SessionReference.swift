import Foundation

/// A `- Session:` value, checked before the app acts on it. The daily file is
/// written by runs from outside data (Slack, PR bodies), so only a claude.ai
/// link opens and only a UUID session id resumes: anything else, such as a
/// value starting with `-`, would reach `claude --resume` as a flag.
nonisolated enum SessionReference: Equatable, Sendable {
    case cloud(URL)
    case local(id: String)
    case unknown(String)

    private static let uuidRe = try! NSRegularExpression(
        pattern: #"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z"#
    )

    var isActionable: Bool {
        if case .unknown = self { return false }
        return true
    }

    init(_ raw: String) {
        let value = raw.trimmingCharacters(in: .whitespaces)
        if let url = URL(string: value), url.scheme == "https", url.host == "claude.ai",
           url.port == nil, url.user == nil, url.password == nil,
           url.path.hasPrefix("/code/") || url.path.hasPrefix("/chat/") {
            self = .cloud(url)
        } else if Self.uuidRe.firstMatch(in: value, range: NSRange(location: 0, length: (value as NSString).length)) != nil {
            self = .local(id: value)
        } else {
            self = .unknown(value)
        }
    }
}
