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
    private var sweepTimer: Timer?

    /// Where parsed-log results are cached between launches. `nil` (production
    /// default) resolves to the per-user caches directory; tests inject a temp
    /// URL for isolation.
    private let parseCacheURLOverride: URL?

    init(
        logsDirectory: URL,
        trackerService: UsageTrackerService,
        gitService: GitService? = nil,
        fileEvents: any FileSystemEventSource,
        clock: any ClockSource = SystemClock(),
        timeZone: TimeZone = .current,
        parseCacheURL: URL? = nil
    ) {
        self.logsDirectory = logsDirectory
        self.trackerService = trackerService
        self.gitService = gitService
        self.fileEvents = fileEvents
        self.clock = clock
        self.timeZone = timeZone
        self.parseCacheURLOverride = parseCacheURL
    }

    // MARK: - Filename parsing

    struct ParsedFilename: Equatable, Sendable {
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

    struct ParsedBody: Equatable, Sendable, Codable {
        let endedAt: Date?
        let exitCode: Int?
        let status: RunStatus
        let logSizeBytes: Int64
        let errorsDetected: [DetectedError]
    }

    // Compiled once and reused. `parseBody` runs on every FSEvent for a log
    // file; recompiling these five patterns per call was pure overhead on a
    // hot path (issue #22). The finish-marker pattern matches both historical
    // casings:
    //   `=== SCOUT run finished at …`           (pre-2026-05-01)
    //   `=== Scout Dreaming run finished at …`  (current)
    // The runner script was renamed in scout-plugin around May 2026 to
    // mixed-case "Scout"; our regex was still pinned to all-caps "SCOUT" so
    // every log after the rename parsed as still-running → orphaned (CC-1
    // follow-up). Case-insensitivity is set via the constructor option so the
    // whole pattern handles either casing uniformly — inline `(?i:...)` groups
    // behaved inconsistently across NSRegular builds with lazy optional groups.
    // The `(?: \w+)?` token allows multi-word run-type prefixes ("Scout
    // Dreaming", future "Scout Meta Review", etc.). Capture group 3 (duration
    // seconds) is kept for a future "show duration" surface, currently unused.
    private static let finishRegex = try! NSRegularExpression(
        pattern: #"=== Scout(?: \w+)? run finished at (.+?) \(exit code: (-?\d+)(?:, duration: (\d+)s)?\) ==="#,
        options: [.caseInsensitive]
    )
    private static let timeoutRegex = try! NSRegularExpression(pattern: #"=== TIMEOUT:"#)
    // `=== Another {{INSTANCE_NAME}} session running (PID …) — skipping ===`.
    // INSTANCE_NAME is install-configurable and was renamed all-caps "SCOUT" →
    // mixed-case "Scout" in the scout-plugin runner templates — the same rename
    // `finishRegex` above already handles. This regex was missed: it stayed
    // case-sensitive and pinned to literal "SCOUT", so a post-rename (or
    // custom-instance) concurrency-skip log failed to match and fell through to
    // a phantom `.running` session (issue #31). Match the stable template
    // wrapper case-insensitively and without pinning the instance name.
    // (`finishRegex` still pins the literal "Scout" instance name — correct for
    // the default install, but the same latent limitation for custom instances.)
    private static let concurrencyRegex = try! NSRegularExpression(
        pattern: #"=== Another .+? session running"#,
        options: [.caseInsensitive]
    )
    private static let budgetRegex = try! NSRegularExpression(pattern: #"=== Budget check: skipping this run ==="#)
    private static let rateLimitRegex = try! NSRegularExpression(pattern: #"Rate limit detected"#)

    nonisolated static func parseBody(at url: URL, filename: ParsedFilename) throws -> ParsedBody {
        let data = try Data(contentsOf: url)
        let text = String(data: data, encoding: .utf8) ?? ""
        let size = Int64(data.count)
        let range = NSRange(text.startIndex..., in: text)

        var endedAt: Date? = nil
        var exitCode: Int? = nil
        if let match = finishRegex.firstMatch(in: text, range: range),
           let dateRange = Range(match.range(at: 1), in: text),
           let codeRange = Range(match.range(at: 2), in: text) {
            endedAt = parseScoutTimestamp(String(text[dateRange]))
            exitCode = Int(text[codeRange])
        }

        let hasTimeout = timeoutRegex.firstMatch(in: text, range: range) != nil
        let hasConcurrencySkip = concurrencyRegex.firstMatch(in: text, range: range) != nil
        let hasBudgetSkip = budgetRegex.firstMatch(in: text, range: range) != nil
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

    /// Post-pass over an assembled run list that demotes `.running` entries
    /// when a newer run of the same type has a terminal status. Without this,
    /// a stuck-looking earlier run (e.g. a Dreaming whose finish marker the
    /// shell script failed to write before crashing) keeps showing as
    /// "running" indefinitely even though a later run of the same type
    /// already completed cleanly — that was the original CC-1 / CC-5 bug.
    ///
    /// Logic:
    /// - Find, per `RunType`, the most-recent run whose status is **terminal**
    ///   (anything except `.running`).
    /// - For every still-`.running` run, if a terminal run of the same type
    ///   started strictly later than this one, this run is stale → `.orphaned`.
    ///
    /// Time-based promotion is handled by `promoteOrphan` per-run; this method
    /// only handles the "I have evidence a newer same-type run finished"
    /// branch. Both rules are layered so e.g. an orphan-by-age pass can run
    /// first, then this reconciles cross-run.
    nonisolated static func resolveStaleRunning(_ runs: [Run]) -> [Run] {
        var latestTerminal: [RunType: Date] = [:]
        for r in runs where r.status != .running {
            let prev = latestTerminal[r.type] ?? .distantPast
            if r.startedAt > prev { latestTerminal[r.type] = r.startedAt }
        }
        return runs.map { r -> Run in
            guard r.status == .running,
                  let newerTerminal = latestTerminal[r.type],
                  newerTerminal > r.startedAt
            else { return r }
            return r.with(status: .orphaned)
        }
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

    /// Parse the `date`-style timestamp the runner script writes in finish
    /// markers. Format examples seen in the wild:
    ///   "Sun Apr 19 15:00:01 EDT 2026"     (NA timezone — POSIX recognises)
    ///   "Tue May 12 11:51:48 CEST 2026"    (EU timezone — POSIX does NOT)
    ///
    /// POSIX locale's `DateFormatter` zone table doesn't include CEST/CET,
    /// so the original implementation silently returned nil for every
    /// European-timestamped log and the UI lost run-duration display for
    /// any session run while the user is travelling. Fix: pre-extract a
    /// known zone abbreviation, replace it with a UTC-offset placeholder,
    /// then parse with the offset. Falls back to the original zzz-format
    /// for anything unrecognised.
    nonisolated private static func parseScoutTimestamp(_ s: String) -> Date? {
        // Manual zone → offset table covering every abbreviation the
        // user might see on macOS `date` output. Daylight-saving variants
        // included; offsets are seconds.
        let zoneOffsets: [String: Int] = [
            // North America
            "EDT": -4 * 3600, "EST": -5 * 3600,
            "CDT": -5 * 3600, "CST": -6 * 3600,
            "MDT": -6 * 3600, "MST": -7 * 3600,
            "PDT": -7 * 3600, "PST": -8 * 3600,
            // Europe / UK
            "GMT": 0, "BST": 1 * 3600,
            "CET":  1 * 3600, "CEST": 2 * 3600,
            "EET":  2 * 3600, "EEST": 3 * 3600,
            "WET":  0,        "WEST": 1 * 3600,
            // Misc
            "UTC": 0, "Z": 0,
        ]
        // Find any token in the input that matches a known zone.
        for (abbr, seconds) in zoneOffsets {
            guard s.contains(" \(abbr) ") else { continue }
            // Convert seconds to "+HHMM" / "-HHMM"
            let sign = seconds >= 0 ? "+" : "-"
            let abs = Swift.abs(seconds)
            let hh = abs / 3600
            let mm = (abs % 3600) / 60
            let offsetStr = String(format: "%@%02d%02d", sign, hh, mm)
            let normalised = s.replacingOccurrences(of: " \(abbr) ", with: " \(offsetStr) ")
            let formats = ["EEE MMM d HH:mm:ss Z yyyy", "EEE MMM  d HH:mm:ss Z yyyy"]
            for fmt in formats {
                let f = DateFormatter()
                f.locale = Locale(identifier: "en_US_POSIX")
                f.dateFormat = fmt
                if let d = f.date(from: normalised) { return d }
            }
        }
        // Fallback: original zzz-formatter (handles whatever POSIX recognises).
        let fallbackFormats = ["EEE MMM d HH:mm:ss zzz yyyy", "EEE MMM  d HH:mm:ss zzz yyyy"]
        for fmt in fallbackFormats {
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
            includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else {
            runs = []
            return []
        }
        // Build the base list off the main actor so the UI thread isn't
        // stalled by the per-file parsing loop (hundreds of files).
        // Commits are NOT fetched here — they're resolved lazily via
        // `commits(for:)` when the user opens a Run's detail pane.
        // Identity (size + mtime) per log file keys the on-disk parse cache, so a
        // relaunch re-parses only new or still-growing logs instead of the whole
        // (unbounded) history — the launch-time CPU hang.
        let logMetas: [LogFileMeta] = entries
            .filter { $0.pathExtension == "log" }
            .compactMap { url in
                let rv = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                guard let size = rv?.fileSize, let mtime = rv?.contentModificationDate else { return nil }
                return LogFileMeta(url: url, name: url.lastPathComponent,
                                   sizeBytes: Int64(size), modifiedAt: mtime)
            }
        let tracker = trackerService // capture for nonisolated use below
        let nowSnapshot = clock.now() // capture clock reading for orphan sweep
        let tz = timeZone
        let cacheURL = parseCacheURLOverride ?? Self.defaultParseCacheURL()
        let assembled = await Task.detached { () -> [Run] in
            // Reuse cached parses for unchanged logs; parse only the rest.
            let cache = Self.loadParseCache(at: cacheURL)
            let resolved = Self.resolveCachedBodies(files: logMetas, cache: cache) { meta in
                guard let filename = Self.parseFilename(meta.url, timeZone: tz) else { return nil }
                return try? Self.parseBody(at: meta.url, filename: filename)
            }
            Self.saveParseCache(resolved.updatedCache, at: cacheURL)

            var out: [Run] = []
            for meta in logMetas {
                let url = meta.url
                guard let filename = Self.parseFilename(url, timeZone: tz) else { continue }
                guard let body = resolved.bodies[meta.name] else { continue }
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
        // Cross-run reconciliation: a `.running` entry that has a newer
        // same-type terminal sibling is by definition stale → orphan.
        runs = Self.resolveStaleRunning(assembled)
        startWatching()
        startStaleSweep()
        return runs
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
        // Session logs are appended continuously while a run executes; without
        // coalescing, every FSEvent triggers a full re-parse and the main actor
        // can fall permanently behind under heavy vault churn (issue #22).
        // Subscribe synchronously — calling events(for:) inside the task left
        // a window where events emitted before the task ran were dropped.
        let events = DebouncedFileEvents(base: fileEvents, interval: .milliseconds(250))
            .events(for: logsDirectory)
        watchTask = Task { [weak self] in
            for await event in events {
                if event.url.pathExtension == "log" {
                    await self?.reconcile(changedFile: event.url)
                }
            }
        }
    }

    private func reconcile(changedFile url: URL) async {
        guard let filename = Self.parseFilename(url, timeZone: timeZone) else { return }
        // parseBody does blocking file IO plus several regex scans; run it off
        // the main actor so even a coalesced reconcile burst can't stall the UI
        // thread (issue #22). The result hops back to the main actor on return.
        let parseTask = Task.detached(priority: .utility) {
            try? Self.parseBody(at: url, filename: filename)
        }
        guard let body = await parseTask.value else { return }
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
        // Reconcile after every change — a newly-terminal run may now
        // invalidate an older same-type `.running` entry.
        runs = Self.resolveStaleRunning(updated)
    }

    /// Re-applies orphan/stale rules against the current snapshot. Used by
    /// the periodic sweep timer so a long-running app session still demotes
    /// `.running` → `.orphaned` as clock time crosses the per-type cutoff,
    /// even when no file event fires.
    func sweepStaleStatuses() {
        let now = clock.now()
        let resweeped = runs.map { run -> Run in
            let promoted = Self.promoteOrphan(
                parsedStatus: run.status,
                startedAt: run.startedAt,
                type: run.type,
                now: now
            )
            return promoted == run.status ? run : run.with(status: promoted)
        }
        let resolved = Self.resolveStaleRunning(resweeped)
        // Only republish if anything actually changed — avoids needless
        // SwiftUI re-renders on every tick.
        if resolved != runs { runs = resolved }
    }

    private func startStaleSweep() {
        sweepTimer?.invalidate()
        sweepTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.sweepStaleStatuses() }
        }
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
    /// promoted from `.running` to `.orphaned`. Tuned per run type from
    /// observed realistic upper bounds — the old thresholds (6h briefings,
    /// 12h dreaming) were way too loose and produced multi-hour false
    /// "running" badges in the Now strip and Sessions list. CC-1/CC-5.
    ///
    /// Observed P95s (May 2026 on Alex's box):
    ///   briefing: ~6 min   · consolidation: ~3 min
    ///   dreaming: ~25 min  · research: ~40 min
    /// Cutoffs leave ~3× headroom so a legitimately long run still resolves
    /// as running, but a missing finish-marker tips into orphan inside ~30
    /// minutes of inactivity, not half a day.
    var orphanAfter: TimeInterval {
        switch self {
        case .morningBriefing, .weekendBriefing:
            return 30 * 60           // 30 min — briefing runs short
        case .consolidation:
            return 20 * 60           // 20 min — consolidation runs even shorter
        case .dreaming:
            return 2 * 3600          // 2 h — long-form synthesis
        case .research:
            return 2 * 3600          // 2 h — unchanged, research can legitimately churn
        case .manual:
            return 45 * 60           // 45 min — catch-all, prefer to orphan fast
        }
    }
}
