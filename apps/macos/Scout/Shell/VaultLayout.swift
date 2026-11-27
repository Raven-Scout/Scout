import Foundation

/// Where Scout keeps things in a vault (layout v2, ADR 0011). The engine owns the layout and publishes it as
/// `scoutctl vault layout --json`; until the app reads that command (the app redesign epic), these constants
/// are pinned to its contract fixture by `VaultLayoutContractTests`, so they can't drift from the engine.
/// Every path is vault-relative.
nonisolated enum VaultLayout {
    // Scout's system
    static let brainDir = "scout/brain"
    static let runnersDir = "scout/runners"
    static let scriptsDir = "scout/scripts"
    static let hooksDir = "scout/hooks"

    // Scout's records of its own runs
    static let recordsDir = "scout/records"
    static let dreamingProposalsDir = "scout/records/dreaming-proposals"
    static let wishlistDir = "scout/records/wishlist"
    static let connectorHealth = "scout/records/connector-health.md"
    static let sessionLog = "scout/records/session-log.md"
    static let mistakeAudit = "scout/records/scout-mistake-audit.md"

    // What Scout knows
    static let knowledgeBase = "knowledge-base"
    static let researchQueueDir = "knowledge-base/research-queue"
    static let meetingsRegistry = "knowledge-base/meetings.md"
    /// The space a new vault files notes in by default. The app doesn't read the registry yet, so the Projects
    /// hub link points at this space's index.
    static let defaultSpace = "work"
    static let defaultProjectsIndex = "knowledge-base/work/projects/projects.md"

    // Generated and user input
    static let actionItemsDir = "action-items"
    static let contextNotesDir = "action-items/context"
    static let reportsDir = "reports"
    static let inbox = "inbox/inbox.md"
    /// The user-input folder, derived from the inbox path: InboxReviewTests allows the bare folder word as a string
    /// literal only in InboxReviewService.
    static let inboxDir = String(inbox.split(separator: "/")[0])

    /// The layout v1 defaults of the folder settings. A stored override equal to one of these is the old
    /// default, not a choice, so it is cleared on launch (spec 2026-11-26 §7.4).
    static let v1Defaults: [String: String] = [
        "dreamingProposalsPath": "dreaming-proposals",
        "wishlistPath": "docs/wishlist",
    ]

    /// Clears each folder override that still names its v1 default (relative, or absolute under `scoutDir`).
    static func clearV1DefaultOverrides(_ defaults: UserDefaults, scoutDir: URL) {
        for (key, old) in v1Defaults {
            guard let raw = defaults.string(forKey: key)?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !raw.isEmpty else { continue }
            let expanded = (raw as NSString).expandingTildeInPath
            let oldAbsolute = scoutDir.appendingPathComponent(old).standardizedFileURL.path
            let given = URL(fileURLWithPath: expanded).standardizedFileURL.path
            if raw == old || given == oldAbsolute {
                defaults.removeObject(forKey: key)
            }
        }
    }
}
