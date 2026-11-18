// ScoutTests/PerFile/MarkdownBodyBlockTests.swift
import Testing
@testable import Scout

@Suite("MarkdownBodyBlock")
struct MarkdownBodyBlockTests {
    @Test func splitsProseAndCode() {
        let body = "First para.\n\nSecond para.\n\n```swift\nlet x = 1\n```"
        let blocks = MarkdownBodyBlock.blocks(from: body)
        #expect(blocks == [
            .prose("First para."),
            .prose("Second para."),
            .code(language: "swift", code: "let x = 1"),
        ])
    }
    @Test func proseOnly() {
        #expect(MarkdownBodyBlock.blocks(from: "just text") == [.prose("just text")])
    }

    // MARK: Headings

    @Test func headingsAtEveryLevelEndTheParagraphBeforeThem() {
        let body = "The request.\n## Status\nTwo of four done.\n### Notes ###\n###### Deep"
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .prose("The request."),
            .heading(level: 2, text: "Status"),
            .prose("Two of four done."),
            .heading(level: 3, text: "Notes"),
            .heading(level: 6, text: "Deep"),
        ])
    }

    @Test func aHashWithoutASpaceIsNotAHeading() {
        // A tag or a seven-hash run stays prose.
        #expect(MarkdownBodyBlock.blocks(from: "#PROJ is next") == [.prose("#PROJ is next")])
        #expect(MarkdownBodyBlock.blocks(from: "####### seven") == [.prose("####### seven")])
    }

    @Test func aTrailingHashNeedsASpaceToBeAClosingSequence() {
        #expect(MarkdownBodyBlock.blocks(from: "## Learn C#") == [.heading(level: 2, text: "Learn C#")])
    }

    @Test func aHeadingKeepsItsInlineMarkup() {
        #expect(MarkdownBodyBlock.blocks(from: "## Status of [[projects/demo|the demo]]")
                == [.heading(level: 2, text: "Status of [[projects/demo|the demo]]")])
    }

    @Test func headingsInsideCodeStayCode() {
        #expect(MarkdownBodyBlock.blocks(from: "```\n## not a heading\n- [ ] not a task\n```")
                == [.code(language: nil, code: "## not a heading\n- [ ] not a task")])
    }

    // MARK: Task lists

    @Test func consecutiveTaskLinesFormOneList() {
        let body = "## Status\n- [x] Draft the template\n- [ ] Link it from the index\n* [X] Ask Priya"
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .heading(level: 2, text: "Status"),
            .tasks([
                MarkdownTask(checked: true, text: "Draft the template"),
                MarkdownTask(checked: false, text: "Link it from the index"),
                MarkdownTask(checked: true, text: "Ask Priya"),
            ]),
        ])
    }

    @Test func nestedTasksCarryTheirDepth() {
        let body = "- [ ] Parent\n  - [x] Child\n    - [ ] Grandchild\n1. [ ] Numbered"
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .tasks([
                MarkdownTask(checked: false, text: "Parent", depth: 0),
                MarkdownTask(checked: true, text: "Child", depth: 1),
                MarkdownTask(checked: false, text: "Grandchild", depth: 2),
                MarkdownTask(checked: false, text: "Numbered", depth: 0),
            ]),
        ])
    }

    /// Depth is the rank of the indent within the list, so 2 spaces, 4 spaces
    /// and tabs each nest one level per step.
    @Test(arguments: ["  ", "    ", "\t"])
    func everyIndentStyleNestsOneLevelPerStep(_ step: String) {
        let body = "- [ ] A\n\(step)- [ ] B\n\(step)\(step)- [ ] C"
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .tasks([
                MarkdownTask(checked: false, text: "A", depth: 0),
                MarkdownTask(checked: false, text: "B", depth: 1),
                MarkdownTask(checked: false, text: "C", depth: 2),
            ]),
        ])
    }

    /// One branch indents by 2 spaces, the next by 4: each child is one level down.
    @Test func branchesWithDifferentIndentsNestPerParent() {
        let body = "- [ ] A\n  - [ ] A1\n- [ ] B\n    - [ ] B1\n      - [ ] B1a"
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .tasks([
                MarkdownTask(checked: false, text: "A", depth: 0),
                MarkdownTask(checked: false, text: "A1", depth: 1),
                MarkdownTask(checked: false, text: "B", depth: 0),
                MarkdownTask(checked: false, text: "B1", depth: 1),
                MarkdownTask(checked: false, text: "B1a", depth: 2),
            ]),
        ])
    }

    @Test func aCheckboxWithNoTextIsNotATask() {
        #expect(MarkdownBodyBlock.blocks(from: "- [ ]   ") == [.prose("- [ ]")])
    }

    @Test func aBlankLineInsideAListKeepsItTogetherAndProseEndsIt() {
        let body = "Intro.\n- [ ] One\n\n- [ ] Two\nAfter the list."
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .prose("Intro."),
            .tasks([MarkdownTask(checked: false, text: "One"), MarkdownTask(checked: false, text: "Two")]),
            .prose("After the list."),
        ])
    }

    @Test func aPlainBulletOrALinkIsNotATask() {
        #expect(MarkdownBodyBlock.blocks(from: "- plain bullet\n- [a link](https://example.com)")
                == [.prose("- plain bullet\n- [a link](https://example.com)")])
    }

    @Test func aTaskKeepsAnAliasedWikilinkForTheInlineRenderer() {
        #expect(MarkdownBodyBlock.blocks(from: "- [ ] Read [[docs/wishlist/progress/item|Progress log]]")
                == [.tasks([MarkdownTask(checked: false, text: "Read [[docs/wishlist/progress/item|Progress log]]")])])
    }

    /// The card shape the wishlist phase writes: request, status, open sub-tasks, a log link.
    @Test func aWishlistCardRendersAsStructuredBlocks() {
        let body = """
        Add a template for architecture decisions.

        ## Status
        Two of four sub-tasks done; the index link is next.
        - [ ] Link the template from the KB index
        - [ ] Backfill the three existing decisions

        [[docs/wishlist/progress/2026-01-05-decision-template|Progress log]]
        """
        #expect(MarkdownBodyBlock.blocks(from: body) == [
            .prose("Add a template for architecture decisions."),
            .heading(level: 2, text: "Status"),
            .prose("Two of four sub-tasks done; the index link is next."),
            .tasks([
                MarkdownTask(checked: false, text: "Link the template from the KB index"),
                MarkdownTask(checked: false, text: "Backfill the three existing decisions"),
            ]),
            .prose("[[docs/wishlist/progress/2026-01-05-decision-template|Progress log]]"),
        ])
    }

    // MARK: - prefix(_:characters:)

    @Test func prefixKeepsEverythingUnderBudget() {
        let blocks: [MarkdownBodyBlock] = [.heading(level: 2, text: "Status"), .prose("Short.")]
        let shown = MarkdownBodyBlock.prefix(blocks, characters: 100)
        #expect(shown.blocks == blocks)
        #expect(shown.truncated == false)
    }

    @Test func prefixCutsTheOverflowingBlockAndDropsTheRest() {
        let blocks: [MarkdownBodyBlock] = [.prose("abcde"), .prose("fghij"), .prose("klmno")]
        let shown = MarkdownBodyBlock.prefix(blocks, characters: 7)
        #expect(shown.blocks == [.prose("abcde"), .prose("fg…")])
        #expect(shown.truncated)
    }

    @Test func prefixBoundsAHugeSingleParagraph() {
        // One 9,000-character line, as a long-running lane accumulates.
        let shown = MarkdownBodyBlock.prefix([.prose(String(repeating: "x", count: 9_000))], characters: 1_500)
        #expect(shown.blocks.reduce(0) { $0 + $1.characterCount } <= 1_501)   // + the ellipsis
        #expect(shown.truncated)
    }

    @Test func prefixKeepsWholeTasksOnly() {
        let tasks = [MarkdownTask(checked: true, text: "aaaa"), MarkdownTask(checked: false, text: "bbbb")]
        let shown = MarkdownBodyBlock.prefix([.tasks(tasks)], characters: 6)
        #expect(shown.blocks == [.tasks([tasks[0]])])
        #expect(shown.truncated)
    }

    @Test func prefixWithNoRoomDropsTheBlock() {
        let shown = MarkdownBodyBlock.prefix([.prose("abc"), .code(language: nil, code: "x")], characters: 3)
        #expect(shown.blocks == [.prose("abc")])
        #expect(shown.truncated)
    }
}
