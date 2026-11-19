import Foundation
import Combine

/// Whether `ScheduleEditService.slots` reflects schedule.yaml. Writes are
/// refused unless the most recent load succeeded: a page that failed to load
/// shows no slots, and saving from there would replace the whole file with
/// only what the page knew about.
enum ScheduleLoadState: Equatable {
    case notLoaded
    case loaded
    case failed(String)
}

/// Thrown by `ScheduleEditService.save` when the last load did not succeed.
struct ScheduleNotLoadedError: Error, LocalizedError, Equatable {
    var errorDescription: String? {
        "The schedule couldn't be loaded, so Scout won't save over it. Retry loading it first."
    }
}

/// Thrown by `ScheduleEditService.save` when `scoutctl schedule validate`
/// rejected the new schedule, so nothing was written. `output` is the
/// validator's own words (may be empty); the app shows them only behind
/// Details (R2).
struct ScheduleValidationError: Error, LocalizedError, Equatable {
    let exitCode: Int32
    let output: String

    /// What "Copy details" carries: the validator's output, or its exit code
    /// when it printed nothing.
    var details: String {
        let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "scoutctl schedule validate exited \(exitCode) without a message." : trimmed
    }

    var errorDescription: String? { details }
}

/// Thrown by `ScheduleEditService.save` when two slots share a key. The
/// engine reads schedule.yaml with a YAML loader that keeps the last of two
/// same-named entries, so writing both would silently replace one schedule
/// with the other (UI audit #58). The form refuses this first; this is the
/// write path's own guard.
struct ScheduleDuplicateKeyError: Error, LocalizedError, Equatable {
    let keys: [String]
    var errorDescription: String? { "Duplicate schedule keys: \(keys.joined(separator: ", "))" }
}

