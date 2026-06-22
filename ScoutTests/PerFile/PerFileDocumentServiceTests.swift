// ScoutTests/PerFile/PerFileDocumentServiceTests.swift
import Foundation
import Testing
@testable import Scout

private struct EmptyFileEvents: FileSystemEventSource {
    nonisolated func events(for url: URL) -> AsyncStream<FileSystemEvent> { AsyncStream { $0.finish() } }
}

@MainActor
@Suite("PerFileDocumentService")
struct PerFileDocumentServiceTests {
    private func makeDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("perfile-doc-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @Test func loadsAndCountsActive() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try "---\ntitle: A\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# A\nbody".write(
            to: dir.appendingPathComponent("2026-06-10-a.md"), atomically: true, encoding: .utf8)
        try "---\ntitle: B\nstatus: done\npriority: low\ndate: 2026-06-09\n---\n\n# B\nbody".write(
            to: dir.appendingPathComponent("2026-06-09-b.md"), atomically: true, encoding: .utf8)
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.items.count == 2)
        #expect(svc.activeCount == 1)               // only A is open
        #expect(svc.state == .loaded)
    }

    @Test func missingDirectory() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("absent-\(UUID().uuidString)")
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.items.isEmpty)
        #expect(svc.state == .missing(dir))
    }
}
