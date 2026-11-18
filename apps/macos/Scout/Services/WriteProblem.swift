import Foundation

/// A failed write, said the way the owner needs it: what happened, what to do
/// about it, and the raw details for a bug report. Every writer's errors map
/// into this one shape (`WriteProblem.from(_:action:)`), so every page shows
/// write failures the same way.
///
/// C4 ("plain-language pass") is building the app's shared problem component;
/// this model carries the fields it renders. Until it lands, `WriteProblemBanner`
/// renders it.
struct WriteProblem: Equatable, Identifiable {
    enum Kind: Equatable, Sendable {
        /// The file changed on disk since the app loaded it.
        case stale
        /// Undo or redo was refused: the file changed after the write.
        case undoStale
        /// The task, note or slot the write addressed isn't there any more.
        case notFound
        /// The engine (scoutctl / Python) is missing or too old.
        case environment
        /// The change is on disk, but the git commit failed.
        case committedOnDiskOnly
        /// Anything else: a write, read or validation failure.
        case failed
    }

    let id = UUID()
    let kind: Kind
    /// One line, sentence case: "Couldn't mark the task done".
    let title: String
    /// What happened and what to do next, in plain words.
    let message: String
    /// Engine output or system error text for "Copy details". Never shown
    /// inline.
    let details: String?
    /// Whether trying the same write again could work.
    let retryable: Bool

    static func == (lhs: WriteProblem, rhs: WriteProblem) -> Bool {
        lhs.kind == rhs.kind && lhs.title == rhs.title && lhs.message == rhs.message
            && lhs.details == rhs.details && lhs.retryable == rhs.retryable
    }

    init(kind: Kind, title: String, message: String, details: String? = nil, retryable: Bool) {
        self.kind = kind
        self.title = title
        self.message = message
        self.details = details.flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
        self.retryable = retryable
    }

    /// Map any writer error to a problem. `action` completes "Couldn't …",
    /// e.g. "mark the task done", "save the note", "undo Mark Done".
    static func from(_ error: Error, action: String) -> WriteProblem {
        let title = "Couldn't \(action)"
        switch error {
        case let e as VaultWriteError:
            return from(e, title: title)
        case let e as ActionItemsWriterError:
            return from(e, title: title)
        case let e as KBWriterError:
            return from(e, title: title)
        case let e as StaleScheduleError:
            return WriteProblem(
                kind: .stale, title: title,
                message: "The schedule changed on disk since this page loaded it. Reload to see the current schedule, then make your change again.",
                details: e.errorDescription, retryable: false)
        case let e as ScheduleReloadAfterWriteError:
            return WriteProblem(
                kind: .failed, title: "Saved, but the page didn't update",
                message: "Your change is saved. Scout couldn't read the schedules back afterwards, so this page may be out of date. Reload to see them.",
                details: e.reason, retryable: false)
        case let e as ScheduleValidationError:
            return WriteProblem(
                kind: .failed, title: title,
                message: "Scout's check didn't accept the schedule, so nothing was saved. Check the fields and try again; the check's own words are under Details.",
                details: e.details, retryable: false)
        case let e as ScheduleNotLoadedError:
            return WriteProblem(
                kind: .stale, title: title,
                message: "The schedule didn't load, so Scout won't save over it. Reload the page, then make your change again.",
                details: e.errorDescription, retryable: false)
        case let e as GuardedFileWrite.Failure:
            switch e {
            case .read(let m):
                return WriteProblem(kind: .failed, title: title,
                                    message: "Scout couldn't read the file. Check that the vault folder is still there.",
                                    details: m, retryable: true)
            case .write(let m):
                return writeFailure(title: title, details: m)
            case .conflictPersisted:
                return WriteProblem(kind: .stale, title: title,
                                    message: "The file kept changing while Scout tried to save. A scheduled run is probably writing it; try again in a minute.",
                                    retryable: true)
            }
        case let e as ProposalsWriterError:
            switch e {
            case .frontmatterNotFound(let f), .statusFieldNotFound(let f):
                return WriteProblem(kind: .failed, title: title,
                                    message: "\(f) has no status field Scout can set. Open the file and check its frontmatter.",
                                    retryable: false)
            case .readFailed(let m):
                return WriteProblem(kind: .failed, title: title, message: "Scout couldn't read the proposal file.", details: m, retryable: true)
            case .writeFailed(let m):
                return writeFailure(title: title, details: m)
            }
        case let e as PerFileItemWriterError:
            switch e {
            case .emptyTitle:
                return WriteProblem(kind: .failed, title: title, message: "Give the item a title first.", retryable: false)
            case .frontmatterNotFound(let f):
                return WriteProblem(kind: .failed, title: title, message: "\(f) has no frontmatter Scout can update. Open the file and check it.", retryable: false)
            case .fieldNotFound(let field, let f):
                return WriteProblem(kind: .failed, title: title, message: "\(f) has no \(field) field Scout can set. Open the file and check it.", retryable: false)
            case .readFailed(let m):
                return WriteProblem(kind: .failed, title: title, message: "Scout couldn't read the file.", details: m, retryable: true)
            case .writeFailed(let m):
                return writeFailure(title: title, details: m)
            }
        default:
            return WriteProblem(kind: .failed, title: title,
                                message: "Something went wrong. Try again, and copy the details if it keeps happening.",
                                details: (error as NSError).localizedDescription, retryable: true)
        }
    }

