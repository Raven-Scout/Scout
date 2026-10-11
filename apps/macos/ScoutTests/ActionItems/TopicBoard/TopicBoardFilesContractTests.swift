import Foundation
import Testing
@testable import Scout

/// The app's readers of the topic board's files against the fixture the
/// engine also tests (`plugin/engine/tests/fixtures/contract/topic-board-files.json`).
@Suite("Topic board files — shared fixture")
struct TopicBoardFilesContractTests {
    struct SideCase: Decodable {
        let bullet: String
        let key: String
        let text: String
        let source: String?
    }

    struct DismissedCase: Decodable {
        let text: String
        let keys: [String]
    }

    struct NamesCase: Decodable {
        let text: String
        let names: [String: String]
    }

    struct Fixture: Decodable {
        let side_entries: [SideCase]
        let dismissed_md: DismissedCase
        let topic_names_md: NamesCase
    }

    static func fixture() throws -> Fixture {
        let bundle = Bundle(for: ActionItemsFixtureAnchor.self)
        let url = try #require(
            bundle.url(forResource: "topic-board-files", withExtension: "json")
                ?? bundle.resourceURL?.appendingPathComponent("topic-board-files.json")
        )
        return try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
    }

    @Test func sideEntriesMatch() throws {
        for c in try Self.fixture().side_entries {
            let entry = SideEntry.parse(c.bullet)
            #expect(entry.key == c.key, "\(c.bullet)")
            #expect(entry.text == c.text, "\(c.bullet)")
            #expect(entry.source == c.source, "\(c.bullet)")
        }
    }

    @Test func dismissedFileMatches() throws {
        let c = try Self.fixture().dismissed_md
        #expect(DismissedFileLoader.parse(c.text) == Set(c.keys))
    }

    @Test func topicNamesFileMatches() throws {
        let c = try Self.fixture().topic_names_md
        #expect(TopicNamesParser.parse(c.text) == c.names)
    }
}
