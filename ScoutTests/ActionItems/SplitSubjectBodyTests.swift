import Testing
import Foundation
@testable import Scout

/// `splitSubjectBody` was quadratic: `firstSeparatorOutsideTokens` called
/// `text.distance(from:to:)` — itself O(n) — three times per character, so cost
/// grew with the square of the line length. On a real day's file that one
/// function was 465 ms at `-O`, 68% of the whole 830 ms parse, and task lines
/// run to 9,685 characters.
///
/// The rewrite walks a `[Character]` array with integer indices. Behavior must
/// not move a millimetre: `parser-corpus.json` is byte-identical across three
/// repos and only carries 6 entries with a body, so the safety net here is a
/// differential test against the original implementation, kept verbatim below.
@Suite("splitSubjectBody — separator scanning")
struct SplitSubjectBodyTests {

    // MARK: - Curated cases

    @Test("Splits on each dash separator, outside tokens")
    func splitsOnDashes() {
        #expect(ActionItemsParser.splitSubjectBody("Ship it — by Friday").0 == "Ship it")
        #expect(ActionItemsParser.splitSubjectBody("Ship it — by Friday").1 == "by Friday")
        #expect(ActionItemsParser.splitSubjectBody("Ship it – by Friday").1 == "by Friday")
        #expect(ActionItemsParser.splitSubjectBody("Ship it - by Friday").1 == "by Friday")
    }

    @Test("Falls back to a colon separator when no dash is present")
    func colonFallback() {
        let (s, b) = ActionItemsParser.splitSubjectBody("Blocked: waiting on Priya")
        #expect(s == "Blocked")
        #expect(b == "waiting on Priya")
    }

    @Test("A separator inside a token is not a split point")
    func separatorsInsideTokensAreIgnored() {
        // Each of these has its only ` — ` inside a token, so nothing splits.
        for raw in [
            "**Bold — inner** and the rest",
            "`code — inner` and the rest",
            "~~struck — inner~~ and the rest",
            "[[wiki — inner]] and the rest",
            "[label — inner](https://example.com/x) and the rest",
        ] {
            let (s, b) = ActionItemsParser.splitSubjectBody(raw)
            #expect(b == "", "should not split: \(raw)")
            #expect(s == raw, "subject should be the whole line: \(raw)")
        }
    }

    @Test("Splits after a token closes")
    func splitsAfterTokenCloses() {
        let (s, b) = ActionItemsParser.splitSubjectBody("**PROJ-1234 shipped** — Priya merged it")
        #expect(s == "**PROJ-1234 shipped**")
        #expect(b == "Priya merged it")
    }

    @Test("No separator leaves the line intact with an empty body")
    func noSeparator() {
        let (s, b) = ActionItemsParser.splitSubjectBody("Just a subject")
        #expect(s == "Just a subject")
        #expect(b == "")
    }

    @Test("Grapheme clusters are not split mid-character")
    func graphemeSafety() {
        let (s, b) = ActionItemsParser.splitSubjectBody("🚧 Blocked 👩‍👩‍👧‍👦 — see the thread")
        #expect(s == "🚧 Blocked 👩‍👩‍👧‍👦")
        #expect(b == "see the thread")
    }

    // MARK: - Differential against the original implementation

    @Test("Agrees with the original implementation across generated inputs")
    func differentialAgainstReference() {
        var checked = 0
        for raw in Self.generatedCorpus() {
            let new = ActionItemsParser.splitSubjectBody(raw)
            let old = Self.referenceSplitSubjectBody(raw)
            #expect(new.0 == old.0, "subject differs for: \(raw)")
            #expect(new.1 == old.1, "body differs for: \(raw)")
            checked += 1
        }
        // Guard against the generator silently collapsing to nothing.
        #expect(checked > 1500)
    }

    /// Fragments chosen to drive every branch of the scanner: each token kind
    /// both closed and unclosed, each separator, and separators sitting inside
    /// and outside tokens.
    static func generatedCorpus() -> [String] {
        let fragments = [
            "plain text",
            "**bold**",
            "**bold — dash**",
            "**unclosed bold",
            "`code`",
            "`code — dash`",
            "`unclosed code",
            "~~struck~~",
            "~~struck — dash~~",
            "[[wikilink]]",
            "[[wiki|alias]]",
            "[[wiki — dash]]",
            "[label](https://example.com/a)",
            "[label — dash](https://example.com/a)",
            "[unclosed label",
            "]stray close",
            ")stray paren",
            "PROJ-1234",
            "🚧 emoji",
            "colon: inside",
        ]
        let separators = ["", " — ", " – ", " - ", ": ", " ", "—", "-"]

        var out: [String] = []
        for a in fragments {
            for sep in separators {
                for b in fragments {
                    out.append("\(a)\(sep)\(b)")
                }
            }
        }
        // A few hand-built shapes the product doesn't reach.
        out.append(contentsOf: [
            "",
            " ",
            " — ",
            "— leading",
            "trailing —",
            "a — b — c",
            "a: b: c",
            "**a** — **b** — **c**",
            "[[a]] — [[b]]",
            "`a` — `b`",
            "[a](u) — [b](v)",
            "**[[nested]] — after**",
            "[[**nested bold** — x]] — after",
        ])
        return out
    }

    // MARK: - The original, kept verbatim as the differential reference
    //
    // Do not "clean up" or re-optimize this copy — its whole value is being the
    // pre-rewrite behavior. If a corpus entry ever legitimately changes, change
    // the production code and this reference together, deliberately.

