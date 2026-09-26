import Testing
import Foundation
@testable import Scout

@Suite("ScheduleEditService — E2E (opt-in)")
struct ScheduleEditE2ETest {

    @Test("round-trip edit and revert against real vault")
    @MainActor
    func test_round_trip_edit_and_revert_against_real_vault() async throws {
        guard let vault = ProcessInfo.processInfo.environment["SCOUT_DATA_DIR"] else {
            // Opt-in only — skip silently when the env var is not set.
            // CI never sets SCOUT_DATA_DIR, so this test is a no-op there.
            return
        }

        let canonical = URL(fileURLWithPath: vault)
            .appendingPathComponent(".scout-state")
            .appendingPathComponent("schedule.yaml")

        guard FileManager.default.fileExists(atPath: canonical.path) else {
            return  // Vault exists but has no schedule.yaml; nothing to test.
        }

        let originalText = try String(contentsOf: canonical, encoding: .utf8)

        let scoutctl = URL(fileURLWithPath: "/usr/bin/env")
        let runner: any ProcessRunner = SystemProcessRunner()
        let service = ScheduleEditService(
            scoutctl: scoutctl,
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"]
        )

        try await service.loadAll()

        guard let target = service.slots.first(where: { $0.key == "morning-briefing" }) else {
            return  // Vault has no morning-briefing slot; nothing to test.
        }
        let originalCooldown = target.cooldownMinutes

        // ── Phase 1: write sentinel value, reload, assert it persisted ──────

        var bumped = service.slots
        if let idx = bumped.firstIndex(where: { $0.key == "morning-briefing" }) {
            bumped[idx] = Slot(
                key: target.key,
                type: target.type,
                runner: target.runner,
                firesAtLocal: target.firesAtLocal,
                weekdays: target.weekdays,
                missedWindowHours: target.missedWindowHours,
                onMiss: target.onMiss,
                cooldownMinutes: 999_999,
                budgetUsd: target.budgetUsd,
                tz: target.tz,
                runtime: target.runtime
            )
        }
        try await service.save(allSlots: bumped)
        try await service.loadAll()
        #expect(
            service.slots.first(where: { $0.key == "morning-briefing" })?.cooldownMinutes == 999_999,
            "cooldown should be sentinel value after first save"
        )

        // ── Phase 2: revert to original cooldown, reload, assert restored ───

        var restored = service.slots
        if let idx = restored.firstIndex(where: { $0.key == "morning-briefing" }) {
            let cur = restored[idx]
            restored[idx] = Slot(
                key: cur.key,
                type: cur.type,
                runner: cur.runner,
                firesAtLocal: cur.firesAtLocal,
                weekdays: cur.weekdays,
                missedWindowHours: cur.missedWindowHours,
                onMiss: cur.onMiss,
                cooldownMinutes: originalCooldown,
                budgetUsd: cur.budgetUsd,
                tz: cur.tz,
                runtime: cur.runtime
            )
        }
        try await service.save(allSlots: restored)
        try await service.loadAll()
        #expect(
            service.slots.first(where: { $0.key == "morning-briefing" })?.cooldownMinutes == originalCooldown,
            "cooldown should be restored to original after revert save"
        )

        // ── Phase 3: sanity-check header survived the round trip ─────────────

        let afterText = try String(contentsOf: canonical, encoding: .utf8)
        let beforeHeader = originalText.components(separatedBy: "\nslots:").first ?? ""
        let afterHeader = afterText.components(separatedBy: "\nslots:").first ?? ""
        #expect(
            beforeHeader == afterHeader,
            "YAML header (everything before \\nslots:) should survive a round-trip save"
        )
    }
}
