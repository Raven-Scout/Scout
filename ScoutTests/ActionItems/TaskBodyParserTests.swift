import Testing
import Foundation
@testable import Scout

@Suite("Task body parser")
struct TaskBodyParserTests {
    @Test func plainBodyIsSingleParagraph() {
        let blocks = TaskBodyParser.blocks(from: "Just a simple note with no structure.")
        #expect(blocks == [.paragraph(label: nil, text: "Just a simple note with no structure.")])
    }

    @Test func emptyBodyYieldsNothing() {
        #expect(TaskBodyParser.blocks(from: "   ").isEmpty)
    }

    @Test func splitsBoldLabelClauses() {
        let body = "**Why:** it matters because reasons. **Backend:** post-cutover runs differ."
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [
            .paragraph(label: "Why", text: "it matters because reasons."),
            .paragraph(label: "Backend", text: "post-cutover runs differ."),
        ])
    }

    @Test func leadTextBeforeFirstLabelBecomesUnlabeledParagraph() {
        let body = "Some preface here. **Why:** the reason."
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [
            .paragraph(label: nil, text: "Some preface here."),
            .paragraph(label: "Why", text: "the reason."),
        ])
    }

    @Test func liftsInlineNumberedChecklist() {
        let body = "**Checklist:** (1) do the first thing; (2) then the second; (3) finally the third."
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [
            .steps(label: "Checklist", items: ["do the first thing", "then the second", "finally the third"]),
        ])
    }

    @Test func checklistWithPrefaceSplitsParagraphThenSteps() {
        let body = "**Plan:** here is the approach (1) step one; (2) step two."
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [
            .paragraph(label: "Plan", text: "here is the approach"),
            .steps(label: nil, items: ["step one", "step two"]),
        ])
    }

    @Test func nonSequentialParensAreNotSteps() {
        // A year in parens must not be read as a step marker.
        let body = "Released in (2024) and again in (2025) with fixes."
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [.paragraph(label: nil, text: "Released in (2024) and again in (2025) with fixes.")])
    }

    @Test func pullsTrailingWikilinkCluster() {
        let body = "The point. Tooling: [[some-skill]] skill. [[ai-costs]] [[kai-backend]] [[people/martin-vasko]]"
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [
            .paragraph(label: nil, text: "The point. Tooling: [[some-skill]] skill."),
            .links(["ai-costs", "kai-backend", "people/martin-vasko"]),
        ])
    }

    @Test func singleTrailingLinkStaysInline() {
        let body = "See the doc [[reference]]"
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks == [.paragraph(label: nil, text: "See the doc [[reference]]")])
    }

    @Test func realisticBodyDecomposesIntoBlocks() {
        let body = "**Overnight progress:** ten sessions audited the pipelines. **Why:** [[AI-2619]] ruled out drift, leaving a residual. **5-step checklist:** (1) split LS traces; (2) redo per-stack; (3) verify child runs; (4) grep prod Helm; (5) carve out non-Kai. **Caveat:** OTel never emits cost. [[ai-costs]] [[kai-backend]] [[evals]]"
        let blocks = TaskBodyParser.blocks(from: body)
        #expect(blocks.count == 5)
        guard case .paragraph(label: "Overnight progress", _) = blocks[0] else { Issue.record("0"); return }
        guard case .paragraph(label: "Why", _) = blocks[1] else { Issue.record("1"); return }
        guard case .steps(label: "5-step checklist", let items) = blocks[2] else { Issue.record("2"); return }
        #expect(items.count == 5)
        #expect(items[0] == "split LS traces")
        guard case .paragraph(label: "Caveat", _) = blocks[3] else { Issue.record("3"); return }
        guard case .links(let targets) = blocks[4] else { Issue.record("4"); return }
        #expect(targets == ["ai-costs", "kai-backend", "evals"])
    }
}
