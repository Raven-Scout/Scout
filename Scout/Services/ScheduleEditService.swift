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
    ///
    /// When the JSON decode fails, throws an NSError whose
    /// `localizedDescription` includes a snippet of scoutctl's actual stdout
    /// so the user can tell what scoutctl printed instead of JSON (stale
    /// version, stdout log leak, etc.) rather than just "not valid JSON".
    func loadAll() async throws {
        let result = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["schedule", "list", "--json"],
            environment: [:],
            workingDirectory: nil
        )
        do {
            let decoded = try JSONDecoder().decode([Slot].self, from: result.stdout)
            self.slots = decoded
            self.loadedMtime = (try? FileManager.default
                .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
        } catch {
            throw NSError(
                domain: "ScheduleEditService.loadAll",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: ScheduleService.formatDecodeFailure(
                    stdout: result.stdout, stderr: result.stderr
                )]
            )
        }
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

        // 2. Compose YAML with header preservation (Task 7).
        // extractHeader reads everything before `\nslots:` from the canonical
        // file. On failure (missing file, no anchor) we fall back to the
        // header-less hand-rolled emit — better to lose the header than refuse
        // to save.
        let header = (try? extractHeader(from: canonicalSchedulePath)) ?? ""
        let body: String
        if header.isEmpty {
            body = serializeSlotsToYAML(allSlots)
        } else {
            body = header + "\nslots:\n" + serializeSlotsToYAMLBody(allSlots)
        }

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

    /// Delete a slot by key and persist via the same atomic-write path as save.
    /// Throws if the key isn't in the current slot list.
    func delete(slotKey: String) async throws {
        guard slots.contains(where: { $0.key == slotKey }) else {
            throw NSError(
                domain: "ScheduleEditService.delete",
                code: 404,
                userInfo: [NSLocalizedDescriptionKey: "no such slot: \(slotKey)"]
            )
        }
        let remaining = slots.filter { $0.key != slotKey }
        try await save(allSlots: remaining)
    }

    /// Serialize slot array to a YAML string matching the engine's expected
    /// shape: top-level `schema_version: 1` then `slots:` mapping. Insertion
    /// order is preserved because we emit slots in the array's order.
    /// Used as the header-less fallback path (Task 6) and when no `\nslots:`
    /// anchor is found in the canonical file.
    private func serializeSlotsToYAML(_ slots: [Slot]) -> String {
        var out = "schema_version: 1\n"
        out += "slots:\n"
        out += serializeSlotsToYAMLBody(slots)
        return out
    }

    /// Compose ONLY the indented slot blocks (no `schema_version:`, no `slots:`
    /// opener). Used by the header-preservation path (Task 7) so the canonical
    /// file's existing `schema_version: 1` + `slots:` line are not duplicated.
    ///
    /// Field order, indentation, and quoting exactly match the full emitter
    /// above — these two functions share the same slot-emit logic.
    private func serializeSlotsToYAMLBody(_ slots: [Slot]) -> String {
        var out = ""
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

    /// Read the canonical file and capture everything from start of file up to
    /// (but not including) the `\nslots:` line. Returns "" if the file doesn't
    /// exist or has no anchor — caller falls back to a header-less emit.
    ///
    /// Spec §7.2: header-only preservation. Inline slot-block comments are NOT
    /// preserved by this approach; that's an explicit tradeoff to avoid
    /// byte-stream splicing fragility.
    private func extractHeader(from path: URL) throws -> String {
        let raw = try String(contentsOf: path, encoding: .utf8)
        // Split on the literal `\nslots:` sequence. If the anchor is present,
        // parts[0] is everything before it (the header). If absent, the array
        // has exactly one element and we fall back by returning "".
        let parts = raw.components(separatedBy: "\nslots:")
        guard parts.count > 1 else { return "" }
        return parts[0]
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
