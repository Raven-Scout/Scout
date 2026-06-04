import Foundation

/// A small source/context chip shown in a task card's collapsed header — the
/// scannable "who/where" line inspired by the triage artifact's chips. Derived
/// purely from a task's deep links and carry marker; no new data.
struct TaskChip: Identifiable, Equatable {
    enum Glyph: Equatable {
        case github, linear, slack, carry
    }

    let glyph: Glyph
    let label: String

    var id: String { "\(label)" }

    /// Derive the chip row for a task: a count/label per deep-link kind (PRs,
    /// Linear, Slack), the repo slug when a single GitHub repo is referenced,
    /// and a "carried <date>" chip when the task was carried in from a prior
    /// day. Order is stable: GitHub → Linear → Slack → carry.
    static func chips(for task: ActionTask, carriedLabel: @autoclosure () -> String? = nil) -> [TaskChip] {
        var chips: [TaskChip] = []

        let prs = task.deepLinks.compactMap { link -> String? in
            if case .githubPR(let repo, _, _) = link { return repo } else { return nil }
        }
        if !prs.isEmpty {
            chips.append(TaskChip(glyph: .github, label: prs.count == 1 ? "1 PR" : "\(prs.count) PRs"))
            // Surface the repo only when every PR points at the same one.
            let repos = Set(prs)
            if repos.count == 1, let repo = repos.first {
                chips.append(TaskChip(glyph: .github, label: repo))
            }
        }

        let linearCount = task.deepLinks.filter { if case .linear = $0 { return true } else { return false } }.count
        if linearCount > 0 {
            chips.append(TaskChip(glyph: .linear, label: linearCount == 1 ? "Linear" : "\(linearCount) Linear"))
        }

        let slackCount = task.deepLinks.filter { if case .slackThread = $0 { return true } else { return false } }.count
        if slackCount > 0 {
            chips.append(TaskChip(glyph: .slack, label: slackCount == 1 ? "Slack" : "\(slackCount) Slack"))
        }

        if let carried = carriedLabel() {
            chips.append(TaskChip(glyph: .carry, label: "carried \(carried)"))
        }

        return chips
    }
}
