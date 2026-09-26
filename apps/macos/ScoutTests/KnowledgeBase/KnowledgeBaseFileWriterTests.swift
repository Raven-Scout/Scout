import Foundation
import Testing
@testable import Scout

/// Records the commits a writer requests, and can be told to fail so the
/// "written but uncommitted" path is exercised.
private final class RecordingGit: GitServiceProtocol, @unchecked Sendable {
    struct Commit: Equatable { let paths: [String]; let message: String }
    private let lock = NSLock()
    private var _commits: [Commit] = []
    private let shouldFail: Bool

    init(shouldFail: Bool = false) { self.shouldFail = shouldFail }

    var commits: [Commit] { lock.lock(); defer { lock.unlock() }; return _commits }

    struct Boom: Error {}

    func commitPaths(_ relPaths: [String], message: String) async throws {
        lock.lock(); _commits.append(Commit(paths: relPaths, message: message)); lock.unlock()
        if shouldFail { throw Boom() }
    }
}

@Suite("KnowledgeBaseFileWriter")
struct KnowledgeBaseFileWriterTests {

    /// A scout dir containing a `knowledge-base/` root, symlink-resolved so it
    /// matches what the writer's in-KB guard computes.
    private func makeScoutDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("kb-writer-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("knowledge-base"), withIntermediateDirectories: true)
        return dir
    }

    private func kbRoot(_ scout: URL) -> URL { scout.appendingPathComponent("knowledge-base") }

    private func read(_ url: URL) throws -> String {
        try String(contentsOf: url, encoding: .utf8)
    }

    // MARK: - normalizedFileName

