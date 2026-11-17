import Foundation

/// Thrown by `ScheduleEditService.save` when the canonical schedule.yaml's
/// mtime differs from the one captured at the most recent load — i.e.
/// someone (Vim, scoutctl, etc.) edited, created or removed it concurrently.
/// The UI catches this, surfaces a banner, and prompts the user to reload
/// before saving again.
///
/// Either date is nil when the file did not exist at that moment.
struct StaleScheduleError: Error, LocalizedError {
    let loadedAt: Date?
    let modifiedAt: Date?

    var errorDescription: String? {
        guard let modifiedAt else {
            return "The schedule was removed outside the app. Reload to see the changes before saving."
        }
        return "The schedule changed outside the app at \(modifiedAt.formatted(date: .omitted, time: .shortened)). Reload to see the changes before saving."
    }
}

/// Thrown by `ScheduleEditService` when a write landed in schedule.yaml but
/// the reload after it failed. Unlike every other save error, the change
/// IS on disk: callers must treat the write as done (register its undo,
/// don't restore what it replaced, don't offer to write it again).
nonisolated struct ScheduleReloadAfterWriteError: Error, LocalizedError {
    /// The write that landed, for undo (nil when it changed no bytes).
    let change: VaultChange?
    /// Why the reload failed.
    let reason: String

    var errorDescription: String? {
        "Your change is saved, but Scout couldn't read the schedules back afterwards: \(reason)"
    }
}
