import Foundation

/// Read-modify-write a file with a guard against a concurrent writer clobbering
/// it in the read→write window.
///
/// The app's per-file writers (wishlist/research via `PerFileItemWriter`,
/// proposals via `ProposalsWriter`) read a markdown file, rewrite one
/// frontmatter field, and write it back. A scout-plugin session writing the
/// same file between our read and our atomic write would be silently
/// overwritten (issue #48). This guards that window: it captures the file's
/// modification date at read time and re-checks it just before writing — if a
/// concurrent writer touched the file, it re-reads and reapplies `transform`
/// onto the fresh contents instead of clobbering them.
///
/// `transform` must be deterministic (idempotent re-application onto updated
/// contents must be well-defined) and returns the new contents, or the
/// unchanged input to signal "nothing to do" (no write performed).
enum GuardedFileWrite {
    enum Failure: Error, Equatable {
        case read(String)
        case write(String)
        /// The file kept changing under us across every attempt — a persistent
        /// concurrent writer. Surfaced rather than risking a clobber.
        case conflictPersisted
    }

    /// Apply `transform` to the contents of `url`, re-reading and reapplying if
    /// the file's modification date changes between our read and write (up to
    /// `maxAttempts`). Returns true if a write happened, false for the no-op
    /// case (transform returned unchanged contents).
    @discardableResult
    static func apply(
        to url: URL,
        maxAttempts: Int = 4,
        modificationDate: @Sendable (URL) -> Date? = GuardedFileWrite.fsModificationDate,
        transform: (String) throws -> String
    ) throws -> Bool {
        for _ in 0..<maxAttempts {
            let text: String
            do { text = try String(contentsOf: url, encoding: .utf8) }
            catch { throw Failure.read(error.localizedDescription) }
            let mtimeAtRead = modificationDate(url)

            let updated = try transform(text)
            guard updated != text else { return false }

            // A concurrent writer landed between our read and now → loop and
            // reapply onto their content rather than overwriting it.
            if modificationDate(url) != mtimeAtRead { continue }

            do { try updated.write(to: url, atomically: true, encoding: .utf8) }
            catch { throw Failure.write(error.localizedDescription) }
            return true
        }
        throw Failure.conflictPersisted
    }

    /// Filesystem modification date of `url`, or nil if it can't be read.
    static let fsModificationDate: @Sendable (URL) -> Date? = { url in
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }
}
