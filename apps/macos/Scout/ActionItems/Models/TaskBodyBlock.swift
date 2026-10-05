import Foundation

/// A structural block parsed out of a task body. Scout writes task bodies as one
/// dense run — bold-label clauses (`**Why:** …`), inline `(1)…(2)…` checklists,
/// and a trailing cluster of `[[wikilinks]]` — which renders as an unreadable
/// wall. `TaskBodyParser` breaks that run into typed blocks so the card's
/// expanded detail can lay each out with its own typography.
enum TaskBodyBlock: Equatable {
    /// A paragraph, optionally introduced by a bold label (`**Why:**`).
    /// `text` is raw markdown (inline formatting + wikilinks preserved).
    case paragraph(label: String?, text: String)
    /// A numbered list lifted from an inline `(1)…(2)…(3)…` enumeration.
    case steps(label: String?, items: [String])
    /// The trailing run of `[[wikilink]]` targets, pulled out of the prose so
    /// they render as context pills instead of inline noise.
    case links([String])
}

enum TaskBodyParser {
    /// Parse a raw task body into ordered blocks. A body with no recognizable
    /// structure yields a single `.paragraph(nil, …)` (after any trailing
    /// wikilink cluster is split off), so simple bodies still render fine.
    static func blocks(from rawBody: String) -> [TaskBodyBlock] {
        let body = rawBody.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return [] }

        // 1. Split off the trailing wikilink cluster (the final run of
        //    consecutive `[[…]]` separated only by whitespace). Inline
        //    wikilinks earlier in the prose stay put.
        let (prose, linkTargets) = splitTrailingLinks(body)

        var blocks: [TaskBodyBlock] = []

        // 2. Break the prose into labeled segments at `**Label:**` markers.
        for segment in labeledSegments(prose) {
            blocks.append(contentsOf: segmentBlocks(label: segment.label, text: segment.text))
        }

        if blocks.isEmpty && !prose.isEmpty {
            blocks.append(.paragraph(label: nil, text: prose))
        }

        if !linkTargets.isEmpty {
            blocks.append(.links(linkTargets))
        }
        return blocks
    }

    // MARK: - Trailing links

    private static func splitTrailingLinks(_ body: String) -> (prose: String, targets: [String]) {
        guard let re = try? NSRegularExpression(pattern: #"((?:\s*\[\[[^\]]+\]\])+)\s*$"#) else {
            return (body, [])
        }
        let ns = body as NSString
        guard let m = re.firstMatch(in: body, range: NSRange(location: 0, length: ns.length)) else {
            return (body, [])
        }
        let cluster = ns.substring(with: m.range(at: 1))
        let targets = wikilinkTargets(in: cluster)
        // Only treat as a pulled-out cluster if there are at least two links —
        // a single trailing link reads fine inline and pulling it looks odd.
        guard targets.count >= 2 else { return (body, []) }
        let prose = ns.substring(to: m.range.location).trimmingCharacters(in: .whitespacesAndNewlines)
        return (prose, targets)
    }

    private static func wikilinkTargets(in s: String) -> [String] {
        guard let re = try? NSRegularExpression(pattern: #"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]"#) else { return [] }
        let ns = s as NSString
        return re.matches(in: s, range: NSRange(location: 0, length: ns.length)).map {
            ns.substring(with: $0.range(at: 1))
        }
    }

    // MARK: - Labeled segments

    private struct Segment { let label: String?; let text: String }

    private static func labeledSegments(_ prose: String) -> [Segment] {
        guard !prose.isEmpty else { return [] }
        // `**Label:**` — short bold run ending in a colon inside the asterisks.
        guard let re = try? NSRegularExpression(pattern: #"\*\*\s*([^*\n]{1,48}?)\s*:\s*\*\*"#) else {
            return [Segment(label: nil, text: prose)]
        }
        let ns = prose as NSString
        let matches = re.matches(in: prose, range: NSRange(location: 0, length: ns.length))
        guard !matches.isEmpty else { return [Segment(label: nil, text: prose)] }

        var segments: [Segment] = []

        // Lead text before the first label.
        let leadEnd = matches[0].range.location
        if leadEnd > 0 {
            let lead = trimSeparators(ns.substring(to: leadEnd))
            if !lead.isEmpty { segments.append(Segment(label: nil, text: lead)) }
        }

        for (i, m) in matches.enumerated() {
            let label = ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces)
            let textStart = m.range.location + m.range.length
            let textEnd = (i + 1 < matches.count) ? matches[i + 1].range.location : ns.length
            let text = trimSeparators(ns.substring(with: NSRange(location: textStart, length: textEnd - textStart)))
            segments.append(Segment(label: label, text: text))
        }
        return segments
    }

    // MARK: - Step detection

    private static func segmentBlocks(label: String?, text: String) -> [TaskBodyBlock] {
        guard let steps = inlineSteps(in: text) else {
            return text.isEmpty && label != nil
                ? [.paragraph(label: label, text: "")]
                : [.paragraph(label: label, text: text)]
        }
        var out: [TaskBodyBlock] = []
        if !steps.preface.isEmpty {
            out.append(.paragraph(label: label, text: steps.preface))
            out.append(.steps(label: nil, items: steps.items))
        } else {
            out.append(.steps(label: label, items: steps.items))
        }
        return out
    }

    /// Detect an inline `(1)…(2)…(3)…` enumeration. Only fires when the markers
    /// are exactly `1, 2, …, n` in order (so a stray `(2025)` doesn't trip it).
    private static func inlineSteps(in text: String) -> (preface: String, items: [String])? {
        guard let re = try? NSRegularExpression(pattern: #"\((\d+)\)"#) else { return nil }
        let ns = text as NSString
        let matches = re.matches(in: text, range: NSRange(location: 0, length: ns.length))
        guard matches.count >= 2 else { return nil }
        for (i, m) in matches.enumerated() {
            guard Int(ns.substring(with: m.range(at: 1))) == i + 1 else { return nil }
        }

        let preface = trimSeparators(ns.substring(to: matches[0].range.location))
        var items: [String] = []
        for (i, m) in matches.enumerated() {
            let start = m.range.location + m.range.length
            let end = (i + 1 < matches.count) ? matches[i + 1].range.location : ns.length
            var item = ns.substring(with: NSRange(location: start, length: end - start))
                .trimmingCharacters(in: .whitespaces)
            while let last = item.last, ";.,".contains(last) { item.removeLast() }
            items.append(item.trimmingCharacters(in: .whitespaces))
        }
        return (preface, items)
    }

    // MARK: - Helpers

    /// Trim whitespace plus a leading em/en-dash or hyphen joiner often used
    /// between a `**Label:**` and its text.
    private static func trimSeparators(_ s: String) -> String {
        var out = s.trimmingCharacters(in: .whitespacesAndNewlines)
        for joiner in ["— ", "– ", "- "] where out.hasPrefix(joiner) {
            out = String(out.dropFirst(joiner.count)).trimmingCharacters(in: .whitespacesAndNewlines)
            break
        }
        return out
    }
}
