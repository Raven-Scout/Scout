import SwiftUI

/// The main window's sidebar: a native `List(selection:)` in the sidebar
/// style, so arrow keys move between pages, VoiceOver reads each row as a
/// selectable item with its badge, and the row highlight follows the system.
/// (It was a hand-built stack of buttons with none of that, C7.) The Go menu
/// (`GoCommands`) reaches the same pages, all but Health with ⌘1–⌘8.
///
/// The last row, Settings, isn't a page: it opens the Settings window (⌘,),
/// Scout's only Settings (UI audit #10), and keeps its update count and
/// attention mark so the sidebar still says when Settings needs a look.
struct SidebarView: View {
    @Binding var selection: SidebarItem
    @Environment(\.openSettings) private var openSettings
    /// Count of agent sessions that need you — drives the badge on the
    /// Sessions row. Hidden when zero.
    var sessionsBadge: Int = 0
    /// Count of proposals awaiting a decision — drives the badge on the
    /// Proposals row. Hidden when zero.
    var proposalsBadge: Int = 0
    /// Count of active wishlist items — drives the badge on the Wishlist row.
    var wishlistBadge: Int = 0
    /// Count of active research topics — drives the badge on the Research row.
    var researchBadge: Int = 0
    /// Number of update tracks (app, plugin) with an update available — drives
    /// the badge on the Settings row; it opens Settings ▸ Updates.
    var settingsBadge: Int = 0
    /// True when the engine needs the user's attention (spec §5) — draws a
    /// warning symbol on the Settings row, labelled for VoiceOver.
    var settingsAttention: Bool = false
    /// True when run health is red — draws the warning dot on the Health row.
    var healthAttention: Bool = false
    /// True while the engine gates the tabs (spec §5): every page has nothing
    /// to show, so its row renders dimmed — still selectable (it lands on the
    /// engine gate). The Settings row stays live and undimmed.
    var tabsGated: Bool = false

    var body: some View {
        List(selection: listSelection) {
            Section("Scout") {
                ForEach(SidebarItem.allCases, id: \.self) { item in
                    row(item)
                }
            }
            Section("App") {
                settingsRow
            }
        }
        .listStyle(.sidebar)
        .accessibilityIdentifier("sidebar")
    }

    /// `List` selects through an optional; the window always shows a page,
    /// so a deselect (clicking empty space) keeps the current one.
    private var listSelection: Binding<SidebarItem?> {
        Binding(get: { selection },
                set: { if let item = $0 { selection = item } })
    }

    /// Pure dimming rule: every page while gated. (The Settings row, which
    /// opens a window rather than a page, never dims.)
    nonisolated static func isDimmed(_ item: SidebarItem, tabsGated: Bool) -> Bool {
        tabsGated
    }

    /// Identifier of the Settings row, for UI tests.
    nonisolated static let settingsRowIdentifier = "sidebar-settings"

    /// What VoiceOver says for the Settings row beyond its title.
    nonisolated static func settingsAccessibilityValue(updates: Int, attention: Bool) -> String {
        var parts: [String] = []
        if updates > 0 { parts.append("\(updates) update\(updates == 1 ? "" : "s") available") }
        if attention { parts.append("needs attention") }
        return parts.joined(separator: ", ")
    }

    /// The badge count a row shows (0 = none).
    func badge(for item: SidebarItem) -> Int {
        switch item {
        case .sessions:  return sessionsBadge
        case .proposals: return proposalsBadge
        case .wishlist:  return wishlistBadge
        case .research:  return researchBadge
        default:         return 0
        }
    }

    /// What VoiceOver says for a row beyond its title: the badge, the
    /// attention state and the gate. Pure, so it can be unit-tested.
    nonisolated static func accessibilityValue(for item: SidebarItem, badge: Int,
                                               attention: Bool, tabsGated: Bool) -> String {
        var parts: [String] = []
        if badge > 0 { parts.append(item.badgeDescription(badge)) }
        if attention { parts.append("needs attention") }
        if isDimmed(item, tabsGated: tabsGated) { parts.append("unavailable until Scout is set up") }
        return parts.joined(separator: ", ")
    }

    /// Whether a page row carries the attention mark: Health when run
    /// health is red. (The Settings row has its own, `settingsAttention`.)
    private func hasAttention(_ item: SidebarItem) -> Bool {
        item == .health && healthAttention
    }

    /// Opens the Settings window: at Updates when an update is waiting, at
    /// Engine when the engine needs the user, else where it was.
    private var settingsRow: some View {
        Button {
            if settingsBadge > 0 {
                HelpActions.showSettings(openSettings, at: SettingsAnchor.updates)
            } else if settingsAttention {
                HelpActions.showEngineSettings(openSettings)
            } else {
                openSettings()
            }
        } label: {
            Label {
                HStack(spacing: 6) {
                    Text("Settings…")
                    if settingsAttention {
                        Image(systemName: "exclamationmark.circle.fill")
                            .foregroundStyle(DS.Status.warn)
                            .help("Needs attention")
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            } icon: {
                Image(systemName: "gearshape")
            }
        }
        .buttonStyle(.plainHit) // ds-guard: hit area — a sidebar row, drawn like its neighbours
        .badge(settingsBadge)
        .help("Open Settings (⌘,)")
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Settings")
        .accessibilityValue(Self.settingsAccessibilityValue(updates: settingsBadge, attention: settingsAttention))
        .accessibilityHint("Opens the Settings window")
        .accessibilityIdentifier(Self.settingsRowIdentifier)
    }

    private func row(_ item: SidebarItem) -> some View {
        let attention = hasAttention(item)
        let count = badge(for: item)
        return Label {
            HStack(spacing: 6) {
                Text(item.title)
                if attention {
                    // A symbol, not a colour-only dot, so it reads without colour too.
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(DS.Status.err)
                        .help("Needs attention")
                }
            }
        } icon: {
            Image(systemName: item.systemImage)
        }
        .badge(count)
        .opacity(Self.isDimmed(item, tabsGated: tabsGated) ? 0.5 : 1)
        .tag(item)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(item.title)
        .accessibilityValue(Self.accessibilityValue(for: item, badge: count,
                                                    attention: attention, tabsGated: tabsGated))
        .accessibilityIdentifier(item.accessibilityIdentifier)
    }
}
