import Foundation

/// INTERIM (card C3): reads the runner's own "skipping this run" log lines,
/// so a run the runner skipped on purpose shows as skipped instead of as
/// running and then orphaned. These skips exit before the runner writes its
/// "run finished" line, so without this the log looks unfinished.
///
/// The run ledger (`.scout-logs/run-outcomes.jsonl`, card C8) records the
/// same outcomes properly; once the app reads it, this stays only as the
/// fallback for runs without a ledger row. Keep it small: one pattern per
/// runner line, in `plugin/templates/run-*.sh.tmpl` and
/// `plugin/templates/scripts/scout-lock.sh.tmpl`.
///
/// The budget and "another session running" skips keep their older
/// matchers in `SessionLogService`.
nonisolated enum RunSkipLine {
    private static let lines: [(pattern: String, status: RunStatus)] = [
        // `=== Gate probe: Scout couldn't check its privacy hooks safely here (…) — skipping this run ===`
        // `=== Gate probe failed, or scoutctl is missing at …: … — skipping this run (scoutctl gates probe) ===`
        (#"^=== Gate probe\b.*skipping this run"#, .skippedGates),
        // `=== Write sandbox: couldn't be set up here (exit N; …) — skipping this run ===`
        (#"^=== Write sandbox:.*skipping this run"#, .skippedSandbox),
        // `=== agent runtime X: past its daily token ceiling — skipping this run ===` (ledger: skipped_budget)
        (#"^=== agent runtime .*daily token ceiling.*skipping this run"#, .skippedBudget),
        // `=== agent runtime X refused here (exit N; …) — skipping this run ===`
        (#"^=== agent runtime .*skipping this run"#, .skippedRuntime),
        // `=== model resolve failed for X — skipping this run (…) ===`
        (#"^=== model resolve failed\b.*skipping this run"#, .skippedRuntime),
        // `=== Agnes holds the repository lease — skipping this run (lease_held) ===`, and the
        // `lease_unavailable` variants ("can't take the repository lease — skipping (lease_unavailable)").
        (#"^=== .*repository lease.*skipping"#, .skippedLease),
        // `=== Redundant run: a scout run (…) finished ok Ns ago, during this run's Ns lock wait — skipping ===`
        (#"^=== Redundant run:.*skipping"#, .skippedRedundant),
    ]
    private static let patterns: [(regex: NSRegularExpression, status: RunStatus)] = lines.map { line in
        (try! NSRegularExpression(pattern: line.pattern, options: [.caseInsensitive, .anchorsMatchLines]), line.status)
    }

    /// The skip this log records, or nil when it records none. The first
    /// pattern in the list above that matches wins.
    static func status(in text: String) -> RunStatus? {
        let range = NSRange(text.startIndex..., in: text)
        return patterns.first { $0.regex.firstMatch(in: text, range: range) != nil }?.status
    }
}
