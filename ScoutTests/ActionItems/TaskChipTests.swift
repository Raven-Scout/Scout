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
        let same = TaskChip.chips(for: task(links: [pr("example-org/mcp-server", 1), pr("example-org/mcp-server", 2)]))
        #expect(same.contains { $0.label == "example-org/mcp-server" })

        let mixed = TaskChip.chips(for: task(links: [pr("a/b", 1), pr("c/d", 2)]))
        #expect(!mixed.contains { $0.glyph == .github && $0.label.contains("/") })
    }

    @Test func linearAndSlackChips() {
        let chips = TaskChip.chips(for: task(links: [
            .linear(id: "PROJ-1"),
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
                .linear(id: "PROJ-1"),
                pr("a/b", 1),
            ]),
            carriedLabel: "Jun 2"
        )
        // GitHub group emits PR-count then the single-repo chip, so two
        // .github chips lead — the point is the kind ordering across groups.
        #expect(chips.map(\.glyph) == [.github, .github, .linear, .slack, .carry])
    }

    @Test func prChipCarriesPRUrls() {
        let chips = TaskChip.chips(for: task(links: [pr("example-org/crm", 925)]))
        let prChip = chips.first { $0.label == "1 PR" }
        #expect(prChip?.links.map(\.url) == [URL(string: "https://github.com/example-org/crm/pull/925")!])
    }

    @Test func repoChipOpensRepoHomepage() {
        let chips = TaskChip.chips(for: task(links: [pr("example-org/crm", 925)]))
        let repoChip = chips.first { $0.label == "example-org/crm" }
        #expect(repoChip?.links.map(\.url) == [URL(string: "https://github.com/example-org/crm")!])
    }

    @Test func multiPRChipListsEachPR() {
        let chips = TaskChip.chips(for: task(links: [pr("a/b", 1), pr("a/b", 2)]))
        let prChip = chips.first { $0.label == "2 PRs" }
        #expect(prChip?.links.count == 2)
    }

    @Test func carryChipHasNoLinks() {
        let chips = TaskChip.chips(for: task(links: []), carriedLabel: "Jun 2")
        #expect(chips.first?.links.isEmpty == true)
    }

    // MARK: - Refs-block chips (entity / cross-ref / plain)

    @Test func entityChipUsesLastPathSegmentAndOpens() {
        let chips = TaskChip.chips(for: task(links: [.entity(path: "people/alex", label: nil)]))
        let chip = chips.first { $0.glyph == .entity }
        #expect(chip?.label == "alex")                     // last path segment
        #expect(chip?.links.count == 1)                    // opens the note
    }

    @Test func entityChipPrefersWikilinkAlias() {
        let chips = TaskChip.chips(for: task(links: [.entity(path: "people/priya", label: "Priya")]))
        #expect(chips.first { $0.glyph == .entity }?.label == "Priya")
    }

    @Test func crossRefChipIsInert() {
        let chips = TaskChip.chips(for: task(links: [.crossRef(tag: "XREF")]))
        let chip = chips.first { $0.glyph == .crossRef }
        #expect(chip?.label == "#XREF")
        #expect(chip?.links.isEmpty == true)               // resolves in-app, no URL
    }

    @Test func plainRefChipIsInert() {
        let chips = TaskChip.chips(for: task(links: [.plainRef(text: "??garbage")]))
        let chip = chips.first { $0.glyph == .plain }
        #expect(chip?.label == "??garbage")
        #expect(chip?.links.isEmpty == true)
    }

    // MARK: - ForEach identity (#96)

    /// Two different entities whose last path segment matches render the same
    /// label ("alex"), which made their ids collide and left SwiftUI free to
    /// draw either row. Identity now comes from the token, so they differ.
    @Test func sameLabelDifferentEntitiesGetDistinctIDs() {
        let chips = TaskChip.chips(for: task(links: [
            .entity(path: "people/alex", label: nil),
            .entity(path: "team/alex", label: nil),
        ]))
        let entity = chips.filter { $0.glyph == .entity }
        #expect(entity.count == 2)
        #expect(entity.map(\.label) == ["alex", "alex"])          // same display text
        #expect(Set(entity.map(\.id)).count == 2)                 // distinct identity
    }

    /// A cross-ref and a plain token that display identically must also stay
    /// distinct — the chips are inert, so label is all they otherwise share.
    @Test func crossRefAndPlainWithSameLabelGetDistinctIDs() {
        let chips = TaskChip.chips(for: task(links: [
            .crossRef(tag: "XREF"),
            .plainRef(text: "#XREF"),
        ]))
        #expect(Set(chips.map(\.id)).count == chips.count)
    }

    /// Every chip in a realistic mixed row is uniquely identified.
    @Test func allChipIDsUniqueAcrossMixedRow() {
        let chips = TaskChip.chips(
            for: task(links: [
                .entity(path: "people/alex", label: nil),
                .entity(path: "team/alex", label: nil),
                .linear(id: "PROJ-3026"),
                pr("example-org/repo", 7056),
                .crossRef(tag: "5864M"),
                .plainRef(text: "??garbage"),
            ]),
            carriedLabel: "Jun 2"
        )
        #expect(Set(chips.map(\.id)).count == chips.count)
    }

    /// The grouped and carry chips summarise zero or many tokens, so they keep
    /// the glyph+label fallback rather than borrowing one token's identity.
    @Test func groupedChipsFallBackToGlyphAndLabel() {
        let chips = TaskChip.chips(for: task(links: [pr("a/b", 1)]), carriedLabel: "Jun 2")
        #expect(chips.first { $0.label == "1 PR" }?.id == "github:1 PR")
        #expect(chips.first { $0.glyph == .carry }?.id == "carry:carried Jun 2")
    }

    /// Coverage rule: every Refs token yields at least one chip — none vanish.
    @Test func everyRefsTokenYieldsAChip() {
        let chips = TaskChip.chips(for: task(links: [
            .entity(path: "people/alex", label: nil),
            .linear(id: "PROJ-3026"),
            pr("example-org/repo", 7056),
            .crossRef(tag: "XREF"),
            .plainRef(text: "??garbage"),
        ]))
        #expect(chips.contains { $0.glyph == .entity })
        #expect(chips.contains { $0.glyph == .linear })
        #expect(chips.contains { $0.glyph == .github })
        #expect(chips.contains { $0.glyph == .crossRef })
        #expect(chips.contains { $0.glyph == .plain })
    }
}
