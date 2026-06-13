import Foundation

/// Which terminal Scout uses to open an interactive Claude Code CLI session
/// from an action item. Named cases are macOS-specific; `.custom` is the
/// platform-agnostic escape hatch (a user-supplied command template) and the
/// seam a future Linux/Windows port would extend.
enum CLITerminal: String, CaseIterable, Identifiable {
    case auto
    case terminalApp
    case iterm2
    case custom

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .auto:        return "Auto"
        case .terminalApp: return "Terminal.app"
        case .iterm2:      return "iTerm2"
        case .custom:      return "Custom command"
        }
    }
}

/// Injected into `ClaudeLauncher` so the launch logic stays a pure, testable
/// function of its inputs rather than reading `UserDefaults` directly.
struct CLIConfig: Equatable {
    let claudePathOverride: String   // "" = no override
    let terminal: CLITerminal
    let customCommand: String        // "" = none; used only when terminal == .custom

    static let auto = CLIConfig(claudePathOverride: "", terminal: .auto, customCommand: "")
}
