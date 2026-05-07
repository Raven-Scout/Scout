import Foundation
import Combine

@MainActor
final class ScheduleEditService: ObservableObject {
    @Published private(set) var slots: [Slot] = []
    @Published private(set) var loadedMtime: Date?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]
    let canonicalSchedulePath: URL

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        canonicalSchedulePath: URL,
        argumentsPrefix: [String] = []
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.canonicalSchedulePath = canonicalSchedulePath
    }

    /// Reads the live schedule via `scoutctl schedule list --json`, decodes,
    /// publishes. Captures the canonical file's mtime for the stale-check
    /// performed by save().
    func loadAll() async throws {
        let result = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["schedule", "list", "--json"],
            environment: [:],
            workingDirectory: nil
        )
        let decoded = try JSONDecoder().decode([Slot].self, from: result.stdout)
        self.slots = decoded
        self.loadedMtime = (try? FileManager.default
            .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
    }

    /// Writes the candidate slots to canonical.
    /// Steps (mirror §7.1 of the spec):
    /// 1. Stale-check: live mtime must equal loadedMtime; else throw StaleScheduleError.
    /// 2. Compose YAML (header preservation in Task 7).
    /// 3. Write to tmpfile in same directory.
    /// 4. Validate via scoutctl schedule validate --target <tmpfile>.
    /// 5. Atomic-rename via FileManager.replaceItemAt.
    /// 6. Reload via scoutctl schedule list --json + recapture mtime.
    func save(allSlots: [Slot]) async throws {
        // 1. Stale-check.
        let liveMtime: Date? = (try? FileManager.default
            .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
        if let live = liveMtime, let loaded = loadedMtime, live > loaded {
            throw StaleScheduleError(loadedAt: loaded, modifiedAt: live)
        }

        // 2. Compose YAML — Task 7 adds header preservation. For now, pure emit.
        let body = serializeSlotsToYAML(allSlots)

        // 3. Tmpfile in same directory; defer guarantees cleanup on every exit path.
        let tmp = canonicalSchedulePath
            .deletingLastPathComponent()
            .appendingPathComponent("schedule.yaml.\(UUID().uuidString).tmp")
        try body.write(to: tmp, atomically: false, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }

        // 4. Validate via scoutctl.
        let validate = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["schedule", "validate", "--target", tmp.path],
            environment: [:],
            workingDirectory: nil
        )
        guard validate.exitCode == 0 else {
            let stderr = String(data: validate.stderr, encoding: .utf8) ?? ""
            throw NSError(
                domain: "ScheduleEditService.save",
                code: Int(validate.exitCode),
                userInfo: [NSLocalizedDescriptionKey: stderr]
            )
        }

        // 5. Atomic rename. replaceItemAt consumes tmp on success, so the
        // defer becomes a no-op (removeItem on a missing file fails silently
        // because of the `try?`).
        _ = try FileManager.default.replaceItemAt(
            canonicalSchedulePath,
            withItemAt: tmp,
            backupItemName: nil,
            options: [.usingNewMetadataOnly]
        )

        // 6. Reload + recapture mtime.
        try await loadAll()
    }

    /// Serialize slot array to a YAML string matching the engine's expected
    /// shape: top-level `schema_version: 1` then `slots:` mapping. Insertion
    /// order is preserved because we emit slots in the array's order.
    /// Header preservation (comments above `schema_version`) is deferred to
    /// Task 7 — for now we emit a plain YAML body.
    private func serializeSlotsToYAML(_ slots: [Slot]) -> String {
        var out = "schema_version: 1\n"
        out += "slots:\n"
        for slot in slots {
            out += "  \(slot.key):\n"
            out += "    type: \(slot.type.rawValue)\n"
            out += "    runner: \(yamlScalar(slot.runner))\n"
            out += "    fires_at_local: \(yamlQuoted(slot.firesAtLocal))\n"
            out += "    weekdays: [\(slot.weekdays.joined(separator: ", "))]\n"
            out += "    missed_window_hours: \(slot.missedWindowHours)\n"
            out += "    on_miss: \(slot.onMiss.rawValue)\n"
            out += "    cooldown_minutes: \(slot.cooldownMinutes)\n"
            if let b = slot.budgetUsd {
                out += "    budget_usd: \(b)\n"
            }
            if let tz = slot.tz {
                out += "    tz: \(yamlQuoted(tz))\n"
            }
            out += "    runtime: \(slot.runtime.rawValue)\n"
        }
        return out
    }

    /// Emit a YAML scalar — quote it if it contains characters that would
    /// otherwise need escaping (colons, leading dashes, etc.). For our
    /// known runner-script values (e.g. `run-scout.sh`), bare emission is safe.
    private func yamlScalar(_ s: String) -> String {
        if s.contains(":") || s.contains("#") || s.hasPrefix("-") || s.hasPrefix("[") || s.hasPrefix("{") {
            return yamlQuoted(s)
        }
        return s
    }

    /// Emit a double-quoted YAML scalar. Escapes embedded quotes and backslashes.
    private func yamlQuoted(_ s: String) -> String {
        let escaped = s
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        return "\"\(escaped)\""
    }
}
