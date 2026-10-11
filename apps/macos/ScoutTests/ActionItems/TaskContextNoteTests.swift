import Testing
import Foundation
@testable import Scout

@Suite("Task context note — loader")
struct TaskContextNoteTests {
    private func vault() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("ctx-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("action-items/context"), withIntermediateDirectories: true)
        return dir
    }

    private func write(_ text: String, tag: String, in vault: URL) throws {
        try text.write(to: vault.appendingPathComponent("action-items/context/\(tag).md"),
                       atomically: true, encoding: .utf8)
    }

    @Test func urlForValidTag() throws {
        let v = try vault()
        #expect(TaskContextNote.url(for: "DETX", scoutDirectory: v)?.path
                == v.appendingPathComponent("action-items/context/DETX.md").path)
    }

    @Test(arguments: ["../X", "ab", "TOOLONGTAG", "1234", "DE/TX", "DETX.md", ""])
    func badTagsReadNothing(tag: String) throws {
        let v = try vault()
        try write("secret", tag: "DETX", in: v)
        #expect(TaskContextNote.url(for: tag, scoutDirectory: v) == nil)
        #expect(TaskContextNote.load(tag: tag, scoutDirectory: v) == nil)
    }

    @Test func loadStripsFrontmatter() throws {
        let v = try vault()
        try write("---\ntag: DETX\ntitle: \"Order\"\n---\n\nWaiting on Priya.\n", tag: "DETX", in: v)
        #expect(TaskContextNote.load(tag: "DETX", scoutDirectory: v) == "Waiting on Priya.")
    }

    @Test func noFrontmatterKeepsWholeText() throws {
        let v = try vault()
        try write("Just a body.\n", tag: "PLN", in: v)
        #expect(TaskContextNote.load(tag: "PLN", scoutDirectory: v) == "Just a body.")
    }

    @Test func frontmatterOnlyIsNil() throws {
        let v = try vault()
        try write("---\ntag: PLN\n---\n", tag: "PLN", in: v)
        #expect(TaskContextNote.load(tag: "PLN", scoutDirectory: v) == nil)
    }

    @Test func missingFileAndNilTagAreNil() throws {
        let v = try vault()
        #expect(TaskContextNote.load(tag: "NESTX", scoutDirectory: v) == nil)
        #expect(TaskContextNote.load(tag: nil, scoutDirectory: v) == nil)
    }
}
