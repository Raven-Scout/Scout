import Testing
import Foundation
@testable import Scout

@Suite("GuardedFileWrite (read-modify-write race guard)")
struct GuardedFileWriteTests {
    private func tmpFile(_ contents: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("gfw-\(UUID().uuidString).txt")
        try contents.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    @Test func appliesTransformWhenStable() throws {
        let url = try tmpFile("hello")
        defer { try? FileManager.default.removeItem(at: url) }
        let wrote = try GuardedFileWrite.apply(
            to: url, modificationDate: { _ in Date(timeIntervalSince1970: 1) }
        ) { $0 + " world" }
        #expect(wrote == true)
        #expect(try String(contentsOf: url, encoding: .utf8) == "hello world")
    }

    @Test func noOpWhenTransformReturnsUnchanged() throws {
        let url = try tmpFile("x")
        defer { try? FileManager.default.removeItem(at: url) }
        let wrote = try GuardedFileWrite.apply(to: url, modificationDate: { _ in nil }) { $0 }
        #expect(wrote == false)
        #expect(try String(contentsOf: url, encoding: .utf8) == "x")
    }

    @Test func reappliesOntoConcurrentChangeInsteadOfClobbering() throws {
        // #48: a plugin write that lands between our read and our write must
        // NOT be clobbered. The mtime guard detects the change and re-reads,
        // reapplying the transform onto the concurrent content.
        let url = try tmpFile("line1\n")
        defer { try? FileManager.default.removeItem(at: url) }

        // Stateful provider simulating a concurrent writer: on the pre-write
        // mtime check of the first attempt it writes a new line to the file
        // and reports a changed mtime, forcing one re-read.
        final class Sim: @unchecked Sendable {
            let url: URL; var calls = 0
            init(_ u: URL) { url = u }
            func mtime(_ u: URL) -> Date? {
                calls += 1
                if calls == 2 {
                    try? "line1\nplugin-added\n".write(to: url, atomically: true, encoding: .utf8)
                    return Date(timeIntervalSince1970: 999)
                }
                return Date(timeIntervalSince1970: 100)
            }
        }
        let sim = Sim(url)

        let wrote = try GuardedFileWrite.apply(
            to: url, modificationDate: { sim.mtime($0) }
        ) { $0 + "app-added\n" }

        #expect(wrote == true)
        let final = try String(contentsOf: url, encoding: .utf8)
        #expect(final.contains("plugin-added"))  // concurrent change survived
        #expect(final.contains("app-added"))     // our change applied too
    }

    @Test func throwsConflictWhenFileNeverStabilizes() throws {
        let url = try tmpFile("a")
        defer { try? FileManager.default.removeItem(at: url) }
        // mtime differs on every pre-write check → never stable.
        final class Ticker: @unchecked Sendable {
            var n = 0
            func mtime(_ u: URL) -> Date? { n += 1; return Date(timeIntervalSince1970: TimeInterval(n)) }
        }
        let t = Ticker()
        #expect(throws: GuardedFileWrite.Failure.conflictPersisted) {
            try GuardedFileWrite.apply(to: url, maxAttempts: 3, modificationDate: { t.mtime($0) }) { $0 + "z" }
        }
    }
}
