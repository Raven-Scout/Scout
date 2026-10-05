import Testing
import Foundation
@testable import Scout

@Suite("PlainSubject — matches Python _strip_markdown_tokens")
struct PlainSubjectTests {
    @Test func stripsBold() {
        #expect(ActionItemsParser.plainSubject("**Engage on [[PROJ-123]] pricing plan**") == "Engage on PROJ-123 pricing plan")
    }

    @Test func stripsStrikethrough() {
        #expect(ActionItemsParser.plainSubject("~~Call mechanic~~") == "Call mechanic")
    }

    @Test func stripsInlineCode() {
        #expect(ActionItemsParser.plainSubject("Run `action-items/render.py`") == "Run action-items/render.py")
    }

    @Test func stripsWikilinkPreservingTarget() {
        #expect(ActionItemsParser.plainSubject("Deep dive on [[backend-service]]") == "Deep dive on backend-service")
    }

    @Test func stripsAliasedWikilinkPreservingTarget() {
        // Note: Python strips aliased wikilinks to the target, NOT the alias.
        // See action-items/add_comment.py _strip_markdown_tokens.
        #expect(ActionItemsParser.plainSubject("See [[PROJ-123|pricing debate]]") == "See PROJ-123")
    }

    @Test func stripsMarkdownLink() {
        #expect(ActionItemsParser.plainSubject("[PROJ-123 comment](https://linear.app/...)") == "PROJ-123 comment")
    }

    @Test func leavesPlainTextUntouched() {
        #expect(ActionItemsParser.plainSubject("Finish the plugin PR") == "Finish the plugin PR")
    }
}
