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

    init(fileURL: URL) {
        self.fileURL = fileURL
        self.acks = Self.load(from: fileURL)
    }

    func isAcked(_ fingerprint: String) -> Bool {
        queue.sync { acks[fingerprint] != nil }
    }

    func ack(fingerprint: String) {
        queue.sync {
            acks[fingerprint] = Date()
            Self.persist(acks, to: fileURL)
        }
    }

    /// Remove fingerprints not present in `active`. Called from
    /// `ConnectorHealthService.refresh()` after loading the alerts log.
    func gc(active: Set<String>) {
        queue.sync {
            let before = acks.count
            acks = acks.filter { active.contains($0.key) }
            if acks.count != before { Self.persist(acks, to: fileURL) }
        }
    }

    // MARK: - Persistence

    private static func load(from url: URL) -> [String: Date] {
        guard let data = try? Data(contentsOf: url) else { return [:] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return (try? decoder.decode([String: Date].self, from: data)) ?? [:]
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
