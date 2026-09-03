import Foundation

/// Recognizes the HTML `<details>` markup the action-items sessions use to
/// archive superseded content inside a markdown section.
///
/// Two properties of the real vault files drive the design:
///
/// 1. **The tags are not balanced.** A representative day carried 26 opens
///    against 24 closes. Depth is therefore advisory — the parser force-closes
///    at the next `## ` heading rather than trusting a counter to return to
///    zero.
/// 2. **`<details>` also appears as prose.** Run notes discuss the markup
///    itself (``…so the `<details>` block isn't orphaned``), so a bare
///    substring search over-counts. ``scan(_:)`` masks inline-code spans first.
enum HTMLDetailsScanner {
    struct Scan: Equatable {
        /// `<details>` opening tags on this line.
        let opens: Int
        /// `</details>` closing tags on this line.
        let closes: Int
        /// `<summary>…</summary>` text with inner HTML stripped, if present.
        let summary: String?

        /// Whether this line carries any `<details>` markup at all. Lines that
        /// don't are ordinary content and go through the normal parse path.
        var isTagLine: Bool { opens > 0 || closes > 0 }
    }

    static func scan(_ line: String) -> Scan {
        // Cheap reject: the overwhelming majority of lines have no angle
        // brackets at all, and this runs once per line of a ~2 MB file.
        guard line.contains("<") else { return Scan(opens: 0, closes: 0, summary: nil) }

        let masked = maskCodeSpans(line)
        let opens = count(openRe, in: masked)
        let closes = count(closeRe, in: masked)
        guard opens > 0 || closes > 0 else {
            return Scan(opens: 0, closes: 0, summary: nil)
        }
        return Scan(opens: opens, closes: closes, summary: summary(in: masked))
    }

    /// Replace every `<` that sits inside a backtick code span with `x`.
    ///
    /// Only `<` is rewritten, and only to another single UTF-16 unit, so the
    /// masked string stays byte-for-byte index-compatible with the original —
    /// ranges found in the mask can be sliced out of either string. Nothing
    /// else needs masking: a tag cannot exist without its opening bracket.
    static func maskCodeSpans(_ line: String) -> String {
        guard line.contains("`") else { return line }
        var out = ""
        out.reserveCapacity(line.count)
        var inCode = false
        for ch in line {
            if ch == "`" {
                inCode.toggle()
                out.append(ch)
            } else {
                out.append(inCode && ch == "<" ? "x" : ch)
            }
        }
        return out
    }

    /// `<details>` / `<details class="…">`, but not `<detailsomething>`.
    private static let openRe = try! NSRegularExpression(pattern: #"<details(?=[\s/>])[^>]*>"#)
    private static let closeRe = try! NSRegularExpression(pattern: #"</details\s*>"#)
    private static let summaryRe = try! NSRegularExpression(
        pattern: #"<summary[^>]*>(.*?)</summary>"#,
        options: [.dotMatchesLineSeparators]
    )
    /// Any remaining HTML element inside a summary — `<b>`, `<code>`, `<br/>`.
    private static let innerTagRe = try! NSRegularExpression(pattern: #"</?[A-Za-z][^>]*>"#)

    private static func count(_ re: NSRegularExpression, in s: String) -> Int {
        re.numberOfMatches(in: s, range: NSRange(s.startIndex..., in: s))
    }

    private static func summary(in masked: String) -> String? {
        let range = NSRange(masked.startIndex..., in: masked)
        guard let m = summaryRe.firstMatch(in: masked, range: range),
              let inner = Range(m.range(at: 1), in: masked) else { return nil }
        let raw = String(masked[inner])
        let stripped = innerTagRe.stringByReplacingMatches(
            in: raw,
            range: NSRange(raw.startIndex..., in: raw),
            withTemplate: ""
        )
        return stripped.trimmingCharacters(in: .whitespaces)
    }
}
