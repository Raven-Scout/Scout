import Testing
import Foundation
@testable import Scout

@Suite("ConnectorHealthService")
struct ConnectorHealthServiceTests {
    @Test func buildsMatrixFromFixtureAndFiltersAckedAlerts() async throws {
        // The committed fixture (connector-calls-2026-04-22.jsonl) ages out of
        // the service's 14-day window once real-world time advances. Synthesize
        // an equivalent fixture in-line keyed off "now" so this test stays
        // green regardless of when it runs. Mirrors the structure of the
        // committed fixture: three sessions across the same connectors,
        // including legacy keys (mcp:plugin_*) that should canonicalize.
        let logsDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("Connector_fixture_\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: logsDir) }

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        let now = Date()
        let s1 = iso.string(from: now.addingTimeInterval(-3600 * 6))   // 6h ago
        let s2 = iso.string(from: now.addingTimeInterval(-3600 * 4))   // 4h ago
        let s3 = iso.string(from: now.addingTimeInterval(-3600 * 2))   // 2h ago
        let firstSeen = iso.string(from: now.addingTimeInterval(-3600 * 4))
        let alertTs = iso.string(from: now.addingTimeInterval(-3600 * 3))

        let callsJSONL = """
        {"ts":"\(s1)","session_id":"s1","mode":"briefing","tool":"mcp__plugin_slack_slack__slack_send_message","connector":"mcp:plugin_slack_slack","error":false}
        {"ts":"\(s1)","session_id":"s1","mode":"briefing","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":false}
        {"ts":"\(s2)","session_id":"s2","mode":"consolidation","tool":"mcp__plugin_slack_slack__slack_read_channel","connector":"mcp:plugin_slack_slack","error":false}
        {"ts":"\(s2)","session_id":"s2","mode":"consolidation","tool":"mcp__claude_ai_Google_Drive__list_recent_files","connector":"mcp:claude_ai_Google_Drive","error":true,"err":"auth expired"}
        {"ts":"\(s2)","session_id":"s2","mode":"consolidation","tool":"mcp__claude_ai_Google_Drive__list_recent_files","connector":"mcp:claude_ai_Google_Drive","error":true,"err":"auth expired"}
        {"ts":"\(s2)","session_id":"s2","mode":"consolidation","tool":"mcp__claude_ai_Google_Drive__list_recent_files","connector":"mcp:claude_ai_Google_Drive","error":true,"err":"auth expired"}
        {"ts":"\(s3)","session_id":"s3","mode":"dreaming","tool":"mcp__plugin_linear_linear__list_issues","connector":"mcp:plugin_linear_linear","error":false}
        {"ts":"\(s3)","session_id":"s3","mode":"dreaming","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":true,"err":"rate-limited"}
        {"ts":"\(s3)","session_id":"s3","mode":"dreaming","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":false}
        {"ts":"\(s3)","session_id":"s3","mode":"dreaming","tool":"mcp__claude_ai_Gmail__search_threads","connector":"mcp:claude_ai_Gmail","error":false}
        """
        try callsJSONL.write(
            to: logsDir.appendingPathComponent("connector-calls-now.jsonl"),
            atomically: true, encoding: .utf8
        )
        let alertLine = "\(alertTs) | CRITICAL | mcp:claude_ai_Google_Drive | zero successful calls in last 3 runs | first_seen=\(firstSeen)\n"
        try alertLine.write(
            to: logsDir.appendingPathComponent("connector-alerts.log"),
            atomically: true, encoding: .utf8
        )

        let ackURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString)-acked.json")
        defer { try? FileManager.default.removeItem(at: ackURL) }

        let service = await ConnectorHealthService(
            logsDirectory: logsDir,
            ackStoreURL: ackURL,
            fileEvents: NoopFS(),
            connectors: ["mcp:claude_ai_Slack",
                         "mcp:claude_ai_Gmail",
                         "mcp:claude_ai_Google_Drive"]
        )
        try await service.loadInitial()
        let matrix = await service.matrix
        #expect(matrix.sessionsNewestFirst.count == 3)

        // The fixture log has one active CRITICAL (Drive).
        let active = await service.activeAlerts
        #expect(active.count == 1)
        #expect(active.first?.connector == "mcp:claude_ai_Google_Drive")

        // Acknowledge it → activeAlerts is empty.
        let fp = active.first!.fingerprint
        await service.acknowledge(fingerprint: fp)
        #expect(await service.activeAlerts.isEmpty)
    }

    // MARK: - Plan 4 Task 8: roster snapshot loading

