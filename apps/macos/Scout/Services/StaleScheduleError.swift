import Foundation

/// Thrown by `ScheduleEditService.save` when the canonical schedule.yaml's
/// mtime advanced since the most recent reload — i.e. someone (Vim,
/// scoutctl, etc.) edited it concurrently. The UI catches this, surfaces a
/// banner, and prompts the user to reload before saving again.
struct StaleScheduleError: Error, LocalizedError {
    let loadedAt: Date
    let modifiedAt: Date

    var errorDescription: String? {
        "The schedule changed outside the app at \(modifiedAt.formatted(date: .omitted, time: .shortened)). Reload to see the changes before saving."
    }
}
