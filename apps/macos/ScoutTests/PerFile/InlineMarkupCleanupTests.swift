// ScoutTests/PerFile/InlineMarkupCleanupTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("InlineMarkupCleanup")
struct InlineMarkupCleanupTests {
    private func clean(_ s: String) -> String { InlineMarkupCleanup.clean(s) }

    // MARK: Math

    @Test func inlineMathBecomesPlainText() {
        // The shape a research card showed as "(\ge 1.5)".
        #expect(clean(#"Keep the ratio \(\ge 1.5\) for now."#) == "Keep the ratio ≥ 1.5 for now.")
        #expect(clean(#"Error stays $\le 0.02$ on both sets."#) == "Error stays ≤ 0.02 on both sets.")
        #expect(clean(#"Display: \[x \times y \approx 3\]"#) == "Display: x × y ≈ 3")
        #expect(clean(#"Always math: $$a + b$$"#) == "Always math: a + b")
    }

    @Test func mathCommandsDegradeToReadableText() {
        #expect(clean(#"\(\frac{a}{b} = \text{half}\)"#) == "a/b = half")
        #expect(clean(#"\(n\,\mathrm{ms}\)"#) == "n ms")
        // An unknown command keeps its name; braces go.
        #expect(clean(#"\(\hat{x}\)"#) == "hatx")
        // Markdown-significant characters are escaped so they show as written.
        #expect(clean(#"\(x_1 \cdot x_2\)"#) == #"x\_1 · x\_2"#)
    }

    @Test func pricesAndEscapedParenthesesAreNotMath() {
        #expect(clean("It costs $5 and $6 a month.") == "It costs $5 and $6 a month.")
        // A command between two prices is still not math (Pandoc's rule: the
        // closing `$` follows a non-space and isn't followed by a digit).
        #expect(clean(#"Plan A is $5 (p \ge 0.1); plan B is $10."#) == "Plan A is $5 (p ≥ 0.1); plan B is $10.")
        #expect(clean(#"From $ \ge 3$ on"#) == #"From $ ≥ 3$ on"#)
        #expect(clean(#"Use \(see above\) here."#) == #"Use \(see above\) here."#)
    }

    @Test func aBareSymbolCommandInProseBecomesItsCharacter() {
        #expect(clean(#"p \ge 0.05 and n \neq 0"#) == "p ≥ 0.05 and n ≠ 0")
        // Followed by a letter it's a different word, not the command.
        #expect(clean(#"C:\new\temp"#) == #"C:\new\temp"#)
    }

    // MARK: Bold

    @Test func anUnbalancedBoldMarkerIsDropped() {
        #expect(clean("**Status** is fine but **cut off here") == "**Status** is fine but cut off here")
        #expect(clean("**Unclosed") == "Unclosed")
    }

    /// The stray mark is the one the parser can't pair, not always the last
    /// one (UI audit T3: Research cards still showed a literal `**`).
    @Test func theStrayMarkIsTheOneThatPairsWithNothing() {
        #expect(clean("**A and **B** done") == "A and **B** done")
        #expect(clean("**one** and **two and **three**") == "**one** and two and **three**")
        // Two strays, both cut off.
        #expect(clean("**open and **again") == "open and again")
        // A closer with no opener before it.
        #expect(clean("trailing** and **real**") == "trailing and **real**")
    }

    @Test func aBoldMarkerBetweenSpacesIsLeftAndDoesNotCountTowardPairing() {
        #expect(clean("x ** 2") == "x ** 2")
        #expect(clean("A ** stray, then **real bold**") == "A ** stray, then **real bold**")
    }

    /// An odd `**` that isn't a cut-off bold span stays as written.
    @Test(arguments: [
        "src/**/*.swift",
        "Call f(**kwargs) to pass them on",
        "fn(a, **kwargs)",
        "2**8 is 256",
        "[the docs](https://example.com/a**)",
        "see https://example.com/**glob",
    ])
    func anOddMarkerThatIsntBoldIsLeft(_ text: String) {
        #expect(clean(text) == text)
    }

