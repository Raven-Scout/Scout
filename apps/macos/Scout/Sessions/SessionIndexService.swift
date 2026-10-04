import Foundation
import Combine

/// Keeps the Sessions page's copy of `sessions-index.json` current by running
/// `scoutctl session index` (spec §6.5, addendum §10). The engine does all the
/// parsing and derivation; this service decides *when* to ask, decodes the
/// answer off the main actor, and publishes it only when it changed.
@MainActor
final class SessionIndexService: ObservableObject {

    enum Availability: Equatable {
        case ok
        /// `scoutctl` could not be run at all.
        case engineMissing
        /// `scoutctl` has no `session index` (scout-plugin older than 0.11.0).
        case engineTooOld
        /// The index declares a schema this build cannot read.
        case unsupportedSchema(Int)
    }

    /// The outcome of the last PR build, for the header's PR line.
    struct PRStatus: Equatable {
        let finishedAt: Date
        let refreshed: Int
        let errors: [SessionSourceError]
    }

    struct Configuration {
        var scoutctl: URL
        var argumentsPrefix: [String]
        var runner: any ProcessRunner
        var fileEvents: any FileSystemEventSource
        /// `<vault>/.scout-cache/sessions-index.json`
        var indexFile: URL
        var watchRoots: [URL]
        var intervals: SessionsRefresh.Intervals = .production
        var clock: any ClockSource = SystemClock()
        /// What `scoutctl` runs with; puts the user's tool directories (and
        /// so `gh`) on PATH even when Scout was launched from the Dock.
        var environment: [String: String] = SessionsRefresh.engineEnvironment()

        var cacheDirectory: URL { indexFile.deletingLastPathComponent() }
    }

    /// Republished only when the content (everything but `generated_at`) changes.
    @Published private(set) var index: SessionIndex?
    @Published private(set) var availability: Availability = .ok
    @Published private(set) var lastError: String?
    @Published private(set) var prStatus: PRStatus?
    /// The sidebar badge. Separate from `index` so AppState can forward it
    /// without re-rendering the whole window on every refresh.
    @Published private(set) var needsYouCount = 0
    /// When the last fast build finished. Not `@Published`: it changes every
    /// build; the header reads it from a `TimelineView`.
    private(set) var lastRefreshAt: Date?

    private let config: Configuration
    private var started = false
    private var isVisible = false
    private var fastTask: Task<Void, Never>?
    private var fastPending = false
    private var prTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?
    private var prLoopTask: Task<Void, Never>?
    private var watchTasks: [Task<Void, Never>] = []

    init(configuration: Configuration) {
        self.config = configuration
    }

    // MARK: Lifecycle

