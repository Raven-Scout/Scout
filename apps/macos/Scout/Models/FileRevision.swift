import Foundation

/// One commit in a single file's history, with that file's patch only
/// (rename-aware). Insertions/deletions on `commit` count this file's lines,
/// not the whole commit's. (#43)
nonisolated struct FileRevision: Identifiable, Equatable, Sendable {
    let commit: Commit
    let patch: String
    var id: String { commit.id }
    /// True when this commit created the file (a run filing an item, or a
    /// bulk import) — its date is when the file appeared, not when the work
    /// was resolved.
    var createsFile: Bool {
        patch.split(separator: "\n").contains { $0.hasPrefix("new file mode") || $0 == "--- /dev/null" }
    }
}
