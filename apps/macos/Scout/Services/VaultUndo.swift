import Combine
import Foundation
import os

/// Which way a `VaultChange` is being re-applied.
nonisolated enum UndoDirection: Sendable {
    case undo, redo

    var verb: String { self == .undo ? "undo" : "redo" }
}

/// One reversible action, as the two async operations that reverse and
/// re-apply it. Most steps restore file bytes (`restoring(_:via:)`); a step
/// whose engine has a real inverse (mark done ↔ reopen) runs that instead,
/// which stays correct even after a scheduled run edited the same file.
struct UndoStep {
    /// Shown as "Undo <actionName>" in the Edit menu and the toast.
    let actionName: String
    let undo: @MainActor () async throws -> Void
    let redo: @MainActor () async throws -> Void

    /// Restore the change's file bytes through `apply`, normally the owning
    /// writer's `restore(_:_:)`, which runs it in that writer's serial queue
    /// and commits the files.
    static func restoring(
        _ change: VaultChange,
        via apply: @escaping @MainActor (VaultChange, UndoDirection) async throws -> Void
    ) -> UndoStep {
        UndoStep(
            actionName: change.actionName,
            undo: { try await apply(change, .undo) },
            redo: { try await apply(change, .redo) }
        )
    }
}

nonisolated extension VaultChange {
    /// Apply `direction`, then commit the touched files (best effort: a failed
    /// commit leaves the restore on disk, and the next run's commit picks it
    /// up). Shared by every writer's `restore`.
    func apply(_ direction: UndoDirection, git: (any GitServiceProtocol)?, repo: URL) async throws {
        switch direction {
        case .undo: try undo()
        case .redo: try redo()
        }
        let paths = urls.compactMap { BoardWriter.relativePath(of: $0, in: repo) }
        guard !paths.isEmpty else { return }
        try? await git?.commitPaths(paths, message: "app: \(direction.verb) \(actionName.lowercased())")
    }
}

/// Registers vault writes with the window's `UndoManager`, so Edit ▸ Undo
/// (⌘Z) and Redo (⇧⌘Z) reverse them, and runs the async undo when asked.
///
/// `UndoManager` is synchronous: the handler must register its inverse before
/// it returns, or redo lands on the wrong stack. So the inverse is registered
/// first, optimistically, and if the async reversal then fails, every entry for
/// that action is removed (one token object per action) and the problem is
/// published for the page to show.
@MainActor
final class VaultUndoController: ObservableObject {
    /// The latest undo/redo failure, for the page to show. Pages clear it.
    @Published var problem: WriteProblem?
    /// The action the page's "Undo" toast offers, until it's used or replaced.
    @Published private(set) var latest: Registered?

    struct Registered: Equatable {
        let actionName: String
        fileprivate let token: Token
        static func == (a: Registered, b: Registered) -> Bool { a.token === b.token }
    }

    /// The `UndoManager` target for one action. It carries the action, so
    /// the manager's own entries keep it alive; `live` holds it too while the
    /// toast can still reach it. A failure removes every entry for just this
    /// token.
    final class Token {
        let step: UndoStep
        let refresh: @MainActor () async -> Void
        /// False once an undo or redo failed: the stacks no longer describe
        /// the file.
        var valid = true
        init(step: UndoStep, refresh: @escaping @MainActor () async -> Void) {
            self.step = step
            self.refresh = refresh
        }
    }

    private static let log = Logger(subsystem: "com.scout.Scout", category: "VaultUndo")
    private weak var latestRegisteredToken: Token?
    /// Recent tokens, held strongly so their actions stay undoable whether or
    /// not the undo manager retains its targets. Bounded: each token carries
    /// file bytes.
    private var live: [Token] = []
    static let liveLimit = 50

    /// Register a completed write. `refresh` reloads the page from disk after
    /// an undo or redo, whether it worked or not.
    func register(
        _ step: UndoStep,
        with manager: UndoManager?,
        refresh: @escaping @MainActor () async -> Void
    ) {
        let token = Token(step: step, refresh: refresh)
        latest = Registered(actionName: step.actionName, token: token)
        latestRegisteredToken = token
        live.append(token)
        if live.count > Self.liveLimit { live.removeFirst(live.count - Self.liveLimit) }
        if let manager { registerUndo(token, manager) }
    }

    /// Undo the action the toast offers. Goes through the undo manager when
    /// that action is still the newest entry on its stack, so Redo works
    /// afterwards; otherwise (something else was done since) it reverses just
    /// that action directly and drops its menu entries.
    func undoLatest(with manager: UndoManager?) {
        guard let latest else { return }
        let token = latest.token
        self.latest = nil
        if let manager, manager.canUndo, manager.undoActionName == token.step.actionName,
           latestRegisteredToken === token {
            manager.undo()
        } else {
            manager?.removeAllActions(withTarget: token)
            Task { await run(token, .undo, manager: nil) }
        }
    }

    func dismissLatest() { latest = nil }

    // MARK: - Registration

    private func registerUndo(_ token: Token, _ manager: UndoManager) {
        manager.registerUndo(withTarget: token) { [weak self, weak manager] token in
            MainActor.assumeIsolated {
                guard let self, let manager else { return }
                self.registerRedo(token, manager)
                Task { await self.run(token, .undo, manager: manager) }
            }
        }
        manager.setActionName(token.step.actionName)
    }

    private func registerRedo(_ token: Token, _ manager: UndoManager) {
        manager.registerUndo(withTarget: token) { [weak self, weak manager] token in
            MainActor.assumeIsolated {
                guard let self, let manager else { return }
                self.registerUndo(token, manager)
                Task { await self.run(token, .redo, manager: manager) }
            }
        }
        manager.setActionName(token.step.actionName)
    }

    // MARK: - Running

    /// Run one direction of `token`'s action. Exposed to tests through
    /// `perform(_:_:)`.
    private func run(_ token: Token, _ direction: UndoDirection, manager: UndoManager?) async {
        guard token.valid else { return }
        if latest?.token === token { latest = nil }
        let step = token.step
        do {
            switch direction {
            case .undo: try await step.undo()
            case .redo: try await step.redo()
            }
        } catch {
            Self.log.debug("\(direction.verb, privacy: .public) \(step.actionName, privacy: .public) failed: \(error.localizedDescription, privacy: .public)")
            token.valid = false
            manager?.removeAllActions(withTarget: token)
            problem = WriteProblem.from(error, action: "\(direction.verb) \(step.actionName.lowercased())")
        }
        await token.refresh()
    }
}
