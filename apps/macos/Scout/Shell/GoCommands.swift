import SwiftUI

// MARK: - Page metadata

/// Display data for each main-window page, shared by the sidebar, the Go
/// menu and VoiceOver, so the three can't drift apart.
nonisolated extension SidebarItem {
    var title: String {
        switch self {
        case .controlCenter: return "Control Center"
        case .health:        return "Health"
        case .sessions:      return "Sessions"
        case .actionItems:   return "Action Items"
        case .schedules:     return "Schedules"
        case .proposals:     return "Proposals"
        case .wishlist:      return "Wishlist"
        case .research:      return "Research"
        case .knowledgeBase: return "Knowledge Base"
        }
    }

    var systemImage: String {
        switch self {
        case .controlCenter: return "chart.bar.doc.horizontal"
        case .health:        return "heart.text.square"
        case .sessions:      return "rectangle.stack.badge.person.crop"
        case .actionItems:   return "checklist"
        case .schedules:     return "calendar.badge.clock"
        case .proposals:     return "lightbulb"
        case .wishlist:      return "star"
        case .research:      return "magnifyingglass"
        case .knowledgeBase: return "books.vertical"
        }
    }

    /// The Go menu's ⌘-number. Spelled out per page, not derived from the
    /// case order: there are only nine digits, so a new page has to be given
    /// one (or `nil`, no shortcut) on purpose — this switch won't compile
    /// until it is. `AccessibilityFoundationTests` checks the keys stay unique.
    var goShortcutKey: Character? {
        switch self {
        case .controlCenter: return "1"
        case .health:        return nil   // no free key; Go ▸ Health is a menu item only
        case .sessions:      return "2"
        case .actionItems:   return "3"
        case .schedules:     return "4"
        case .proposals:     return "5"
        case .wishlist:      return "6"
        case .research:      return "7"
        case .knowledgeBase: return "8"
        }
    }

    /// Stable identifier for UI tests: `sidebar-<page>`.
    var accessibilityIdentifier: String {
        switch self {
        case .controlCenter: return "sidebar-control-center"
        case .health:        return "sidebar-health"
        case .sessions:      return "sidebar-sessions"
        case .actionItems:   return "sidebar-action-items"
        case .schedules:     return "sidebar-schedules"
        case .proposals:     return "sidebar-proposals"
        case .wishlist:      return "sidebar-wishlist"
        case .research:      return "sidebar-research"
        case .knowledgeBase: return "sidebar-knowledge-base"
        }
    }

    /// How VoiceOver reads a row's badge ("3 need you", "1 update").
    func badgeDescription(_ count: Int) -> String {
        switch self {
        case .sessions:  return "\(count) need\(count == 1 ? "s" : "") you"
        case .proposals: return "\(count) awaiting a decision"
        case .wishlist, .research: return "\(count) active"
        default:         return "\(count)"
        }
    }
}

// MARK: - Go menu

/// The Go menu: one item per page with ⌘1–⌘8, like Finder's Go menu
/// (Settings is the app menu's ⌘,, not a page). It
/// fronts the main window (reopening it if it was closed) and switches the
/// sidebar through `AppState.requestedSidebar`, the same path the menu-bar
/// panel already uses.
struct GoCommands: Commands {
    let appState: AppState

    var body: some Commands {
        CommandMenu("Go") {
            GoMenuItems(appState: appState)
        }
    }
}

private struct GoMenuItems: View {
    let appState: AppState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        ForEach(SidebarItem.allCases, id: \.self) { item in
            Button(item.title) {
                appState.requestedSidebar = item
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)   // as the menu-bar panel does
            }
            .keyboardShortcut(item.goShortcutKey.map { KeyboardShortcut(KeyEquivalent($0), modifiers: .command) })
        }
    }
}
