import Foundation
import Testing
@testable import Scout

/// `TaskDeepLink` is `nonisolated` so a parse can build one off the main actor,
/// but its URL builder read `UserDefaults.standard` — the same Cocoa-backed
/// string read off-thread that #103 hoisted out of the parser. The workspace is
/// now a parameter the main-actor caller supplies, so these run with no
/// defaults store at all.
@Suite("TaskDeepLink — Linear workspace")
struct TaskDeepLinkWorkspaceTests {

    @Test func linearURLUsesTheSuppliedWorkspace() {
        let link = TaskDeepLink.linear(id: "PROJ-1234")
        #expect(link.openURL(linearWorkspace: "acme-co")?.absoluteString
                == "https://linear.app/acme-co/issue/PROJ-1234")
    }

    @Test func emptyWorkspaceFallsBackToLinearHome() {
        // Settings ships the workspace blank; the chip still has to go
        // somewhere rather than render a dead link.
        let link = TaskDeepLink.linear(id: "PROJ-1234")
        #expect(link.openURL(linearWorkspace: "")?.absoluteString == "https://linear.app/")
    }

    @Test func otherRefKindsIgnoreTheWorkspace() {
        let raw = URL(string: "https://github.com/example-org/widgets/pull/7")!
        let slack = URL(string: "https://acme-co.slack.com/archives/C0123456789/p1700000000000000")!

        #expect(TaskDeepLink.githubPR(repo: "example-org/widgets", number: 7, rawURL: raw)
            .openURL(linearWorkspace: "acme-co") == raw)
        #expect(TaskDeepLink.slackThread(slack).openURL(linearWorkspace: "acme-co") == slack)
        #expect(TaskDeepLink.entity(path: "people/priya", label: nil)
            .openURL(linearWorkspace: "acme-co")?.absoluteString
            == "obsidian://open?vault=Scout&file=people/priya")
    }

    @Test func inAppRefsHaveNoURL() {
        #expect(TaskDeepLink.crossRef(tag: "IOTA").openURL(linearWorkspace: "acme-co") == nil)
        #expect(TaskDeepLink.plainRef(text: "see the runbook").openURL(linearWorkspace: "acme-co") == nil)
    }
}
