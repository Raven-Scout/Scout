import Foundation

/// The author handle in a comment line, `  - <handle>: <text>`. The app's
/// parser and the engine's both accept only `[A-Za-z][A-Za-z0-9._-]*` there,
/// so the name from Settings is folded into that shape: transliterated to
/// ASCII (Ł → L, ø → o, ß → ss, other scripts romanized), spaces turned into
/// dashes, anything else removed, leading digits skipped. An empty result
/// falls back to "user", the Settings default.
nonisolated enum CommentAuthor {
    static let fallback = "user"

    static func handle(_ name: String) -> String {
        // Diacritic folding alone drops letters that aren't accented forms
        // (Ł, ø, ß, Æ) and every non-Latin script; transliterate first.
        let latin = name.applyingTransform(StringTransform("Any-Latin; Latin-ASCII"), reverse: false) ?? name
        let folded = latin.folding(options: [.diacriticInsensitive, .widthInsensitive], locale: Locale(identifier: "en_US_POSIX"))
        var out = ""
        var pendingDash = false
        for scalar in folded.unicodeScalars {
            let v = scalar.value
            // Whitespace and dashes both separate words; a run of them
            // becomes one dash ("Alex - Rivera" → "Alex-Rivera").
            if scalar.properties.isWhitespace || v == 45 {
                pendingDash = !out.isEmpty
                continue
            }
            let isLetter = (65...90).contains(v) || (97...122).contains(v)
            let isDigit = (48...57).contains(v)
            let isMark = v == 46 || v == 95   // . _
            guard isLetter || isDigit || isMark else { continue }
            if out.isEmpty && !isLetter { continue }
            if pendingDash {
                out.append("-")
                pendingDash = false
            }
            out.unicodeScalars.append(scalar)
        }
        return out.isEmpty ? fallback : out
    }

    /// Whether a parsed comment was written by this user. Comments written
    /// through scoutctl carry the handle; inline `//==<< … >>==//` comments
    /// carry the raw Settings name, so either counts.
    static func isOwn(commentAuthor: String, userName: String) -> Bool {
        let author = commentAuthor.lowercased()
        return author == handle(userName).lowercased()
            || author == userName.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}
