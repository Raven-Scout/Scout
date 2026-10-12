import Testing
import Foundation
@testable import Scout

@Suite("Topic board — Ask Claude prompts")
@MainActor
struct BoardPromptTests {
    private func board() throws -> TopicBoard {
        let lines = [
            "# Action Items — 2026-07-20", "",
            "## 🔴 Urgent",
            "- [ ] [#AAAA] **Land the API** — merge once CI is green",
            "  - Topic: #STRM",
            "- [ ] [#BBBB] **Ship the UI** — needs the API",
            "  - Topic: #STRM",
            "  - State: blocked",
            "  - Depends: #AAAA",
            "  - Session: https://claude.ai/code/session_0456",
        ]
        let text = lines.joined(separator: "\n")
        let doc = try ActionItemsParser.parse(
            text: text, sourceURL: URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md"), sourceBytes: text.utf8.count
        )
        let meta = TopicMeta(
            tag: "STRM", title: "Stream reattach", session: "https://claude.ai/code/session_0123",
            summary: "API, then UI", customTitle: nil
        )
        return TopicBoard.build(sections: doc.sections, topics: [meta], today: .now)
    }

    @Test func topicPromptListsStepsWithNesting() throws {
        let topic = try #require(try board().topics.first)
        #expect(BoardPrompt.topic(topic) == """
        Help me make progress on this workstream: Stream reattach

        API, then UI

        Root session: https://claude.ai/code/session_0123

        Steps:
        - Mine [#AAAA]: Land the API — merge once CI is green
          - Blocked [#BBBB]: Ship the UI — needs the API
        """)
    }

    @Test func stepPromptAddsBoardContext() throws {
        let blocked = try #require(try board().topics.first?.steps.first?.blocked.first?.item)
        let prompt = BoardPrompt.step(blocked, topic: "Stream reattach")
        #expect(prompt.hasPrefix("Help me make progress on this action item:"))
        #expect(prompt.hasSuffix("""
        On the board:
        - Topic: Stream reattach
        - State: Blocked
        - Waits on: #AAAA
        - Session: https://claude.ai/code/session_0456
        """))
    }
}
