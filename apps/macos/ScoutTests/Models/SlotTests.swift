import XCTest
@testable import Scout

final class SlotTests: XCTestCase {
    func test_decode_from_scoutctl_list_json() throws {
        let json = """
        {
          "key": "morning-briefing",
          "type": "briefing",
          "runner": "run-scout.sh",
          "fires_at_local": "08:00",
          "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
          "missed_window_hours": 4,
          "on_miss": "fire",
          "cooldown_minutes": 60,
          "budget_usd": null,
          "tz": null,
          "runtime": "local"
        }
        """.data(using: .utf8)!
        let slot = try JSONDecoder().decode(Slot.self, from: json)
        XCTAssertEqual(slot.key, "morning-briefing")
        XCTAssertEqual(slot.type, .briefing)
        XCTAssertEqual(slot.runner, "run-scout.sh")
        XCTAssertEqual(slot.firesAtLocal, "08:00")
        XCTAssertEqual(slot.weekdays, ["Mon", "Tue", "Wed", "Thu", "Fri"])
        XCTAssertEqual(slot.missedWindowHours, 4)
        XCTAssertEqual(slot.onMiss, .fire)
        XCTAssertEqual(slot.cooldownMinutes, 60)
        XCTAssertNil(slot.budgetUsd)
        XCTAssertNil(slot.tz)
        XCTAssertEqual(slot.runtime, .local)
    }

    func test_decode_defaults_runtime_to_local_when_absent() throws {
        // Pre-Plan-6 vault YAMLs round-tripped through the engine emit no
        // runtime field — Swift must default to .local for compatibility.
        let json = """
        {
          "key": "s",
          "type": "briefing",
          "runner": "run-scout.sh",
          "fires_at_local": "08:00",
          "weekdays": ["Mon"],
          "missed_window_hours": 4,
          "on_miss": "fire",
          "cooldown_minutes": 60,
          "budget_usd": null,
          "tz": null
        }
        """.data(using: .utf8)!
        let slot = try JSONDecoder().decode(Slot.self, from: json)
        XCTAssertEqual(slot.runtime, .local)
        XCTAssertNil(slot.model, "engines before per-slot models send no model")
    }

    func test_decode_reads_model_and_null_model() throws {
        func decode(_ modelJSON: String) throws -> Slot {
            let json = """
            {"key": "s", "type": "consolidation", "runner": "run-scout.sh",
             "fires_at_local": "13:00", "weekdays": ["Mon"], "missed_window_hours": 2,
             "on_miss": "collapse", "cooldown_minutes": 90, "budget_usd": null, "tz": null,
             "runtime": "local"\(modelJSON)}
            """.data(using: .utf8)!
            return try JSONDecoder().decode(Slot.self, from: json)
        }
        XCTAssertEqual(try decode(#", "model": "sonnet""#).model, "sonnet")
        XCTAssertNil(try decode(#", "model": null"#).model)
    }

    func test_draft_round_trip_keeps_model() {
        let slot = Slot(key: "midday-consolidation", type: .consolidation, runner: "run-scout.sh",
                        firesAtLocal: "13:00", weekdays: ["Mon"], missedWindowHours: 2,
                        onMiss: .collapse, cooldownMinutes: 90, model: "sonnet")
        let draft = SlotDraft(from: slot)
        XCTAssertEqual(draft.model, "sonnet")
        XCTAssertEqual(draft.toSlot(), slot)
        XCTAssertFalse(draft.isDirty(against: slot))
    }

    func test_round_trip_encode_decode() throws {
        let original = Slot(
            key: "research",
            type: .research,
            runner: "run-research.sh",
            firesAtLocal: "14:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .skip,
            cooldownMinutes: 240,
            budgetUsd: 5.0,
            tz: "America/New_York",
            runtime: .local,
            model: "claude-sonnet-5-5"
        )
        let encoded = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(Slot.self, from: encoded)
        XCTAssertEqual(original, decoded)
    }
}
