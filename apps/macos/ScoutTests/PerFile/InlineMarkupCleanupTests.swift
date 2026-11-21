// ScoutTests/PerFile/InlineMarkupCleanupTests.swift
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
}
