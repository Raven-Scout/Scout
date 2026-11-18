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

    @Test func aBoldMarkerBetweenSpacesIsDropped() {
        #expect(clean("before ** after") == "before after")
        #expect(clean("A ** stray, then **real bold**") == "A stray, then **real bold**")
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
