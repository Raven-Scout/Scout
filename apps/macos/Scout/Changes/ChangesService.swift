import Combine
import Foundation

/// What the Changes tab shows: the three engine documents read together.
struct ChangesSnapshot: Equatable {
    let changes: ChangesDocument
    let engagement: EngagementDocument
    let digest: ChangesDigest
}

/// Reads the changes map through `scoutctl` (read-only): `changes --json`, `engagement --json` and
/// `changes digest --json`. It re-reads on launch, when the change or engagement logs change, when a
/// governance-pending file changes (the "needs your OK" list moves with those files), and every few minutes.
///
/// The app never writes engine state: owner decisions go through `ChangesDecisions`.
@MainActor
final class ChangesService: ObservableObject {
    enum State: Equatable {
        case idle
        case loading
        case loaded(ChangesSnapshot)
        /// The engine predates the changes map, or speaks a newer schema than this app reads.
        case unsupported
        /// A short reason. Never document content.
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var lastChecked: Date?

    /// The three reads, in the order `refresh` runs them.
    nonisolated static let changesArguments = ["changes", "--json", "--since", "365d", "--limit", "5000"]
    nonisolated static let engagementArguments = ["engagement", "--json", "--since", "365d"]
    nonisolated static let digestArguments = ["changes", "digest", "--json"]

    /// The engine's change and engagement logs, under `logsDirectory`.
    nonisolated static let watchedLogs: Set<String> = ["changes.jsonl", "engagement.jsonl"]

    private let scoutctl: @MainActor () -> URL?
    private let runner: any ProcessRunner
    private let environment: [String: String]
    private let logsDirectory: URL
    private let pendingDirectory: URL
    private let fileEvents: any FileSystemEventSource
    private let clock: any ClockSource
    private var watchTasks: [Task<Void, Never>] = []
    private var timer: Timer?
    private var generation = 0

    init(scoutctl: @escaping @MainActor () -> URL?, runner: any ProcessRunner, environment: [String: String] = [:],
         logsDirectory: URL, pendingDirectory: URL, fileEvents: any FileSystemEventSource,
         clock: any ClockSource = SystemClock()) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.environment = environment
        self.logsDirectory = logsDirectory
        self.pendingDirectory = pendingDirectory
        self.fileEvents = fileEvents
        self.clock = clock
    }

    var snapshot: ChangesSnapshot? {
        if case .loaded(let snapshot) = state { return snapshot }
        return nil
    }

    func refresh() async {
        generation += 1
        let mine = generation
        guard let exe = scoutctl() else {
            state = .failed("Scout's engine isn't set up yet.")
            return
        }
        if snapshot == nil { state = .loading }
        let runner = runner, environment = environment
        let results: [ProcessResult]
        do {
            async let changes = runner.run(executable: exe, arguments: Self.changesArguments,
                                           environment: environment, workingDirectory: nil)
            async let engagement = runner.run(executable: exe, arguments: Self.engagementArguments,
                                              environment: environment, workingDirectory: nil)
            async let digest = runner.run(executable: exe, arguments: Self.digestArguments,
                                          environment: environment, workingDirectory: nil)
            results = try await [changes, engagement, digest]
        } catch {
            guard mine == generation else { return }
            state = .failed("Couldn't run Scout's engine.")
            lastChecked = clock.now()
            return
        }
        guard mine == generation else { return }
        lastChecked = clock.now()
        state = Self.state(changes: results[0], engagement: results[1], digest: results[2])
    }

    /// Pure: what the three results mean together. Any `unsupported` wins over a failure, because
    /// the fix (update the engine or the app) is the same whichever document said so.
    nonisolated static func state(changes: ProcessResult, engagement: ProcessResult, digest: ProcessResult) -> State {
        let changesParsed = ChangesDocument.parse(changes.stdout)
        let engagementParsed = EngagementDocument.parse(engagement.stdout)
        let digestParsed = ChangesDigest.parse(digest.stdout)
        let all: [(String, ProcessResult, ChangesParseError?)] = [
            ("changes", changes, changesParsed.failureValue),
            ("engagement", engagement, engagementParsed.failureValue),
            ("digest", digest, digestParsed.failureValue),
        ]
        for (_, result, failure) in all {
            if failure != nil, lacksCommand(result) { return .unsupported }
            if case .unsupportedSchema? = failure { return .unsupported }
        }
        for (name, _, failure) in all where failure != nil {
            return .failed("The engine's \(name) report couldn't be read.")
        }
        guard case .success(let c) = changesParsed, case .success(let e) = engagementParsed,
              case .success(let d) = digestParsed else { return .failed("The engine's reports couldn't be read.") }
        return .loaded(ChangesSnapshot(changes: c, engagement: e, digest: d))
    }

    /// Click's usage error for a subcommand this engine doesn't have.
    nonisolated static func lacksCommand(_ result: ProcessResult) -> Bool {
        let err = String(decoding: result.stderr, as: UTF8.self).lowercased()
        return result.exitCode == 2 && err.contains("no such command")
    }

    /// Watch the change and engagement logs and the governance-pending files, and re-read on a timer. Idempotent.
    func start(interval: TimeInterval = 300) {
        if watchTasks.isEmpty {
            let logs = DebouncedFileEvents(base: fileEvents, interval: .milliseconds(500)).events(for: logsDirectory)
            let pending = DebouncedFileEvents(base: fileEvents, interval: .milliseconds(500)).events(for: pendingDirectory)
            watchTasks = [
                Task { [weak self] in
                    for await event in logs where Self.watchedLogs.contains(event.url.lastPathComponent) {
                        await self?.refresh()
                    }
                },
                Task { [weak self] in
                    for await _ in pending { await self?.refresh() }
                },
            ]
        }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    isolated deinit {
        watchTasks.forEach { $0.cancel() }
        timer?.invalidate()
    }
}

private extension Result {
    var failureValue: Failure? {
        if case .failure(let error) = self { return error }
        return nil
    }
}
