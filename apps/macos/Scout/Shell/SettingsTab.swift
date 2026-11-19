import SwiftUI

/// The tabs of the Settings window (⌘,). Settings used to be one scroll
/// about 7,000 pt tall, shown both as a sidebar page and as this window
/// (UI audit #10, #82); it is now only this window, split by what people
/// come to change: everyday choices first, power-user paths last.
nonisolated enum SettingsTab: String, CaseIterable, Identifiable, Sendable {
    case general, engine, runs, actionItems, advanced, updates

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general:     return "General"
        case .engine:      return "Engine"
        case .runs:        return "Runs"
        case .actionItems: return "Action Items"
        case .advanced:    return "Advanced"
        case .updates:     return "Updates"
        }
    }

    var systemImage: String {
        switch self {
        case .general:     return "gearshape"
        case .engine:      return "engine.combustion"
        case .runs:        return "gauge.with.dots.needle.33percent"
        case .actionItems: return "checklist"
        case .advanced:    return "wrench.and.screwdriver"
        case .updates:     return "arrow.triangle.2.circlepath"
        }
    }

    /// The tab a `SettingsAnchor` lives on, so every deep link ("Open
    /// Settings ▸ Engine", the panel's "Update available") lands on its tab.
    init?(anchor: String) {
        switch anchor {
        case SettingsAnchor.engine:     self = .engine
        case SettingsAnchor.updates:    self = .updates
        case SettingsAnchor.authorship: self = .general
        default:                        return nil
        }
    }

    var accessibilityIdentifier: String { "settings-tab-\(rawValue)" }
}
