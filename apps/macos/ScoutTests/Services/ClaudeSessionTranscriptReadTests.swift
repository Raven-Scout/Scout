import Foundation
import Testing
@testable import Scout

/// Guards the read budget of `ClaudeSessionService.activity(for:)`.
///
/// Finding *which* transcript belongs to a run only needs `customTitle` and
/// the first `timestamp`, both of which sit in the opening lines. The service
/// used to answer that question by fully parsing every transcript in
/// `~/.claude/projects/<vault>` — 265 MB across 222 files on a real vault,
/// 7.3 s of CPU and a ~490 MB resident footprint that malloc never returned.
/// Only the transcript that actually matches may be read in full.
@Suite("ClaudeSessionService transcript read budget")
@MainActor
struct ClaudeSessionTranscriptReadTests {

    /// Records every read so a test can assert the matcher's byte budget.
    final class CountingReader: TranscriptByteReading, @unchecked Sendable {
        private let lock = NSLock()
        private var _bytes: [URL: Int] = [:]
        private let inner = FileSystemTranscriptReader()

        func read(_ url: URL, maxBytes: Int?) throws -> Data {
            let data = try inner.read(url, maxBytes: maxBytes)
            lock.lock()
            _bytes[url, default: 0] += data.count
            lock.unlock()
            return data
        }

        func bytesRead(forFileNamed name: String) -> Int {
            lock.lock(); defer { lock.unlock() }
            return _bytes.first { $0.key.lastPathComponent == name }?.value ?? 0
        }
    }

    private func makeDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("claude-read-budget-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private func titleFragment(for date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmm"
        return f.string(from: date)
    }

    private func isoString(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }

    /// A transcript whose head carries `customTitle`/`timestamp`, padded out
    /// past `paddedToBytes` with further tool_use lines.
    @discardableResult
    private func writeTranscript(
        in dir: URL, file: String, sessionId: String, title: String,
        timestamp: Date, paddedToBytes: Int, modified: Date
    ) throws -> URL {
        var text = """
        {"sessionId":"\(sessionId)","customTitle":"\(title)",\
        "timestamp":"\(isoString(timestamp))"}

        """
        let filler = """
        {"sessionId":"\(sessionId)","timestamp":"\(isoString(timestamp))",\
        "message":{"content":[{"type":"tool_use","id":"pad","name":"Bash",\
        "input":{"command":"\(String(repeating: "x", count: 200))"}}]}}

        """
        while text.utf8.count < paddedToBytes { text += filler }
        let url = dir.appendingPathComponent(file)
        try text.write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes(
            [.modificationDate: modified], ofItemAtPath: url.path)
        return url
    }

    /// The matcher may read at most this much of a transcript it rejects.
    /// Sized from the real archive: 222 of 224 transcripts carry `customTitle`
    /// in the first 5 lines, the two stragglers at ~195 KB.
    private static let headCap = 256 * 1024