    @Test("a bare name gains the default .md extension")
    func normalizedFileName_appendsMarkdownExtension() throws {
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("alex") == "alex.md")
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("  alex  ") == "alex.md")
    }

    @Test("an existing extension is preserved")
    func normalizedFileName_keepsExplicitExtension() throws {
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("notes.txt") == "notes.txt")
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("a.md") == "a.md")
    }

    @Test("a caller-supplied default extension is honoured")
    func normalizedFileName_customDefaultExtension() throws {
        #expect(try KnowledgeBaseFileWriter.normalizedFileName("x", defaultExtension: "txt") == "x.txt")
    }

    @Test("an empty or whitespace-only name is rejected")
    func normalizedFileName_rejectsEmpty() {
        #expect(throws: KBWriterError.emptyName) {
            try KnowledgeBaseFileWriter.normalizedFileName("")
        }
        #expect(throws: KBWriterError.emptyName) {
            try KnowledgeBaseFileWriter.normalizedFileName("   \n ")
        }
    }

    @Test("path separators and dot names cannot escape the chosen directory")
    func normalizedFileName_rejectsPathSeparators() {
        for bad in ["../evil", "a/b", #"a\b"#, ".", ".."] {
            #expect(throws: KBWriterError.self) {
                try KnowledgeBaseFileWriter.normalizedFileName(bad)
            }
        }
    }

    // MARK: - relativePathInRepo

    @Test("a path inside the repo becomes repo-relative")
    func relativePathInRepo_insideRepo() {
        let rel = KnowledgeBaseFileWriter.relativePathInRepo(
            fileURL: URL(fileURLWithPath: "/repo/knowledge-base/people/alex.md"),
            repo: URL(fileURLWithPath: "/repo"))
        #expect(rel == "knowledge-base/people/alex.md")
    }

    @Test("a path outside the repo degrades to its file name")
    func relativePathInRepo_outsideRepoUsesLastComponent() {
        let rel = KnowledgeBaseFileWriter.relativePathInRepo(
            fileURL: URL(fileURLWithPath: "/elsewhere/alex.md"),
            repo: URL(fileURLWithPath: "/repo"))
        #expect(rel == "alex.md")
    }

    // MARK: - KBWriterError messages

    @Test("every writer error renders a user-facing message")
    func kbWriterError_userMessages() {
        #expect(KBWriterError.emptyName.userMessage == "The name can't be empty.")
        #expect(KBWriterError.alreadyExists("a.md").userMessage == "A file named a.md already exists.")
        #expect(KBWriterError.notFound("a.md").userMessage == "a.md no longer exists.")
        #expect(KBWriterError.writeFailed("disk full").userMessage == "disk full")
        #expect(KBWriterError.conflict(file: "a.md").userMessage == "a.md changed on disk.")
        #expect(KBWriterError.outsideKnowledgeBase("a.md").userMessage
                == "a.md is outside the knowledge base.")
        #expect(KBWriterError.commitFailed("lock").userMessage
                == "The change was written, but the git commit failed: lock")
    }

    @Test("message(for:) unwraps writer errors and falls back for others")
    func kbWriterError_messageForAnyError() {
        #expect(KBWriterError.message(for: KBWriterError.emptyName) == "The name can't be empty.")
        struct Other: LocalizedError { var errorDescription: String? { "something else" } }
        #expect(KBWriterError.message(for: Other()) == "something else")
    }

    // MARK: - save

    @Test("saving a new file writes it and commits the relative path")
    func save_newFileWritesAndCommits() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let git = RecordingGit()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: git)
        let file = kbRoot(scout).appendingPathComponent("people/alex.md")

        try await writer.save(fileURL: file, contents: "# Alex\n", baselineContents: nil, label: "alex.md")

        #expect(try read(file) == "# Alex\n")
        #expect(git.commits == [.init(paths: ["knowledge-base/people/alex.md"],
                                      message: "app: edit alex.md")])
    }

    @Test("saving over an unchanged file succeeds")
    func save_matchingBaselineOverwrites() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        let file = kbRoot(scout).appendingPathComponent("a.md")
        try "old".write(to: file, atomically: true, encoding: .utf8)

        try await writer.save(fileURL: file, contents: "new", baselineContents: "old", label: "a.md")
        #expect(try read(file) == "new")
    }

    @Test("a file changed on disk since load is a conflict, not a clobber")
    func save_divergedBaselineThrowsConflict() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        let file = kbRoot(scout).appendingPathComponent("a.md")
        try "changed by someone else".write(to: file, atomically: true, encoding: .utf8)

        await #expect(throws: KBWriterError.conflict(file: "a.md")) {
            try await writer.save(fileURL: file, contents: "mine",
                                  baselineContents: "what I loaded", label: "a.md")
        }
        #expect(try read(file) == "changed by someone else")   // untouched
    }

    @Test("a nil baseline on an existing file is treated as a conflict")
    func save_nilBaselineOnExistingFileIsConflict() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        let file = kbRoot(scout).appendingPathComponent("a.md")
        try "on disk".write(to: file, atomically: true, encoding: .utf8)

        await #expect(throws: KBWriterError.conflict(file: "a.md")) {
            try await writer.save(fileURL: file, contents: "mine",
                                  baselineContents: nil, label: "a.md")
        }
    }

    @Test("a path outside the knowledge base is refused")
    func save_outsideKnowledgeBaseIsRefused() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let git = RecordingGit()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: git)
        let outside = scout.appendingPathComponent("action-items.md")

        await #expect(throws: KBWriterError.outsideKnowledgeBase("action-items.md")) {
            try await writer.save(fileURL: outside, contents: "x",
                                  baselineContents: nil, label: "action-items.md")
        }
        #expect(!FileManager.default.fileExists(atPath: outside.path))
        #expect(git.commits.isEmpty)
    }

    @Test("a failed commit surfaces as commitFailed with the file already written")
    func save_commitFailureStillLeavesFileOnDisk() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(
            scoutDirectory: scout, gitService: RecordingGit(shouldFail: true))
        let file = kbRoot(scout).appendingPathComponent("a.md")

        await #expect(throws: KBWriterError.self) {
            try await writer.save(fileURL: file, contents: "body",
                                  baselineContents: nil, label: "a.md")
        }
        #expect(try read(file) == "body")   // written, just uncommitted
    }

    @Test("a nil git service skips committing entirely")
    func save_withoutGitServiceStillWrites() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: nil)
        let file = kbRoot(scout).appendingPathComponent("a.md")
        try await writer.save(fileURL: file, contents: "x", baselineContents: nil, label: "a.md")
        #expect(try read(file) == "x")
    }

    // MARK: - createFile

    @Test("createFile writes the note and commits its creation")
    func createFile_writesAndCommits() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let git = RecordingGit()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: git)

        let url = try await writer.createFile(
            in: kbRoot(scout), name: "priya", initialContents: "# Priya\n")

        #expect(url.lastPathComponent == "priya.md")
        #expect(try read(url) == "# Priya\n")
        #expect(git.commits.first?.paths == ["knowledge-base/priya.md"])
        #expect(git.commits.first?.message == "app: create knowledge-base/priya.md")
    }

    @Test("createFile refuses to overwrite an existing note")
    func createFile_alreadyExists() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        try "existing".write(to: kbRoot(scout).appendingPathComponent("a.md"),
                             atomically: true, encoding: .utf8)

        await #expect(throws: KBWriterError.alreadyExists("a.md")) {
            _ = try await writer.createFile(in: kbRoot(scout), name: "a", initialContents: "new")
        }
        #expect(try read(kbRoot(scout).appendingPathComponent("a.md")) == "existing")
    }

    @Test("createFile rejects an empty name")
    func createFile_emptyName() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        await #expect(throws: KBWriterError.emptyName) {
            _ = try await writer.createFile(in: kbRoot(scout), name: "  ", initialContents: "")
        }
    }

    @Test("createFile refuses a directory outside the knowledge base")
    func createFile_outsideKnowledgeBase() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        await #expect(throws: KBWriterError.self) {
            _ = try await writer.createFile(in: scout, name: "a", initialContents: "")
        }
    }

    // MARK: - delete

    @Test("delete removes the file and commits the removal")
    func delete_removesAndCommits() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let git = RecordingGit()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: git)
        let file = kbRoot(scout).appendingPathComponent("gone.md")
        try "bye".write(to: file, atomically: true, encoding: .utf8)

        try await writer.delete(fileURL: file, label: "gone.md")

        #expect(!FileManager.default.fileExists(atPath: file.path))
        #expect(git.commits.first?.paths == ["knowledge-base/gone.md"])
        #expect(git.commits.first?.message == "app: delete gone.md")
    }

    @Test("deleting a file that isn't there reports notFound")
    func delete_missingFile() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        await #expect(throws: KBWriterError.notFound("nope.md")) {
            try await writer.delete(
                fileURL: kbRoot(scout).appendingPathComponent("nope.md"), label: "nope.md")
        }
    }

    // MARK: - rename

    @Test("rename moves the file and commits both paths")
    func rename_movesAndCommitsBothPaths() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let git = RecordingGit()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: git)
        let file = kbRoot(scout).appendingPathComponent("old.md")
        try "body".write(to: file, atomically: true, encoding: .utf8)

        let dest = try await writer.rename(fileURL: file, to: "new")

        #expect(dest.lastPathComponent == "new.md")
        #expect(try read(dest) == "body")
        #expect(!FileManager.default.fileExists(atPath: file.path))
        #expect(git.commits.first?.paths == ["knowledge-base/old.md", "knowledge-base/new.md"])
        #expect(git.commits.first?.message
                == "app: rename knowledge-base/old.md → knowledge-base/new.md")
    }

    @Test("rename keeps the original extension when the new name omits one")
    func rename_preservesOriginalExtension() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        let file = kbRoot(scout).appendingPathComponent("notes.txt")
        try "x".write(to: file, atomically: true, encoding: .utf8)

        let dest = try await writer.rename(fileURL: file, to: "renamed")
        #expect(dest.lastPathComponent == "renamed.txt")
    }

    @Test("rename onto an existing name is refused")
    func rename_destinationExists() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        let a = kbRoot(scout).appendingPathComponent("a.md")
        let b = kbRoot(scout).appendingPathComponent("b.md")
        try "a".write(to: a, atomically: true, encoding: .utf8)
        try "b".write(to: b, atomically: true, encoding: .utf8)

        await #expect(throws: KBWriterError.alreadyExists("b.md")) {
            _ = try await writer.rename(fileURL: a, to: "b")
        }
        #expect(try read(a) == "a")
        #expect(try read(b) == "b")
    }

    @Test("renaming a file that isn't there reports notFound")
    func rename_missingSource() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: RecordingGit())
        await #expect(throws: KBWriterError.notFound("nope.md")) {
            _ = try await writer.rename(
                fileURL: kbRoot(scout).appendingPathComponent("nope.md"), to: "x")
        }
    }

    // MARK: - serialization

    @Test("concurrent writes are serialized and all land")
    func writes_areSerialized() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let git = RecordingGit()
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: git)

        try await withThrowingTaskGroup(of: Void.self) { group in
            for i in 0..<8 {
                group.addTask {
                    _ = try await writer.createFile(
                        in: self.kbRoot(scout), name: "note-\(i)", initialContents: "\(i)")
                }
            }
            try await group.waitForAll()
        }

        for i in 0..<8 {
            let url = kbRoot(scout).appendingPathComponent("note-\(i).md")
            #expect(try read(url) == "\(i)")
        }
        #expect(git.commits.count == 8)
    }

    // MARK: - box

    @Test("the SwiftUI box hands back the writer it wraps")
    func writerBox_exposesWriter() async throws {
        let scout = try makeScoutDir(); defer { try? FileManager.default.removeItem(at: scout) }
        let writer = KnowledgeBaseFileWriter(scoutDirectory: scout, gitService: nil)
        #expect(KnowledgeBaseWriterBox(writer: writer).writer === writer)
    }
}
