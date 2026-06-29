import Foundation

/// The channel a reply is owed on, parsed from the `channel:` frontmatter field
/// of a `drafts/<TAG>.md` file. Drives the small channel badge and decides
/// whether a `subject:` is shown (email/PR titles) versus hidden (chat).
nonisolated enum DraftChannel: Equatable, Sendable {
    case email
    case slack
    case linear
    case github
    case whatsapp
    /// Any channel string we don't recognize; preserved verbatim for display.
    case other(String)

    static func parse(_ rawValue: String) -> DraftChannel {
        switch rawValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "email":    return .email
        case "slack":    return .slack
        case "linear":   return .linear
        case "github":   return .github
        case "whatsapp": return .whatsapp
        case let other:  return .other(other)
        }
    }

    var displayName: String {
        switch self {
        case .email:            return "Email"
        case .slack:            return "Slack"
        case .linear:           return "Linear"
        case .github:           return "GitHub"
        case .whatsapp:         return "WhatsApp"
        case .other(let raw):   return raw.isEmpty ? "—" : raw
        }
    }

    /// SF Symbol shown on the channel badge.
    var systemImage: String {
        switch self {
        case .email:    return "envelope"
        case .slack:    return "number"
        case .linear:   return "square.stack.3d.up"
        case .github:   return "chevron.left.forwardslash.chevron.right"
        case .whatsapp: return "message"
        case .other:    return "bubble.left"
        }
    }

    /// True for channels whose drafts carry a meaningful `subject:` (email
    /// subject, PR/issue title). Chat channels omit it.
    var usesSubject: Bool {
        switch self {
        case .email, .linear, .github: return true
        case .slack, .whatsapp, .other: return false
        }
    }
}
