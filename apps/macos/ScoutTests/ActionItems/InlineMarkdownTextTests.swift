import Testing
import Foundation
@testable import Scout

// @MainActor is load-bearing, exactly as on KBTagTests' "Tag search" suite:
// `InlineMarkdownText.attributedString` and its static cache are MainActor-
// isolated in the app module (SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor), but
// the test target compiles nonisolated-by-default in Swift 5 mode, so an
// unannotated suite runs on a background thread and races the test host's
// main-thread renders of the same cache — a ~50%-flaky
// `-[NSIndexPath count]` heap-corruption crash under `xcodebuild test`.
@MainActor
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

    /// A parenthetical italic (the shape Sam saw unrendered) also emphasizes.
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

    // MARK: Aliased wikilinks

    /// The label shown and the `scout-wiki://` link each run carries.
    private func wikilinks(_ raw: String) -> (plain: String, links: [String]) {
        let attr = InlineMarkdownText.attributedString(for: raw)
        let links = attr.runs.compactMap { $0.link }.filter { $0.scheme == "scout-wiki" }.map(\.absoluteString)
        return (String(attr.characters), links)
    }

    @Test func aliasedWikilinkShowsTheAliasAndLinksTheTarget() {
        let r = wikilinks("Ask [[people/alex|Alex]] first")
        #expect(r.plain == "Ask Alex first")
        #expect(r.links == ["scout-wiki://people%2Falex"])
    }

    @Test func aliasWithAHeadingAnchorKeepsTheAnchorOnTheTarget() {
        let r = wikilinks("See [[projects/demo#Status|the status]].")
        #expect(r.plain == "See the status.")
        #expect(r.links == ["scout-wiki://projects%2Fdemo%23Status"])
    }

    /// Notes escape the alias pipe inside table cells (`\|`); the backslash
    /// used to stay on the target, so the link resolved to nothing.
    @Test func escapedPipeAliasDropsTheBackslashFromTheTarget() {
        let r = wikilinks(#"| [[docs/wishlist/progress/item\|Progress log]] |"#)
        #expect(r.plain.contains("Progress log"))
        #expect(!r.plain.contains("[["))
        #expect(r.links == ["scout-wiki://docs%2Fwishlist%2Fprogress%2Fitem"])
    }

    @Test func aWikilinkInsideACodeSpanStaysLiteralCode() {
        let r = wikilinks("Write `[[people/alex|Alex]]` to link a person, like [[people/priya|Priya]].")
        #expect(r.plain == "Write [[people/alex|Alex]] to link a person, like Priya.")
        #expect(r.links == ["scout-wiki://people%2Fpriya"])
    }

    @Test func aliasedLinksSideBySideStaySeparate() {
        let r = wikilinks("[[people/alex|Alex]] and [[people/sam|Sam]]")
        #expect(r.plain == "Alex and Sam")
        #expect(r.links == ["scout-wiki://people%2Falex", "scout-wiki://people%2Fsam"])
    }
}
