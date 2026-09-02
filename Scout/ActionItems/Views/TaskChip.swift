import Foundation

/// A small source/context chip shown in a task card's collapsed header — the
/// scannable "who/where" line inspired by the triage artifact's chips. Derived
/// purely from a task's deep links and carry marker; no new data.
struct TaskChip: Identifiable, Equatable {
    enum Glyph: Equatable {
        case github, linear, slack, carry
        /// Sourced from a task's `- Refs:` sub-bullet.
        case entity, crossRef, plain
    }

    /// A single click target behind a chip. A chip may summarise several links
    /// (e.g. "2 PRs"); each `Link` becomes a dropdown entry, or — when a chip
    /// has exactly one — the chip opens it directly. A chip with no links
    /// (e.g. "carried Jun 2") renders as static text.
    struct Link: Identifiable, Equatable {
        let label: String
        let url: URL
        var id: String { url.absoluteString }
    }

    let glyph: Glyph
    let label: String
    /// Click targets: 0 = static (no action), 1 = open directly, >1 = dropdown.
    let links: [Link]

    /// `links` is defaulted so call sites and tests that construct a chip by
    /// glyph+label keep compiling and comparing equal.
    init(glyph: Glyph, label: String, links: [Link] = []) {
        self.glyph = glyph
        self.label = label
        self.links = links
    }

    var id: String { "\(label)" }

    /// Derive the chip row for a task: a count/label per deep-link kind (PRs,
    /// Linear, Slack), the repo slug when a single GitHub repo is referenced,
    /// and a "carried <date>" chip when the task was carried in from a prior
    /// day. Order is stable: GitHub → Linear → Slack → carry. Each chip carries
    /// the URL(s) it points at via `links`.
    static func chips(for task: ActionTask, carriedLabel: @autoclosure () -> String? = nil) -> [TaskChip] {
        var chips: [TaskChip] = []

        let prs = task.deepLinks.compactMap { link -> (repo: String, link: Link)? in
            if case .githubPR(let repo, _, _) = link, let url = link.openURL {
                return (repo, Link(label: link.displayLabel, url: url))
            }
            return nil
        }
        if !prs.isEmpty {
            chips.append(TaskChip(
                glyph: .github,
                label: prs.count == 1 ? "1 PR" : "\(prs.count) PRs",
                links: prs.map(\.link)
            ))
            // Surface the repo only when every PR points at the same one; the
            // repo chip opens the repo homepage, distinct from the PR(s).
            let repos = Set(prs.map(\.repo))
            if repos.count == 1, let repo = repos.first {
                let repoLinks = URL(string: "https://github.com/\(repo)")
                    .map { [Link(label: repo, url: $0)] } ?? []
                chips.append(TaskChip(glyph: .github, label: repo, links: repoLinks))
            }
        }

        let linearLinks = task.deepLinks.compactMap { link -> Link? in
            if case .linear = link, let url = link.openURL { return Link(label: link.displayLabel, url: url) }
            return nil
        }
        if !linearLinks.isEmpty {
            chips.append(TaskChip(
                glyph: .linear,
                label: linearLinks.count == 1 ? "Linear" : "\(linearLinks.count) Linear",
                links: linearLinks
            ))
        }

        let slackLinks = task.deepLinks.compactMap { link -> Link? in
            if case .slackThread = link, let url = link.openURL { return Link(label: link.displayLabel, url: url) }
            return nil
        }
        if !slackLinks.isEmpty {
            chips.append(TaskChip(
                glyph: .slack,
                label: slackLinks.count == 1 ? "Slack" : "\(slackLinks.count) Slack",
                links: slackLinks
            ))
        }

        // Refs-block kinds: one chip per token, in source order. Entity chips
        // open the KB note (obsidian URL); cross-ref and plain chips are inert
        // (no link) — they resolve in-app or carry no target.
        for link in task.deepLinks {
            switch link {
            case .entity:
                let links = link.openURL.map { [Link(label: link.displayLabel, url: $0)] } ?? []
                chips.append(TaskChip(glyph: .entity, label: link.displayLabel, links: links))
            case .crossRef:
                chips.append(TaskChip(glyph: .crossRef, label: link.displayLabel))
            case .plainRef:
                chips.append(TaskChip(glyph: .plain, label: link.displayLabel))
            case .linear, .githubPR, .slackThread:
                break   // handled by the grouped kinds above
            }
        }

        if let carried = carriedLabel() {
            chips.append(TaskChip(glyph: .carry, label: "carried \(carried)"))
        }

        return chips
    }
}
