import Foundation

/// What `scoutctl inbox review … --json` prints (engine `scout/delegate_review.py`, #550 V4b3): the proposals
/// the owner's agents sent that wait for review, the observations already accepted, and the previews that bind
/// a promote or an unpromote to the exact text the owner saw.
///
/// A contract with the engine: `ScoutTests/Fixtures/inbox-review.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/inbox-review.json`. Every text field is an agent's: the engine has
/// already made hidden characters visible (`\u{…}`), and the app shows it as plain, monospaced text, never
/// Markdown and never with links detected. Status values stay strings, so a value this build doesn't know never
/// fails the decode.
nonisolated enum InboxReview {
    /// One proposal waiting for review, or a promote or unpromote preview of an accepted observation.
    nonisolated struct Item: Equatable, Sendable, Decodable, Identifiable {
        let delegate: String
        let ulid: String
        /// observation | comment | op
        let kind: String
        /// waiting | held | limited, or accepted | promoted for a preview
        let status: String
        let proposed: String
        let madeAt: String
        let op: String?
        let tag: String?
        let date: String?
        /// The agent's text, hidden characters shown as `\u{…}`.
        let text: String
        let about: String?
        let source: String?
        /// Words that mix scripts or use another script than the text's: possible look-alikes.
        let lookAlikes: [String]
        /// A preview's hash of the note's bytes, which the app passes back as `--previewed`.
        let sha256: String?
        /// A preview's delegates: who would see it (promote), or who sees it now (unpromote).
        let audience: [String]?

        var id: String { ulid }

        /// Only an observation waiting or a held comment can be accepted; an op over the day's limit can only be
        /// rejected (the next day's apply retries it otherwise).
        var canAccept: Bool { (kind == "observation" && status == "waiting") || (kind == "comment" && status == "held") }

        private enum CodingKeys: String, CodingKey {
            case delegate, ulid, kind, status, proposed, op, tag, date, text, about, source, sha256, audience
            case madeAt = "made_at", lookAlikes = "look_alikes"
        }
    }

    /// An accepted observation: ids and times only. Its text comes from a preview.
    nonisolated struct Accepted: Equatable, Sendable, Decodable, Identifiable {
        let delegate: String
        let ulid: String
        let note: String
        let acceptedAt: String?
        let promoted: Bool
        /// nil: every delegate that can see it (or not promoted).
        let promotedTo: [String]?
        let promotedAt: String?

        var id: String { ulid }

        private enum CodingKeys: String, CodingKey {
            case delegate, ulid, note, promoted
            case acceptedAt = "accepted_at", promotedTo = "promoted_to", promotedAt = "promoted_at"
        }
    }

    /// What an accept, reject, promote or unpromote did.
    nonisolated struct Decision: Equatable, Sendable, Decodable {
        let ok: Bool
        let delegate: String
        let ulid: String
        /// accepted | rejected | promoted | unpromoted
        let outcome: String
        let note: String?
        let notes: [String]
    }

    /// A refused command: its code, and the engine's own sentence, which the app shows as it is.
    nonisolated struct Refusal: Equatable, Sendable, Decodable {
        let ok: Bool
        let code: String
        let message: String
    }

    static func items(_ data: Data) -> [Item]? { try? JSONDecoder().decode([Item].self, from: data) }
    static func accepted(_ data: Data) -> [Accepted]? { try? JSONDecoder().decode([Accepted].self, from: data) }
    static func preview(_ data: Data) -> Item? {
        guard let item = try? JSONDecoder().decode(Item.self, from: data), let sha = item.sha256, isSHA256(sha),
              item.audience != nil else { return nil }
        return item
    }
    static func decision(_ data: Data) -> Decision? {
        guard let d = try? JSONDecoder().decode(Decision.self, from: data), d.ok else { return nil }
        return d
    }
    static func refusal(_ data: Data) -> Refusal? {
        guard let r = try? JSONDecoder().decode(Refusal.self, from: data), !r.ok,
              DelegateStatus.isRefusalCode(r.code) else { return nil }
        return r
    }

    /// A proposal's ULID as the engine writes one, matched whole. The app passes only these to scoutctl.
    static func isULID(_ text: String) -> Bool { wholeMatch(text, #"[0-7][0-9A-HJKMNP-TV-Z]{25}"#) }

    /// A preview's sha256, matched whole.
    static func isSHA256(_ text: String) -> Bool { wholeMatch(text, #"[0-9a-f]{64}"#) }

    private static func wholeMatch(_ text: String, _ pattern: String) -> Bool {
        guard let regex = try? Regex(pattern) else { return false }
        return (try? regex.wholeMatch(in: text)) != nil
    }

    // MARK: - Words

    /// The header every agent's text is shown under.
    static func header(_ item: Item) -> String {
        var what = item.kind
        if let op = item.op, item.kind != "observation" {
            what = "\(item.kind) \(op)" + (item.tag.map { " [#\($0)]" } ?? "") + (item.date.map { " \($0)" } ?? "")
        }
        return "FROM AGENT delegate-\(item.delegate) · \(what) · \(item.status)"
    }

    static func names(_ ids: [String]) -> String { ids.map { "delegate-\($0)" }.joined(separator: ", ") }

    /// The accept confirmation's button: it names the agent the text comes from.
    static func acceptButton(_ item: Item) -> String { "Accept from delegate-\(item.delegate)" }

    /// The promote confirmation's button: it names every delegate who will see the note. nil when none would.
    static func promoteButton(_ preview: Item) -> String? {
        guard let audience = preview.audience, !audience.isEmpty else { return nil }
        return "Promote to \(names(audience))"
    }

    /// The unpromote confirmation's button: it names every delegate who stops seeing the note.
    static func unpromoteButton(_ preview: Item) -> String {
        guard let audience = preview.audience, !audience.isEmpty else { return "Make private again" }
        return "Make private again: hide from \(names(audience))"
    }

    static let acceptedObservationNote = "An accepted observation stays private: no agent sees it, its author "
        + "included, until you promote it. Editing it by hand rules out promoting it."
}
