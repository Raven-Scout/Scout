import Testing
import Foundation
@testable import Scout

@Suite("Deep link detection")
struct DeepLinkDetectionTests {
    @Test func detectsLinearIDsAcrossAllPrefixes() {
        let text = "Blocked by [[PROJ-2879]] and OPS-3853; see DESK-15915, TEAM-321, PLAT-12, META-42."
        let links = ActionItemsParser.detectDeepLinks(in: text)
        let linearIDs = links.compactMap { if case .linear(let id) = $0 { return id } else { return nil } }
        #expect(linearIDs == ["PROJ-2879", "OPS-3853", "DESK-15915", "TEAM-321", "PLAT-12", "META-42"])
    }

    @Test func dedupesRepeatedLinearID() {
        let text = "[[PROJ-2619]] comment; [[PROJ-2619]] again; PROJ-2619 third mention."
        let links = ActionItemsParser.detectDeepLinks(in: text)
        #expect(links.count == 1)
        if case .linear(let id) = links.first! { #expect(id == "PROJ-2619") } else { Issue.record("expected linear") }
    }

    @Test func detectsGitHubPR() {
        let text = "PR https://github.com/acme-co/api-kit/pull/68 landed."
        let links = ActionItemsParser.detectDeepLinks(in: text)
        #expect(links.count == 1)
        if case .githubPR(let repo, let num, _) = links.first! {
            #expect(repo == "acme-co/api-kit")
            #expect(num == 68)
        } else {
            Issue.record("expected githubPR")
        }
    }

    @Test func detectsSlackThread() {
        let text = "See https://acme-co.slack.com/archives/C01234ABCDE/p1700000000123456?thread_ts=1700000000.123456"
        let links = ActionItemsParser.detectDeepLinks(in: text)
        #expect(links.count == 1)
        if case .slackThread = links.first! {} else { Issue.record("expected slackThread") }
    }

    @Test func detectsSchemelessSlackThread() {
        // Sessions sometimes write the source as a bare host with no scheme, e.g.
        // "Slack attachment `acme-co.slack.com/archives/C01234ABCDE/p1700000000123456`".
        let text = "Slack attachment acme-co.slack.com/archives/C01234ABCDE/p1700000000123456"
        let links = ActionItemsParser.detectDeepLinks(in: text)
        #expect(links.count == 1)
        if case .slackThread(let url) = links.first! {
            // The scheme must be normalized to https so the link is openable.
            #expect(url.scheme == "https")
            #expect(url.absoluteString == "https://acme-co.slack.com/archives/C01234ABCDE/p1700000000123456")
        } else {
            Issue.record("expected slackThread")
        }
    }

    @Test func dedupesSchemedAndSchemelessSlackThread() {
        // The same thread written once with and once without a scheme is one link.
        let text = """
        https://acme-co.slack.com/archives/C01/p1700000000123456 \
        and again acme-co.slack.com/archives/C01/p1700000000123456
        """
        let links = ActionItemsParser.detectDeepLinks(in: text)
        #expect(links.count == 1)
    }

    @Test func returnsEmptyForPlainText() {
        #expect(ActionItemsParser.detectDeepLinks(in: "Call mechanic about oil change.").isEmpty)
    }

    @Test func preservesDetectionOrder() {
        let text = "[[PROJ-2879]] then https://github.com/acme-co/api-kit/pull/68 then PROJ-3007."
        let links = ActionItemsParser.detectDeepLinks(in: text)
        #expect(links.count == 3)
        if case .linear(let a) = links[0] { #expect(a == "PROJ-2879") } else { Issue.record() }
        if case .githubPR = links[1] {} else { Issue.record() }
        if case .linear(let b) = links[2] { #expect(b == "PROJ-3007") } else { Issue.record() }
    }
}
