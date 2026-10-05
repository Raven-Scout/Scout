import Foundation
import Combine

/// Polls `scoutctl schedule list-upcoming --json` every 60 s and exposes the
/// decoded result. Plan 5 stops scout-app from dispatching launchd plists —
/// the engine owns the schedule, the app is a UI mirror.
///
/// `scoutctl` may be either the binary path directly OR `/usr/bin/env`
/// (with `scoutctl` injected as the first arg via `argumentsPrefix`); both
/// patterns are supported so production can use PATH lookup while tests
/// can pin an explicit path.
@MainActor
final class ScheduleService: ObservableObject {
    @Published private(set) var upcoming: [UpcomingRun] = []
    /// Most recent error surface for the UI. `nil` when the last refresh
    /// succeeded. Previously every error was swallowed silently and the
    /// user saw an empty heartbeat strip with no clue why (e.g. scoutctl
    /// not on the GUI app's PATH).
    @Published private(set) var lastError: String? = nil

    private let runner: any ProcessRunner
    private let scoutctl: URL
    private let argumentsPrefix: [String]
    private let clock: any ClockSource
    private var pollTimer: Timer?

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        argumentsPrefix: [String] = [],
        clock: any ClockSource = SystemClock()
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.clock = clock
    }

    func start() {
        pollTimer?.invalidate()  // idempotency guard — drop any prior timer so
                                 // a double-call doesn't orphan a still-firing one.
        Task { await self.refresh() }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    func stop() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    /// Internal refresh hook. Called by `start()` on the 60 s tick.
    /// `nonisolated` flavoured: it calls `await runner.run` (off-actor) and
    /// only writes `self.upcoming` while back on `@MainActor`. Errors are
    /// swallowed — the next tick retries. Plan 6+ adds a UI banner for
    /// persistent failures.
    ///
    /// Post-processing (CC-2 / CC-3): scoutctl emits entries in insertion
    /// order (by slot, not by time). The Control Center wants strict
    /// chronological order with past entries dropped, so every consumer
    /// (NowStripView.nextColumn, UpcomingStripView, StatusBarView) can take
    /// `upcoming.first` and get the soonest fire.
    func refresh() async {
        let output: ProcessResult
        do {
            output = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + ["schedule", "list-upcoming", "--window", "24", "--json"],
                environment: [:],
                workingDirectory: nil
            )
        } catch {
            // Exec failed entirely (scoutctl not on path, etc.).
            self.lastError = formatRunnerError(error)
            return
        }
        do {
            let parsed = try JSONDecoder().decode([RawUpcomingRun].self, from: output.stdout)
            let decoded = parsed.compactMap { raw in
                UpcomingRun(
                    slotKey: raw.slot_key,
                    slotType: raw.slot_type,
                    scheduledAtUTC: raw.scheduled_at_utc
                )
            }
            let now = clock.now()
            self.upcoming = decoded
                .filter { $0.scheduledAt > now }
                .sorted { $0.scheduledAt < $1.scheduledAt }
            self.lastError = nil
        } catch {
            // Decode failed: scoutctl ran but stdout wasn't the JSON we
            // expected. Surface a snippet of what was actually returned so
            // the user can tell whether it's a stale scoutctl, a stdout
            // log leak, or a real schema drift. The earlier message ("not
            // valid JSON") gave no actionable signal.
            self.lastError = Self.formatDecodeFailure(
                stdout: output.stdout, stderr: output.stderr
            )
        }
    }

    /// Render an exec error (process failed to even run) into a single-line
    /// message. Keeps PATH-not-found visible as the most likely real-world
    /// failure mode without dragging in OS-error machinery.
    private func formatRunnerError(_ error: Error) -> String {
        let text = String(describing: error)
        if text.contains("ENOENT") || text.contains("No such file") {
            return "scoutctl not found — check that scout-plugin is installed."
        }
        if text.contains("ProcessResult") || text.contains("exitCode") {
            return "scoutctl returned an error. Try `scoutctl schedule list-upcoming --json` in a terminal."
        }
        return "scoutctl exec failed: \(text.prefix(160))"
    }

    /// Build an actionable error message when scoutctl ran but its stdout
    /// didn't decode as the expected JSON. Includes the first ~200 chars of
    /// stdout (and stderr when stdout is empty) so field reports surface
    /// the actual response rather than a generic "not valid JSON" string.
    static func formatDecodeFailure(stdout: Data, stderr: Data) -> String {
        let snippet = previewBytes(stdout, max: 200)
        if snippet.isEmpty {
            let errSnippet = previewBytes(stderr, max: 200)
            if errSnippet.isEmpty {
                return "scoutctl returned empty output. Try `scoutctl schedule list-upcoming --json` in a terminal."
            }
            return "scoutctl returned no JSON. stderr: \(errSnippet)"
        }
        return "scoutctl output wasn't JSON: \(snippet)"
    }

    /// Turn the first `max` bytes of a Data into a single-line debug snippet:
    /// best-effort UTF-8 decode, newlines collapsed to spaces, trimmed. Used
    /// by both ScheduleService and ScheduleEditService when reporting decode
    /// failures back to the user.
    static func previewBytes(_ data: Data, max: Int) -> String {
        guard !data.isEmpty else { return "" }
        let slice = data.prefix(max)
        let raw = String(data: slice, encoding: .utf8) ?? "<binary>"
        let oneLine = raw
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
            .trimmingCharacters(in: .whitespaces)
        if data.count > max {
            return oneLine + "…"
        }
        return oneLine
    }

    /// Wire format from `scoutctl schedule list-upcoming --json`. Snake-case
    /// to match the engine output verbatim — keep this struct private so the
    /// app's own `UpcomingRun` (the public model) stays the single canonical
    /// shape outside this file.
    private struct RawUpcomingRun: Decodable {
        let slot_key: String
        let slot_type: String
        let scheduled_at_local: String
        let scheduled_at_utc: String
    }
}

extension UpcomingRun {
    /// Decode an entry from the engine JSON contract. Returns nil if the
    /// `slot_key` doesn't map to a known `RunType` or if the timestamp can't
    /// be parsed — the caller filters via `compactMap`.
    init?(slotKey: String, slotType: String, scheduledAtUTC: String) {
        guard let type = RunType(slotKey: slotKey) else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: scheduledAtUTC) else { return nil }
        self.id = "\(slotKey)-\(scheduledAtUTC)"
        self.slotKey = slotKey
        self.type = type
        self.scheduledAt = date
    }
}