    @Test func balancedBoldIsKept() {
        #expect(clean("**one** and **two**") == "**one** and **two**")
        #expect(clean("***both***") == "***both***")
    }

    // MARK: Code spans

    @Test func codeSpansAreLeftAlone() {
        #expect(clean(#"Call `f(**kwargs)` with `\ge`"#) == #"Call `f(**kwargs)` with `\ge`"#)
        #expect(clean(#"`\(\ge\)` vs \(\ge 2\)"#) == #"`\(\ge\)` vs ≥ 2"#)
    }

    @Test func plainTextIsUnchanged() {
        let s = "A [[wikilink|alias]], a [link](https://example.com) and `code`."
        #expect(clean(s) == s)
    }

    // MARK: Linear math (the regexes it replaced, kept as the specification)

    static let oldMathRes: [(NSRegularExpression, requiresCommand: Bool)] = [
        (try! NSRegularExpression(pattern: #"\$\$(.+?)\$\$"#), false),
        (try! NSRegularExpression(pattern: #"\\\((.+?)\\\)"#), true),
        (try! NSRegularExpression(pattern: #"\\\[(.+?)\\\]"#), true),
        (try! NSRegularExpression(pattern: #"(?<![\\$])\$(?=[^\s$])([^$\n]*?[^\s$\\])\$(?!\d)"#), true),
    ]
    static let oldCommandRe = try! NSRegularExpression(pattern: #"\\[A-Za-z]+"#)
    static let oldBareSymbolRe = try! NSRegularExpression(
        pattern: #"\\("# + InlineMarkupCleanup.symbols.keys.sorted { $0.count > $1.count }.joined(separator: "|")
            + #")(?![A-Za-z])"#
    )

    /// The old `plainMath`: each regex in turn, every match spliced in last first.
    static func oldPlainMath(_ text: String) -> String {
        func replace(_ re: NSRegularExpression, _ s: String, _ transform: (String) -> String?) -> String {
            let ns = s as NSString
            var out = ns
            for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed() {
                guard let r = transform(ns.substring(with: m.range(at: 1))) else { continue }
                out = out.replacingCharacters(in: m.range, with: r) as NSString
            }
            return out as String
        }
        var s = text
        for (re, requiresCommand) in oldMathRes {
            s = replace(re, s) { content in
                let ns = content as NSString
                if requiresCommand,
                   oldCommandRe.firstMatch(in: content, range: NSRange(location: 0, length: ns.length)) == nil { return nil }
                return InlineMarkupCleanup.mathAsText(content)
            }
        }
        return replace(oldBareSymbolRe, s) { InlineMarkupCleanup.symbols[$0] }
    }

    static func oldSpans(_ pattern: NSRegularExpression, _ s: String) -> [[NSRange]] {
        pattern.matches(in: s, range: NSRange(location: 0, length: (s as NSString).length)).map { [$0.range, $0.range(at: 1)] }
    }

    static let delimiters: [(open: String, close: String, regex: NSRegularExpression)] = [
        ("$$", "$$", oldMathRes[0].0), (#"\("#, #"\)"#, oldMathRes[1].0), (#"\["#, #"\]"#, oldMathRes[2].0),
    ]

    static let mathCases = [
        "", "$$", "$$$", "$$$$", "$$$$$", "$$a$$", "$$a$", "$$\n$$", "$$a\n$$", "$$é$$", "$$😀$$",
        #"\(\)"#, #"\(\)\)"#, #"\(x\)"#, #"\(\ge\)"#, #"\(\ge"#, #"\(\(\ge\)\)"#, "\\(a\rb\\)",
        "\\(a\u{2028}b\\)", "\\(a\u{0B}\\ge\\)", #"\[\ge\]\[\le\]"#, #"\[ \] \[\times\]"#,
        #"$\ge$"#, #"$5 \ge $6"#, #"x \(\frac{a}{b}\) y $$\text{t}$$ z"#, #"\\(\ge\\)"#,
        #"$$\(\ge\)$$"#, #"\($$\ge$$\)"#, #"é\(é\ge é\)é"#, #"😀\[\pm 😀\]"#, #"\ge\le\new"#,
    ]

    @Test(arguments: mathCases)
    func delimitedSpansMatchTheOldRegexes(_ s: String) {
        for d in Self.delimiters {
            let spans = InlineMarkupCleanup.delimitedSpans(in: s, open: d.open, close: d.close).map { [$0.whole, $0.content] }
            #expect(spans == Self.oldSpans(d.regex, s), "\(d.open) in \(s.debugDescription)")
        }
    }

    @Test(arguments: mathCases)
    func plainMathMatchesTheOldRegexes(_ s: String) {
        #expect(InlineMarkupCleanup.plainMath(s) == Self.oldPlainMath(s), "\(s.debugDescription)")
    }

    /// Seeded, so a failure names a string that fails again.
    @Test func plainMathMatchesTheOldRegexesOnRandomText() {
        var rng = SeededGenerator(seed: 0x5C0)
        let alphabet = ["\\", "\\", "(", ")", "[", "]", "$", "$", "ge", "x", " ", "\n", "\r", "\u{2028}", "é", "😀",
                        "{", "}", "*", "_", "frac", "text", "1", #"\ge"#, #"\("#, #"\)"#, "$$", #"\["#, #"\]"#, "`"]
        for _ in 0..<5_000 {
            let s = (0..<Int.random(in: 0...20, using: &rng)).map { _ in alphabet.randomElement(using: &rng)! }.joined()
            #expect(InlineMarkupCleanup.plainMath(s) == Self.oldPlainMath(s), "\(s.debugDescription)")
            for d in Self.delimiters {
                let spans = InlineMarkupCleanup.delimitedSpans(in: s, open: d.open, close: d.close).map { [$0.whole, $0.content] }
                #expect(spans == Self.oldSpans(d.regex, s), "\(d.open) in \(s.debugDescription)")
            }
        }
    }

    /// About 50k characters each. The first five took the old regexes 5 to
    /// 14 s each in a release build (0.002 s or so now), so a revert fails;
    /// the rest guard the passes that were linear already. The limits are
    /// generous because CI's macOS runners vary about 2×.
    static let pathologicalMath: [(name: String, text: String)] = [
        ("unclosed \\(", String(repeating: #"\("#, count: 25_000)),
        ("unclosed \\[", String(repeating: #"\["#, count: 25_000)),
        ("unclosed \\( after é", String(repeating: #"é\("#, count: 16_667)),
        ("unclosed \\[ after an emoji", String(repeating: #"😀\["#, count: 12_500)),
        ("unclosed \\( with a command", String(repeating: #"\(\ge "#, count: 8_334)),
        ("many closed spans", String(repeating: #"\(\ge\) "#, count: 3_000)),
        ("dollars", String(repeating: "$", count: 50_000)),
        ("dollar math after é", String(repeating: #"é$\ge"#, count: 10_000)),
        ("bare symbols", String(repeating: #"\ge "#, count: 12_500)),
        // Each `**` pairs with nothing; they used to be removed one copy of the text at a time.
        ("stray bold marks", String(repeating: "**a ", count: 6_250)),
    ]

    @Test("unclosed or repeated math cleans quickly", arguments: pathologicalMath.map(\.name))
    func pathologicalMathIsFast(_ name: String) throws {
        let text = try #require(Self.pathologicalMath.first { $0.name == name }).text
        let elapsed = ContinuousClock().measure { _ = InlineMarkupCleanup.clean(text) }
        #expect(elapsed < .seconds(2), "\(name): \(elapsed)")
    }
}

/// SplitMix64: a fixed sequence for a given seed.
struct SeededGenerator: RandomNumberGenerator {
    private var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
