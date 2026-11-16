import SwiftUI
import Testing
@testable import Scout

/// VoiceOver labels, values, selected state and identifiers for the custom
/// controls and the sidebar (C7). The view tests read the real accessibility
/// tree through `AccessibilityTree`; the rest pin the pure label builders.
@MainActor
@Suite("Accessibility foundation", .serialized)
struct AccessibilityFoundationTests {

    // MARK: - Custom controls, read from the accessibility tree

    @Test("a settings toggle is named by its row and reports On/Off")
    func settingsToggleIsLabelled() async {
        for isOn in [true, false] {
            var value = isOn
            let binding = Binding(get: { value }, set: { value = $0 })
            let nodes = await AccessibilityTree.nodes(of: SettingsCard {
                SettingsRow(title: "Launch Scout at login", help: "Start automatically.") {
                    SettingsToggle(isOn: binding)
                }
            })
            let toggle = nodes.first { $0.label == "Launch Scout at login" }
            #expect(toggle != nil, "no element labelled by the row title:\n\(nodes.map(\.description).joined(separator: "\n"))")
            #expect(toggle?.value == (isOn ? "On" : "Off") || toggle?.value == (isOn ? "1" : "0"),
                    "toggle value is \(toggle?.value ?? "nil")")
        }
    }

    @Test("a settings input is named by its field label, not only its placeholder")
    func settingsInputIsLabelled() async {
        var text = ""
        let binding = Binding(get: { text }, set: { text = $0 })
        let nodes = await AccessibilityTree.nodes(of: SettingsField(label: "Linear workspace", help: "Your workspace.") {
            SettingsInput(text: binding, placeholder: "e.g. acme-co")
        })
        #expect(nodes.contains { $0.label == "Linear workspace" },
                "\(nodes.map(\.description).joined(separator: "\n"))")
    }

    @Test("the segmented control marks only the chosen segment selected")
    func segmentedControlReportsSelection() async {
        var choice = "Board"
        let binding = Binding(get: { choice }, set: { choice = $0 })
        let nodes = await AccessibilityTree.nodes(of: EditorialSegmentedControl(
            selection: binding,
            options: [("List", "List"), ("Board", "Board"), ("Topics", "Topics")],
            accessibilityLabel: "View"))
        let segments = nodes.filter { ["List", "Board", "Topics"].contains($0.label) }
        #expect(segments.count == 3, "\(nodes.map(\.description).joined(separator: "\n"))")
        #expect(segments.filter(\.isSelected).map(\.label) == ["Board"])
    }

    @Test("the sidebar exposes one identified row per page, with the current page selected")
    func sidebarRowsAreIdentified() async {
        var selection = SidebarItem.schedules
        let binding = Binding(get: { selection }, set: { selection = $0 })
        let nodes = await AccessibilityTree.nodes(
            of: SidebarView(selection: binding, proposalsBadge: 2, settingsAttention: true),
            size: CGSize(width: 240, height: 700))
        let ids = Set(nodes.map(\.identifier))
        for item in SidebarItem.allCases {
            #expect(ids.contains(item.accessibilityIdentifier),
                    "missing \(item.accessibilityIdentifier) in:\n\(nodes.map(\.description).joined(separator: "\n"))")
        }
        let proposals = nodes.first { $0.identifier == SidebarItem.proposals.accessibilityIdentifier }
        #expect(proposals?.label == "Proposals")
        #expect(proposals?.value == "2 awaiting a decision")
        let settings = nodes.first { $0.identifier == SidebarItem.settings.accessibilityIdentifier }
        #expect(settings?.value == "needs attention")
    }

    // MARK: - Sidebar and Go menu metadata

    @Test("Go menu shortcuts are unique digits, ⌘1 Control Center to ⌘9 Settings")
    func goShortcutsAreUnique() {
        let keys = SidebarItem.allCases.compactMap(\.goShortcutKey)
        #expect(Set(keys).count == keys.count, "two pages share a Go shortcut")
        #expect(keys.allSatisfy { "123456789".contains($0) })
        #expect(SidebarItem.controlCenter.goShortcutKey == "1")
        #expect(SidebarItem.settings.goShortcutKey == "9")
        // In sidebar order, so ⌘-numbers count down the list.
        #expect(keys == keys.sorted())
    }

    @Test("page titles and identifiers are unique and non-empty")
    func pageMetadataIsUnique() {
        let titles = SidebarItem.allCases.map(\.title)
        let ids = SidebarItem.allCases.map(\.accessibilityIdentifier)
        #expect(Set(titles).count == titles.count && titles.allSatisfy { !$0.isEmpty })
        #expect(Set(ids).count == ids.count && ids.allSatisfy { $0.hasPrefix("sidebar-") })
        #expect(SidebarItem.allCases.allSatisfy { NSImage(systemSymbolName: $0.systemImage, accessibilityDescription: nil) != nil })
    }

    @Test("a sidebar row's spoken value combines badge, attention and the gate")
    func sidebarAccessibilityValue() {
        #expect(SidebarView.accessibilityValue(for: .sessions, badge: 0, attention: false, tabsGated: false) == "")
        #expect(SidebarView.accessibilityValue(for: .sessions, badge: 1, attention: false, tabsGated: false) == "1 needs you")
        #expect(SidebarView.accessibilityValue(for: .sessions, badge: 3, attention: false, tabsGated: false) == "3 need you")
        #expect(SidebarView.accessibilityValue(for: .settings, badge: 2, attention: true, tabsGated: true)
                == "2 updates available, needs attention")
        #expect(SidebarView.accessibilityValue(for: .wishlist, badge: 0, attention: false, tabsGated: true)
                == "unavailable until Scout is set up")
    }

    @Test("the sidebar renders with the native list at every selection and while gated")
    func sidebarRenders() {
        for item in SidebarItem.allCases {
            var selection = item
            let binding = Binding(get: { selection }, set: { selection = $0 })
            ViewHost.render(SidebarView(selection: binding, sessionsBadge: 4, settingsBadge: 1,
                                        settingsAttention: true, tabsGated: item == .wishlist),
                            size: CGSize(width: 240, height: 700))
        }
    }

    // MARK: - Pure label builders

    @Test("heatmap days read as a date and a run count")
    func heatmapLabels() throws {
        let date = try #require(Calendar(identifier: .gregorian).date(from: DateComponents(year: 2026, month: 11, day: 12)))
        let none = ActivityHeatmapView.accessibilityLabel(date: date, successes: 0, failures: 0)
        #expect(none.hasSuffix(", no runs"))
        #expect(ActivityHeatmapView.accessibilityLabel(date: date, successes: 1, failures: 0).hasSuffix(", 1 run"))
        #expect(ActivityHeatmapView.accessibilityLabel(date: date, successes: 2, failures: 1).hasSuffix(", 3 runs, 1 failed"))
    }

    @Test("a slot's weekday strip reads as day names")
    func weekdayLabels() {
        #expect(SchedulesTimelineView.weekdaysAccessibilityLabel(["Fri", "Mon", "Wed"]) == "Runs Monday, Wednesday, Friday")
        #expect(SchedulesTimelineView.weekdaysAccessibilityLabel(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) == "Runs every day")
        #expect(SchedulesTimelineView.weekdaysAccessibilityLabel([]) == "No run days")
    }

    @Test("the link graph lists its notes for VoiceOver, centre first then by links")
    func graphAccessibleNodes() {
        let graph = KBGraph(
            nodes: [
                KBGraphNode(id: "a.md", label: "Alpha", group: KBEntityGroup.allCases[0], degree: 1, isCenter: false),
                KBGraphNode(id: "b.md", label: "Beta", group: KBEntityGroup.allCases[0], degree: 5, isCenter: false),
                KBGraphNode(id: "c.md", label: "Center", group: KBEntityGroup.allCases[0], degree: 2, isCenter: true),
            ],
            edges: [])
        let ordered = KBGraphCanvas.accessibleNodes(graph).map(\.label)
        #expect(ordered == ["Center", "Beta", "Alpha"])
        #expect(KBGraphCanvas.accessibilityLabel(for: graph.nodes[2]) == "Center, this note, 2 links")
        #expect(KBGraphCanvas.accessibilityLabel(for: graph.nodes[0]) == "Alpha, 1 link")
    }
}
