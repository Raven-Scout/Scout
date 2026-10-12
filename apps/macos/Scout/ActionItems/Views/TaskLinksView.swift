import SwiftUI
import AppKit

/// Deep-link chips for Linear tickets, GitHub PRs, and Slack threads.
/// Editorial style: boxed chip with tinted icon + label + subtle arrow.
struct TaskLinksView: View {
    let links: [TaskDeepLink]
    @Environment(\.openKBNote) private var openKBNote

    /// Only links with an external URL open here; in-app refs (cross-refs) and
    /// plain tokens surface as chips in the collapsed header, not this list.
    private var openableLinks: [TaskDeepLink] { links.filter { $0.openURL != nil } }

    var body: some View {
        if openableLinks.isEmpty {
            EmptyView()
        } else {
            HStack(spacing: 6) {
                ForEach(openableLinks) { link in
                    if case .entity(let target, _) = link, let openKBNote {
                        // In the KB tab, not Obsidian (Raven-Scout/Scout#295).
                        Button {
                            openKBNote(target)
                        } label: {
                            chipBody(for: link, external: false)
                        }
                        .buttonStyle(.plainHit)
                        .help("Open \(link.displayLabel) in the Knowledge Base")
                    } else if let url = link.openURL {
                        Button {
                            NSWorkspace.shared.open(url)
                        } label: {
                            chipBody(for: link)
                        }
                        .buttonStyle(.plainHit)
                        .help(url.absoluteString)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func chipBody(for link: TaskDeepLink, external: Bool = true) -> some View {
        HStack(spacing: 5) {
            Image(systemName: iconName(for: link))
                .font(.system(size: 10))
                .foregroundStyle(iconColor(for: link))
            Text(label(for: link))
                .font(DS.sans(11.5, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Image(systemName: external ? "arrow.up.right" : "arrow.right")
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
        case .entity:      return "doc.text"
        case .crossRef:    return "number.square"
        case .plainRef:    return "tag"
        }
    }

    private func iconColor(for link: TaskDeepLink) -> Color {
        switch link {
        case .linear:      return Color(red: 0.45, green: 0.35, blue: 0.80)
        case .githubPR:    return DS.Ink.p2
        case .slackThread: return Color(red: 0.80, green: 0.40, blue: 0.55)
        case .entity, .crossRef, .plainRef: return DS.Ink.p3
        }
    }

    private func label(for link: TaskDeepLink) -> String {
        switch link {
        case .linear(let id):               return id
        case .githubPR(let repo, let n, _): return "\(repo)#\(n)"
        case .slackThread:                  return "slack thread"
        case .entity, .crossRef, .plainRef: return link.displayLabel
        }
    }
}
