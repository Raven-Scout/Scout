import Foundation

/// Inline code spans, found in linear time.
///
/// It finds the same spans the regex `` (`+)[^`\n].*?\1 `` found: a run of
/// backticks, at least one character that isn't a backtick or a newline, then
/// the first later run of at least as many backticks on the same line, which
/// closes it. That regex backtracked over every backtick in a run, so a 10 KB
/// line of backticks in a run log took seconds on the main thread (review of
/// #751). `InlineMarkdownText`, `InlineMarkupCleanup` and `PlainText` all
/// read code spans through this.
nonisolated enum CodeSpanScanner {

    /// Each span's UTF-16 range in `text`, backticks included, in order and
    /// never overlapping.
    static func ranges(in text: String) -> [NSRange] {
        let u = Array(text.utf16)
        let n = u.count
        guard u.contains(tick) else { return [] }

        // runEnd[i]: for a backtick at i, the index just past its run.
        var runEnd = [Int](repeating: 0, count: n)
        // longest[i]: the most backticks in a row starting at or after i,
        // before the line ends. A closer that can't exist is ruled out in O(1).
        var longest = [Int](repeating: 0, count: n + 1)
        var i = n - 1
        while i >= 0 {
            if u[i] == tick {
                runEnd[i] = i + 1 < n && u[i + 1] == tick ? runEnd[i + 1] : i + 1
                longest[i] = max(runEnd[i] - i, longest[i + 1])
            } else {
                longest[i] = isLineBreak(u[i]) ? 0 : longest[i + 1]
            }
            i -= 1
        }

        var out: [NSRange] = []
        var p = 0
        while p < n {
            guard u[p] == tick else { p += 1; continue }
            let fence = runEnd[p] - p
            let first = p + fence           // the span's first character: not a backtick, by construction
            let from = first + 1            // where a closer may start
            guard first < n, u[first] != newline, from < n, longest[from] >= fence else {
                p += 1
                continue
            }
            // A closer exists on this line, so this walk ends before the line
            // does, and everything it walks over joins the span.
            var j = from
            while true {
                if u[j] == tick {
                    if runEnd[j] - j >= fence { break }
                    j = runEnd[j]
                } else {
                    j += 1
                }
            }
            out.append(NSRange(location: p, length: j + fence - p))
            p = j + fence
        }
        return out
    }

    private static let tick = UInt16(UInt8(ascii: "`"))
    private static let newline = UInt16(UInt8(ascii: "\n"))

    /// The characters a regex `.` doesn't match (ICU's line terminators).
    private static func isLineBreak(_ c: UInt16) -> Bool {
        switch c {
        case 0x0A, 0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029: return true
        default: return false
        }
    }
}
