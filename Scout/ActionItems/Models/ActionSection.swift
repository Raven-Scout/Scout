import Foundation

struct ActionSection: Identifiable, Equatable, Hashable, Sendable {
    enum Kind: String, Equatable, Hashable, Sendable {
        case urgent, todo, watching, personal
        case focus, meetings, done, digest
        case neutral
    }

    struct Table: Equatable, Hashable, Sendable {
        let headers: [String]
        let rows: [[String]]
    }

    /// Deterministic across reparses: derived by `ActionItemsParser.stableID`
    /// from the section's index, kind, and title, so SwiftUI keeps the section
    /// identity stable through write-triggered reparses (avoiding a scroll
    /// reset). Not a durable identifier — renaming/reordering re-derives it.
    let id: UUID
    /// Section heading emoji (e.g. "🔴"), or empty for plain-title sections.
    let emoji: String
    /// Section heading title without the emoji prefix.
    let title: String
    let kind: Kind
    let tasks: [ActionTask]
    /// Non-task bullets (used in 💡 Focus and 📋 Digest).
    let bullets: [String]
    /// Tables (used in 📅 Meetings).
    let tables: [Table]
    /// `### subheads` found inside this section.
    let subheads: [String]
}
