import Foundation

/// Counts a successful app edit as engagement: after the wrapped git service commits, `record` runs.
///
/// Every app write that reaches the vault commits through a `GitServiceProtocol`, so wrapping the one the
/// writers share counts each successful edit without touching the writers. A commit that throws records
/// nothing, and `record` cannot fail the write: it returns nothing and runs only after the commit succeeded.
nonisolated final class EngagementRecordingGit: GitServiceProtocol {
    private let base: any GitServiceProtocol
    private let record: @Sendable () async -> Void

    init(base: any GitServiceProtocol, record: @escaping @Sendable () async -> Void) {
        self.base = base
        self.record = record
    }

    func commitPaths(_ relPaths: [String], message: String) async throws {
        try await base.commitPaths(relPaths, message: message)
        await record()
    }
}

/// Late binding for `EngagementRecordingGit`'s `record`: the writers are built before `ChangesDecisions`
/// exists, so they get this and `AppState` binds the decisions object once it has made it. Held weakly (the
/// app owns `ChangesDecisions`); until bound, it records nothing. A refused or failing `.appEdit` is
/// ignored: counting an edit never decides whether the edit worked.
@MainActor
final class EngagementRecorder {
    private weak var decisions: ChangesDecisions?

    func bind(_ decisions: ChangesDecisions) { self.decisions = decisions }

    func record() async {
        guard let decisions else { return }
        _ = await decisions.perform(.appEdit)
    }
}
