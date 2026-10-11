import Foundation
import Testing
@testable import Scout

@Suite("Topics file — parser")
struct TopicsFileParserTests {
    @Test func parsesTopicsWithFields() {
        let topics = TopicsFileParser.parse("""
        # Topics

        Prose under the title is ignored.

        ## [#STRM] Make stream reattach survive a restart
        - Session: https://claude.ai/code/session_0123
        - Summary: Two PRs, the second needs the first
        - Renamed: Reattach
        - Owner: alex

        ## [#QBLK] Ship the tracing job
        - Summary: Waiting on Priya's review
        """)
        #expect(topics == [
            TopicMeta(
                tag: "STRM",
                title: "Make stream reattach survive a restart",
                session: "https://claude.ai/code/session_0123",
                summary: "Two PRs, the second needs the first",
                customTitle: nil
            ),
            TopicMeta(tag: "QBLK", title: "Ship the tracing job", session: nil, summary: "Waiting on Priya's review", customTitle: nil),
        ])
    }

    @Test func displayTitlePrefersTheRename() {
        let renamed = TopicMeta(tag: "STRM", title: "Scout title", session: nil, summary: nil, customTitle: "Mine")
        let plain = TopicMeta(tag: "STRM", title: "Scout title", session: nil, summary: nil, customTitle: nil)
        #expect(renamed.displayTitle == "Mine")
        #expect(plain.displayTitle == "Scout title")
    }

    @Test func untitledTopicFallsBackToItsTag() {
        #expect(TopicsFileParser.parse("## [#STRM]").map(\.title) == ["STRM"])
    }

    @Test func skipsHeadingsThatAreNotTopicsAndTheirFields() {
        let topics = TopicsFileParser.parse("""
        ## [#555] A GitHub ref, not a tag
        - Session: https://claude.ai/code/session_9
        ## Notes
        - Summary: not a topic
        ## [#STRM] Real
        """)
        #expect(topics == [TopicMeta(tag: "STRM", title: "Real", session: nil, summary: nil, customTitle: nil)])
    }

    @Test func namesComeFromTheirOwnFile() {
        let names = TopicNamesParser.parse("""
        # Topic names

        - [#STRM] Reattach
        - [#QBLK]
        - [#555] Not a tag
        - QBLK Missing brackets
        - [#STRM] Reattach, renamed again
        """)
        #expect(names == ["STRM": "Reattach, renamed again"])
    }

    @Test func loaderOverlaysNamesOnTopics() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try "## [#STRM] Scout title\n- Renamed: Ignored\n## [#QBLK] Other\n".write(
            to: dir.appendingPathComponent(TopicsFileLoader.fileName), atomically: true, encoding: .utf8)
        try "- [#STRM] Mine\n".write(
            to: dir.appendingPathComponent(TopicsFileLoader.namesFileName), atomically: true, encoding: .utf8)
        #expect(TopicsFileLoader.load(from: dir).map(\.displayTitle) == ["Mine", "Other"])
    }
}
