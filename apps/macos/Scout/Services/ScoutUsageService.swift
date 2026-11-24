import Combine
import Foundation

/// Today's and this week's usage of Scout's own scheduled runs, from
/// `scoutctl budget usage --json`.
///
/// The app used to sum a per-session token log itself. On a real vault that
/// showed ~270,000M tokens a day: it counted the same message once per
/// streamed content block and took in sessions that were not Scout's runs.
/// The engine already scopes usage to the runs in its usage tracker and
/// dedupes by message id (`scout/scripts/usage_summary.py`), so the app asks
/// it instead of re-parsing transcripts. Shape mirrors `BudgetSettingsService`.
@MainActor
final class ScoutUsageService: ObservableObject {
    @Published private(set) var summary: UsageSummary?
    @Published private(set) var lastError: String?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]
    private let now: @Sendable () -> Date
    private var rolloverTask: Task<Void, Never>?

    static let arguments = ["budget", "usage", "--json"]

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        argumentsPrefix: [String] = [],
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.now = now
    }

    /// Re-read the summary. A failure keeps the last good summary (the card
    /// keeps showing it) and records why, so a transient engine error doesn't
    /// blank the card.
    ///
    /// Never two `scoutctl` reads at once: a call while one is reading asks
    /// for one more read after it (the data may have changed since that read
    /// started, as when a tracker row lands) and waits for it. The Today tile
    /// and the usage card each refresh when the Control Center appears.
    /// Values are only assigned when they change, since every publish
    /// re-renders the whole window through `AppState`.
    func refresh() async {
        if let inFlight {
            readAgain = true
            await inFlight.value
            return
        }
        let task = Task {
            repeat {
                self.readAgain = false
                await self.read()
            } while self.readAgain
        }
        inFlight = task
        await task.value
        inFlight = nil
    }

    private var inFlight: Task<Void, Never>?
    private var readAgain = false

    private func read() async {
        do {
            let result = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + Self.arguments,
                environment: [:],
                workingDirectory: nil
            )
            guard result.exitCode == 0 else {
                let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
                setLastError(Self.failureMessage(exitCode: result.exitCode, stderr: stderr))
                return
            }
            let decoded = try Self.decode(result.stdout)
            if decoded != summary { summary = decoded }
            setLastError(nil)
            scheduleRollover()
        } catch {
            setLastError(error.localizedDescription)
        }
    }

    private func setLastError(_ error: String?) {
        if error != lastError { lastError = error }
    }

    /// Re-read when the summary's "today" ends, so the card and the status
    /// bar don't keep showing yesterday's figures as today's after midnight.
    private func scheduleRollover() {
        rolloverTask?.cancel()
        guard let end = summary?.today.endDate, let delay = Self.rolloverDelay(until: end, now: now()) else {
            return
        }
        rolloverTask = Task { [weak self] in
            // ContinuousClock keeps counting while the Mac sleeps.
            try? await Task.sleep(for: .seconds(delay), clock: .continuous)
            guard !Task.isCancelled else { return }
            await self?.refresh()
        }
    }

    /// Seconds until just after *end*, or nil when it has already passed (a
    /// summary whose day is over by this clock schedules nothing, so a clock
    /// that disagrees with the engine's can't loop).
    nonisolated static func rolloverDelay(until end: Date, now: Date) -> TimeInterval? {
        let remaining = end.timeIntervalSince(now)
        return remaining > 0 ? remaining + 1 : nil
    }

    /// What the card says when `scoutctl budget usage` fails. An engine older
    /// than the command exits 2 with typer's "No such command" usage text,
    /// which means nothing to a reader, so that case asks for a newer engine
    /// instead. Exit 2 alone is any usage error, so the text must match too.
    nonisolated static func failureMessage(exitCode: Int32, stderr: String) -> String {
        let text = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
        if exitCode == 2 && text.contains("No such command") {
            return "it needs a newer Scout engine (this one has no scoutctl budget usage)."
        }
        return text.isEmpty ? "Scout's engine couldn't report usage (exit \(exitCode))." : text
    }

    nonisolated static func decode(_ data: Data) throws -> UsageSummary {
        let decoded = try JSONDecoder().decode(UsageSummary.self, from: data)
        guard decoded.schemaVersion == UsageSummary.supportedSchemaVersion else {
            throw NSError(
                domain: "ScoutUsageService",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey:
                    "Scout's engine reports usage in a newer format (\(decoded.schemaVersion)); update the app."]
            )
        }
        return decoded
    }
}
