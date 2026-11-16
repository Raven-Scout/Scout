import SwiftUI
import AppKit

/// Where Help ▸ … links go (app audit C11).
enum HelpLink: CaseIterable {
    case help, repository, privacy, troubleshooting, reportIssue

    var title: String {
        switch self {
        case .help: return "Scout Help"
        case .repository: return "Scout on GitHub"
        case .privacy: return "Privacy Policy"
        case .troubleshooting: return "Troubleshooting Guide"
        case .reportIssue: return "Report an Issue…"
        }
    }

    var url: URL {
        switch self {
        case .help: return URL(string: "https://raven-scout.github.io/Scout/")!
        case .repository: return URL(string: "https://github.com/Raven-Scout/Scout")!
        case .privacy: return URL(string: "https://raven-scout.github.io/Scout/privacy.html")!
        case .troubleshooting:
            return URL(string: "https://github.com/Raven-Scout/Scout/blob/main/plugin/README.md#faq--troubleshooting")!
        case .reportIssue: return URL(string: "https://github.com/Raven-Scout/Scout/issues/new")!
        }
    }
}

/// Window ids for Help's in-app pages.
enum HelpWindow {
    static let doctor = "doctor"
    static let diagnostics = "diagnostics"
}

extension Notification.Name {
    /// Scroll an open Settings view to its Engine section.
    static let scoutShowEngineSettings = Notification.Name("scout.showEngineSettings")
}

/// Scroll targets inside Settings.
enum SettingsAnchor {
    static let engine = "settings-engine"
}

/// The actions Help ▸ … and Settings ▸ Engine ▸ Troubleshooting share, so the
/// two places name and do the same things.
enum HelpActions {
    /// Set when Engine Settings… opens a Settings window that hasn't appeared yet.
    private static var engineSettingsRequested = false

    /// Open Settings at its Engine section.
    static func showEngineSettings(_ openSettings: OpenSettingsAction) {
        engineSettingsRequested = true
        openSettings()
        NotificationCenter.default.post(name: .scoutShowEngineSettings, object: nil)
    }

    /// Whether Engine Settings… asked for the Engine section; reading it clears it.
    static func takeEngineSettingsRequest() -> Bool {
        defer { engineSettingsRequested = false }
        return engineSettingsRequested
    }

    /// `<vault>/.scout-logs` when it exists, else the vault itself.
    static func logsFolder(vault: URL, fileManager: FileManager = .default) -> URL {
        let logs = vault.appendingPathComponent(".scout-logs", isDirectory: true)
        var isDir: ObjCBool = false
        return fileManager.fileExists(atPath: logs.path, isDirectory: &isDir) && isDir.boolValue ? logs : vault
    }

    static func showLogsFolder(vault: URL) {
        NSWorkspace.shared.open(logsFolder(vault: vault))
    }
}

/// The Help menu (replaces the system's "Help isn't available" item).
struct HelpCommands: View {
    let vault: URL
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openURL) private var openURL
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        link(.help)
        link(.repository)
        link(.privacy)
        Divider()
        Button("Run Doctor…") { openWindow(id: HelpWindow.doctor) }
        Button("Show Logs Folder") { HelpActions.showLogsFolder(vault: vault) }
        Button("Export Diagnostics…") { openWindow(id: HelpWindow.diagnostics) }
        Divider()
        Section("Troubleshooting") {
            link(.troubleshooting)
            Button("Engine Settings…") { HelpActions.showEngineSettings(openSettings) }
            link(.reportIssue)
        }
    }

    private func link(_ link: HelpLink) -> some View {
        Button(link.title) { openURL(link.url) }
    }
}

/// Settings ▸ Engine ▸ Troubleshooting: the same three actions as the Help menu.
struct TroubleshootingRow: View {
    let vault: URL
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        SettingsRow(title: "Troubleshooting",
                    help: "The same tools as the Help menu. Diagnostics never include your notes.") {
            HStack(spacing: 10) {
                Button("Run Doctor…") { openWindow(id: HelpWindow.doctor) }
                Button("Show Logs Folder") { HelpActions.showLogsFolder(vault: vault) }
                Button("Export Diagnostics…") { openWindow(id: HelpWindow.diagnostics) }
            }
            .buttonStyle(.plainHit)
            .font(DS.sans(12))
        }
    }
}
