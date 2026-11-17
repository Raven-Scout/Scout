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
            return "schedule.yaml was removed after it was loaded. Reload to bring in changes."
        }
        return "schedule.yaml was modified externally at \(modifiedAt). Reload to bring in changes."
    }
}
