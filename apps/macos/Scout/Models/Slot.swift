import Foundation

/// Swift mirror of the engine's Slot dataclass. Decoded from
/// `scoutctl schedule list --json` (snake_case keys); encoded back to
/// snake_case for round-trip tests + future `runtime` field shape parity.
///
/// Source of truth: engine/scout/schedule.py::Slot. Keep field set in sync.
struct Slot: Identifiable, Equatable, Hashable, Sendable, Codable {
    let key: String
    let type: SlotType
    let runner: String
    let firesAtLocal: String
    let weekdays: [String]
    let missedWindowHours: Int
    let onMiss: OnMissPolicy
    let cooldownMinutes: Int
    let budgetUsd: Double?
    let tz: String?
    let runtime: SlotRuntime
    /// The Claude model this slot runs on (`opus`, `sonnet`, `haiku` or a full
    /// `claude-…` ID). nil = inherit scout-config's `models.default`. Must
    /// round-trip through the YAML emitter, or saving any slot would drop it.
    let model: String?

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key
        case type
        case runner
        case firesAtLocal = "fires_at_local"
        case weekdays
        case missedWindowHours = "missed_window_hours"
        case onMiss = "on_miss"
        case cooldownMinutes = "cooldown_minutes"
        case budgetUsd = "budget_usd"
        case tz
        case runtime
        case model
    }

    init(
        key: String,
        type: SlotType,
        runner: String,
        firesAtLocal: String,
        weekdays: [String],
        missedWindowHours: Int,
        onMiss: OnMissPolicy,
        cooldownMinutes: Int,
        budgetUsd: Double? = nil,
        tz: String? = nil,
        runtime: SlotRuntime = .local,
        model: String? = nil
    ) {
        self.key = key
        self.type = type
        self.runner = runner
        self.firesAtLocal = firesAtLocal
        self.weekdays = weekdays
        self.missedWindowHours = missedWindowHours
        self.onMiss = onMiss
        self.cooldownMinutes = cooldownMinutes
        self.budgetUsd = budgetUsd
        self.tz = tz
        self.runtime = runtime
        self.model = model
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.key = try c.decode(String.self, forKey: .key)
        self.type = try c.decode(SlotType.self, forKey: .type)
        self.runner = try c.decode(String.self, forKey: .runner)
        self.firesAtLocal = try c.decode(String.self, forKey: .firesAtLocal)
        self.weekdays = try c.decode([String].self, forKey: .weekdays)
        self.missedWindowHours = try c.decode(Int.self, forKey: .missedWindowHours)
        self.onMiss = try c.decode(OnMissPolicy.self, forKey: .onMiss)
        self.cooldownMinutes = try c.decode(Int.self, forKey: .cooldownMinutes)
        self.budgetUsd = try c.decodeIfPresent(Double.self, forKey: .budgetUsd)
        self.tz = try c.decodeIfPresent(String.self, forKey: .tz)
        // Forward-compat: default to .local when the engine omits the field.
        self.runtime = try c.decodeIfPresent(SlotRuntime.self, forKey: .runtime) ?? .local
        // Absent on engines before per-slot models, and null when the slot inherits.
        self.model = try c.decodeIfPresent(String.self, forKey: .model)
    }
}

enum SlotType: String, CaseIterable, Codable, Sendable {
    case briefing
    case consolidation
    case dreaming
    case research
    case manual
}

enum OnMissPolicy: String, CaseIterable, Codable, Sendable {
    case fire
    case skip
    case collapse
}

enum SlotRuntime: String, CaseIterable, Codable, Sendable {
    case local
    case remote  // Reserved for Plan 7. UI renders disabled.
}
