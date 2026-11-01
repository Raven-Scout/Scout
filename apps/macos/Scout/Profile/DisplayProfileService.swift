import Combine
import Foundation
import OSLog

/// Whether the profile on disk can be used and rewritten.
enum DisplayProfileStatus: Equatable {
    /// Read fine (or missing). `warnings` lists what was ignored.
    case ok(warnings: [String])
    /// Not JSON, not an object, or a bad `schema`. The last good profile stays.
    case unreadable(reason: String)
    /// Written by a newer app. Read as unreadable so it is never downgraded.
    case unsupportedSchema(found: Int)

    var canWrite: Bool {
        if case .ok = self { return true }
        return false
    }
}

/// Owns `scout-display.json` (spec sections 4 and 5): reads it once at init,
/// follows edits made outside the app, and writes in-app changes as patches.
/// It never commits; the next session that commits the vault picks the file up.
@MainActor
final class DisplayProfileService: ObservableObject {
    @Published private(set) var profile: DisplayProfile
    @Published private(set) var status: DisplayProfileStatus
    /// Set when the last write failed (permissions, full disk); cleared by the
    /// next successful write.
    @Published private(set) var writeError: String?
    /// The List/Board view for this session. The toolbar switch sets it and it
    /// is never written. It starts from the profile's default view and follows
    /// the default whenever that changes.
    @Published var currentView: ActionItemsViewMode

    let fileURL: URL

    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?
    private var loggedContent: Data??

    private static let log = Logger(subsystem: "com.scout.Scout", category: "DisplayProfile")

    init(scoutDirectory: URL, fileEvents: any FileSystemEventSource) {
        // FSEvents reports real paths; see `URL.resolvingRealPath()`.
        self.fileURL = scoutDirectory.resolvingRealPath().appendingPathComponent(DisplayProfileCodec.fileName)
        self.fileEvents = fileEvents
        // One small synchronous read, so the tab opens in the saved view
        // instead of flashing List first.
        let snapshot = Self.read(fileURL, lastGood: DisplayProfile())
        self.profile = snapshot.profile
        self.status = snapshot.status
        self.currentView = snapshot.profile.actionItems.defaultView
        logOnce(snapshot)
    }

    deinit {
        watchTask?.cancel()
    }

    func startWatching() {
        watchTask?.cancel()
        let url = fileURL
        // Subscribe before the task starts so no early event is lost.
        let events = fileEvents.events(for: url)
        watchTask = Task { [weak self] in
            for await event in events where event.url.lastPathComponent == url.lastPathComponent {
                self?.reload()
            }
        }
    }

    /// Re-read the file. The app's own writes come back here too and re-parse
    /// to the value already published, so nothing republishes.
    func reload() {
        let snapshot = Self.read(fileURL, lastGood: profile)
        if snapshot.profile != profile { apply(snapshot.profile) }
        if snapshot.status != status { status = snapshot.status }
        logOnce(snapshot)
    }

    /// Apply a change from Settings or the View menu. It always takes effect
    /// on screen; it reaches the file only when the file is one the app may
    /// rewrite.
    func update(_ change: (inout ActionItemsDisplay) -> Void) {
        var next = profile
        change(&next.actionItems)
        let entries = next.changedEntries(from: profile)
        guard !entries.isEmpty else { return }
        apply(next)
        guard status.canWrite else { return }
        write(entries)
    }

    // MARK: - Private

    private struct Snapshot {
        let data: Data?
        let profile: DisplayProfile
        let status: DisplayProfileStatus
    }

    /// Publish a new profile; a new default view is also shown right away.
    private func apply(_ next: DisplayProfile) {
        if next.actionItems.defaultView != profile.actionItems.defaultView {
            currentView = next.actionItems.defaultView
        }
        profile = next
    }

    private static func read(_ url: URL, lastGood: DisplayProfile) -> Snapshot {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return Snapshot(data: nil, profile: DisplayProfile(), status: .ok(warnings: []))
        }
        guard let data = try? Data(contentsOf: url) else {
            return Snapshot(data: nil, profile: lastGood, status: .unreadable(reason: "the file can't be opened"))
        }
        switch DisplayProfileCodec.decode(data) {
        case .success(let decoded):
            return Snapshot(data: data, profile: decoded.profile, status: .ok(warnings: decoded.warnings))
        case .failure(let failure):
            return Snapshot(data: data, profile: lastGood, status: status(for: failure))
        }
    }

    private static func status(for failure: DisplayProfileCodec.Failure) -> DisplayProfileStatus {
        switch failure {
        case .unreadable(let reason):    return .unreadable(reason: reason)
        case .unsupportedSchema(let n):  return .unsupportedSchema(found: n)
        }
    }

    private func write(_ entries: [DisplayProfile.Entry]) {
        do {
            let existing = FileManager.default.fileExists(atPath: fileURL.path) ? try Data(contentsOf: fileURL) : nil
            let data = try DisplayProfileCodec.patch(existing, with: entries)
            try data.write(to: fileURL, options: .atomic)
            writeError = nil
        } catch let failure as DisplayProfileCodec.Failure {
            // The file turned unreadable since the last load; hold writes.
            status = Self.status(for: failure)
        } catch {
            writeError = error.localizedDescription
            Self.log.error("couldn't write \(self.fileURL.path, privacy: .public): \(error.localizedDescription, privacy: .public)")
        }
    }

    /// Log each distinct file content once, not on every reload.
    private func logOnce(_ snapshot: Snapshot) {
        guard loggedContent != .some(snapshot.data) else { return }
        loggedContent = .some(snapshot.data)
        let name = DisplayProfileCodec.fileName
        switch snapshot.status {
        case .ok(let warnings):
            for warning in warnings {
                Self.log.warning("\(name, privacy: .public): \(warning, privacy: .public)")
            }
        case .unreadable(let reason):
            Self.log.error("\(name, privacy: .public) is ignored: \(reason, privacy: .public)")
        case .unsupportedSchema(let found):
            Self.log.error("\(name, privacy: .public) has schema \(found, privacy: .public), newer than this app; it is ignored and won't be rewritten")
        }
    }
}
