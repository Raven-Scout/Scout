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
    /// Statuses from the run ledger (`scoutctl runs --json`), by log file name.
    /// A ledger row is the record of a finished run, so it wins over what the
    /// log's lines suggested; runs without a row (older engines, a run still
    /// going) keep their log-derived status.
    private var ledgerStatuses: [String: RunStatus] = [:]
    /// Each ledger row's slot key, by log file name: the schedule a run ran
    /// for, which "Run again" fires (`Run.slotKey`).
    private var ledgerSlots: [String: String] = [:]
    /// Each ledger row's `trigger`, by log file name (`Run.trigger`).
    private var ledgerTriggers: [String: String] = [:]
    /// Each ledger row's `failure_class`, by log file name (`Run.failureClass`).
    private var ledgerFailureClasses: [String: String] = [:]
    /// Each ledger row's write protection and integrity check, by log file name (`Run.protection`).
    private var ledgerProtection: [String: Run.Protection] = [:]
    /// Each ledger row's run name (`DisplayName.ledgerRun` of its slot or mode), by log file name
    /// (`Run.ledgerName`): the ledger, not the log's file name, says what kind of run it was.
    private var ledgerNames: [String: String] = [:]
    /// Each ledger row's slot-or-mode key (`DisplayName.ledgerRunKey`), by log file name
    /// (`Run.ledgerKey`): the key the name, the "Started by you" badge and the Type filter share.
    private var ledgerKeys: [String: String] = [:]

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
        /// The runner's PID when the log has the seconds+PID name a run falls
        /// back to when another run already claimed this minute's log; nil for
        /// the minute-named log.
        var pid: Int? = nil
        /// `-SS-PID` (as written in the name) for a seconds+PID log, nil for a
        /// minute-named one. Keeps per-run file names (feedback) apart.
        var uniqueSuffix: String? = nil
    }

    nonisolated static func parseFilename(
        _ url: URL,
        timeZone: TimeZone = .current
    ) -> ParsedFilename? {
        let name = url.deletingPathExtension().lastPathComponent
        // Accept <runner>-YYYY-MM-DD_HH-MM (the run that claimed the minute's
        // log, and every log written before unique names) and
        // <runner>-YYYY-MM-DD_HH-MM-SS-PID (a later run in the same minute),
        // for runner scout, dreaming or research.
        guard let underscoreIdx = name.firstIndex(of: "_") else { return nil }
        let head = String(name[..<underscoreIdx])                       // "scout-2026-04-19"
        let tail = String(name[name.index(after: underscoreIdx)...])    // "08-03"
        let headParts = head.components(separatedBy: "-")
        guard headParts.count == 4 else { return nil }
        let runner = headParts[0]
        guard ["scout", "dreaming", "research"].contains(runner) else { return nil }

        let tailParts = tail.components(separatedBy: "-")
        guard tailParts.count == 2 || tailParts.count == 4,
              tailParts.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isASCII) && $0.allSatisfy(\.isNumber) })
        else { return nil }
        var second = 0
        var pid: Int? = nil
        var uniqueSuffix: String? = nil
        if tailParts.count == 4 {
            guard tailParts[2].count == 2, let s = Int(tailParts[2]), s < 60,
                  let p = Int(tailParts[3]), p > 0
            else { return nil }
            second = s
            pid = p
            uniqueSuffix = "-\(tailParts[2])-\(tailParts[3])"
        }

        // Log filenames carry the system's local-clock time at the moment the
        // shell script ran (`date "+%Y-%m-%d_%H-%M-%S"`). Parsing them in any
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
        components.second = second
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
        return ParsedFilename(runnerScript: runnerScript, type: type, startedAt: date,
                              pid: pid, uniqueSuffix: uniqueSuffix)
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
        /// Absolute start from the log header; nil when the header is absent.
        let startedAt: Date?
        let endedAt: Date?
        let exitCode: Int?
        let status: RunStatus
        let logSizeBytes: Int64
        let errorsDetected: [DetectedError]
        /// From the runner's `=== model: <model> ===` line; nil for older logs.
        var model: String? = nil
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
    // `=== Scout[ <Kind>] run starting at <date> <ZONE> <year> ===` — every
    // historical casing (`SCOUT`, `Scout`, `Scout Research`, …). Unlike the
    // filename, this carries the zone the run was logged in, so it is the
    // absolute start (#43: filename starts drift by the zone delta once the
    // machine's zone changes).
    private static let startRegex = try! NSRegularExpression(
        pattern: #"=== Scout(?: \w+)? run starting at (.+?) ==="#,
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
    // The line must say "skipping": the lock library also writes
    // `=== Another … session running (PID …) — waiting up to Ns for the lock ===`
    // for a run that then gets the lock and runs (card C3).
    private static let concurrencyRegex = try! NSRegularExpression(
        pattern: #"=== Another .+? session running.*skipping"#,
        options: [.caseInsensitive]
    )
    private static let budgetRegex = try! NSRegularExpression(pattern: #"=== Budget check: skipping this run ==="#)
    private static let rateLimitRegex = try! NSRegularExpression(pattern: #"Rate limit detected"#)
    // `=== model: sonnet ===`, written by the runner before the session starts.
    private static let modelRegex = try! NSRegularExpression(
        pattern: #"^=== model: ([a-z0-9][a-z0-9.\-]*) ===$"#,
        options: [.anchorsMatchLines]
    )

    nonisolated static func parseBody(at url: URL, filename: ParsedFilename) throws -> ParsedBody {
        let data = try Data(contentsOf: url)
        let text = String(data: data, encoding: .utf8) ?? ""
        let size = Int64(data.count)
        let range = NSRange(text.startIndex..., in: text)

        var startedAt: Date? = nil
        if let match = startRegex.firstMatch(in: text, range: range),
           let dateRange = Range(match.range(at: 1), in: text) {
            startedAt = parseScoutTimestamp(String(text[dateRange]))
        }

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
        } else if let skip = RunSkipLine.status(in: text) {
            status = skip
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

        var model: String? = nil
        if let match = modelRegex.firstMatch(in: text, range: range),
           let modelRange = Range(match.range(at: 1), in: text) {
            model = String(text[modelRange])
        }

        let errors = scanErrors(in: text)
        return ParsedBody(
            startedAt: startedAt,
            endedAt: endedAt,
            exitCode: exitCode,
            status: status,
            logSizeBytes: size,
            errorsDetected: errors,
            model: model
        )
    }

    /// Promote a `.running` run to `.orphaned` once it has gone longer than
    /// the per-type `orphanAfter` threshold without progress. All other
    /// statuses pass through unchanged.
    ///
    /// `watcherStall` is the answer when there is one: the awake time without
    /// progress that the run's progress watcher reports (`RunProgress`, the
    /// same line and rule the engine's lock reaper and `scoutctl health` use).
    /// The log is then ignored: on a vault linked to Agnes the lease keepalive
    /// writes it every five minutes whether or not claude does anything, so a
    /// hung claude behind a live shell stayed "running" (review of #701).
    ///
    /// Without one (a vault whose runners predate the watcher, or a watcher
    /// that died), quiet time counts from the run's last sign of life: the
    /// later of its start and `lastActivityAt` (the log's modification time),
    /// as #701 had it. Counting from the start alone marked a healthy
    /// 23-minute consolidation "Didn't finish" while it was still working.
    nonisolated static func promoteOrphan(
        parsedStatus: RunStatus,
        startedAt: Date,
        lastActivityAt: Date? = nil,
        watcherStall: TimeInterval? = nil,
        type: RunType,
        now: Date
    ) -> RunStatus {
        guard parsedStatus == .running else { return parsedStatus }
        if let watcherStall {
            return watcherStall > type.orphanAfter ? .orphaned : .running
        }
        let lastSignOfLife = max(startedAt, lastActivityAt ?? startedAt)
        return now.timeIntervalSince(lastSignOfLife) > type.orphanAfter
            ? .orphaned
            : .running
    }

    /// The log's modification time — the run's latest sign of life — or nil
    /// when the file can't be read.
    nonisolated static func logModifiedAt(_ url: URL) -> Date? {
        try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
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
    ///   (anything except `.running`), leaving out lock skips
    ///   (`.skippedConcurrency`): the runner skips on the session lock before
    ///   it holds it, so a lock skip is evidence that an earlier run is still
    ///   alive, not that it died. Every other skip happens after the runner
    ///   took the lock, so, like a finished run, it shows the earlier run no
    ///   longer holds it.
    /// - For every still-`.running` run, if a terminal run of the same type
    ///   started strictly later than this one, this run is stale → `.orphaned`.
    ///
    /// Time-based promotion is handled by `promoteOrphan` per-run; this method
    /// only handles the "I have evidence a newer same-type run finished"
    /// branch. Both rules are layered so e.g. an orphan-by-age pass can run
    /// first, then this reconciles cross-run.
    nonisolated static func resolveStaleRunning(_ runs: [Run]) -> [Run] {
        var latestTerminal: [RunType: Date] = [:]
        for r in runs where r.status != .running && r.status != .skippedConcurrency {
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

    /// Replace each run's status with its ledger row's, and give it the row's
    /// slot key and name, matched by log file name. Each run has its own log (a later
    /// run in the same minute logs to a seconds+PID name), so a row matches
    /// exactly one run.
    nonisolated static func overlayLedger(
        _ runs: [Run], statuses: [String: RunStatus], slots: [String: String] = [:],
        triggers: [String: String] = [:], failureClasses: [String: String] = [:],
        protection: [String: Run.Protection] = [:], names: [String: String] = [:],
        keys: [String: String] = [:]
    ) -> [Run] {
        guard !statuses.isEmpty || !slots.isEmpty || !triggers.isEmpty || !failureClasses.isEmpty
                || !protection.isEmpty || !names.isEmpty || !keys.isEmpty else { return runs }
        return runs.map { run in
            let name = run.logPath.lastPathComponent
            var out = run
            if let status = statuses[name], status != out.status { out = out.with(status: status) }
            if let slot = slots[name], slot != out.slotKey { out.slotKey = slot }
            if let trigger = triggers[name], trigger != out.trigger { out.trigger = trigger }
            if let fc = failureClasses[name], fc != out.failureClass { out.failureClass = fc }
            if let facts = protection[name], facts != out.protection { out.protection = facts }
            if let runName = names[name], runName != out.ledgerName { out.ledgerName = runName }
            if let key = keys[name], key != out.ledgerKey { out.ledgerKey = key }
            return out
        }
    }

    /// Adopt the run ledger's statuses and slots (from `RunLedgerService`) and republish.
    func applyLedger(_ entries: [String: RunLedgerEntry]) {
        ledgerStatuses = entries.mapValues(\.runStatus)
        ledgerSlots = entries.mapValues(\.slot)
        ledgerTriggers = entries.compactMapValues(\.trigger)
        ledgerFailureClasses = entries.compactMapValues(\.failureClass)
        ledgerNames = entries.compactMapValues { DisplayName.ledgerRun(slot: $0.slot, mode: $0.mode, runner: $0.runner, startedAt: $0.startedAt) }
        ledgerKeys = entries.compactMapValues { DisplayName.ledgerRunKey(slot: $0.slot, mode: $0.mode) }
        ledgerProtection = entries.compactMapValues { e in
            guard e.sandbox != nil || e.integrity != nil else { return nil }
            return Run.Protection(sandbox: e.sandbox, sandboxSource: e.sandboxSource, integrity: e.integrity,
                                  mode: e.mode)
        }
        let updated = Self.resolveStaleRunning(overlaid(runs))
        if updated != runs { runs = updated }
    }

    private func overlaid(_ runs: [Run]) -> [Run] {
        Self.overlayLedger(runs, statuses: ledgerStatuses, slots: ledgerSlots, triggers: ledgerTriggers,
                           failureClasses: ledgerFailureClasses, protection: ledgerProtection,
                           names: ledgerNames, keys: ledgerKeys)
    }

    nonisolated private static func scanErrors(in text: String) -> [DetectedError] {
        RunLogErrorScanner.scan(text)
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
        let progress = RunProgress.readFresh(logsDirectory: logsDirectory, now: nowSnapshot)
        let tz = timeZone
        let cacheURL = parseCacheURLOverride
            ?? Self.defaultParseCacheURL(scoutDirectory: logsDirectory.deletingLastPathComponent())
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
                // A skipped run spent nothing; a nearby cost row is another run's.
                let cost = body.status.isSkip ? nil : await tracker.cost(
                    matching: filename.type.costTrackerKey,
                    near: filename.startedAt,
                    tolerance: 120
                )
                let status = Self.promoteOrphan(
                    parsedStatus: body.status,
                    startedAt: filename.startedAt,
                    lastActivityAt: meta.modifiedAt,
                    watcherStall: progress?.stall(for: url),
                    type: filename.type,
                    now: nowSnapshot
                )
                let run = Run(
                    id: Run.makeId(type: filename.type, startedAt: filename.startedAt, pid: filename.pid),
                    type: filename.type,
                    runnerScript: filename.runnerScript,
                    source: .launchdScheduled,
                    scheduledAt: nil,
                    startedAt: filename.startedAt,
                    endedAt: body.endedAt,
                    headerStartedAt: body.startedAt,
                    status: status,
                    exitCode: body.exitCode,
                    cost: cost?.budgetSpent,
                    budgetCap: cost?.budgetCap,
                    logPath: url,
                    logSizeBytes: body.logSizeBytes,
                    errorsDetected: body.errorsDetected,
                    commits: [],
                    retryOf: nil,
                    model: body.model
                )
                out.append(run)
            }
            out.sort { $0.startedAt > $1.startedAt }
            return out
        }.value
        // Cross-run reconciliation: a `.running` entry that has a newer
        // same-type terminal sibling is by definition stale → orphan.
        runs = Self.resolveStaleRunning(overlaid(assembled))
        startWatching()
        startStaleSweep()
        return runs
    }

    /// Resolve commits for a Run on demand. Called by the detail pane when the
    /// user opens the Diff tab — keeps loadInitial() from doing O(N) git calls
    /// on the main thread at launch. The window and the subject claim are
    /// shared with the reverse `CommitRunLinker` (#43), so the subject filter
    /// runs here rather than as a git `matchingPrefix`: a `run-scout.sh` run
    /// claims briefing, weekend-briefing and consolidation commits alike.
    func commits(for run: Run) async -> [Commit] {
        guard let git = gitService else { return [] }
        let window = run.commitWindow(now: clock.now())
        let all = (try? await git.commits(
            between: window.lowerBound,
            and: window.upperBound,
            matchingPrefix: ""
        )) ?? []
        return all.filter(run.claims)
    }

    /// The commits in which the post-run integrity check put back the protected files this run
    /// changed (`scout.sandbox.integrity`: "Scout: restore protected files a <mode> run changed in
    /// git"). Only asked for a run whose ledger row says `restored`, so nothing is guessed from
    /// the log. They don't name a run family, so `commits(for:)` never claims them. The subject
    /// must name this run's own mode, so a run that started just after another run's restore
    /// never shows that one's commit; without a mode there is nothing to match, and none is shown.
    func restoreCommits(for run: Run) async -> [Commit] {
        guard let git = gitService, run.protection?.integrity == "restored",
              let prefix = Self.restoreCommitPrefix(mode: run.protection?.mode) else { return [] }
        let window = run.commitWindow(now: clock.now())
        return (try? await git.commits(between: window.lowerBound, and: window.upperBound,
                                       matchingPrefix: prefix)) ?? []
    }

    /// `scout.sandbox.integrity`'s subject for a run of `mode`, up to the mode; nil without one.
    nonisolated static func restoreCommitPrefix(mode: String?) -> String? {
        guard let mode, !mode.isEmpty else { return nil }
        return "Scout: restore protected files a \(mode) run changed in git"
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
        let logsDirectory = logsDirectory
        let now = clock.now()
        let parseTask = Task.detached(priority: .utility) { () -> (ParsedBody, TimeInterval?)? in
            guard let body = try? Self.parseBody(at: url, filename: filename) else { return nil }
            return (body, RunProgress.readFresh(logsDirectory: logsDirectory, now: now)?.stall(for: url))
        }
        guard let (body, watcherStall) = await parseTask.value else { return }
        // A skipped run spent nothing; a nearby cost row is another run's.
        let cost = body.status.isSkip ? nil : trackerService.cost(
            matching: filename.type.costTrackerKey,
            near: filename.startedAt,
            tolerance: 120
        )
        let status = Self.promoteOrphan(
            parsedStatus: body.status,
            startedAt: filename.startedAt,
            lastActivityAt: Self.logModifiedAt(url),
            watcherStall: watcherStall,
            type: filename.type,
            now: now
        )
        // Commits left empty; resolved lazily via `commits(for:)` when the
        // detail pane opens. Keeps reconciliation cheap on every FS event.
        let commits: [Commit] = []
        let newRun = Run(
            id: Run.makeId(type: filename.type, startedAt: filename.startedAt, pid: filename.pid),
            type: filename.type,
            runnerScript: filename.runnerScript,
            source: .launchdScheduled,
            scheduledAt: nil,
            startedAt: filename.startedAt,
            endedAt: body.endedAt,
            headerStartedAt: body.startedAt,
            status: status,
            exitCode: body.exitCode,
            cost: cost?.budgetSpent,
            budgetCap: cost?.budgetCap,
            logPath: url,
            logSizeBytes: body.logSizeBytes,
            errorsDetected: body.errorsDetected,
            commits: commits,
            retryOf: nil,
            model: body.model
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
        runs = Self.resolveStaleRunning(overlaid(updated))
    }

    /// Re-applies orphan/stale rules against the current snapshot. Used by
    /// the periodic sweep timer so a long-running app session still demotes
    /// `.running` → `.orphaned` as clock time crosses the per-type cutoff,
    /// even when no file event fires.
    ///
    /// It also brings back a `.orphaned` run that never wrote its last line
    /// when the progress watcher's fresh line names it and shows it working
    /// again: a run that stalled past its cutoff and then resumed writes
    /// nothing to its log (`claude -p` doesn't), so no file event would.
    /// The line is read without blocking and at most 4 KB (`RunProgress`).
    func sweepStaleStatuses() {
        let now = clock.now()
        // Read once per sweep, and only when a run could change.
        let progress = runs.contains(where: Self.mayChangeOnSweep)
            ? RunProgress.readFresh(logsDirectory: logsDirectory, now: now) : nil
        let resweeped = runs.map { run -> Run in
            // Only a running run, or an unfinished one the watcher still names, can change.
            let parsedStatus: RunStatus
            if run.status == .running {
                parsedStatus = .running
            } else if Self.mayChangeOnSweep(run), progress?.names(run.logPath) == true {
                parsedStatus = .running
            } else {
                return run
            }
            let promoted = Self.promoteOrphan(
                parsedStatus: parsedStatus,
                startedAt: run.startedAt,
                lastActivityAt: Self.logModifiedAt(run.logPath),
                watcherStall: progress?.stall(for: run.logPath),
                type: run.type,
                now: now
            )
            return promoted == run.status ? run : run.with(status: promoted)
        }
        let resolved = Self.resolveStaleRunning(overlaid(resweeped))
        // Only republish if anything actually changed — avoids needless
        // SwiftUI re-renders on every tick.
        if resolved != runs { runs = resolved }
    }

    /// A run the sweep may move: running, or orphaned without ever writing its last line.
    nonisolated static func mayChangeOnSweep(_ run: Run) -> Bool {
        run.status == .running || (run.status == .orphaned && run.endedAt == nil && run.exitCode == nil)
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
}

extension RunType {
    /// How long a run with no terminal marker may go without progress before
    /// it is promoted from `.running` to `.orphaned` (see `promoteOrphan`).
    /// The old thresholds (6h briefings, 12h dreaming) were way too loose and
    /// produced multi-hour false "running" badges in the Now strip and
    /// Sessions list. CC-1/CC-5.
    ///
    /// With the run's progress watcher (`RunProgress`) this bounds awake time
    /// with no CPU burned and no tool started or ended, so a hung claude is
    /// "Didn't finish" after it, however often the lease keepalive writes the
    /// log, and a slow run that keeps working never is. Without one it bounds
    /// silence in the log since the later of the start and its last line.
    var orphanAfter: TimeInterval {
        switch self {
        case .morningBriefing, .weekendBriefing:
            return 30 * 60           // 30 min — briefing runs short
        case .consolidation:
            return 20 * 60           // 20 min without progress
        case .dreaming:
            return 2 * 3600          // 2 h — long-form synthesis
        case .research:
            return 2 * 3600          // 2 h — unchanged, research can legitimately churn
        case .manual:
            return 45 * 60           // 45 min — catch-all, prefer to orphan fast
        }
    }
}
