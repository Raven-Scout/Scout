import SwiftUI

/// The right-hand side of a collapsed step row: Linear priority, a "no ticket"
/// hint, one chip per PR and how many steps wait on this one.
struct StepTagsView: View {
    let step: BoardStep

    private var task: ActionTask { step.item.task }

    private var pullRequests: [(label: String, url: URL?)] {
        task.deepLinks.compactMap { link in
            guard case .githubPR(let repo, let number, let url) = link else { return nil }
            return ("\(repo.split(separator: "/").last.map(String.init) ?? repo)#\(number)", url)
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            if let label = Self.priorityLabel(task.fields.priority) {
                Text(label)
                    .font(DS.sans(11.5, weight: .medium))
                    .foregroundStyle(task.fields.priority == 1 ? DS.Status.err : DS.Ink.p2)
            }
            if !step.item.hasTicketOrThread {
                Text("no ticket").foregroundStyle(DS.Ink.p4)
            }
            ForEach(pullRequests, id: \.label) { pr in
                PullRequestChip(label: pr.label, url: pr.url)
            }
            if step.blockedCount > 0 {
                Text("\(step.blockedCount) blocked")
            }
        }
        .font(DS.sans(11.5))
        .foregroundStyle(DS.Ink.p3)
        .lineLimit(1)
        .fixedSize()
    }

    static func priorityLabel(_ priority: Int?) -> String? {
        switch priority {
        case 1: "Urgent"
        case 2: "High"
        case 3: "Medium"
        case 4: "Low"
        default: nil
        }
    }
}
