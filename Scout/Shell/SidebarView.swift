import SwiftUI

/// Editorial sidebar: sand-colored group label, flat row, accent fill on the
/// selected item. Matches the handoff bundle's `.sb-item` language.
struct SidebarView: View {
    @Binding var selection: SidebarItem

    var body: some View {
        List(selection: $selection) {
            Section {
                sidebarRow(.controlCenter, label: "Control Center", system: "chart.bar.doc.horizontal")
                sidebarRow(.actionItems,   label: "Action Items",   system: "checklist")
                sidebarRow(.schedules,     label: "Schedules",      system: "calendar.badge.clock")
            } header: {
                Text("Scout")
                    .font(DS.sans(11, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
            }
            Section {
                sidebarRow(.settings, label: "Settings", system: "gearshape")
            }
        }
        .listStyle(.sidebar)
        .frame(minWidth: 200)
    }

    private func sidebarRow(_ item: SidebarItem, label: String, system: String) -> some View {
        Label {
            Text(label)
                .font(DS.sans(13))
        } icon: {
            Image(systemName: system)
                .font(.system(size: 13))
                .foregroundStyle(selection == item ? DS.Accent.ink : DS.Ink.p3)
        }
        .tag(item)
    }
}
