import Testing
import Foundation
import CryptoKit
@testable import Scout

@Suite("Parser contract — Swift side")
struct ParserContractTests {
    static let bundle = Bundle(for: ActionItemsFixtureAnchor.self)

    /// Must equal the canonical scout-plugin corpus digest (Task M3.3).
    /// If this fails, the two corpus copies have drifted — re-copy from the
    /// plugin, do not edit only one side.
    /// On an intentional corpus change: re-copy the canonical corpus into both
    /// repos, then update this digest to the output of
    /// `shasum -a 256 ScoutTests/Fixtures/parser-corpus.json`.
    static let canonicalSHA256 = "4ebe8ae34a5b945bb5165ebd6bb6b818986c2cafec0ad30910bfd3fcb66e21a1"

    struct Corpus: Decodable {
        let entries: [Entry]
    }

    struct Entry: Decodable {
        struct Expected: Decodable {
            let short_prefix: String?
            let subject: String
            let plain_subject: String
            let body: String
        }
        let name: String
        let line: String
        let expected: Expected
    }

    private static func corpusURL() throws -> URL {
        guard let url = bundle.url(forResource: "parser-corpus", withExtension: "json")
                ?? bundle.resourceURL?.appendingPathComponent("parser-corpus.json") else {
            Issue.record("parser-corpus.json not in test bundle")
            throw CocoaError(.fileReadNoSuchFile)
        }
        return url
    }

    @Test func corpusMatchesCanonicalChecksum() throws {
        let data = try Data(contentsOf: try Self.corpusURL())
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        #expect(digest == Self.canonicalSHA256,
                "Corpus drift: scout-app copy != scout-plugin canonical. Re-copy, don't edit one side.")
    }

    @Test func parserMatchesContract() throws {
        let data = try Data(contentsOf: try Self.corpusURL())
        let entries = try JSONDecoder().decode(Corpus.self, from: data).entries
        #expect(!entries.isEmpty)
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-04-20.md")
        for e in entries {
            let text = "# T\n\n## 🔴 Urgent\n\n\(e.line)\n"
            let doc: ActionItemsDocument
            do {
                doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
            } catch {
                Issue.record("\(e.name): threw \(error)")
                continue
            }
            let tasks = doc.sections.flatMap { $0.tasks }
            guard let t = tasks.first, tasks.count == 1 else {
                Issue.record("\(e.name): expected exactly one task, got \(tasks.count)")
                continue
            }
            #expect(t.shortPrefix == e.expected.short_prefix, "\(e.name): short_prefix")
            #expect(t.subject == e.expected.subject, "\(e.name): subject")
            #expect(t.plainSubject == e.expected.plain_subject, "\(e.name): plain_subject")
            #expect(t.body == e.expected.body, "\(e.name): body")
        }
    }
}
