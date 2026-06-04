import Testing
import Foundation
@testable import Scout

@Suite("Task chips")
struct TaskChipTests {
    private func task(links: [TaskDeepLink]) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: false, subject: "s", plainSubject: "s",
            body: "", comments: [], deepLinks: links, snoozedUntil: nil, carriedInFrom: nil
        )
    }

    private func pr(_ repo: String, _ n: Int) -> TaskDeepLink {
        .githubPR(repo: repo, number: n, rawURL: URL(string: "https://github.com/\(repo)/pull/\(n)")!)
    }

    @Test func emptyWhenNoLinksNoCarry() {
        #expect(TaskChip.chips(for: task(links: [])).isEmpty)
    }

    @Test func singleAndPluralPRCounts() {
        #expect(TaskChip.chips(for: task(links: [pr("a/b", 1)])).first?.label == "1 PR")
        let two = TaskChip.chips(for: task(links: [pr("a/b", 1), pr("a/b", 2)]))
        #expect(two.first?.label == "2 PRs")
    }

    @Test func surfacesRepoOnlyWhenSingleRepo() {
        let same = TaskChip.chips(for: task(links: [pr("keboola/mcp-server", 1), pr("keboola/mcp-server", 2)]))
        #expect(same.contains { $0.label == "keboola/mcp-server" })

        let mixed = TaskChip.chips(for: task(links: [pr("a/b", 1), pr("c/d", 2)]))
        #expect(!mixed.contains { $0.glyph == .github && $0.label.contains("/") })
    }

    @Test func linearAndSlackChips() {
        let chips = TaskChip.chips(for: task(links: [
            .linear(id: "AI-1"),
            .slackThread(URL(string: "https://x.slack.com/archives/C/p1")!),
        ]))
        #expect(chips.contains { $0.glyph == .linear && $0.label == "Linear" })
        #expect(chips.contains { $0.glyph == .slack && $0.label == "Slack" })
    }

    @Test func carryChipAppended() {
        let chips = TaskChip.chips(for: task(links: []), carriedLabel: "Jun 2")
        #expect(chips == [TaskChip(glyph: .carry, label: "carried Jun 2")])
    }

    @Test func stableOrderGitHubLinearSlackCarry() {
        let chips = TaskChip.chips(
            for: task(links: [
                .slackThread(URL(string: "https://x.slack.com/archives/C/p1")!),
                .linear(id: "AI-1"),
                pr("a/b", 1),
            ]),
            carriedLabel: "Jun 2"
        )
        // GitHub group emits PR-count then the single-repo chip, so two
        // .github chips lead — the point is the kind ordering across groups.
        #expect(chips.map(\.glyph) == [.github, .github, .linear, .slack, .carry])
    }
}
