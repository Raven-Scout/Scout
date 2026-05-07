import Foundation
import Combine
import SwiftUI

@MainActor
final class SessionLogService: ObservableObject {
    @Published private(set) var runs: [Run] = []

    private let logsDirectory: URL
    private let trackerService: UsageTrackerService
    private let gitService: GitService?
    private let fileEvents: any FileSystemEventSource
    private let clock: any ClockSource
    private let timeZone: TimeZone
    private var watchTask: Task<Void, Never>?

    init(
        logsDirectory: URL,
        trackerService: UsageTrackerService,
        gitService: GitService? = nil,
        fileEvents: any FileSystemEventSource,
        clock: any ClockSource = SystemClock(),
        timeZone: TimeZone = .current
    ) {
        self.logsDirectory = logsDirectory
        self.trackerService = trackerService
        self.gitService = gitService
        self.fileEvents = fileEvents
        self.clock = clock
        self.timeZone = timeZone
    }

    // MARK: - Filename parsing

    struct ParsedFilename: Equatable {
        let runnerScript: String
        let type: RunType
        let startedAt: Date
    }

    nonisolated static func parseFilename(
        _ url: URL,
        timeZone: TimeZone = .current
    ) -> ParsedFilename? {
        let name = url.deletingPathExtension().lastPathComponent
        // Accept: scout-YYYY-MM-DD_HH-MM, dreaming-YYYY-MM-DD_HH-MM, research-YYYY-MM-DD_HH-MM
        guard let underscoreIdx = name.firstIndex(of: "_") else { return nil }
        let head = String(name[..<underscoreIdx])                       // "scout-2026-04-19"
        let tail = String(name[name.index(after: underscoreIdx)...])    // "08-03"
        let headParts = head.components(separatedBy: "-")
        guard headParts.count == 4 else { return nil }
        let runner = headParts[0]
        guard ["scout", "dreaming", "research"].contains(runner) else { return nil }

        let tailParts = tail.components(separatedBy: "-")
        guard tailParts.count == 2 else { return nil }

        // Log filenames carry the system's local-clock time at the moment the
        // shell script ran (`date "+%Y-%m-%d_%H-%M"`). Parsing them in any
        // fixed zone breaks the moment the user travels — the run's wall
        // clock would shift, and downstream `since`/`until` git filters
        // would window past the actual commits. Honor the caller-supplied
        // zone (defaults to current).
        var components = DateComponents()
        components.year = Int(headParts[1])
        components.month = Int(headParts[2])
        components.day = Int(headParts[3])
        components.hour = Int(tailParts[0])
        components.minute = Int(tailParts[1])
        components.timeZone = timeZone
        guard let date = Calendar(identifier: .gregorian).date(from: components) else { return nil }

        let runnerScript: String = {
            switch runner {
            case "scout": return "run-scout.sh"
            case "dreaming": return "run-dreaming.sh"
            case "research": return "run-research.sh"
            default: return "run-scout.sh"
            }
        }()

        let type = deriveType(runner: runner, date: date, timeZone: timeZone)
        return ParsedFilename(runnerScript: runnerScript, type: type, startedAt: date)
    }

    nonisolated static func deriveType(
        runner: String,
        date: Date,
        timeZone: TimeZone = .current
    ) -> RunType {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let hour = cal.component(.hour, from: date)
        let weekday = cal.component(.weekday, from: date)   // 1=Sun ... 7=Sat
        let isWeekend = (weekday == 1 || weekday == 7)

        switch runner {
        case "scout":
            if isWeekend {
                // Weekend manual runs were previously collapsed to .manual,
                // which then poisoned both the displayed type and the commit
                // prefix filter. Bucket by hour so the row says something
                // meaningful — a 7pm Sunday rebuild reads as a consolidation,
                // not as a generic "manual run".
                switch hour {
                case ..<10: return .weekendBriefing
                default:    return .consolidation
                }
            }
            switch hour {
            case 8:  return .morningBriefing
            // Off-slot weekday runs still belong to the briefing/consolidation
            // family — pick the closest scheduled bucket so the prefix filter
            // ("briefing"/"consolidation") still catches the run's commits.
            case ..<10: return .morningBriefing
            default:    return .consolidation
            }
        case "dreaming":
            return .dreaming
        case "research":
            return .research
        default:
            return .manual
        }
    }

    // MARK: - Body parsing

    struct ParsedBody: Equatable {
        let endedAt: Date?
        let exitCode: Int?
        let status: RunStatus
        let logSizeBytes: Int64
        let errorsDetected: [DetectedError]
    }