    @Test("a non-matching transcript is never read in full")
    func activity_readsOnlyTheHeadOfNonMatchingTranscripts() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)

        // The decoy is big and sorted first (newest mtime), so the matcher
        // must walk past it before reaching the real match.
        let decoy = try writeTranscript(
            in: dir, file: "decoy.jsonl", sessionId: "decoy",
            title: "scout-dreaming-20200101-0000",
            timestamp: started.addingTimeInterval(-99_999),
            paddedToBytes: 2_000_000, modified: started.addingTimeInterval(60))
        try writeTranscript(
            in: dir, file: "wanted.jsonl", sessionId: "wanted",
            title: "scout-briefing-\(titleFragment(for: started))",
            timestamp: started, paddedToBytes: 1_000, modified: started)

        let decoySize = try FileManager.default
            .attributesOfItem(atPath: decoy.path)[.size] as? Int ?? 0
        #expect(decoySize > Self.headCap, "decoy must exceed the head cap to be meaningful")

        let reader = CountingReader()
        let svc = ClaudeSessionService(projectsDirectory: dir, reader: reader)
        let activity = await svc.activity(for: Run.make(startedAt: started))

        #expect(activity?.sessionId == "wanted")
        let decoyBytes = reader.bytesRead(forFileNamed: "decoy.jsonl")
        #expect(
            decoyBytes <= Self.headCap,
            "matcher read \(decoyBytes) bytes of a \(decoySize)-byte transcript it rejected"
        )
    }

    /// What a transcript that declares itself immediately should cost. The
    /// head cap is the ceiling for awkward files, not the price of the
    /// common case — on the real archive 222 of 224 transcripts name
    /// themselves in the first five lines, so paying the full cap on every
    /// one of them still moved 52 MB per lookup.
    private static let firstChunk = 64 * 1024

    @Test("a transcript that names itself on line 1 is read in kilobytes, not the full cap")
    func activity_readsOnlyAFirstChunkWhenTheTitleIsEarly() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)

        // Title and timestamp are both on line 1; the remaining 2 MB is
        // tool-call padding the matcher has no reason to touch.
        try writeTranscript(
            in: dir, file: "decoy.jsonl", sessionId: "decoy",
            title: "scout-dreaming-20200101-0000",
            timestamp: started.addingTimeInterval(-99_999),
            paddedToBytes: 2_000_000, modified: started.addingTimeInterval(60))
        try writeTranscript(
            in: dir, file: "wanted.jsonl", sessionId: "wanted",
            title: "scout-briefing-\(titleFragment(for: started))",
            timestamp: started, paddedToBytes: 1_000, modified: started)

        let reader = CountingReader()
        let svc = ClaudeSessionService(projectsDirectory: dir, reader: reader)
        let activity = await svc.activity(for: Run.make(startedAt: started))

        #expect(activity?.sessionId == "wanted")
        let decoyBytes = reader.bytesRead(forFileNamed: "decoy.jsonl")
        #expect(
            decoyBytes <= Self.firstChunk,
            "read \(decoyBytes) bytes of a transcript that identified itself on line 1"
        )
    }

    @Test("a transcript hiding its title past the first chunk is still matched")
    func activity_stillMatchesATitleBeyondTheFirstChunk() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let fragment = titleFragment(for: started)

        // Mirrors the two real transcripts that carry customTitle at ~195 KB:
        // 120 KB of padding, then the identifying line.
        var text = """
        {"sessionId":"late","timestamp":"\(isoString(started))"}

        """
        let filler = """
        {"sessionId":"late","timestamp":"\(isoString(started))",\
        "message":{"content":[{"type":"tool_use","id":"pad","name":"Bash",\
        "input":{"command":"\(String(repeating: "x", count: 200))"}}]}}

        """
        while text.utf8.count < 120_000 { text += filler }
        text += """
        {"sessionId":"late","customTitle":"scout-briefing-\(fragment)"}

        """
        try text.write(to: dir.appendingPathComponent("late.jsonl"),
                       atomically: true, encoding: .utf8)

        let svc = ClaudeSessionService(projectsDirectory: dir, reader: CountingReader())
        let activity = await svc.activity(for: Run.make(startedAt: started))
        #expect(activity?.sessionId == "late")
    }

    @Test("the matched transcript is still read in full so its tool calls survive")
    func activity_matchedTranscriptIsFullyParsed() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)

        let wanted = try writeTranscript(
            in: dir, file: "wanted.jsonl", sessionId: "wanted",
            title: "scout-briefing-\(titleFragment(for: started))",
            timestamp: started, paddedToBytes: 600_000, modified: started)
        let size = try FileManager.default
            .attributesOfItem(atPath: wanted.path)[.size] as? Int ?? 0

        let reader = CountingReader()
        let svc = ClaudeSessionService(projectsDirectory: dir, reader: reader)
        let activity = try #require(await svc.activity(for: Run.make(startedAt: started)))

        #expect(activity.calls.isEmpty == false, "padding tool_use lines must be parsed")
        #expect(reader.bytesRead(forFileNamed: "wanted.jsonl") >= size)
    }

    @Test("the timestamp fallback also avoids reading rejected transcripts in full")
    func activity_timestampFallbackReadsOnlyHeads() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let started = Date(timeIntervalSince1970: 1_781_600_000)

        // No title matches, so the ±600 s proximity rule decides. The far
        // session is large and must not be pulled in whole to be rejected.
        let far = try writeTranscript(
            in: dir, file: "far.jsonl", sessionId: "far", title: "unrelated",
            timestamp: started.addingTimeInterval(500),
            paddedToBytes: 2_000_000, modified: started.addingTimeInterval(60))
        try writeTranscript(
            in: dir, file: "near.jsonl", sessionId: "near", title: "unrelated",
            timestamp: started.addingTimeInterval(30),
            paddedToBytes: 1_000, modified: started)

        let farSize = try FileManager.default
            .attributesOfItem(atPath: far.path)[.size] as? Int ?? 0
        let reader = CountingReader()
        let svc = ClaudeSessionService(projectsDirectory: dir, reader: reader)
        let activity = await svc.activity(for: Run.make(startedAt: started))

        #expect(activity?.sessionId == "near")
        let farBytes = reader.bytesRead(forFileNamed: "far.jsonl")
        #expect(
            farBytes <= Self.headCap,
            "fallback read \(farBytes) bytes of a \(farSize)-byte transcript it rejected"
        )
    }
}
