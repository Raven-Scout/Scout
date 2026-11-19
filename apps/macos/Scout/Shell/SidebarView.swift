import SwiftUI

/// The main window's sidebar: a native `List(selection:)` in the sidebar
/// style, so arrow keys move between pages, VoiceOver reads each row as a
/// selectable item with its badge, and the row highlight follows the system.
/// (It was a hand-built stack of buttons with none of that, C7.) The Go menu
/// (`GoCommands`) reaches the same pages, ⌘1–⌘9 in this order (Settings: ⌘,).
struct SidebarView: View {
    @Binding var selection: SidebarItem
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
    /// the badge on the Settings row, where Settings ▸ Updates lives.
    var settingsBadge: Int = 0
    /// True when the engine needs the user's attention (spec §5) — draws a
    /// warning symbol on the Settings row, labelled for VoiceOver.
    var settingsAttention: Bool = false
    /// True when run health is red — draws the warning dot on the Health row.
    var healthAttention: Bool = false
    /// True while the engine gates the tabs (spec §5): every row but Settings
    /// has nothing to show, so it renders dimmed — still selectable (it lands
    /// on the engine gate). Settings stays live and undimmed.
    var tabsGated: Bool = false

    var body: some View {
        List(selection: listSelection) {
            Section("Scout") {
                ForEach(SidebarItem.allCases.filter { $0 != .settings }, id: \.self) { item in
                    row(item)
                }
            }
            Section("App") {
                row(.settings)
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

    /// Pure dimming rule: only while gated, and never the Settings row.
    nonisolated static func isDimmed(_ item: SidebarItem, tabsGated: Bool) -> Bool {
        tabsGated && item != .settings
    }

    /// The badge count a row shows (0 = none).
    func badge(for item: SidebarItem) -> Int {
        switch item {
        case .sessions:  return sessionsBadge
        case .proposals: return proposalsBadge
        case .wishlist:  return wishlistBadge
        case .research:  return researchBadge
        case .settings:  return settingsBadge
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

    /// Whether a row carries the attention mark: Settings when the engine
    /// needs the user, Health when run health is red.
    private func hasAttention(_ item: SidebarItem) -> Bool {
        switch item {
        case .settings: return settingsAttention
        case .health:   return healthAttention
        default:        return false
        }
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
                        .foregroundStyle(item == .health ? DS.Status.err : DS.Status.warn)
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
