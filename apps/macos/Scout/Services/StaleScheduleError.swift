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
