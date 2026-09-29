import Testing
import Foundation
@testable import Scout

@Suite("Fake scout run — end to end")
struct FakeScoutRunIntegrationTests {

    @Test func detectsSyntheticRun() async throws {
        // Set up a sandbox Scout directory
        let sandbox = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-e2e-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: sandbox, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: sandbox) }

        let logsDir = sandbox.appendingPathComponent(".scout-logs")
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let trackerURL = logsDir.appendingPathComponent("usage-tracker.jsonl")
        FileManager.default.createFile(atPath: trackerURL.path, contents: Data())

        let watcher = FileWatcher()
        let tracker = await UsageTrackerService(trackerURL: trackerURL, fileEvents: watcher)
        _ = try await tracker.loadInitial()
        let svc = await SessionLogService(
            logsDirectory: logsDir,
            trackerService: tracker,
            fileEvents: watcher,
            parseCacheURL: sandboxParseCacheURL(in: sandbox)
        )
        _ = try await svc.loadInitial()

        // Give FSEvents a moment to arm
        try await Task.sleep(nanoseconds: 400_000_000)

        // Write a synthetic log + tracker line directly. (The sandboxed test
        // process can't reliably shell out to fake-scout-run.sh; we just
        // reproduce its effect here.)
        let ts = Self.timestampForFilename()
        let log = """
        === SCOUT run starting at \(Date()) ===
        (fake run for integration test)
        === SCOUT run finished at \(Date()) (exit code: 0, duration: 5s) ===
        """
        let logURL = logsDir.appendingPathComponent("scout-\(ts).log")
        try log.write(to: logURL, atomically: true, encoding: .utf8)

        let trackerLine = """
        {"ts":"\(ISO8601DateFormatter().string(from: Date()))","ts_et":"test","type":"briefing","budget_cap":10,"budget_spent":0.01,"exit_code":0,"source":"session"}\n
        """
        if let handle = try? FileHandle(forWritingTo: trackerURL) {
            defer { try? handle.close() }
            try handle.seekToEnd()
            if let data = trackerLine.data(using: .utf8) { try handle.write(contentsOf: data) }
        }

        let logFiles = (try? FileManager.default.contentsOfDirectory(
            at: logsDir, includingPropertiesForKeys: nil
        )) ?? []
        let logCreated = logFiles.contains { $0.pathExtension == "log" }
        #expect(logCreated, "synthetic log file should exist at \(logURL.path)")

        // Wait up to 10s for reconciliation (FSEvents latency can be variable)
        let start = Date()
        var detected: [Run] = []
        while Date().timeIntervalSince(start) < 10 {
            detected = await svc.runs
            if !detected.isEmpty { break }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        #expect(!detected.isEmpty, "service should detect the synthetic run within 10 seconds")
    }

    private static func timestampForFilename() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd_HH-mm"
        f.timeZone = TimeZone(identifier: "America/New_York")
        return f.string(from: Date())
    }
}
