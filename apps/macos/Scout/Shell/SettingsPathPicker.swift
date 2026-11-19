import AppKit
import SwiftUI

/// What a path setting must point at, and why a stored value can't be used
/// (UI audit #82). Pure, so the rules are unit-tested without a panel.
nonisolated enum SettingsPathCheck {
    enum Kind: Sendable { case folder, executable }

    /// The stored text as a file URL: `~` expands against `home`, as
    /// `AppState.resolveScoutDirectory` does. Blank or relative → nil.
    static func url(for path: String, home: URL) -> URL? {
        let raw = path.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw == "~" { return home }
        if raw.hasPrefix("~/") { return home.appendingPathComponent(String(raw.dropFirst(2))) }
        if raw.hasPrefix("/") { return URL(fileURLWithPath: raw) }
        return nil
    }

    /// Why `path` can't be used, in plain words, or nil when it's fine.
    /// Blank means "use the default" and is always fine.
    static func problem(for path: String, kind: Kind, home: URL,
                        fileManager: FileManager = .default) -> String? {
        let raw = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        guard let url = url(for: raw, home: home) else {
            return "Scout ignores this path because it isn't a full path. Choose the \(kind == .folder ? "folder" : "file") again."
        }
        var isDir: ObjCBool = false
        guard fileManager.fileExists(atPath: url.path, isDirectory: &isDir) else {
            return kind == .folder ? "This folder doesn't exist." : "No file is at this path."
        }
        switch kind {
        case .folder:
            return isDir.boolValue ? nil : "This is a file, not a folder."
        case .executable:
            if isDir.boolValue { return "This is a folder, not the claude program." }
            return fileManager.isExecutableFile(atPath: url.path) ? nil : "This file can't be run as a program."
        }
    }

    /// How a chosen URL is stored: under the home folder as `~/…`, so the
    /// setting reads the way the defaults beside it do.
    static func storedText(for url: URL, home: URL) -> String {
        let path = url.standardizedFileURL.path
        let homePath = home.standardizedFileURL.path
        if path == homePath { return "~" }
        if path.hasPrefix(homePath + "/") { return "~" + path.dropFirst(homePath.count) }
        return path
    }
}

/// A path setting as a chooser: the current path (or the default, dimmed),
/// "Choose…" for a standard open panel, and "Use Default" to clear it. It
/// replaces a free-text field, which accepted relative paths the app then
/// silently ignored (UI audit #82). A stored value that no longer works
/// says why below.
struct SettingsPathPicker: View {
    @Binding var path: String
    /// What blank means, shown dimmed while the setting is blank.
    let defaultLabel: String
    let kind: SettingsPathCheck.Kind
    /// The open panel's prompt, e.g. "Choose the Wishlist folder".
    let panelMessage: String
    var home: URL = FileManager.default.homeDirectoryForCurrentUser
    @Environment(\.settingsControlLabel) private var rowLabel

    private var isDefault: Bool { path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var problem: String? { SettingsPathCheck.problem(for: path, kind: kind, home: home) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(isDefault ? defaultLabel : path)
                    .font(DS.mono(12))
                    .foregroundStyle(isDefault ? DS.Ink.p4 : DS.Ink.p1)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
                    .padding(.horizontal, 10)
                    .frame(height: 30)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(DS.Paper.sunk)
                            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                    )
                    .accessibilityLabel(rowLabel ?? panelMessage)
                    .accessibilityValue(isDefault ? "Default, \(defaultLabel)" : path)
                Button("Choose…", action: choose)
                    .buttonStyle(.ds(.secondary, size: .small))
                    .accessibilityLabel("Choose \(rowLabel ?? "path")…")
                if !isDefault {
                    Button("Use Default") { path = "" }
                        .buttonStyle(.ds(.link, size: .small))
                        .accessibilityLabel("Use the default \(rowLabel ?? "path")")
                }
            }
            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.sans(11.5))
                    .foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.message = panelMessage
        panel.prompt = "Choose"
        panel.canChooseDirectories = kind == .folder
        panel.canChooseFiles = kind == .executable
        panel.canCreateDirectories = kind == .folder
        panel.allowsMultipleSelection = false
        // `claude` usually lives in ~/.local/bin, a hidden folder.
        panel.showsHiddenFiles = kind == .executable
        let current = SettingsPathCheck.url(for: isDefault ? defaultLabel : path, home: home)
        panel.directoryURL = kind == .folder ? current : current?.deletingLastPathComponent()
        guard panel.runModal() == .OK, let url = panel.url else { return }
        path = SettingsPathCheck.storedText(for: url, home: home)
    }
}
