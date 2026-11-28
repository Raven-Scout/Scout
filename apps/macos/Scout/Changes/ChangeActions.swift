import Foundation

/// When a change in the list may be undone or marked wrong. Pure: the buttons read these.
nonisolated enum ChangeActions {
    /// Undo needs a confirmed history (`verified`), a change that is not governance, one that was
    /// tested, and one that no rollback has already reverted. The snapshot can't show an undo that
    /// is still pending, so the button also disables itself after a `.done` until the next refresh
    /// shows the row rolled back.
    static func canUndo(_ row: ChangeRow, verified: Bool) -> Bool {
        verified
            && !row.governance
            && row.test.state != "not-tested"
            && row.test.rolledBackBy == nil
    }

    /// Marking wrong needs a verdict to disagree with, and only once.
    static func canMarkWrong(_ row: ChangeRow) -> Bool {
        row.test.verdict != nil && !row.test.markedWrong
    }
}
