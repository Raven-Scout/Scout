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
}