    /// Snapshot present + valid → defaultConnectors reflects its keys.
    @Test func loadsRosterFromSnapshot() async throws {
        let fixtures = Bundle(for: FixtureAnchor.self).resourceURL!
        let snapshotURL = fixtures.appendingPathComponent("connectors.snapshot.json")
        // The fixture is committed to the repo by Plan 4 Task 8 — bail out
        // explicitly if it's missing so the failure mode is informative.
        try #require(FileManager.default.fileExists(atPath: snapshotURL.path),
                     "expected fixture connectors.snapshot.json bundled into ScoutTests")

        let result = ConnectorHealthService.loadRoster(from: snapshotURL)
        switch result {
        case .success(let keys):
            // Sanity: 10 connectors; canonical claude.ai keys, not the legacy
            // mcp:plugin_* keys.
            #expect(keys.count == 10)
            #expect(keys.contains("mcp:claude_ai_Slack"))
            #expect(keys.contains("mcp:claude_ai_Linear"))
            #expect(keys.contains("mcp:claude_ai_Gmail"))
            #expect(keys.contains("notify:telegram"))
            #expect(!keys.contains("mcp:plugin_slack_slack"))
            // Order matches YAML insertion order — Slack first, Telegram last.
            #expect(keys.first == "mcp:claude_ai_Slack")
            #expect(keys.last == "notify:telegram")
        case .failure(let err):
            Issue.record("loadRoster should have succeeded: \(err.message)")
        }
    }

    /// Service initialised with an explicit snapshot URL pointing at the
    /// bundled fixture uses those keys (no fallback).
    @Test func serviceUsesSnapshotKeysWhenAvailable() async throws {
        let fixtures = Bundle(for: FixtureAnchor.self).resourceURL!
        let snapshotURL = fixtures.appendingPathComponent("connectors.snapshot.json")
        try #require(FileManager.default.fileExists(atPath: snapshotURL.path))

        let logsDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("Connector_snap_\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: logsDir) }

        let ackURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString)-acked.json")
        defer { try? FileManager.default.removeItem(at: ackURL) }

        let service = await ConnectorHealthService(
            logsDirectory: logsDir,
            ackStoreURL: ackURL,
            fileEvents: NoopFS(),
            connectors: nil,
            snapshotURL: snapshotURL
        )

        // Fallback reason should be unset (we read the snapshot successfully).
        let reason = await service.rosterFallbackReason
        #expect(reason == nil, "expected snapshot path; got fallback: \(reason ?? "")")

        // Matrix is keyed on the snapshot connectors.
        let matrix = await service.matrix
        #expect(matrix.connectors.count == 10)
        #expect(matrix.connectors.contains("mcp:claude_ai_Slack"))
    }

    /// Snapshot missing → service uses fallback list AND surfaces a reason.
    @Test func serviceFallsBackWhenSnapshotMissing() async throws {
        let logsDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("Connector_fb_\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: logsDir) }

        let ackURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(UUID().uuidString)-acked.json")
        defer { try? FileManager.default.removeItem(at: ackURL) }

        let bogusURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("does-not-exist-\(UUID().uuidString).json")

        let service = await ConnectorHealthService(
            logsDirectory: logsDir,
            ackStoreURL: ackURL,
            fileEvents: NoopFS(),
            connectors: nil,
            snapshotURL: bogusURL
        )

        // Operator-visible signal must be set.
        let reason = await service.rosterFallbackReason
        #expect(reason != nil)
        #expect(reason?.contains("scoutctl connectors snapshot") == true)

        // Service still works — uses the fallback list.
        let matrix = await service.matrix
        #expect(matrix.connectors == ConnectorHealthService.fallbackConnectors)
        #expect(matrix.connectors.contains("mcp:claude_ai_Slack"))
    }

    /// The hardcoded fallback uses the canonical claude.ai keys, not the
    /// legacy `mcp:plugin_*` keys that never matched what the shell emitted.
    @Test func fallbackUsesCanonicalKeys() {
        let fb = ConnectorHealthService.fallbackConnectors
        #expect(fb.contains("mcp:claude_ai_Slack"))
        #expect(fb.contains("mcp:claude_ai_Linear"))
        #expect(!fb.contains("mcp:plugin_slack_slack"))
        #expect(!fb.contains("mcp:plugin_linear_linear"))
        #expect(fb.count == 10)
    }

    /// Malformed snapshot → loadRoster returns failure, not a crash.
    @Test func loadRosterRejectsMalformedJSON() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("bad-snap-\(UUID().uuidString).json")
        try Data("not json at all".utf8).write(to: tmp)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let result = ConnectorHealthService.loadRoster(from: tmp)
        switch result {
        case .success:
            Issue.record("expected failure on malformed JSON")
        case .failure(let err):
            #expect(err.message.contains("decode") || err.message.contains("failed"))
        }
    }
}
