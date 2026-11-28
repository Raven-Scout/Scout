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
        case .changes:       return "Changes"
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
        case .changes:       return "chart.xyaxis.line"
        case .research:      return "magnifyingglass"
        case .knowledgeBase: return "books.vertical"
        }
    }

    /// The Go menu's ⌘-number: the page's place in the sidebar's Scout
    /// section, so ⌘3 is the third row. Settings isn't a page: the App
    /// section's Settings row opens the ⌘, window (UI audit #10). Spelled
    /// out per page, not derived from the case order: there are only nine
    /// digits, so a new page has to be given
    /// one (or `nil`, no shortcut) on purpose — this switch won't compile
    /// until it is. `AccessibilityFoundationTests` checks the keys stay unique
    /// and in sidebar order.
    var goShortcutKey: Character? {
        switch self {
        case .controlCenter: return "1"
        case .health:        return "2"
        case .sessions:      return "3"
        case .actionItems:   return "4"
        case .schedules:     return "5"
        case .changes:       return "6"
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
        case .changes:       return "sidebar-changes"
        case .research:      return "sidebar-research"
        case .knowledgeBase: return "sidebar-knowledge-base"
        }
    }

    /// How VoiceOver reads a row's badge ("3 need you", "1 update").
    func badgeDescription(_ count: Int) -> String {
        switch self {
        case .sessions, .changes: return "\(count) need\(count == 1 ? "s" : "") you"
        case .research:  return "\(count) active"
        default:         return "\(count)"
        }
    }
}

// MARK: - Go menu

/// The Go menu: one item per page, ⌘1–⌘8 down the sidebar, like Finder's
/// Go menu (Settings isn't a page: it is the app menu's ⌘, window). It
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
