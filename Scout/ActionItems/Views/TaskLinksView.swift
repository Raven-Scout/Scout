import SwiftUI
import AppKit

/// Deep-link chips for Linear tickets, GitHub PRs, and Slack threads.
/// Editorial style: boxed chip with tinted icon + label + subtle arrow.
struct TaskLinksView: View {
    let links: [TaskDeepLink]

    var body: some View {
        if links.isEmpty {
            EmptyView()
        } else {
            HStack(spacing: 6) {
                ForEach(links) { link in
                    Button {
                        NSWorkspace.shared.open(link.openURL)
                    } label: {
                        chipBody(for: link)
                    }
                    .buttonStyle(.plainHit)
                    .help(link.openURL.absoluteString)
                }
            }
        }
    }

    @ViewBuilder
    private func chipBody(for link: TaskDeepLink) -> some View {
        HStack(spacing: 5) {
            Image(systemName: iconName(for: link))
                .font(.system(size: 10))
                .foregroundStyle(iconColor(for: link))
            Text(label(for: link))
                .font(DS.sans(11.5, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Image(systemName: "arrow.up.right")
                .font(.system(size: 9))
                .foregroundStyle(DS.Ink.p4)
        }
        .padding(.horizontal, 8)
        .frame(height: 22)
        .background(EditorialChipBackground())
    }

    private func iconName(for link: TaskDeepLink) -> String {
        switch link {
        case .linear:      return "l.circle"
        case .githubPR:    return "circle.fill"
        case .slackThread: return "number"
        }
    }

    private func iconColor(for link: TaskDeepLink) -> Color {
        switch link {
        case .linear:      return Color(red: 0.45, green: 0.35, blue: 0.80)
        case .githubPR:    return DS.Ink.p2
        case .slackThread: return Color(red: 0.80, green: 0.40, blue: 0.55)
        }
    }

    private func label(for link: TaskDeepLink) -> String {
        switch link {
        case .linear(let id):             return id
        case .githubPR(let repo, let n, _): return "\(repo)#\(n)"
        case .slackThread:                return "slack thread"
        }
    }
}
