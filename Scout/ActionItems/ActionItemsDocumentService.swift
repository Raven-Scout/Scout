import Combine
import CryptoKit
import Foundation
import SwiftUI

@MainActor
final class ActionItemsDocumentService: ObservableObject {
    enum State: Equatable {
        case idle
        case loading(Date)
        case loaded(ActionItemsDocument)
        case missing(date: Date, expectedURL: URL)
        case failed(Error)

        static func == (lhs: State, rhs: State) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle): return true
            case (.loading(let a), .loading(let b)): return a == b
            case (.loaded(let a), .loaded(let b)): return a == b
            case (.missing(let a, let au), .missing(let b, let bu)): return a == b && au == bu
            case (.failed, .failed): return true
            default: return false
            }
        }
    }

    /// Everything the document on screen was parsed from.
    ///
    /// One user action parses the same file twice — `handleOp` reparses
    /// explicitly so the change shows immediately, and scoutctl's own write
    /// trips FSEvents a moment later — so the second parse is pure waste. An
    /// mtime/size guard can't catch that safely: flipping `- [ ]` to `- [x]`
    /// leaves the file exactly as long. A content digest has no such blind
    /// spot.
    ///
    /// The URL and the byline are part of the key because they are parse
    /// inputs the bytes don't capture: the document's date comes from the
    /// filename, and the byline names the author of every `//==<< … >>==//`
    /// inline comment. Two days can hold byte-identical files.
    nonisolated struct SourceFingerprint: Equatable, Sendable {
        let url: URL
        let byline: String
        /// SHA-256 of the file's bytes.
        let digest: Data
    }

    /// What one off-actor read + parse produced.
    nonisolated enum ParseOutcome: Sendable {
        /// The bytes hash to the fingerprint of the document already on
        /// screen. Nothing was parsed and nothing is published.
        case unchanged
        case parsed(ActionItemsDocument, SourceFingerprint)
    }

    @Published private(set) var state: State = .idle

    /// How long a burst of FSEvents for one write is collected before the
    /// file is reparsed. scoutctl rewrites the whole day per edit, which trips
    /// several events.
    static let watchDebounce: Duration = .milliseconds(250)

    private let directory: URL
    /// The FSEvents source, already debounced. `DebouncedFileEvents` does what
    /// a hand-rolled 250 ms sleep in ``startWatching()`` used to, on a fixed
    /// window rather than a resetting one — so a file being appended to
    /// continuously still surfaces once per interval instead of only when the
    /// churn stops (#22).
    private let fileEvents: any FileSystemEventSource
    private let defaults: UserDefaults
    private var currentDate: Date?
    private var watchTask: Task<Void, Never>?

    /// The most recent read + parse.
    ///
    /// Held so the next request can cancel it. The `generation` guard below
    /// already discards a superseded *result*, but the work itself ran to
    /// completion — a background core parsing a document nobody will ever see.
    /// It outlives its own task, so between requests it reads as the record of
    /// what the last one did, `.unchanged` included; the next request clears it.
    private(set) var parseTask: Task<ParseOutcome, Error>?

    /// Fingerprint of the bytes behind the document currently on screen, or
    /// `nil` whenever `state` is anything but a parsed document. A parse may
    /// only be skipped while this really does describe what the user is
    /// looking at, so ``publish(_:)`` drops it on every other state.
    private var publishedFingerprint: SourceFingerprint?

    /// Bumped on every reparse request. A parse runs off the main actor, so
    /// two can overlap — a slow day switching to a fast one, or a write landing
    /// mid-load. The result of a parse whose generation is no longer current is
    /// discarded, otherwise the stale one finishes last and wins.
    private var generation: UInt64 = 0

    init(
        directory: URL,
        fileEvents: any FileSystemEventSource,
        defaults: UserDefaults = .standard
    ) {
        self.directory = directory
        self.fileEvents = DebouncedFileEvents(base: fileEvents, interval: Self.watchDebounce)
        self.defaults = defaults
    }

    /// Byline for Obsidian `//==<< … >>==//` inline comments.
    ///
    /// Deliberately main-actor isolated: `UserDefaults` hands back
    /// Cocoa-backed strings, and reading one off-thread is the shape that has
    /// faulted in this codebase before. Callers read it here and hand the
    /// result to the `nonisolated` parser.
    static func inlineCommentAuthor(from defaults: UserDefaults) -> String {
        defaults.string(forKey: "authorName") ?? "user"
    }

    /// Load the action-items file for ``date`` (local timezone). Starts (or
    /// restarts) the FSEvents subscription filtered to that date's filename.
    func load(date: Date) async {
        currentDate = date
        // Keep an already-loaded copy of this same day on screen while the
        // reparse runs. Publishing `.loading` here would tear the card tree
        // down to a spinner and rebuild it on every return to the tab, even
        // though the result is byte-identical and the equality gate in
        // ``publish(_:)`` would otherwise absorb it.
        if !isShowingDocument(for: date) {
            publish(.loading(date))
        }
        await reparse()
        startWatching()
    }

    /// Recompute the displayed document for the currently-loaded date. Called
    /// by the view after a successful CLI invocation so the user sees the
    /// change ASAP even if FSEvents is briefly laggy — the file watcher then
    /// fires for the same write, and that second request now hashes the bytes,
    /// finds them unchanged and returns without parsing.
    /// `async` because the parse it drives is: callers that need to observe the
    /// outcome — including the `#47` guarantee that a failed reparse surfaces
    /// as `.failed` rather than leaving stale `.loaded` state — must await it.
    func reparseCurrent() async {
        await reparse()
    }

    private func isShowingDocument(for date: Date) -> Bool {
        if case .loaded(let doc) = state { return doc.sourceURL == url(for: date) }
        return false
    }

    /// Reparse the file for `currentDate`. The URL is derived here rather than
    /// passed in, so a reparse queued before a day switch (the watcher's
    /// debounce) cannot land the previous day's document under the new date.
    private func reparse() async {
        guard let date = currentDate else { return }
        let url = url(for: date)

        // Every request supersedes the ones in flight — including one that
        // ends in `.missing`, otherwise a slow parse of the previous day
        // finishes last and overwrites it.
        generation &+= 1
        let mine = generation

        // Cooperative: `ActionItemsParser.parse` checks `Task.isCancelled` as
        // it walks the file, so a superseded parse stops within a few hundred
        // lines instead of finishing a document that will be thrown away.
        // Cleared as well as cancelled so the paths that return below leave no
        // finished task holding the previous day's document alive.
        parseTask?.cancel()
        parseTask = nil

        guard FileManager.default.fileExists(atPath: url.path) else {
            publish(.missing(date: date, expectedURL: url))
            return
        }

        let byline = Self.inlineCommentAuthor(from: defaults)
        // Snapshotting this here is safe because of the generation guard: no
        // parse that started before this one can publish after it, so what is
        // on screen when this result lands is what is on screen now.
        let onScreen = publishedFingerprint

        // Off the main actor: parsing a real day costs hundreds of
        // milliseconds, and doing it here froze the UI on every load and every
        // checkbox click. `ActionItemsDocument` is `Sendable` and the parser is
        // `nonisolated`, so only value types cross.
        let task = Task.detached(priority: .userInitiated) { () throws -> ParseOutcome in
            let data = try Data(contentsOf: url)
            let fingerprint = SourceFingerprint(
                url: url,
                byline: byline,
                digest: Data(SHA256.hash(data: data))
            )
            // Hashing 1.8 MB costs single-digit milliseconds against the
            // hundreds the parse costs, so the redundant reparse after every
            // write becomes a read and a hash.
            guard fingerprint != onScreen else { return .unchanged }
            try Task.checkCancellation()
            let text = String(data: data, encoding: .utf8) ?? ""
            let document = try ActionItemsParser.parse(
                text: text,
                sourceURL: url,
                sourceBytes: data.count,
                inlineCommentAuthor: byline
            )
            return .parsed(document, fingerprint)
        }
        parseTask = task
        let result = await task.result

        // A newer request started while this one was parsing — drop it rather
        // than overwrite fresher state with stale content.
        guard mine == generation else { return }

        switch result {
        case .success(.unchanged):
            // The document on screen already is this file. Nothing to do.
            break
        case .success(.parsed(let document, let fingerprint)):
            publishedFingerprint = fingerprint
            publish(.loaded(document))
        case .failure(is CancellationError):
            // Superseded mid-parse. The generation guard above normally
            // catches this first; this covers teardown.
            break
        case .failure(let error):
            publish(.failed(error))
        }
    }

    /// Assign `state` only when it actually changed.
    ///
    /// `@Published` fires `objectWillChange` on every assignment, equal or not,
    /// and republishing a byte-identical document rebuilt the entire view tree
    /// (~475 cards, ~1.8 s) for no change at all.
    private func publish(_ next: State) {
        // The skip gate may only fire while `state` really is the document
        // those bytes produced, so anything else drops the fingerprint and the
        // next request parses.
        switch next {
        case .loaded: break
        default: publishedFingerprint = nil
        }

        // `State ==` treats any two failures as equal (it exists for
        // `.onChange` coalescing), so a second, different error would be
        // swallowed here and the first error's text would stay on screen.
        // Failures are cheap to republish; always let them through.
        if case .failed = next {
            state = next
            return
        }
        guard state != next else { return }
        state = next
    }

    private func startWatching() {
        watchTask?.cancel()
        let stream = fileEvents.events(for: directory)
        watchTask = Task { [weak self] in
            for await event in stream {
                guard let self else { return }
                guard let date = await MainActor.run(body: { self.currentDate }) else { continue }
                let expected = await MainActor.run(body: { self.url(for: date) })
                guard event.url.lastPathComponent == expected.lastPathComponent else { continue }
                await self.reparse()
            }
        }
    }

    func url(for date: Date) -> URL {
        directory.appendingPathComponent("action-items-\(ActionItemsDay.stem(for: date)).md")
    }

    deinit {
        watchTask?.cancel()
        parseTask?.cancel()
    }
}