@MainActor
final class ScheduleEditService: ObservableObject {
    @Published private(set) var slots: [Slot] = []
    /// Canonical schedule.yaml's mtime as of the last successful load, or nil
    /// when the file did not exist then. `save` compares it with the live
    /// mtime, so nil is a real baseline ("no file"), never "skip the check".
    @Published private(set) var loadedMtime: Date?
    @Published private(set) var loadState: ScheduleLoadState = .notLoaded
    /// The schedule file's bytes as of the last successful load (captured
    /// before scoutctl reads it, like `loadedMtime`). `save` also compares
    /// content, so a same-second rewrite can't slip past the mtime compare
    /// (C10). nil data = no file then.
    private(set) var loadedSnapshot: FileSnapshot?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]
    let canonicalSchedulePath: URL
    /// Unsaved slot-form drafts to reconcile after every successful load, so
    /// a saved or orphaned draft is settled even while the Schedules page is
    /// off screen (an undo from another page, a save that finished after the
    /// page closed). nil: none (tests that don't need it).
    private let drafts: SlotDraftStore?
    private var watchTask: Task<Void, Never>?
    private var pendingExternalReload: Task<Void, Never>?

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        canonicalSchedulePath: URL,
        argumentsPrefix: [String] = [],
        drafts: SlotDraftStore? = nil
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.canonicalSchedulePath = canonicalSchedulePath
        self.drafts = drafts
    }

    /// Reads the live schedule via `scoutctl schedule list --json`, decodes,
    /// publishes. Captures the canonical file's mtime for the stale-check
    /// performed by save().
    ///
    /// The mtime is captured BEFORE scoutctl reads the file, so an edit that
    /// lands during the read makes the next save stale instead of slipping by.
    /// Any failure (scoutctl missing, bad JSON) sets `loadState` to `.failed`
    /// and leaves `slots` as they were; `save` refuses until a load succeeds.
    ///
    /// When the JSON decode fails, throws an NSError whose
    /// `localizedDescription` includes a snippet of scoutctl's actual stdout
    /// so the user can tell what scoutctl printed instead of JSON (stale
    /// version, stdout log leak, etc.) rather than just "not valid JSON".
    func loadAll() async throws {
        let mtimeBeforeRead = currentMtime()
        let snapshotBeforeRead = try? FileSnapshot.capture(canonicalSchedulePath)
        let result: ProcessResult
        do {
            result = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + ["schedule", "list", "--json"],
                environment: [:],
                workingDirectory: nil
            )
        } catch {
            loadState = .failed(error.localizedDescription)
            throw error
        }
        do {
            let decoded = try JSONDecoder().decode([Slot].self, from: result.stdout)
            self.slots = decoded
            self.loadedMtime = mtimeBeforeRead
            self.loadedSnapshot = snapshotBeforeRead
            self.loadState = .loaded
            drafts?.reconcile(with: decoded)
        } catch {
            let message = ScheduleService.formatDecodeFailure(
                stdout: result.stdout, stderr: result.stderr
            )
            loadState = .failed(message)
            throw NSError(
                domain: "ScheduleEditService.loadAll",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: message]
            )
        }
    }

    // MARK: - Watching schedule.yaml (UI audit #61)

    /// Reload whenever schedule.yaml changes on disk, so the page never
    /// shows (or saves against) a schedule that's out of date. Watches the
    /// file's folder, since a save replaces the file rather than editing it.
    ///
    /// A reload respects unsaved edits: `loadAll` reconciles `SlotDraftStore`
    /// (#669), and an open form rebases its draft onto the new slot, keeping
    /// the user's values and naming any field changed on both sides (C10).
    /// Reloads queue behind saves (`serialized`), and one that finds the
    /// bytes the app last loaded (its own write) does nothing.
    func startWatching(_ source: any FileSystemEventSource, debounce: Duration = .milliseconds(300)) {
        watchTask?.cancel()
        let folder = canonicalSchedulePath.deletingLastPathComponent().resolvingRealPath()
        let name = canonicalSchedulePath.lastPathComponent
        // Subscribe before the task starts so no early event is lost.
        let events = source.events(for: folder)
        watchTask = Task { [weak self] in
            for await event in events where event.url.lastPathComponent == name {
                self?.scheduleExternalReload(after: debounce)
            }
        }
    }

    func stopWatching() {
        watchTask?.cancel()
        watchTask = nil
        pendingExternalReload?.cancel()
        pendingExternalReload = nil
    }

    /// Coalesce a burst of events (an editor's save is several) into one reload.
    private func scheduleExternalReload(after delay: Duration) {
        guard pendingExternalReload == nil else { return }
        pendingExternalReload = Task { [weak self] in
            try? await Task.sleep(for: delay)
            guard !Task.isCancelled, let self else { return }
            self.pendingExternalReload = nil
            await self.reloadIfChangedOnDisk()
        }
    }

    /// Reload if schedule.yaml no longer holds what the last load read.
    /// Queued behind any save, delete or undo in flight. Returns whether it
    /// reloaded. A failed reload lands in `loadState` like any other.
    @discardableResult
    func reloadIfChangedOnDisk() async -> Bool {
        (try? await serialized { () async throws -> Bool in
            let onDisk = try? FileSnapshot.capture(self.canonicalSchedulePath)
            if self.loadState == .loaded, let loaded = self.loadedSnapshot, onDisk?.data == loaded.data {
                return false
            }
            try? await self.loadAll()
            return true
        }) ?? false
    }

    private func currentMtime() -> Date? {
        (try? FileManager.default
            .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
    }

    /// Writes the candidate slots to canonical.
    /// Steps (mirror §7.1 of the spec):
    /// 0. Refuse unless the last load succeeded (ScheduleNotLoadedError).
    /// 1. Stale-check: live mtime must equal loadedMtime (nil = no file);
    ///    else throw StaleScheduleError. Never skipped.
    /// 2. Compose YAML (header preservation in Task 7).
    /// 3. Write to tmpfile in same directory.
    /// 4. Validate via scoutctl schedule validate --target <tmpfile>.
    /// 5. Atomic-rename via FileManager.replaceItemAt.
    /// 6. Reload via scoutctl schedule list --json + recapture mtime.
    ///
    /// Returns the file's bytes before and after for undo (`restore`), nil
    /// when the save changed nothing. `actionName` names it in the Edit menu.
    ///
    /// Saves, deletes and undo/redo run one at a time (`serialized`), so an
    /// undo can't land while a save waits on `schedule validate`.
    @discardableResult
    func save(allSlots: [Slot], actionName: String = "Edit Schedule") async throws -> VaultChange? {
        try await serialized { try await self.performSave(allSlots: allSlots, actionName: actionName) }
    }

    /// Save `transform` applied to the slots as they are when this write's
    /// turn comes, not when it was asked for: a second save queued behind a
    /// first then builds on the first's result instead of writing stale slots
    /// over it.
    @discardableResult
    func update(actionName: String, _ transform: @escaping @MainActor ([Slot]) throws -> [Slot]) async throws -> VaultChange? {
        try await serialized {
            try await self.performSave(allSlots: try transform(self.slots), actionName: actionName)
        }
    }

    /// Save the form's edit to one existing slot. `saved` was typed against
    /// `baseline`, the slot as the form last saw it; by the time this
    /// write's turn comes, a reload (the schedule.yaml watcher, UI audit
    /// #61) may have brought in a change made elsewhere. The edit is
    /// rebased onto the slot as it is then: fields the user didn't touch
    /// take the new values, so that change isn't written back over. A
    /// field changed on both sides, or a slot that's gone, throws
    /// `StaleScheduleError` and writes nothing.
    @discardableResult
    func saveSlot(_ saved: Slot, baseline: Slot, actionName: String = "Edit Schedule") async throws -> VaultChange? {
        try await update(actionName: actionName) { slots in
            guard slots.contains(where: { $0.key == baseline.key }) else {
                throw StaleScheduleError(loadedAt: self.loadedMtime, modifiedAt: self.loadedMtime,
                                         removedSlot: baseline.key)
            }
            guard let next = Self.applying(saved, baseline: baseline, to: slots) else {
                throw StaleScheduleError(loadedAt: self.loadedMtime, modifiedAt: self.loadedMtime ?? .now)
            }
            return next
        }
    }

    /// `slots` with `saved` (typed against `baseline`) rebased onto the slot
    /// of the same key, or nil when that slot is gone or a field changed
    /// both in `saved` and in `slots` since `baseline`.
    nonisolated static func applying(_ saved: Slot, baseline: Slot, to slots: [Slot]) -> [Slot]? {
        guard let current = slots.first(where: { $0.key == baseline.key }) else { return nil }
        let rebased = SlotDraft(from: saved).rebased(from: baseline, onto: current)
        guard rebased.conflicts.isEmpty else { return nil }
        let merged = rebased.draft.toSlot()
        return slots.map { $0.key == baseline.key ? merged : $0 }
    }

    /// Tail of the write chain: each write awaits the previous one.
    private var writeTail: Task<Void, Never>?

    private func serialized<T>(_ work: @escaping @MainActor () async throws -> T) async throws -> T {
        let previous = writeTail
        let task = Task { @MainActor in
            _ = await previous?.value
            return try await work()
        }
        writeTail = Task { _ = try? await task.value }
        return try await task.value
    }

    private func performSave(allSlots: [Slot], actionName: String) async throws -> VaultChange? {
        // 0. Never write from a page that doesn't know what's in the file.
        guard loadState == .loaded else { throw ScheduleNotLoadedError() }
        // Never write two slots under one key: the engine would keep only
        // the last (UI audit #58).
        let duplicates = Dictionary(grouping: allSlots.map(\.key), by: { $0 })
            .filter { $0.value.count > 1 }.keys.sorted()
        guard duplicates.isEmpty else { throw ScheduleDuplicateKeyError(keys: duplicates) }

        // 1. Stale-check. Compares the optionals directly, so a file that
        // appeared, vanished or changed (in either direction) since the load
        // all count as stale.
        let liveMtime = currentMtime()
        if liveMtime != loadedMtime {
            throw StaleScheduleError(loadedAt: loadedMtime, modifiedAt: liveMtime)
        }
        let before = try FileSnapshot.capture(canonicalSchedulePath)
        if let loadedSnapshot, loadedSnapshot.data != before.data {
            throw StaleScheduleError(loadedAt: loadedMtime, modifiedAt: liveMtime)
        }

        // 2. Compose YAML with header preservation (Task 7).
        // extractHeader reads everything before `\nslots:` from the canonical
        // file. A missing file or a file with no anchor falls back to the
        // header-less hand-rolled emit. A file that exists but can't be read
        // throws: we just loaded it, so a failing read is a reason to stop,
        // not to write over it without its header.
        let header = FileManager.default.fileExists(atPath: canonicalSchedulePath.path)
            ? try extractHeader(from: canonicalSchedulePath)
            : ""
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
            throw ScheduleValidationError(exitCode: validate.exitCode, output: stderr)
        }

        // 5. Atomic rename. Re-check the bytes first: `schedule validate` is a
        // subprocess, and a write that landed while it ran must not be
        // clobbered (C10). replaceItemAt consumes tmp on success, so the
        // defer becomes a no-op (removeItem on a missing file fails silently
        // because of the `try?`).
        if try FileSnapshot.capture(canonicalSchedulePath).data != before.data {
            throw StaleScheduleError(loadedAt: loadedMtime, modifiedAt: currentMtime())
        }
        _ = try FileManager.default.replaceItemAt(
            canonicalSchedulePath,
            withItemAt: tmp,
            backupItemName: nil,
            options: [.usingNewMetadataOnly]
        )

        // The undo baseline is the bytes renamed into place, not a re-read
        // after the reload below, which could already hold someone else's
        // edit.
        let change = VaultChange(actionName: actionName, before: [before],
                                 after: [FileSnapshot(url: canonicalSchedulePath, data: Data(body.utf8))])

        // 6. Reload + recapture mtime. The write has landed by now, so a
        // failed reload says so instead of passing for a failed write.
        do {
            try await loadAll()
        } catch {
            throw ScheduleReloadAfterWriteError(change: change, reason: error.localizedDescription)
        }
        return change
    }

    /// Undo or redo a save: put the schedule file's earlier (or later) bytes
    /// back, refused if the file changed since, then reload. Not committed:
    /// schedule saves aren't either. The restored bytes were valid when they
    /// were written, so they skip `schedule validate`.
    func restore(_ change: VaultChange, _ direction: UndoDirection) async throws {
        try await serialized {
            do {
                try await change.apply(direction, git: nil, repo: self.canonicalSchedulePath.deletingLastPathComponent())
            } catch {
                try? await self.loadAll()
                throw error
            }
            try await self.loadAll()
        }
    }

    /// Delete a slot by key and persist via the same atomic-write path as save.
    /// Throws if the key isn't in the current slot list.
    @discardableResult
    func delete(slotKey: String) async throws -> VaultChange? {
        guard loadState == .loaded else { throw ScheduleNotLoadedError() }
        guard slots.contains(where: { $0.key == slotKey }) else {
            throw NSError(
                domain: "ScheduleEditService.delete",
                code: 404,
                userInfo: [NSLocalizedDescriptionKey: "no such slot: \(slotKey)"]
            )
        }
        return try await serialized {
            let remaining = self.slots.filter { $0.key != slotKey }
            return try await self.performSave(allSlots: remaining, actionName: "Delete Schedule")
        }
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
            if let model = slot.model {
                out += "    model: \(yamlScalar(model))\n"
            }
            if let agentRuntime = slot.agentRuntime {
                out += "    agent_runtime: \(yamlScalar(agentRuntime))\n"
            }
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
