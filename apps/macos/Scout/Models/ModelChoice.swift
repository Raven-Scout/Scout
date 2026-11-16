import Foundation

/// A slot's model as the editor shows it. The engine stores a slot's model as
/// an optional string (`nil` = inherit scout-config's `models.default`); this
/// maps that string onto the picker's options without losing a full model ID
/// someone wrote into schedule.yaml by hand.
///
/// Source of truth for precedence and allowed values: engine/scout/models.py.
enum ModelChoice: Hashable, Sendable {
    /// No `model:` on the slot: it runs on the default.
    case inherit
    case opus
    case sonnet
    case haiku
    /// A full `claude-…` ID set outside the app. Shown, kept, never offered.
    case custom(String)

    init(model: String?) {
        switch model?.trimmingCharacters(in: .whitespaces).lowercased() {
        case nil, "": self = .inherit
        case "opus": self = .opus
        case "sonnet": self = .sonnet
        case "haiku": self = .haiku
        case let other?: self = .custom(other)
        }
    }

    /// What goes back into `Slot.model`.
    var model: String? {
        switch self {
        case .inherit: return nil
        case .opus: return "opus"
        case .sonnet: return "sonnet"
        case .haiku: return "haiku"
        case .custom(let id): return id
        }
    }

    /// Short display name: "Opus", "Sonnet", "Haiku", or the custom ID itself.
    static func displayName(for model: String) -> String {
        switch ModelChoice(model: model) {
        case .opus: return "Opus"
        case .sonnet: return "Sonnet"
        case .haiku: return "Haiku"
        case .custom(let id): return id
        case .inherit: return model
        }
    }

    /// The picker's options for a slot, in order. `defaultModel` labels the
    /// inherit option ("Default (Opus)"); a custom ID already on the slot is
    /// appended so the current value is always selectable.
    static func slotOptions(defaultModel: String, current: ModelChoice) -> [(label: String, value: ModelChoice)] {
        var options: [(label: String, value: ModelChoice)] = [
            ("Default (\(displayName(for: defaultModel)))", .inherit),
            ("Opus", .opus),
            ("Sonnet", .sonnet),
            ("Haiku", .haiku),
        ]
        if case .custom(let id) = current {
            options.append((id, current))
        }
        return options
    }

    /// The Settings default picker's options: the three aliases, plus the
    /// current value when it is a custom ID (there is no "inherit" for the default).
    static func defaultOptions(current: ModelChoice) -> [(label: String, value: ModelChoice)] {
        var options: [(label: String, value: ModelChoice)] = [("Opus", .opus), ("Sonnet", .sonnet), ("Haiku", .haiku)]
        if case .custom(let id) = current {
            options.append((id, current))
        }
        return options
    }
}

/// `scoutctl model show --json`. Key names follow engine/scout/cli.py's
/// `cli_model_show`, whose test (`test_show_json_lists_default_and_every_slot`)
/// pins this exact shape.
struct ModelSettings: Codable, Equatable, Sendable {
    struct Resolved: Codable, Equatable, Sendable {
        let model: String
        /// env | slot | config | builtin
        let source: String
    }

    struct SlotModel: Codable, Equatable, Sendable {
        let key: String
        let model: String
        let source: String
    }

    let `default`: Resolved
    let slots: [SlotModel]
    let aliases: [String]

    /// True when the default comes from the built-in fallback, not a choice the
    /// user made in scout-config.yaml.
    var defaultIsBuiltIn: Bool { self.default.source == "builtin" }
}
