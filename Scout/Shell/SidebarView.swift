import SwiftUI

/// Editorial sidebar: sand-colored group label, flat row, neumorphic-pressed
/// chrome on the active item. Matches the handoff bundle's `.sb-item.active`
/// language from Scout.html.
struct SidebarView: View {
    @Binding var selection: SidebarItem
    /// Count of proposals awaiting a decision — drives the badge on the
    /// Proposals row. Hidden when zero.
    var proposalsBadge: Int = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            groupLabel("Scout")
            row(.controlCenter, label: "Control Center", system: "chart.bar.doc.horizontal")
            row(.actionItems,   label: "Action Items",   system: "checklist")
            row(.schedules,     label: "Schedules",      system: "calendar.badge.clock")
            row(.proposals,     label: "Proposals",      system: "lightbulb", badge: proposalsBadge)
            Spacer().frame(height: 10)
            groupLabel("App")
            row(.settings,      label: "Settings",       system: "gearshape")
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(
            LinearGradient(
                colors: [DS.Paper.sunk, DS.Paper.base],
                startPoint: .leading,
                endPoint: .trailing
            )
        )
        .overlay(alignment: .trailing) {
            Rectangle().fill(DS.Rule.soft).frame(width: 0.5)
        }
    }

    private func groupLabel(_ text: String) -> some View {
        Text(text.uppercased())
            .font(DS.sans(10.5, weight: .medium))
            .tracking(0.08 * 10.5)
            .foregroundStyle(DS.Ink.p4)
            .padding(.horizontal, 10)
            .padding(.top, 6)
            .padding(.bottom, 4)
    }

    @ViewBuilder
    private func row(_ item: SidebarItem, label: String, system: String, badge: Int = 0) -> some View {
        let isActive = selection == item
        Button {
            selection = item
        } label: {
            HStack(spacing: 8) {
                Image(systemName: system)
                    .font(.system(size: 13))
                    .foregroundStyle(isActive ? DS.Accent.ink : DS.Ink.p3)
                    .frame(width: 16, height: 16)
                Text(label)
                    .font(DS.sans(13))
                    .foregroundStyle(isActive ? DS.Ink.p1 : DS.Ink.p2)
                Spacer(minLength: 0)
                if badge > 0 {
                    Text("\(badge)")
                        .font(DS.sans(10.5, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6)
                        .frame(minWidth: 18, minHeight: 16)
                        .background(Capsule().fill(DS.Accent.fill))
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                if isActive {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(DS.Paper.base)
                        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                        .shadow(color: DS.Neumorphic.shadow.opacity(0.35), radius: 2, x: 1, y: 1)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
    }
}
