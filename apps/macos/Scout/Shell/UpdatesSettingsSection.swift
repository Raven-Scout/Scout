import SwiftUI
import AppKit

/// Settings ▸ Updates. Two independent tracks: Scout.app (Sparkle owns
/// detection + installation; we only mirror what it reports) and the plugin
/// (detect + hand off — the app can't apply a plugin update itself, so the
/// primary action copies `/scout-update` for Claude Code). The plugin row
/// only shows for engines Scout.app doesn't manage — see
/// `UpdateService.pluginRowVisible`.
struct UpdatesSettingsSection: View {
    @EnvironmentObject private var updates: UpdateService

    var body: some View {
        SettingsCard {
            UpdateTrackRow(
                title: "Scout.app",
                status: updates.appUpdate,
                disabledNote: updates.appUpdatesEnabled ? nil
                    : "Automatic updates are disabled in development builds.",
                primaryTitle: updates.appUpdate.isAvailable ? "Install…" : nil,
                primaryAction: { updates.check(.app) },      // Sparkle shows the update dialog
                checkAction: { updates.check(.app) }
            )
            if updates.pluginRowVisible {
                UpdateTrackRow(
                    title: "Plugin (not managed by Scout.app)",
                    status: updates.pluginUpdate,
                    disabledNote: nil,
                    primaryTitle: updates.pluginUpdate.isAvailable ? "Copy /scout-update" : nil,
                    primaryAction: copyPluginUpdateCommand,
                    checkAction: { updates.check(.plugin) },
                    footnote: updates.pluginUpdate.isAvailable
                        ? "Paste it into Claude Code to update the plugin. Scout.app updates only the engines it manages."
                        : nil,
                    linkTitle: updates.pluginReleasesURL != nil ? "What's new" : nil,
                    linkAction: { if let url = updates.pluginReleasesURL { NSWorkspace.shared.open(url) } }
                )
            }
        }
    }

    private func copyPluginUpdateCommand() {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(UpdateService.pluginUpdateCommand, forType: .string)
    }
}

// MARK: - Row

/// Pure text logic for `UpdateTrackRow`'s version line, pulled out of the
/// (file-private) row view so it can be unit-tested without standing up a
/// view hierarchy.
enum UpdateRowFormatting {
    static func versionLine(currentVersion: String?, latestVersion: String?, state: UpdateStatus.State) -> String {
        let current = currentVersion ?? "—"
        switch state {
        case .available:
            return "\(current) → \(latestVersion ?? "?") available"
        case .upToDate:
            return "\(current) · up to date"
        case .checking:
            return "\(current) · checking…"
        case .error:
            return "\(current) · last check failed"
        case .idle:
            return current
        }
    }
}

/// One update track: title, `current → latest`, a state chip, an optional
/// primary action (Install… / Copy /scout-update), and Check now.
private struct UpdateTrackRow: View {
    let title: String
    let status: UpdateStatus
    let disabledNote: String?
    let primaryTitle: String?
    let primaryAction: () -> Void
    let checkAction: () -> Void
    var footnote: String? = nil
    var linkTitle: String? = nil
    var linkAction: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 8) {
                        Text(title)
                            .font(DS.sans(13, weight: .medium))
                            .foregroundStyle(DS.Ink.p1)
                        chip
                    }
                    Text(disabledNote ?? versionLine)
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                    if let footnote {
                        Text(footnote)
                            .font(DS.sans(11.5))
                            .foregroundStyle(DS.Ink.p3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if disabledNote == nil {
                    HStack(spacing: 8) {
                        if let linkTitle {
                            Button(linkTitle, action: linkAction).buttonStyle(.link)
                        }
                        if let primaryTitle {
                            Button(primaryTitle, action: primaryAction)
                        }
                        Button("Check now", action: checkAction)
                            .disabled(status.state == .checking)
                    }
                }
            }
            .padding(.vertical, 14)
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5).opacity(0.6)
        }
    }

    private var versionLine: String {
        UpdateRowFormatting.versionLine(currentVersion: status.currentVersion, latestVersion: status.latestVersion, state: status.state)
    }

    @ViewBuilder private var chip: some View {
        switch status.state {
        case .available: UpdateChip(text: "Update available", color: DS.Status.warn)
        case .upToDate:  UpdateChip(text: "Up to date",       color: DS.Status.ok)
        case .checking:  UpdateChip(text: "Checking…",        color: DS.Ink.p3)
        case .error:     UpdateChip(text: "Couldn't check",   color: DS.Status.err)
        case .idle:      EmptyView()
        }
    }
}

private struct UpdateChip: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(DS.sans(10.5, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(color.opacity(0.12)))
    }
}
