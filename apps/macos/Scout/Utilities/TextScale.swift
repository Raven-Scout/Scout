import SwiftUI
import Observation

/// The app-wide text size, the one knob every `DS` font goes through.
///
/// macOS has no Dynamic Type for third-party apps (`DynamicTypeSize` and the
/// `Font.TextStyle` sizes don't move with any system setting), so Scout
/// offers its own: Settings ▸ General ▸ Text size. `DS.sans`/`serif`/`mono`
/// and `DS.font(_:)` multiply their design size by `factor`.
///
/// `@Observable`: a view's `body` that builds a `DS` font reads `size`, so
/// changing the setting re-renders exactly those views, in every window, with
/// no relaunch and no loss of view state.
@Observable
final class TextScale {
    /// The instance the design system reads. Settings writes it.
    static let shared = TextScale()

    /// `UserDefaults` key holding `Size.rawValue`.
    nonisolated static let defaultsKey = "textSize"

    nonisolated enum Size: String, CaseIterable, Identifiable, Sendable {
        case standard, large, larger, largest

        var id: String { rawValue }

        /// Multiplier on every design point size.
        var factor: CGFloat {
            switch self {
            case .standard: return 1
            case .large:    return 1.12
            case .larger:   return 1.25
            case .largest:  return 1.4
            }
        }

        var title: String {
            switch self {
            case .standard: return "Standard"
            case .large:    return "Large"
            case .larger:   return "Larger"
            case .largest:  return "Largest"
            }
        }
    }

    var size: Size {
        didSet {
            guard size != oldValue else { return }
            defaults.set(size.rawValue, forKey: Self.defaultsKey)
        }
    }

    var factor: CGFloat { size.factor }

    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.size = Size(rawValue: defaults.string(forKey: Self.defaultsKey) ?? "") ?? .standard
    }
}