    private static func from(_ e: VaultWriteError, title: String) -> WriteProblem {
        switch e {
        case .stale(let f):
            return WriteProblem(kind: .stale, title: title,
                                message: "\(f) changed on disk since this page loaded it. The page now shows the current version; check it and make your change again.",
                                retryable: false)
        case .undoStale(let f):
            return WriteProblem(kind: .undoStale, title: title,
                                message: "\(f) changed after your edit, probably in a scheduled run. Undoing now would overwrite that change, so Scout left the file as it is.",
                                retryable: false)
        case .readFailed(let f, let m):
            return WriteProblem(kind: .failed, title: title, message: "Scout couldn't read \(f).", details: m, retryable: true)
        case .writeFailed(_, let m):
            return writeFailure(title: title, details: m)
        }
    }

    private static func from(_ e: ActionItemsWriterError, title: String) -> WriteProblem {
        switch e {
        case .cliNonZeroExit(let code, let stderr, let cls):
            let details = "exit \(code)\n\(stderr)"
            switch cls {
            case .noMatch:
                return WriteProblem(kind: .notFound, title: title,
                                    message: "That task isn't in the file any more; a scheduled run may have moved or rewritten it. The list now shows the current file.",
                                    details: details, retryable: false)
            case .ambiguous:
                return WriteProblem(kind: .failed, title: title,
                                    message: "More than one task matches, so Scout didn't guess. Make the change in the file itself, or wait for the next run to tag the task.",
                                    details: details, retryable: false)
            case .environment:
                return WriteProblem(kind: .environment, title: title,
                                    message: "Scout's engine is missing a command the app needs. Update the engine in Settings ▸ Engine, then try again.",
                                    details: details, retryable: true)
            case .other:
                return WriteProblem(kind: .failed, title: title,
                                    message: "Scout's engine refused the change. Copy the details if it keeps happening.",
                                    details: details, retryable: true)
            }
        case .processFailed(let err):
            return WriteProblem(kind: .environment, title: title,
                                message: "Scout couldn't start its engine. Check Settings ▸ Engine.",
                                details: err.localizedDescription, retryable: true)
        case .stale(let f):
            return from(VaultWriteError.stale(file: f), title: title)
        }
    }

    private static func from(_ e: KBWriterError, title: String) -> WriteProblem {
        switch e {
        case .conflict(let f):
            return from(VaultWriteError.stale(file: f), title: title)
        case .commitFailed(let m):
            return WriteProblem(kind: .committedOnDiskOnly, title: "Saved, but not committed",
                                message: "The change is on disk, but the git commit failed (often a scheduled run holding git's lock). The next run's commit will pick it up.",
                                details: m, retryable: false)
        case .writeFailed(let m):
            return writeFailure(title: title, details: m)
        case .notFound:
            return WriteProblem(kind: .notFound, title: title, message: e.userMessage, retryable: false)
        case .alreadyExists, .emptyName, .outsideKnowledgeBase:
            return WriteProblem(kind: .failed, title: title, message: e.userMessage, retryable: false)
        }
    }

    private static func writeFailure(title: String, details: String) -> WriteProblem {
        WriteProblem(kind: .failed, title: title,
                     message: "Scout couldn't write the file. Check that the disk isn't full and the vault folder is writable, then try again.",
                     details: details, retryable: true)
    }
}
