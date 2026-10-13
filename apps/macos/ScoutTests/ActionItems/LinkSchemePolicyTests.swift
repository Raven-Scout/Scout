import Testing
import Foundation
@testable import Scout

/// Links in action-items text (Raven-Scout/Scout#435). The text is written by
/// the model from Slack, mail, GitHub and Linear content, so a markdown link's
/// URL is attacker-reachable. Only http/https/mailto may reach `NSWorkspace`;
/// the app's own `scout-wiki://` and `scout-tag://` links stay clickable
/// because they are handled in-app. Everything else renders as plain text, and
/// the click handler refuses it too.
@Suite("Link scheme policy")
struct LinkSchemePolicyTests {
    @Test(arguments: [
        "https://github.com/example-org/widgets/pull/42",
        "https://linear.app/acme/issue/PROJ-1234",
        "https://acme-co.slack.com/archives/C0123456789/p1700000000000000",
        "http://example.com/a",
        "HTTPS://example.com/",
        "mailto:alex@example.com",
    ])
    func allowsWebAndMail(_ raw: String) throws {
        #expect(LinkSchemePolicy.allowsExternalOpen(try #require(URL(string: raw))))
    }

    @Test(arguments: [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,alert(1)",
        "vbscript:msgbox(1)",
        "file:///Applications/Calculator.app",
        "obsidian://new?vault=Scout&content=x",
        "ssh://example.com",
        "x-apple.systempreferences:com.apple.preference.security",
        "scout-wiki://people%2Falex",
        "http:no-host",
        "relative/path",
    ])
    func refusesEverythingElse(_ raw: String) throws {
        #expect(!LinkSchemePolicy.allowsExternalOpen(try #require(URL(string: raw))))
    }
}

// @MainActor for the same reason as InlineMarkdownTextTests: the render cache
// is main-actor state.
@MainActor
@Suite("Inline markdown link safety")
struct InlineMarkdownLinkSafetyTests {
    private func links(_ raw: String) -> [URL] {
        InlineMarkdownText.attributedString(for: raw).runs.compactMap(\.link)
    }

    @Test(arguments: [
        "[click me](javascript:alert(1))",
        "[click me](JaVaScRiPt:alert(1))",
        "[click me](&#106;avascript:alert(1))",
        "[click me](&#x6A;avascript:alert(1))",
        "[click me](data:text/html,alert(1))",
        "[click me](vbscript:msgbox(1))",
        "[click me](file:///Applications/Calculator.app)",
        "[click me](obsidian://new?vault=Scout&content=x)",
        "[click me](x-apple.systempreferences:com.apple.preference.security)",
        "<javascript:alert(1)>",
        "<file:///etc/hosts>",
    ])
    func hostileLinksRenderAsPlainText(_ raw: String) {
        #expect(links(raw).isEmpty, "\(raw) kept a link")
        #expect(!String(InlineMarkdownText.attributedString(for: raw).characters).isEmpty)
    }

    @Test(arguments: [
        ("[the PR](https://github.com/example-org/widgets/pull/42)", "https://github.com/example-org/widgets/pull/42"),
        ("[PROJ-1234](https://linear.app/acme/issue/PROJ-1234)", "https://linear.app/acme/issue/PROJ-1234"),
        ("[thread](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)",
         "https://acme-co.slack.com/archives/C0123456789/p1700000000000000"),
        ("[mail Alex](mailto:alex@example.com)", "mailto:alex@example.com"),
    ])
    func webAndMailLinksStayClickable(_ raw: String, _ expected: String) {
        #expect(links(raw).map(\.absoluteString) == [expected])
    }

    @Test func wikilinksStayClickable() {
        #expect(links("Ping [[people/alex|Alex]]").map { $0.scheme } == ["scout-wiki"])
    }

    @Test func tagChipsStayClickable() {
        #expect(links("ship #PROJB1 now").map { $0.scheme } == [KBTag.scheme])
    }

    @Test func linkLabelSurvivesWhenTheLinkIsDropped() {
        let attr = InlineMarkdownText.attributedString(for: "see [click me](javascript:alert(1)) now")
        #expect(String(attr.characters) == "see click me now")
    }
}

/// The Obsidian fallback a wikilink opens is built from model-written text, so
/// the target must stay inside the `file` value — a `&` can't add parameters.
@MainActor
@Suite("Obsidian fallback URL")
struct ObsidianFallbackURLTests {
    private func queryItems(_ url: URL?) throws -> [URLQueryItem] {
        let url = try #require(url)
        return try #require(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems)
    }

    @Test func targetCannotAddQueryParameters() throws {
        let route = InlineMarkdownText.route(for: "notes/a&vault=Other#x", canOpenKBNote: false, linearWorkspace: "")
        guard case .external(let url) = route else {
            Issue.record("expected an external route, got \(String(describing: route))")
            return
        }
        let items = try queryItems(url)
        #expect(items.map(\.name) == ["vault", "file"])
        #expect(items.first { $0.name == "vault" }?.value == "Scout")
        #expect(items.first { $0.name == "file" }?.value == "notes/a&vault=Other#x")
    }

    @Test func entityChipTargetCannotAddQueryParameters() throws {
        let url = TaskDeepLink.entity(path: "notes/a&vault=Other", label: nil).openURL(linearWorkspace: "")
        let items = try queryItems(url)
        #expect(items.map(\.name) == ["vault", "file"])
        #expect(items.first { $0.name == "file" }?.value == "notes/a&vault=Other")
    }
}
