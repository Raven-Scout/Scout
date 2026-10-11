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
                customTitle: "Reattach"
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
        - Renamed:
        """)
        #expect(topics == [TopicMeta(tag: "STRM", title: "Real", session: nil, summary: nil, customTitle: nil)])
    }
}
