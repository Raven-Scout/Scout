import Foundation

/// Where run notifications go: `NotificationService` in the app, a fake in tests.
protocol RunNotificationPosting: AnyObject {
    func post(run: Run, quietly: Bool)
    /// Whether this run's notification is still in Notification Center.
    func isDelivered(runID: Run.ID) async -> Bool
    /// Removes this run's notification, delivered or still pending.
    func withdraw(runID: Run.ID)
    func postRunAgainRefused(slotKey: String, error: String)
}

extension NotificationService: RunNotificationPosting {}

/// Turns each new list of runs into run notifications under
/// `RunNotificationPolicy`: posts one when a run reaches an outcome worth
/// telling, quietly replaces it when the ledger later says more, and takes it
/// back when the ledger says the run didn't fail after all.
final class RunNotificationTracker {
    private let poster: any RunNotificationPosting
    /// How long a quiet replacement waits before looking a second time for
    /// the notification it replaces (`replace(_:)`), and a withdrawal before
    /// removing it a second time (`withdraw(_:)`).
    private let recheckDelay: Duration

    /// What each run's notification says now, so a later change can replace it.
    private(set) var shown: [Run.ID: RunNotificationPolicy.Shown] = [:]
    private var previousStatus: [Run.ID: RunStatus] = [:]
    /// Runs the app saw start, or first saw after launch (`shouldNotify`'s `isLive`).
    private var live: Set<Run.ID> = []
    /// The run a quiet replacement in flight will post: the latest wanted, so
    /// one that changes again while the check is out posts its newest words.
    private var pending: [Run.ID: Run] = [:]
    /// Replacements and second removals still running (`waitForBackgroundWork`).
    private var background: [UUID: Task<Void, Never>] = [:]

    init(poster: any RunNotificationPosting, recheckDelay: Duration = .seconds(1)) {
        self.poster = poster
        self.recheckDelay = recheckDelay
    }

    /// `armed` is false until the launch-time log load is done, so runs
    /// already on disk never notify.
    func update(runs: [Run], armed: Bool, preferences: RunNotificationPolicy.Preferences) {
        for run in runs {
            let previous = previousStatus[run.id]
            if armed, previous == nil || previous == .running { live.insert(run.id) }
            handle(run, previous: previous, armed: armed, preferences: preferences)
            previousStatus[run.id] = run.status
        }
        let ids = Set(runs.map(\.id))
        shown = shown.filter { ids.contains($0.key) }
    }

    private func handle(_ run: Run, previous: RunStatus?, armed: Bool,
                        preferences: RunNotificationPolicy.Preferences) {
        let current = shown[run.id]
        if RunNotificationPolicy.shouldNotify(previous: previous, run: run, armed: armed, preferences: preferences,
                                              shown: current, isLive: live.contains(run.id)) {
            poster.post(run: run, quietly: false)
            shown[run.id] = RunNotificationPolicy.Shown(run: run)
        } else if RunNotificationPolicy.shouldWithdraw(shown: current, run: run, preferences: preferences) {
            shown[run.id] = nil
            pending[run.id] = nil
            let id = run.id
            poster.withdraw(runID: id)
            spawn { await self.withdrawAgain(id) }
        } else if RunNotificationPolicy.shouldUpdate(shown: current, run: run, preferences: preferences) {
            let inFlight = pending[run.id] != nil
            pending[run.id] = run
            if !inFlight {
                let id = run.id
                spawn { await self.replace(id) }
            }
        } else {
            // Nothing to replace any more (it changed back, or the owner
            // turned that kind off): a replacement in flight posts nothing.
            pending[run.id] = nil
        }
    }

    /// Posts `pending[id]` quietly over the notification already shown, and
    /// only then records it as shown. The first notification and the
    /// ledger's update often land milliseconds apart, before Notification
    /// Center lists the first, so a miss looks once more after
    /// `recheckDelay`; still missing then means the owner dismissed it, and
    /// it stays dismissed.
    private func replace(_ id: Run.ID) async {
        for attempt in 0..<2 {
            if attempt > 0 { try? await Task.sleep(for: recheckDelay) }
            let delivered = await poster.isDelivered(runID: id)
            guard let run = pending[id] else { return }
            if delivered {
                poster.post(run: run, quietly: true)
                shown[id] = RunNotificationPolicy.Shown(run: run)
                pending[id] = nil
                return
            }
        }
        // Dismissed: remember the latest words so later emissions don't look again.
        if let run = pending[id] { shown[id] = RunNotificationPolicy.Shown(run: run) }
        pending[id] = nil
    }

    /// The second half of a withdrawal. A notification posted milliseconds
    /// before the ledger took it back may not be listed (or even pending)
    /// yet, so the first removal finds nothing; this one, `recheckDelay`
    /// later, catches it. Skipped if the run has been notified again since.
    private func withdrawAgain(_ id: Run.ID) async {
        try? await Task.sleep(for: recheckDelay)
        guard shown[id] == nil else { return }
        poster.withdraw(runID: id)
    }

    private func spawn(_ work: @escaping @MainActor () async -> Void) {
        let key = UUID()
        background[key] = Task {
            await work()
            self.background[key] = nil
        }
    }

    /// Waits for every replacement and second removal in flight. For tests.
    func waitForBackgroundWork() async {
        while let task = background.values.first { await task.value }
    }
}
