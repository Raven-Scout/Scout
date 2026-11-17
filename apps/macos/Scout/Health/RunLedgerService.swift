import Foundation
import Combine

/// Reads the run ledger through `scoutctl runs --json` (the contract in
/// `RunLedger`), on launch, whenever `.scout-logs/run-outcomes.jsonl` changes,
/// and every few minutes (findings such as "hasn't run since Tuesday" age with
/// the clock, not with the file). Drives the Health page and the Control
/// Center's run statuses.
@MainActor
final class RunLedgerService: ObservableObject {
    enum State: Equatable {
        case loading
        case loaded(RunLedger)
        /// The engine predates `scoutctl runs` (or a newer one speaks a newer schema).
        case unsupported(String)
        case failed(String)
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var lastChecked: Date?
    /// When each run-health finding id was first seen, among the findings in
    /// the latest read. The Control Center banner holds wake-sensitive
    /// findings until they have persisted (`RunOutcomeDisplay.bannerGrace`).
    /// A finding that clears is forgotten; a read that fails keeps the record.
    @Published private(set) var firstSeen: [String: Date] = [:]

    /// How far back the panel looks.
    static let window = "14d"

    private let scoutctl: URL
    private let argumentsPrefix: [String]
    private let runner: any ProcessRunner
    private let environment: [String: String]
    private let logsDirectory: URL
    private let fileEvents: any FileSystemEventSource
    private let clock: any ClockSource
    private var watchTask: Task<Void, Never>?
    private var timer: Timer?
    private var generation = 0

    init(scoutctl: URL, argumentsPrefix: [String] = [], runner: any ProcessRunner,
         environment: [String: String] = [:], logsDirectory: URL,
         fileEvents: any FileSystemEventSource, clock: any ClockSource = SystemClock()) {
        self.scoutctl = scoutctl
        self.argumentsPrefix = argumentsPrefix
        self.runner = runner
        self.environment = environment
        self.logsDirectory = logsDirectory
        self.fileEvents = fileEvents
        self.clock = clock
    }

    var ledger: RunLedger? {
        if case .loaded(let ledger) = state { return ledger }
        return nil
    }

    /// The run-health block, when the ledger carried one.
    var health: RunHealth? { ledger?.health }

    /// Red run health — the Health row's attention dot.
    var needsAttention: Bool { health?.severity == .red }

    func refresh() async {
        generation += 1
        let mine = generation
        let args = argumentsPrefix + ["runs", "--json", "--since", Self.window]
        let result: ProcessResult
        do {
            result = try await runner.run(executable: scoutctl, arguments: args,
                                          environment: environment, workingDirectory: nil)
        } catch {
            guard mine == generation else { return }
            state = .failed("Couldn't run Scout's engine: \(String(describing: error).prefix(160))")
            lastChecked = clock.now()
            return
        }
        guard mine == generation else { return }
        let now = clock.now()
        lastChecked = now
        let next = Self.state(for: result)
        if case .loaded(let ledger) = next {
            firstSeen = Self.firstSeen(firstSeen, health: ledger.health, now: now)
        }
        state = next
    }

    /// Pure: `old` carried forward to one read's findings. Ids still present
    /// keep their first-seen time, new ids get `now`, cleared ids go.
    nonisolated static func firstSeen(_ old: [String: Date], health: RunHealth?, now: Date) -> [String: Date] {
        var out: [String: Date] = [:]
        for finding in health?.findings ?? [] { out[finding.id] = old[finding.id] ?? now }
        return out
    }

    /// Pure: what one `scoutctl runs --json` result means for the panel.
    nonisolated static func state(for result: ProcessResult) -> State {
        switch RunLedger.parse(result.stdout) {
        case .success(let ledger):
            return .loaded(ledger)
        case .failure(.unsupportedSchema(let version)):
            return .unsupported("This engine reports runs in a newer format (version \(version)). Update the Scout app.")
        case .failure(.engine(let message)):
            return .failed(message)
        case .failure(.notALedger):
            let err = String(decoding: result.stderr, as: UTF8.self)
            // Click's usage error for a subcommand this engine doesn't have.
            if err.contains("No such command") || err.contains("no such command") {
                return .unsupported("This engine doesn't report its run history yet. Update the engine in Settings ▸ Engine.")
            }
            let detail = err.trimmingCharacters(in: .whitespacesAndNewlines)
            return .failed(detail.isEmpty ? "The engine's run report couldn't be read." : String(detail.prefix(200)))
        }
    }

    /// Watch the ledger file and re-read on a timer. Idempotent.
    func start(interval: TimeInterval = 300) {
        if watchTask == nil {
            let events = DebouncedFileEvents(base: fileEvents, interval: .milliseconds(500)).events(for: logsDirectory)
            watchTask = Task { [weak self] in
                for await event in events where event.url.lastPathComponent == "run-outcomes.jsonl" {
                    await self?.refresh()
                }
            }
        }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    isolated deinit {
        watchTask?.cancel()
        timer?.invalidate()
    }
}
