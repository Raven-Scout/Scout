import Foundation

/// Which URLs from action-items text may be handed to the system
/// (Raven-Scout/Scout#435).
///
/// Action items are written by the model from Slack, mail, GitHub and Linear
/// content, so a markdown link's URL is attacker-reachable. `NSWorkspace.open`
/// launches whatever a URL names — a `file://` app bundle, another app's custom
/// scheme, an `obsidian://new` that writes a note — so only web and mail links
/// get through. The app's own `scout-wiki://` and `scout-tag://` links are
/// handled in-app and never reach `NSWorkspace`.
nonisolated enum LinkSchemePolicy {
    static let externalSchemes: Set<String> = ["http", "https", "mailto"]

    static func allowsExternalOpen(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), externalSchemes.contains(scheme) else { return false }
        return scheme == "mailto" || !(url.host ?? "").isEmpty
    }

    /// Characters left unescaped in the `file` value of an `obsidian://open`
    /// URL: the query set minus the ones that would end the value or start
    /// another parameter, so a wikilink target can't add `&vault=…`.
    static let obsidianFileValueAllowed: CharacterSet = {
        var set = CharacterSet.urlQueryAllowed
        set.remove(charactersIn: "&=+#")
        return set
    }()

    /// The Obsidian URL that opens `file` in the Scout vault.
    static func obsidianOpenURL(file: String) -> URL? {
        let encoded = file.addingPercentEncoding(withAllowedCharacters: obsidianFileValueAllowed) ?? file
        return URL(string: "obsidian://open?vault=Scout&file=\(encoded)")
    }
}
