import Foundation

/// Who made a commit in an item's history (#43). Labels come from the commit
/// subject's family, not the linked run's `RunType` (which can be
/// hour-mis-bucketed); only `.run` can jump to Control Center.
nonisolated enum ActivitySource: Equatable, Sendable {
    case run(Run, CommitFamily)   // linked to a run log
    case family(CommitFamily)     // a Scout run by its subject, no run log in the window
    case app                      // Scout.app's own writes ("app: …")
    case other                    // interactive sessions, hand commits, plugin upgrades

    var label: String {
        switch self {
        case .run(_, let f), .family(let f): return f.displayName
        case .app:   return "You"
        case .other: return "Other"
        }
    }

    var run: Run? { if case .run(let r, _) = self { return r } else { return nil } }
}

nonisolated struct ItemActivityEntry: Identifiable, Equatable, Sendable {
    let revision: FileRevision
    let source: ActivitySource
    /// The commit that added the item's current terminal `status:` line.
    let isResolving: Bool
    var id: String { revision.id }
    var commit: Commit { revision.commit }
}

nonisolated enum ItemOutcome: Equatable, Sendable {
    case resolved(ItemActivityEntry)
    /// Terminal status on disk, but no commit adds it yet (the write hasn't
    /// been committed, or was swept into a commit we can't see).
    case resolvedUncommitted

    func summary(date: (Date) -> String) -> String {
        switch self {
        case .resolvedUncommitted:
            return "Resolved — not committed yet"
        case .resolved(let e):
            switch e.source {
            case .run, .family: return "Resolved by \(e.source.label) · \(date(e.commit.timestamp))"
            case .app:          return "Resolved by you · \(date(e.commit.timestamp))"
            case .other:        return "Resolved · \(date(e.commit.timestamp))"
            }
        }
    }
}

nonisolated enum ItemActivity {

    static func source(for commit: Commit, runs: [Run], now: Date) -> ActivitySource {
        if let family = CommitFamily.of(subject: commit.subject) {
            if let run = CommitRunLinker.run(for: commit, in: runs, now: now) {
                return .run(run, family)
            }
            return .family(family)
        }
        return commit.subject.hasPrefix("app:") ? .app : .other
    }

    /// Label each revision (newest first, as `git log` returns them) and flag
    /// the newest one that added the item's current terminal status.
    static func entries(revisions: [FileRevision], runs: [Run], status: ItemStatus,
                        now: Date) -> [ItemActivityEntry] {
        let resolvingID = status.isActive ? nil
            : revisions.first(where: { addsStatus($0.patch, to: status) })?.id
        return revisions.map { rev in
            ItemActivityEntry(revision: rev,
                              source: source(for: rev.commit, runs: runs, now: now),
                              isResolving: rev.id == resolvingID)
        }
    }

    static func outcome(entries: [ItemActivityEntry], status: ItemStatus) -> ItemOutcome? {
        guard !status.isActive else { return nil }
        return entries.first(where: \.isResolving).map(ItemOutcome.resolved) ?? .resolvedUncommitted
    }

    /// True when `patch` adds a `status: <terminal value>` line (quotes
    /// tolerated). Active statuses never count as resolving.
    static func addsStatus(_ patch: String, to status: ItemStatus) -> Bool {
        guard !status.isActive else { return false }
        let wanted = status.frontmatterValue.lowercased()
        return patch.split(separator: "\n").contains { line in
            guard line.hasPrefix("+"), !line.hasPrefix("+++") else { return false }
            let body = line.dropFirst().trimmingCharacters(in: .whitespaces)
            guard body.lowercased().hasPrefix("status:") else { return false }
            let value = body.dropFirst("status:".count)
                .trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "\"'")))
            return value.lowercased() == wanted
        }
    }

    /// `fileURL` relative to `repo`, or nil when it isn't inside it (a custom
    /// folder override). Mirrors `PerFileItemWriter`'s prefix rule but refuses
    /// instead of falling back to the bare filename.
    static func repoRelativePath(of fileURL: URL, repo: URL) -> String? {
        let full = fileURL.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : nil
    }
}
