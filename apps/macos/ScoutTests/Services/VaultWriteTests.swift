import Foundation
import Testing
@testable import Scout

/// The shared write model's primitives (C10): snapshots, the stale-checked
/// atomic replace, and `VaultChange` undo/redo with its stale guard.
@Suite("Vault write model")
struct VaultWriteTests {
    private let dir: URL

    init() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("vault-write-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    private func file(_ name: String, _ text: String?) throws -> URL {
        let url = dir.appendingPathComponent(name)
        if let text { try text.write(to: url, atomically: true, encoding: .utf8) }
        return url
    }

    private func read(_ url: URL) -> String? { try? String(contentsOf: url, encoding: .utf8) }

    // MARK: Snapshots

    @Test func snapshotOfMissingFileHasNoData() throws {
        let snap = try FileSnapshot.capture(dir.appendingPathComponent("nope.md"))
        #expect(snap.data == nil)
        #expect(!snap.exists)
        #expect(snap.digest == nil)
    }

    @Test func snapshotMatchesDiskUntilTheFileChanges() throws {
        let url = try file("a.md", "one")
        let snap = try FileSnapshot.capture(url)
        #expect(snap.matchesDisk())
        try "two".write(to: url, atomically: true, encoding: .utf8)
        #expect(!snap.matchesDisk())
    }

    // MARK: Stale-checked replace

    @Test func replaceWritesWhenDiskMatches() throws {
        let url = try file("a.md", "one")
        try VaultWrite.replace(url, expecting: Data("one".utf8), with: Data("two".utf8))
        #expect(read(url) == "two")
    }

    @Test func replaceRefusesAStaleFileAndLeavesItAlone() throws {
        let url = try file("a.md", "changed by a run")
        #expect(throws: VaultWriteError.stale(file: "a.md")) {
            try VaultWrite.replace(url, expecting: Data("one".utf8), with: Data("two".utf8))
        }
        #expect(read(url) == "changed by a run")
    }

    @Test func replaceWithNilRemovesTheFile() throws {
        let url = try file("a.md", "one")
        try VaultWrite.replace(url, expecting: Data("one".utf8), with: nil)
        #expect(!FileManager.default.fileExists(atPath: url.path))
    }

    @Test func writeFailureIsReportedNotSwallowed() throws {
        // A directory where the file should be: the atomic rename fails.
        let url = dir.appendingPathComponent("blocked.md")
        try FileManager.default.createDirectory(at: url.appendingPathComponent("inside"), withIntermediateDirectories: true)
        #expect(throws: VaultWriteError.self) { try VaultWrite.write(Data("x".utf8), to: url) }
    }

    @Test func atomicWriteLeavesNoTempFilesBehind() throws {
        let url = try file("a.md", "one")
        try VaultWrite.write(Data("two".utf8), to: url)
        let names = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        #expect(names == ["a.md"])
    }

    // MARK: VaultChange

    @Test func unchangedWriteHasNothingToUndo() throws {
        let url = try file("a.md", "same")
        let snap = try FileSnapshot.capture(url)
        #expect(VaultChange(actionName: "Edit", before: [snap], after: [snap]) == nil)
    }

    @Test func undoRestoresBeforeAndRedoRestoresAfter() throws {
        let url = try file("a.md", "before")
        let before = try FileSnapshot.capture(url)
        try "after".write(to: url, atomically: true, encoding: .utf8)
        let change = try #require(VaultChange(actionName: "Edit", before: [before], after: [try FileSnapshot.capture(url)]))

        try change.undo()
        #expect(read(url) == "before")
        try change.redo()
        #expect(read(url) == "after")
    }

    @Test func undoIsRefusedWhenTheFileChangedSinceTheWrite() throws {
        let url = try file("a.md", "before")
        let before = try FileSnapshot.capture(url)
        try "after".write(to: url, atomically: true, encoding: .utf8)
        let change = try #require(VaultChange(actionName: "Edit", before: [before], after: [try FileSnapshot.capture(url)]))
        try "a scheduled run wrote this".write(to: url, atomically: true, encoding: .utf8)

        #expect(throws: VaultWriteError.undoStale(file: "a.md")) { try change.undo() }
        #expect(read(url) == "a scheduled run wrote this")
    }

    @Test func redoIsRefusedWhenTheFileChangedSinceTheUndo() throws {
        let url = try file("a.md", "before")
        let before = try FileSnapshot.capture(url)
        try "after".write(to: url, atomically: true, encoding: .utf8)
        let change = try #require(VaultChange(actionName: "Edit", before: [before], after: [try FileSnapshot.capture(url)]))
        try change.undo()
        try "newer".write(to: url, atomically: true, encoding: .utf8)

        #expect(throws: VaultWriteError.undoStale(file: "a.md")) { try change.redo() }
        #expect(read(url) == "newer")
    }

    @Test func undoOfACreatedFileRemovesIt() throws {
        let url = dir.appendingPathComponent("new.md")
        let before = try FileSnapshot.capture(url)
        try "fresh".write(to: url, atomically: true, encoding: .utf8)
        let change = try #require(VaultChange(actionName: "Add", before: [before], after: [try FileSnapshot.capture(url)]))
        try change.undo()
        #expect(!FileManager.default.fileExists(atPath: url.path))
        try change.redo()
        #expect(read(url) == "fresh")
    }

    @Test func undoOfARenameMovesTheNoteBack() throws {
        let old = try file("old.md", "body")
        let new = dir.appendingPathComponent("new.md")
        let before = [try FileSnapshot.capture(old), try FileSnapshot.capture(new)]
        try FileManager.default.moveItem(at: old, to: new)
        let change = try #require(VaultChange(actionName: "Rename", before: before,
                                              after: [try FileSnapshot.capture(old), try FileSnapshot.capture(new)]))
        try change.undo()
        #expect(read(old) == "body")
        #expect(!FileManager.default.fileExists(atPath: new.path))
    }

    @Test func aRefusedMultiFileUndoChangesNothing() throws {
        let a = try file("a.md", "a0")
        let b = try file("b.md", "b0")
        let before = [try FileSnapshot.capture(a), try FileSnapshot.capture(b)]
        try "a1".write(to: a, atomically: true, encoding: .utf8)
        try "b1".write(to: b, atomically: true, encoding: .utf8)
        let change = try #require(VaultChange(actionName: "Both", before: before,
                                              after: [try FileSnapshot.capture(a), try FileSnapshot.capture(b)]))
        try "b2".write(to: b, atomically: true, encoding: .utf8)

        #expect(throws: VaultWriteError.undoStale(file: "b.md")) { try change.undo() }
        #expect(read(a) == "a1")
        #expect(read(b) == "b2")
    }
}