    static func referenceSplitSubjectBody(_ rest: String) -> (String, String) {
        let separators = [" — ", " – ", " - "]
        if let idx = referenceFirstSeparatorOutsideTokens(in: rest, separators: separators) {
            for sep in separators {
                let sepLen = sep.count
                if rest.distance(from: idx, to: rest.endIndex) >= sepLen,
                   rest[idx ..< rest.index(idx, offsetBy: sepLen)] == sep {
                    return (
                        String(rest[..<idx]).trimmingCharacters(in: .whitespaces),
                        String(rest[rest.index(idx, offsetBy: sepLen)...]).trimmingCharacters(in: .whitespaces)
                    )
                }
            }
        }
        if let idx = referenceFirstSeparatorOutsideTokens(in: rest, separators: [": "]) {
            let sepLen = 2
            return (
                String(rest[..<idx]).trimmingCharacters(in: .whitespaces),
                String(rest[rest.index(idx, offsetBy: sepLen)...]).trimmingCharacters(in: .whitespaces)
            )
        }
        return (rest, "")
    }

    static func referenceFirstSeparatorOutsideTokens(
        in text: String,
        separators: [String]
    ) -> String.Index? {
        var inBold = false, inStrike = false, inCode = false
        var bracketDepth = 0, parenDepth = 0
        var i = text.startIndex
        while i < text.endIndex {
            let rem = text[i...]
            let two = rem.prefix(2)
            let ch = text[i]
            if ch == "`" && !inBold && !inStrike { inCode.toggle(); i = text.index(after: i); continue }
            if inCode { i = text.index(after: i); continue }
            if two == "**" { inBold.toggle(); i = text.index(i, offsetBy: 2); continue }
            if two == "~~" { inStrike.toggle(); i = text.index(i, offsetBy: 2); continue }
            if two == "[[" { bracketDepth += 1; i = text.index(i, offsetBy: 2); continue }
            if two == "]]" && bracketDepth > 0 { bracketDepth -= 1; i = text.index(i, offsetBy: 2); continue }
            if ch == "[" && bracketDepth == 0 { bracketDepth = 1; i = text.index(after: i); continue }
            if ch == "]" && bracketDepth > 0 && two != "]]" {
                bracketDepth = 0
                let next = text.index(after: i)
                if next < text.endIndex && text[next] == "(" {
                    parenDepth = 1
                    i = text.index(i, offsetBy: 2); continue
                }
                i = text.index(after: i); continue
            }
            if ch == ")" && parenDepth > 0 { parenDepth -= 1; i = text.index(after: i); continue }
            if !inBold && !inStrike && bracketDepth == 0 && parenDepth == 0 {
                for sep in separators {
                    let sepLen = sep.count
                    if text.distance(from: i, to: text.endIndex) >= sepLen,
                       text[i ..< text.index(i, offsetBy: sepLen)] == sep {
                        return i
                    }
                }
            }
            i = text.index(after: i)
        }
        return nil
    }
}

/// The cost curve, not a wall-clock budget.
///
/// A hard millisecond bound would be flaky on a loaded machine, so this asserts
/// the *shape*: quadratic cost quadruples when the input doubles, linear cost
/// roughly doubles. The threshold sits between the two.
@Suite("splitSubjectBody — cost grows linearly")
struct SplitSubjectBodyScalingTests {

    /// A worst-case line: no separator anywhere, so the scanner walks every
    /// character, and peppered with tokens so no branch short-circuits.
    static func line(chars: Int) -> String {
        let unit = "some **bold** and `code` and [[a-wiki-link]] plus prose "
        var s = ""
        while s.count < chars { s += unit }
        return String(s.prefix(chars))
    }

    static func medianMS(of block: () -> Void, runs: Int = 5) -> Double {
        var samples: [Double] = []
        for _ in 0..<runs {
            let t0 = DispatchTime.now().uptimeNanoseconds
            block()
            let t1 = DispatchTime.now().uptimeNanoseconds
            samples.append(Double(t1 - t0) / 1_000_000)
        }
        return samples.sorted()[samples.count / 2]
    }

    @Test("Doubling the line length does not quadruple the time")
    func scalesLinearly() {
        let short = Self.line(chars: 4_000)
        let long = Self.line(chars: 8_000)

        // Warm up, so first-call overhead doesn't land in either sample.
        _ = ActionItemsParser.splitSubjectBody(short)

        let tShort = Self.medianMS(of: { _ = ActionItemsParser.splitSubjectBody(short) })
        let tLong = Self.medianMS(of: { _ = ActionItemsParser.splitSubjectBody(long) })

        // Linear ⇒ ratio ≈ 2. Quadratic ⇒ ratio ≈ 4. Allow generous slack for a
        // noisy machine while still failing the quadratic implementation, which
        // measured 26.7 ms → 99.8 ms (3.7×) at these very sizes.
        let ratio = tLong / max(tShort, 0.0001)
        #expect(ratio < 3.0, "cost ratio 8k/4k was \(ratio) (short \(tShort) ms, long \(tLong) ms)")
    }

    @Test("A pathological line stays far below the quadratic cost")
    func longLineIsFast() {
        // 16k chars measured 410 ms with the quadratic scan. Linear is ~1 ms;
        // 60 ms leaves a wide margin for Debug and CI noise while still being
        // unreachable for the old implementation.
        let huge = Self.line(chars: 16_000)
        _ = ActionItemsParser.splitSubjectBody(huge)
        let t = Self.medianMS(of: { _ = ActionItemsParser.splitSubjectBody(huge) }, runs: 3)
        #expect(t < 60, "16k-char line took \(t) ms")
    }
}
