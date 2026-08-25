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

    /// Stable identity. Placeholders are distinct by their full text; an index
    /// suffix disambiguates two identical prompts in the same body.
    let id: String

    /// Extract every `[TBD: …]` marker from a draft body, in order of
    /// appearance. Identical markers each get their own entry (disambiguated id)
    /// so two same-worded TBDs can be filled independently.
    static func extract(from body: String) -> [DraftInput] {
        guard let re = try? NSRegularExpression(pattern: #"\[TBD:\s*([^\]]*)\]"#) else { return [] }
        let ns = body as NSString
        let matches = re.matches(in: body, range: NSRange(location: 0, length: ns.length))
        var out: [DraftInput] = []
        for (i, m) in matches.enumerated() {
            let full = ns.substring(with: m.range)
            let prompt = m.numberOfRanges > 1
                ? ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespacesAndNewlines)
                : full
            out.append(DraftInput(placeholder: full, prompt: prompt, id: "\(i):\(full)"))
        }
        return out
    }
}