    nonisolated static func parseBody(at url: URL, filename: ParsedFilename) throws -> ParsedBody {
        let data = try Data(contentsOf: url)
        let text = String(data: data, encoding: .utf8) ?? ""
        let size = Int64(data.count)
        let range = NSRange(text.startIndex..., in: text)

        // Matches `=== SCOUT run finished at …`, `=== SCOUT Dreaming run finished at …`,
        // `=== SCOUT Research run finished at …`. The `(?: \w+)?` optional word is
        // single-token only — if a future runner emits a multi-word prefix (e.g.
        // "SCOUT Meta Review run finished"), broaden to `(?: [\w ]+?)?`. Capture
        // group 3 (duration seconds) is kept for a future "show duration" surface
        // but is not currently read.
        let finishRegex = try NSRegularExpression(
            pattern: #"=== SCOUT(?: \w+)? run finished at (.+?) \(exit code: (-?\d+)(?:, duration: (\d+)s)?\) ==="#
        )
        var endedAt: Date? = nil
        var exitCode: Int? = nil
        if let match = finishRegex.firstMatch(in: text, range: range),
           let dateRange = Range(match.range(at: 1), in: text),
           let codeRange = Range(match.range(at: 2), in: text) {
            endedAt = parseScoutTimestamp(String(text[dateRange]))
            exitCode = Int(text[codeRange])
        }

        let timeoutRegex = try NSRegularExpression(pattern: #"=== TIMEOUT:"#)
        let hasTimeout = timeoutRegex.firstMatch(in: text, range: range) != nil
        let concurrencyRegex = try NSRegularExpression(pattern: #"=== Another SCOUT session running"#)
        let hasConcurrencySkip = concurrencyRegex.firstMatch(in: text, range: range) != nil
        let budgetRegex = try NSRegularExpression(pattern: #"=== Budget check: skipping this run ==="#)
        let hasBudgetSkip = budgetRegex.firstMatch(in: text, range: range) != nil
        let rateLimitRegex = try NSRegularExpression(pattern: #"Rate limit detected"#)
        let hasRateLimit = rateLimitRegex.firstMatch(in: text, range: range) != nil

        let status: RunStatus
        if hasTimeout || exitCode == 124 || exitCode == 137 {
            status = .timeout
        } else if hasBudgetSkip {
            status = .skippedBudget
        } else if hasConcurrencySkip {
            status = .skippedConcurrency
        } else if hasRateLimit {
            status = .rateLimited
        } else if exitCode == 0 {
            status = .success
        } else if exitCode != nil {
            status = .failure
        // `endedAt` is always paired with `exitCode` via `finishRegex` — checking
        // `exitCode != nil` alone is sufficient to distinguish finished runs from
        // the running fallback. If the finish regex is ever split (timestamp-only
        // captures possible without an exit code), this branch needs to fold in
        // `endedAt == nil` too.
        } else {
            status = .running   // fallback: no terminal markers, no exit code
        }

        let errors = scanErrors(in: text)
        return ParsedBody(
            endedAt: endedAt,
            exitCode: exitCode,
            status: status,
            logSizeBytes: size,
            errorsDetected: errors
        )
    }

    /// Promote `.running` runs whose `startedAt` is older than the
    /// per-type `orphanAfter` threshold to `.orphaned`. All other
    /// statuses pass through unchanged.
    nonisolated static func promoteOrphan(
        parsedStatus: RunStatus,
        startedAt: Date,
        type: RunType,
        now: Date
    ) -> RunStatus {
        guard parsedStatus == .running else { return parsedStatus }
        return now.timeIntervalSince(startedAt) > type.orphanAfter
            ? .orphaned
            : .running
    }

    nonisolated private static func scanErrors(in text: String) -> [DetectedError] {
        let patterns: [String] = [
            "429", "rate.?limit", "overloaded", "throttle", "too many requests",
            "insufficient_quota", "context_length_exceeded", "internal server error"
        ]
        var out: [DetectedError] = []
        let lines = text.components(separatedBy: "\n")
        for (idx, line) in lines.enumerated() {
            for pat in patterns {
                if line.range(of: pat, options: [.regularExpression, .caseInsensitive]) != nil {
                    let snippet = String(line.prefix(200))
                    out.append(DetectedError(line: idx + 1, pattern: pat, snippet: snippet))
                    break
                }
            }
        }
        return out
    }

    nonisolated private static func parseScoutTimestamp(_ s: String) -> Date? {
        // Format like "Sun Apr 19 15:00:01 EDT 2026" — `date` default output on macOS
        let formats = ["EEE MMM d HH:mm:ss zzz yyyy", "EEE MMM  d HH:mm:ss zzz yyyy"]
        for fmt in formats {
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.dateFormat = fmt
            if let d = f.date(from: s) { return d }
        }
        return nil
    }

    // MARK: - Run assembly

    func loadInitial() async throws -> [Run] {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(
            at: logsDirectory,
            includingPropertiesForKeys: [.fileSizeKey],
            options: [.skipsHiddenFiles]
        ) else {
            runs = []
            return []
        }
        // Build the base list off the main actor so the UI thread isn't
        // stalled by the per-file parsing loop (hundreds of files).
        // Commits are NOT fetched here — they're resolved lazily via
        // `commits(for:)` when the user opens a Run's detail pane.
        let logURLs = entries.filter { $0.pathExtension == "log" }
        let tracker = trackerService // capture for nonisolated use below
        let nowSnapshot = clock.now() // capture clock reading for orphan sweep
        let tz = timeZone
        let assembled = await Task.detached { () -> [Run] in
            var out: [Run] = []
            for url in logURLs {
                guard let filename = Self.parseFilename(url, timeZone: tz) else { continue }
                guard let body = try? Self.parseBody(at: url, filename: filename) else { continue }
                let cost = await tracker.cost(
                    matching: filename.type.costTrackerKey,
                    near: filename.startedAt,
                    tolerance: 120
                )
                let status = Self.promoteOrphan(
                    parsedStatus: body.status,
                    startedAt: filename.startedAt,
                    type: filename.type,
                    now: nowSnapshot
                )
                let run = Run(
                    id: Run.makeId(type: filename.type, startedAt: filename.startedAt),
                    type: filename.type,
                    runnerScript: filename.runnerScript,
                    source: .launchdScheduled,
                    scheduledAt: nil,
                    startedAt: filename.startedAt,
                    endedAt: body.endedAt,
                    status: status,
                    exitCode: body.exitCode,
                    cost: cost?.budgetSpent,
                    budgetCap: cost?.budgetCap,
                    logPath: url,
                    logSizeBytes: body.logSizeBytes,
                    errorsDetected: body.errorsDetected,
                    commits: [],
                    retryOf: nil
                )
                out.append(run)
            }
            out.sort { $0.startedAt > $1.startedAt }
            return out
        }.value
        runs = assembled
        startWatching()
        return assembled
    }

    /// Resolve commits for a Run on demand. Called by the detail pane when the
    /// user opens the Diff tab — keeps loadInitial() from doing O(N) git calls
    /// on the main thread at launch. Pads the upper bound by 5 minutes so
    /// commits that the runner makes in the wind-down phase (after the
    /// "run finished" marker is written) are still picked up.
    func commits(for run: Run) async -> [Commit] {
        guard let git = gitService else { return [] }
        let end = (run.endedAt ?? clock.now()).addingTimeInterval(5 * 60)
        let start = run.startedAt.addingTimeInterval(-30)
        return (try? await git.commits(
            between: start,
            and: end,
            matchingPrefix: run.type.commitsPrefix
        )) ?? []
    }

    private func startWatching() {
        watchTask?.cancel()
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await event in self.fileEvents.events(for: self.logsDirectory) {
                if event.url.pathExtension == "log" {
                    await self.reconcile(changedFile: event.url)
                }
            }
        }
    }

    private func reconcile(changedFile url: URL) async {
        guard let filename = Self.parseFilename(url, timeZone: timeZone) else { return }
        guard let body = try? Self.parseBody(at: url, filename: filename) else { return }
        let cost = trackerService.cost(
            matching: filename.type.costTrackerKey,
            near: filename.startedAt,
            tolerance: 120
        )
        let status = Self.promoteOrphan(
            parsedStatus: body.status,
            startedAt: filename.startedAt,
            type: filename.type,
            now: clock.now()
        )
        // Commits left empty; resolved lazily via `commits(for:)` when the
        // detail pane opens. Keeps reconciliation cheap on every FS event.
        let commits: [Commit] = []
        let newRun = Run(
            id: Run.makeId(type: filename.type, startedAt: filename.startedAt),
            type: filename.type,
            runnerScript: filename.runnerScript,
            source: .launchdScheduled,
            scheduledAt: nil,
            startedAt: filename.startedAt,
            endedAt: body.endedAt,
            status: status,
            exitCode: body.exitCode,
            cost: cost?.budgetSpent,
            budgetCap: cost?.budgetCap,
            logPath: url,
            logSizeBytes: body.logSizeBytes,
            errorsDetected: body.errorsDetected,
            commits: commits,
            retryOf: nil
        )
        var updated = runs
        if let idx = updated.firstIndex(where: { $0.id == newRun.id }) {
            updated[idx] = newRun
        } else {
            updated.insert(newRun, at: 0)
            updated.sort { $0.startedAt > $1.startedAt }
        }
        runs = updated
    }
}

extension RunType {
    /// The `type` string used in usage-tracker.jsonl (coarse-grained,
    /// matches what write-session-cost.sh emits).
    var costTrackerKey: String {
        switch self {
        case .morningBriefing, .weekendBriefing: return "briefing"
        case .consolidation: return "consolidation"
        case .dreaming:      return "dreaming"
        case .research:      return "research"
        case .manual:        return "manual"
        }
    }

    /// The commit-subject prefix used by Scout for this run type. `.manual`
    /// returns an empty string — the run's own logs don't say which family
    /// it ran in, so the commit picker uses the time window only.
    var commitsPrefix: String {
        switch self {
        case .morningBriefing, .weekendBriefing: return "briefing"
        case .consolidation: return "consolidation"
        case .dreaming:      return "dreaming"
        case .research:      return "research"
        case .manual:        return ""
        }
    }
}

extension RunType {
    /// How long after `startedAt` a run with no terminal marker should be
    /// promoted from `.running` to `.orphaned`. Tuned per run type because
    /// briefings are short but dreaming can legitimately run for hours.
    var orphanAfter: TimeInterval {
        switch self {
        case .research:
            return 2 * 3600
        case .morningBriefing, .weekendBriefing, .consolidation:
            return 6 * 3600
        case .dreaming:
            return 12 * 3600
        case .manual:
            return 6 * 3600
        }
    }
}
