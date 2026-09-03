import Foundation

/// A fill-in slot inside a draft body — one `[TBD: …]` marker that Scout left
/// for the user to resolve before sending.
///
/// Scout writes unknowns into a draft body as `[TBD: <what to supply>]`. The app
/// extracts each one into a labeled input field; when the user fills it, the
/// app substitutes the typed value for the whole `[TBD: …]` literal in the body
/// (see ``ReplyDraftsWriter/fill``) so the email reads cleanly.
nonisolated struct DraftInput: Identifiable, Equatable, Sendable {
    /// The full literal to replace, e.g. `[TBD: confirm the meeting time]`.
    let placeholder: String
    /// The human prompt — the text after `TBD:`, used as the field label.
    let prompt: String
    /// Which occurrence of this exact `placeholder` text this is, counting from
    /// zero. Distinct placeholders each start at 0, so filling one marker never
    /// renumbers a differently-worded one. Passed to
    /// ``ReplyDraftsWriter/fill`` so the right instance is replaced.
    let occurrence: Int

    /// Stable identity: the placeholder text plus its occurrence *among
    /// identical placeholders*. Deliberately not the marker's position in the
    /// body — a global ordinal (or a character offset) shifts for every later
    /// marker as soon as an earlier one is filled, which strands the value the
    /// user already typed into those fields.
    var id: String { "\(occurrence):\(placeholder)" }

    /// Compiled once — `extract` is called for every draft parse, and a fresh
    /// `NSRegularExpression` per call is pure overhead.
    private static let marker = try? NSRegularExpression(pattern: #"\[TBD:\s*([^\]]*)\]"#)

    /// Extract every `[TBD: …]` marker from a draft body, in order of
    /// appearance. Identical markers each get their own entry (distinguished by
    /// ``occurrence``) so two same-worded TBDs can be filled independently.
    static func extract(from body: String) -> [DraftInput] {
        guard let re = marker else { return [] }
        let ns = body as NSString
        let matches = re.matches(in: body, range: NSRange(location: 0, length: ns.length))
        var out: [DraftInput] = []
        var seen: [String: Int] = [:]
        for m in matches {
            let full = ns.substring(with: m.range)
            let prompt = m.numberOfRanges > 1
                ? ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespacesAndNewlines)
                : full
            let occurrence = seen[full, default: 0]
            seen[full] = occurrence + 1
            out.append(DraftInput(placeholder: full, prompt: prompt, occurrence: occurrence))
        }
        return out
    }
}
