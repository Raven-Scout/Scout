import Foundation

/// JSON-backed ack sidecar at `.scout-cache/connector-alerts-acked.json`.
///
/// Uses `ConnectorAlert.fingerprint` as the key so acking a *current*
/// alert doesn't suppress a *new* identical-looking alert with a fresh
/// `first_seen_ts`. Call `gc(active:)` once on app launch / before each
/// render to evict fingerprints whose underlying alerts are no longer
/// in the log.
final class ConnectorAckStore {
    private let fileURL: URL
    private var acks: [String: Date]   // fingerprint → acked at
    private let queue = DispatchQueue(label: "ConnectorAckStore.io")

    /// The file existed but couldn't be read or decoded at load. Its acks
    /// aren't in `acks`, so the first write sets it aside instead of
    /// replacing it (see `persist`).
    private var backingUnreadable: Bool

    init(fileURL: URL) {
        self.fileURL = fileURL
        switch Self.load(from: fileURL) {
        case .some(let loaded):
            self.acks = loaded
            self.backingUnreadable = false
        case .none:
            self.acks = [:]
            self.backingUnreadable = true
        }
    }

    func isAcked(_ fingerprint: String) -> Bool {
        queue.sync { acks[fingerprint] != nil }
    }

    func ack(fingerprint: String) {
        queue.sync {
            acks[fingerprint] = Date()
            persistLocked()
        }
    }

    /// Remove fingerprints not present in `active`. Called from
    /// `ConnectorHealthService.refresh()` after loading the alerts log.
    func gc(active: Set<String>) {
        queue.sync {
            let before = acks.count
            acks = acks.filter { active.contains($0.key) }
            if acks.count != before { persistLocked() }
        }
    }

    // MARK: - Persistence

    /// Where an unreadable ack file is moved before the first write replaces it.
    static func unreadableBackupURL(for url: URL) -> URL {
        url.appendingPathExtension("unreadable")
    }

    /// Call on `queue`. If the file was unreadable at load, try it again
    /// (merging what's there) and, if it still can't be read, move it aside
    /// to `<name>.unreadable` rather than overwrite it.
    private func persistLocked() {
        if backingUnreadable {
            if let onDisk = Self.load(from: fileURL) {
                acks = onDisk.merging(acks) { _, mine in mine }
            } else if FileManager.default.fileExists(atPath: fileURL.path) {
                let backup = Self.unreadableBackupURL(for: fileURL)
                try? FileManager.default.removeItem(at: backup)
                guard (try? FileManager.default.moveItem(at: fileURL, to: backup)) != nil else {
                    return   // can't set it aside: keep acks in memory, write nothing
                }
            }
            backingUnreadable = false
        }
        Self.persist(acks, to: fileURL)
    }

    /// `[:]` for a missing file; nil when it exists but can't be read or
    /// decoded. Dates are ISO-8601, with or without fractional seconds.
    static func load(from url: URL) -> [String: Date]? {
        guard FileManager.default.fileExists(atPath: url.path) else { return [:] }
        guard let data = try? Data(contentsOf: url),
              let raw = try? JSONDecoder().decode([String: String].self, from: data)
        else { return nil }
        let plain = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var out: [String: Date] = [:]
        for (key, value) in raw {
            guard let date = plain.date(from: value) ?? fractional.date(from: value) else { return nil }
            out[key] = date
        }
        return out
    }

    private static func persist(_ acks: [String: Date], to url: URL) {
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            encoder.outputFormatting = .prettyPrinted
            let data = try encoder.encode(acks)
            try data.write(to: url, options: .atomic)
        } catch {
            // Swallow — next ack retries; file inspection by user is still
            // valuable but a transient write failure shouldn't crash the app.
        }
    }
}
