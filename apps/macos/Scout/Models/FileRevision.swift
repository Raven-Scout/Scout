import Foundation

/// One commit in a single file's history, with that file's patch only
/// (rename-aware). Insertions/deletions on `commit` count this file's lines,
/// not the whole commit's. (#43)
nonisolated struct FileRevision: Identifiable, Equatable, Sendable {
    let commit: Commit
    let patch: String
    var id: String { commit.id }
}
