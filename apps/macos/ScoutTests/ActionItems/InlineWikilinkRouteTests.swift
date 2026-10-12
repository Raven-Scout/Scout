import Testing
import Foundation
@testable import Scout

/// Where an inline `[[wikilink]]` click goes outside the Knowledge Base
/// (Raven-Scout/Scout#392): the Action Items tab sends entity links to the KB
/// tab like its `- Refs:` chips, Linear keys still open Linear, and anywhere
/// without the KB route keeps opening Obsidian.
@MainActor
@Suite("Inline wikilink routing")
struct InlineWikilinkRouteTests {
    @Test func entityOpensTheKBTabWhereThereIsOne() {
        #expect(InlineMarkdownText.route(for: "people/alex", canOpenKBNote: true, linearWorkspace: "") == .kbNote("people/alex"))
    }

    @Test func entityOpensObsidianWithoutAKBRoute() {
        #expect(InlineMarkdownText.route(for: "people/alex", canOpenKBNote: false, linearWorkspace: "")
            == .external(URL(string: "obsidian://open?vault=Scout&file=people/alex")!))
    }

    @Test(arguments: [true, false])
    func linearKeyOpensLinearEvenWithAKBRoute(canOpenKBNote: Bool) {
        #expect(InlineMarkdownText.route(for: "PROJ-1234", canOpenKBNote: canOpenKBNote, linearWorkspace: "acme")
            == .external(URL(string: "https://linear.app/acme/issue/PROJ-1234")!))
    }
}
