import Testing
import Foundation
@testable import Scout

@Suite("GitHub ref linkifier")
struct GitHubRefLinkifierTests {
    @Test func linkifiesQualifiedRef() {
        let out = GitHubRefLinkifier.linkify("See keboola/mcp-server#553 for details.")
        #expect(out == "See [keboola/mcp-server#553](https://github.com/keboola/mcp-server/issues/553) for details.")
    }

    @Test func linkifiesBareRefsWhenSingleRepoInferable() {
        // The issue #17 example: bare #NNN refs plus a single repo slug mention.
        let out = GitHubRefLinkifier.linkify(
            "Triage mcp-server review requests — #555 (bump GH Actions), #498, #553 in keboola/mcp-server"
        )
        #expect(out.contains("[#555](https://github.com/keboola/mcp-server/issues/555)"))
        #expect(out.contains("[#498](https://github.com/keboola/mcp-server/issues/498)"))
        #expect(out.contains("[#553](https://github.com/keboola/mcp-server/issues/553)"))
    }

    @Test func leavesBareRefsPlainWhenNoRepo() {
        let input = "Bumped #555 and #498 today."
        #expect(GitHubRefLinkifier.linkify(input) == input)
    }

    @Test func leavesBareRefsPlainWhenMultipleReposAmbiguous() {
        let input = "Compare acme/api#1 and acme/web#2 — also #3 somewhere."
        let out = GitHubRefLinkifier.linkify(input)
        // Both qualified refs are linkified...
        #expect(out.contains("[acme/api#1](https://github.com/acme/api/issues/1)"))
        #expect(out.contains("[acme/web#2](https://github.com/acme/web/issues/2)"))
        // ...but the bare #3 stays plain because the repo is ambiguous (two repos).
        #expect(out.contains(" — also #3 somewhere."))
        #expect(!out.contains("issues/3"))
    }

    @Test func infersRepoFromQualifiedSiblingRef() {
        let out = GitHubRefLinkifier.linkify("acme/api#1 then a follow-up #2.")
        #expect(out.contains("[acme/api#1](https://github.com/acme/api/issues/1)"))
        #expect(out.contains("[#2](https://github.com/acme/api/issues/2)"))
    }

    @Test func infersRepoFromGitHubURL() {
        let out = GitHubRefLinkifier.linkify("See https://github.com/acme/api/pull/68 — also #70.")
        #expect(out.contains("[#70](https://github.com/acme/api/issues/70)"))
    }

    @Test func ignoresFilePathSlugsForInference() {
        // action-items/render.py is a file path, not a repo, so #5 stays plain.
        let input = "Edit action-items/render.py to handle #5."
        let out = GitHubRefLinkifier.linkify(input)
        #expect(out == input)
    }

    @Test func ignoresNumericFractionSlugs() {
        // "5/6" is not a repo; bare ref must not be inferred from it.
        let input = "Rolled 5/6 of the way; closes #9."
        let out = GitHubRefLinkifier.linkify(input)
        #expect(out == input)
    }

    @Test func doesNotTouchExistingMarkdownLink() {
        let input = "Already linked [#555](https://example.com/x) here in acme/api."
        let out = GitHubRefLinkifier.linkify(input)
        #expect(out.contains("[#555](https://example.com/x)"))
        // The #555 inside the link label must not be re-linkified (no nested link).
        #expect(!out.contains("issues/555"))
    }

    @Test func doesNotTouchWikilink() {
        let input = "Context [[issue-tracker]] for keboola/mcp-server#1."
        let out = GitHubRefLinkifier.linkify(input)
        #expect(out.contains("[[issue-tracker]]"))
        #expect(out.contains("[keboola/mcp-server#1](https://github.com/keboola/mcp-server/issues/1)"))
    }

    @Test func doesNotTouchInlineCode() {
        let input = "Run `git log #5` in acme/api."
        let out = GitHubRefLinkifier.linkify(input)
        #expect(out.contains("`git log #5`"))
        #expect(!out.contains("issues/5"))
    }

    @Test func leavesPlainTextUnchanged() {
        let input = "Call the mechanic about the oil change."
        #expect(GitHubRefLinkifier.linkify(input) == input)
    }
}