    /// App launch: show the last index on disk, then keep it current at the
    /// hidden cadence (for the badge) and refresh PR state in the background.
    func start() {
        guard !started else { return }
        started = true
        Task { await loadIndexFile() }
        requestFast()
        restartHeartbeat()
        prLoopTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.requestPRs()
                try? await Task.sleep(for: self.config.intervals.prInterval)
            }
        }
    }

    /// The page appeared or disappeared. Visible: watch the sources, refresh
    /// now, heartbeat every 30 s. Hidden: no watches, heartbeat every 5 min.
    func setVisible(_ visible: Bool) {
        guard visible != isVisible else { return }
        isVisible = visible
        if visible {
            subscribeToWatchRoots()
            requestFast()
            requestPRs()
        } else {
            watchTasks.forEach { $0.cancel() }
            watchTasks = []
        }
        restartHeartbeat()
    }

    func stop() {
        started = false
        isVisible = false
        heartbeatTask?.cancel()
        prLoopTask?.cancel()
        watchTasks.forEach { $0.cancel() }
        heartbeatTask = nil
        prLoopTask = nil
        watchTasks = []
    }

    // MARK: Lanes

    /// Ask for a fast build. While one runs, further requests collapse into a
    /// single follow-up build.
    func requestFast() {
        if fastTask != nil {
            fastPending = true
            return
        }
        fastTask = Task { [weak self] in
            guard let self else { return }
            repeat {
                self.fastPending = false
                await self.runFastBuild()
            } while self.fastPending
            self.fastTask = nil
        }
    }

    /// Request a fast build and wait until the build that includes it finishes.
    func refreshFast() async {
        requestFast()
        await fastTask?.value
    }

    /// Ask for a PR build unless one is running. It never waits on, or blocks,
    /// the fast lane; when it ends, a fast build picks up the new PR cache.
    func requestPRs() {
        guard prTask == nil, availability != .engineTooOld, availability != .engineMissing else { return }
        prTask = Task { [weak self] in
            guard let self else { return }
            await self.runPRBuild()
            self.prTask = nil
            self.requestFast()
        }
    }

    /// Request a PR build and wait for it (not for the fast build it triggers).
    func refreshPRs() async {
        requestPRs()
        await prTask?.value
    }

    // MARK: Builds

    private func runFastBuild() async {
        let result: ProcessResult
        do {
            result = try await config.runner.run(
                executable: config.scoutctl,
                arguments: SessionsRefresh.fastArguments(prefix: config.argumentsPrefix),
                environment: config.environment,
                workingDirectory: nil
            )
        } catch {
            availability = .engineMissing
            lastError = Self.describeLaunchFailure(error)
            return
        }
        if let problem = Self.classifyExit(result) {
            switch problem {
            case .engineTooOld:  availability = .engineTooOld
            case .engineMissing: availability = .engineMissing
            default:             break
            }
            lastError = Self.describeExit(result)
            await loadIndexFile()  // the last index on disk still renders
            return
        }
        switch await Self.decodeDetached(result.stdout) {
        case .success(let decoded):
            availability = .ok
            lastError = nil
            lastRefreshAt = config.clock.now()
            publish(decoded)
        case .failure(.unsupportedSchema(let version)):
            availability = .unsupportedSchema(version)  // keep the last good index
        case .failure(let error):
            lastError = Self.describeDecodeFailure(error, stdout: result.stdout, stderr: result.stderr)
        }
    }

    private func runPRBuild() async {
        let result: ProcessResult
        do {
            result = try await config.runner.run(
                executable: config.scoutctl,
                arguments: SessionsRefresh.prArguments(prefix: config.argumentsPrefix),
                environment: config.environment,
                workingDirectory: nil
            )
        } catch {
            prStatus = PRStatus(finishedAt: config.clock.now(), refreshed: 0,
                                errors: [SessionSourceError(source: "gh", message: Self.describeLaunchFailure(error))])
            return
        }
        guard result.exitCode == 0, case .success(let decoded) = await Self.decodeDetached(result.stdout) else {
            prStatus = PRStatus(finishedAt: config.clock.now(), refreshed: 0,
                                errors: [SessionSourceError(source: "gh", message: Self.describeExit(result))])
            return
        }
        // Deliberately not published: see SessionsRefresh.
        prStatus = PRStatus(
            finishedAt: config.clock.now(),
            refreshed: decoded.sourceCounts["prs_refreshed"] ?? 0,
            errors: decoded.sourceErrors.filter { $0.source == "gh" }
        )
    }

    private func publish(_ decoded: SessionIndex) {
        let count = SessionsLayout.needsYouCount(in: decoded)
        if count != needsYouCount { needsYouCount = count }
        if let current = index, current.hasSameContent(as: decoded) { return }
        index = decoded
    }

    /// Show what is on disk while nothing else is shown — at launch, or when
    /// the first build fails. Never over a newer build's index: the file may
    /// be a PR build's older snapshot.
    private func loadIndexFile() async {
        let url = config.indexFile
        guard let data = await Task.detached(priority: .utility, operation: { try? Data(contentsOf: url) }).value,
              case .success(let decoded) = await Self.decodeDetached(data),
              index == nil else { return }
        publish(decoded)
    }

    nonisolated private static func decodeDetached(_ data: Data) async -> Result<SessionIndex, SessionIndexError> {
        await Task.detached(priority: .utility) {
            do {
                return .success(try SessionIndex.decode(data))
            } catch let error as SessionIndexError {
                return .failure(error)
            } catch {
                return .failure(.malformed(String(describing: error)))
            }
        }.value
    }

    // MARK: Scheduling

    private func restartHeartbeat() {
        heartbeatTask?.cancel()
        guard started else { return }
        let interval = isVisible ? config.intervals.visibleHeartbeat : config.intervals.hiddenHeartbeat
        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: interval)
                guard !Task.isCancelled, let self else { return }
                self.requestFast()
            }
        }
    }

    private func subscribeToWatchRoots() {
        let source = DebouncedFileEvents(base: config.fileEvents, interval: config.intervals.eventWindow)
        let cacheDirectory = config.cacheDirectory
        for root in config.watchRoots where FileManager.default.fileExists(atPath: root.path) {
            let stream = source.events(for: root)
            watchTasks.append(Task { [weak self] in
                for await event in stream {
                    if SessionsRefresh.isEngineOwnWrite(event.url, cacheDirectory: cacheDirectory) { continue }
                    self?.requestFast()
                }
            })
        }
    }

    // MARK: Messages

    nonisolated enum ExitProblem: Equatable, Sendable { case engineTooOld, engineMissing, failed }

    nonisolated static func classifyExit(_ result: ProcessResult) -> ExitProblem? {
        guard result.exitCode != 0 else { return nil }
        let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
        if result.exitCode == 2 && stderr.contains("No such command") { return .engineTooOld }
        if result.exitCode == 127 { return .engineMissing }  // `/usr/bin/env scoutctl` with no scoutctl on PATH
        return .failed
    }

    static func describeExit(_ result: ProcessResult) -> String {
        switch classifyExit(result) {
        case .engineTooOld:
            return "This scout-plugin has no `session index` — update it to 0.11.0 or later (`/scout-update`)."
        case .engineMissing:
            return "scoutctl not found — check that scout-plugin is installed."
        default:
            let detail = ScheduleService.previewBytes(result.stderr, max: 200)
            return "`scoutctl session index` exited \(result.exitCode)" + (detail.isEmpty ? "." : ": \(detail)")
        }
    }

    static func describeLaunchFailure(_ error: Error) -> String {
        let text = String(describing: error)
        if text.contains("ENOENT") || text.contains("No such file") || text.contains("doesn’t exist") {
            return "scoutctl not found — check that scout-plugin is installed."
        }
        return "Couldn't run scoutctl: \(text.prefix(160))"
    }

    static func describeDecodeFailure(_ error: SessionIndexError, stdout: Data, stderr: Data) -> String {
        if case .malformed(let detail) = error, !stdout.isEmpty, stdout.first == UInt8(ascii: "{") {
            return "The session index didn't match this app's schema: \(detail.prefix(160))"
        }
        return ScheduleService.formatDecodeFailure(stdout: stdout, stderr: stderr)
    }
}
