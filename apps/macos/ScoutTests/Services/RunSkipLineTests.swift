import Foundation
import Testing
@testable import Scout

/// Card C3: runs the runner skips on purpose exit before the "run finished"
/// line, so they used to show as running and then orphaned. The lines below
/// are copied from `plugin/templates/run-scout.sh.tmpl` and
/// `plugin/templates/scripts/scout-lock.sh.tmpl`, with the shell variables
/// filled in.
@Suite("RunSkipLine")
struct RunSkipLineTests {
    static let header = "=== Scout run starting at Mon Nov 16 07:00:02 CET 2026 ===\n"

    @Test("each runner skip line maps to its ledger outcome", arguments: [
        ("=== Gate probe: Scout couldn't check its privacy hooks safely here (no ~/.claude; see scoutctl gates probe) — skipping this run ===",
         RunStatus.skippedGates),
        ("=== Gate probe failed, or scoutctl is missing at /opt/scout/bin/scoutctl: Scout's privacy hooks are not known to load in /opt/claude — skipping this run (scoutctl gates probe) ===",
         .skippedGates),
        ("=== Write sandbox: couldn't be set up here (exit 3; see scoutctl sandbox status) — skipping this run ===",
         .skippedSandbox),
        ("=== agent runtime claude-code refused here (exit 2; see above, or scoutctl agent-runtime check) — skipping this run ===",
         .skippedRuntime),
        ("=== model resolve failed for other-runtime — skipping this run (opus is Claude Code's) ===",
         .skippedRuntime),
        ("=== agent runtime claude-code: past its daily token ceiling — skipping this run ===",
         .skippedBudget),
        ("=== Agnes holds the repository lease — skipping this run (lease_held) ===",
         .skippedLease),
        ("=== This vault is linked to Agnes, but scoutctl is missing at ?: can't take the repository lease — skipping (lease_unavailable) ===",
         .skippedLease),
        ("=== Couldn't take the repository lease from Agnes (scoutctl exit 1) — skipping this run (lease_unavailable) ===",
         .skippedLease),
        ("=== Redundant run: a scout run (slot morning-briefing, log scout-2026-11-16_06-59.log) finished ok 40s ago, during this run's 120s lock wait — skipping ===",
         .skippedRedundant),
    ])
    func mapsLine(line: String, expected: RunStatus) {
        #expect(RunSkipLine.status(in: Self.header + line + "\n") == expected)
    }

    @Test("lines that don't skip the run match nothing")
    func ignoresNonSkipLines() {
        for line in [
            "=== Note: a scout run (slot morning-briefing, log x.log) finished ok 40s ago — running anyway ===",
            "=== Lock acquired after 30s wait ===",
            "Claude mentioned the words: skipping this run, Gate probe",   // not a runner banner
            "=== Scout run finished at Mon Nov 16 07:20:00 CET 2026 (exit code: 0, duration: 1198s) ===",
        ] {
            #expect(RunSkipLine.status(in: Self.header + line + "\n") == nil, "\(line)")
        }
    }

    @Test("every skip has a plain reason; nothing else does")
    func skipReasons() {
        for status in RunStatus.allCases {
            let expectSkip = status.rawValue.hasPrefix("skipped")
            #expect(status.isSkip == expectSkip, "\(status)")
            #expect((status.skipReason != nil) == expectSkip, "\(status)")
        }
    }

    // MARK: - Through the log parser

    private func parse(_ body: String, name: String) throws -> RunStatus {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        try body.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }
        let parsed = try #require(SessionLogService.parseFilename(url))
        return try SessionLogService.parseBody(at: url, filename: parsed).status
    }

    @Test("a sandbox skip with no finish line is skipped, not running")
    func sandboxSkipIsNotRunning() throws {
        let status = try parse(Self.header
            + "=== Write sandbox: couldn't be set up here (exit 3; see scoutctl sandbox status) — skipping this run ===\n",
            name: "scout-2026-11-16_07-00.log")
        #expect(status == .skippedSandbox)
        // Terminal, so the age rule never turns it into an orphan.
        #expect(SessionLogService.promoteOrphan(parsedStatus: status, startedAt: .distantPast,
                                                type: .morningBriefing, now: Date()) == .skippedSandbox)
    }

    @Test("a run that waited for the lock and then ran is not a concurrency skip")
    func lockWaitThenRunIsNotASkip() throws {
        let status = try parse(Self.header
            + "=== Another Scout session running (PID 4242) — waiting up to 900s for the lock ===\n"
            + "=== Lock acquired after 30s wait ===\n"
            + "=== Scout run finished at Mon Nov 16 07:20:00 CET 2026 (exit code: 0, duration: 1198s) ===\n",
            name: "scout-2026-11-16_07-01.log")
        #expect(status == .success)
    }

    @Test("a lock skip after a wait is still a concurrency skip")
    func lockSkipAfterWait() throws {
        let status = try parse(Self.header
            + "=== Another Scout session running (PID 4242) — waiting up to 900s for the lock ===\n"
            + "=== Another Scout session running (PID 4242) — skipping after a 900s wait ===\n",
            name: "scout-2026-11-16_07-02.log")
        #expect(status == .skippedConcurrency)
    }
}
