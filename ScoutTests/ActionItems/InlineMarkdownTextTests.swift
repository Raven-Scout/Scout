import Testing
import Foundation
@testable import Scout

@Suite("Inline markdown rendering")
struct InlineMarkdownTextTests {
    /// `_word_` must render as an italic (emphasized) run, not literal underscores.
    @Test func underscoreItalicRendersEmphasized() {
        let attr = InlineMarkdownText.attributedString(for: "start _emphasis here_ end")
        let plain = String(attr.characters)
        #expect(!plain.contains("_"))                       // underscores consumed, not literal
        let emphasized = attr.runs.contains { run in
            run.inlinePresentationIntent?.contains(.emphasized) == true
        }
        #expect(emphasized)
    }

    /// A parenthetical italic (the shape Adam saw unrendered) also emphasizes.
    @Test func parentheticalItalicRenders() {
        let attr = InlineMarkdownText.attributedString(for: "note _(net-new from the review)_ tail")
        #expect(!String(attr.characters).contains("_"))
    }

    /// Bold still works after the syntax change.
    @Test func boldStillRenders() {
        let attr = InlineMarkdownText.attributedString(for: "**Reply to Alex**")
        let plain = String(attr.characters)
        #expect(plain == "Reply to Alex")
        let strong = attr.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true }
        #expect(strong)
    }
}
