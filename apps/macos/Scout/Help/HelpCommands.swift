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
    /// Scroll an open Settings view to a section; `object` is its
    /// `SettingsAnchor` id.
    static let scoutShowSettingsSection = Notification.Name("scout.showSettingsSection")
}

/// Scroll targets inside Settings.
enum SettingsAnchor {
    static let engine = "settings-engine"
    /// The panel's "Update available" opens here (audit #93).
    static let updates = "settings-updates"
}

/// The actions Help ▸ … and Settings ▸ Engine ▸ Troubleshooting share, so the
/// two places name and do the same things.
enum HelpActions {
    /// Which Settings section something last asked for, and when. A Settings view that appears soon
    /// after scrolls there; one opened later (⌘, a minute on) opens at the top as usual. An open Settings
    /// view scrolls on the notification instead. The request expires rather than being cleared by whichever
    /// view sees it first, so the main window's Settings page can't swallow a new window's request:
    /// every Settings view that appears within the window scrolls to the section.
    private static var settingsRequest: (anchor: String, at: Date)?
    static let engineSettingsRequestWindow: TimeInterval = 2

    /// Open the Settings window at its Engine section.
    static func showEngineSettings(_ openSettings: OpenSettingsAction) {
        showEngineSettings { openSettings() }
    }

    /// Show Settings at its Engine section, however `open` shows it: the
    /// Settings window, or the main window's Settings page. Every "Open
    /// Settings ▸ Engine" button goes through here, so none opens at the top.
    static func showEngineSettings(_ open: () -> Void, now: Date = Date()) {
        showSettings(at: SettingsAnchor.engine, open, now: now)
    }

    /// Open the Settings window at one of its sections (a `SettingsAnchor` id).
    static func showSettings(_ openSettings: OpenSettingsAction, at anchor: String) {
        showSettings(at: anchor) { openSettings() }
    }

    /// Show Settings at `anchor`'s section, however `open` shows it. Records
    /// the request first, so a Settings view that appears during `open`
    /// sees it, then tells any open one to scroll.
    static func showSettings(at anchor: String, _ open: () -> Void, now: Date = Date()) {
        settingsRequest = (anchor, now)
        open()
        NotificationCenter.default.post(name: .scoutShowSettingsSection, object: anchor)
    }

    /// The section something asked for just now, if any. Reading it doesn't
    /// clear it: it only expires, so a second Settings view appearing in the
    /// same moment still sees it.
    static func settingsRequested(now: Date = Date()) -> String? {
        guard let request = settingsRequest, now.timeIntervalSince(request.at) <= engineSettingsRequestWindow else { return nil }
        return request.anchor
    }

    /// Whether something asked for the Engine section just now (see `settingsRequested`).
    static func engineSettingsRequested(now: Date = Date()) -> Bool {
        settingsRequested(now: now) == SettingsAnchor.engine
    }

    /// For tests: record a request made at `date`.
    static func recordEngineSettingsRequest(at date: Date) { recordSettingsRequest(SettingsAnchor.engine, at: date) }
    static func recordSettingsRequest(_ anchor: String, at date: Date) { settingsRequest = (anchor, date) }

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
