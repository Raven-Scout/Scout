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

    /// One `<details>` region lifted out of the live lists.
    ///
    /// The action-items sessions archive superseded focus lists and parked
    /// run-groups inside `<details><summary>…</summary>` so Obsidian collapses
    /// them. That content is history, not today's work, so it must not reach
    /// `tasks`/`bullets` — but it isn't disposable either (a real "Parked"
    /// block held 124 open rows under the summary "Expand to work them"), so
    /// it lands here instead and the section renders it behind a disclosure.
    ///
    /// Nested regions are flattened into their outermost group: the vault
    /// nests up to nine deep, and nine levels of disclosure is worse than one.
    struct CollapsedGroup: Identifiable, Equatable, Hashable, Sendable {
        /// Deterministic across reparses — see ``ActionItemsParser/stableID(_:)``.
        let id: UUID
        /// `<summary>` text with its inner HTML stripped, or empty when the
        /// source `<details>` carried no summary.
        let summary: String
        let tasks: [ActionTask]
        let bullets: [String]
        /// Archived tables — 📅 Meetings files each day's as-run table under a
        /// `<details>`, and those must not stack below today's.
        let tables: [Table]
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
    /// `<details>` archive regions, in source order. Never merged into
    /// `tasks`/`bullets` — counts, filters, and the urgent badge must all see
    /// live work only.
    let collapsed: [CollapsedGroup]
}
