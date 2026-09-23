import Testing
import Foundation
@testable import Scout

@Suite("GitHub ref linkifier")
struct GitHubRefLinkifierTests {
    @Test func linkifiesQualifiedRef() {
        let out = GitHubRefLinkifier.linkify("See example-org/mcp-server#553 for details.")
        #expect(out == "See [example-org/mcp-server#553](https://github.com/example-org/mcp-server/issues/553) for details.")
    }

    @Test func linkifiesBareRefsWhenSingleRepoInferable() {
        // The issue #17 example: bare #NNN refs plus a single repo slug mention.
        let out = GitHubRefLinkifier.linkify(
            "Triage mcp-server review requests — #555 (bump GH Actions), #498, #553 in example-org/mcp-server"
        )
        #expect(out.contains("[#555](https://github.com/example-org/mcp-server/issues/555)"))
        #expect(out.contains("[#498](https://github.com/example-org/mcp-server/issues/498)"))
        #expect(out.contains("[#553](https://github.com/example-org/mcp-server/issues/553)"))
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
        let input = "Context [[issue-tracker]] for example-org/mcp-server#1."
        let out = GitHubRefLinkifier.linkify(input)
        #expect(out.contains("[[issue-tracker]]"))
        #expect(out.contains("[example-org/mcp-server#1](https://github.com/example-org/mcp-server/issues/1)"))
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

/// The fast-path guard added for the render hot path. Both branches of `refRe`
/// need a `#` immediately followed by a digit, so anything else must come back
/// byte-identical — if that ever stops holding, the guard would silently drop
/// rewrites rather than fail loudly, so pin the invariant it rests on.
@Suite("GitHubRefLinkifier — hash-digit fast path")
struct GitHubRefLinkifierFastPathTests {
    /// Strings with no `#` followed by a digit. One table serves both
    /// assertions below: the guard must skip every entry, and `linkify` must
    /// hand every entry back byte-identical.
    private static let guardSkippedStrings: [String] = [
        "",
        "Plain prose with no refs at all",
        "example-org/scout is the repo",
        "github.com/example-org/scout without a ref",
        "[[people/alex]] and `code` and [label](https://example.com/a)",
        "PROJ-1234 — Priya merged it",
        "Numbers 1234 and 5678 but no hash",
        "🚧 emoji and — an em dash",
        // The case that makes the digit requirement worth having: Scout
        // vaults are full of these, and none is a GitHub ref.
        "[#DEMOTAG] the demo launch is still open",
        "Discussed in #tmp-demo-sync with [[people/priya]]",
        "[#AI3026] and [#RSM] in one line",
        "A trailing hash # and a lone #",
        "no hash at all",
    ]

    @Test("Strings with no #digit are returned unchanged")
    func noHashDigitRoundTrips() {
        for s in Self.guardSkippedStrings {
            #expect(GitHubRefLinkifier.linkify(s) == s, "should be untouched: \(s)")
        }
    }

    @Test("Alpha-leading tags and channel names skip the regex scan entirely")
    func guardSkipsTagsAndChannels() {
        for s in Self.guardSkippedStrings {
            #expect(!GitHubRefLinkifier.containsHashDigit(s), "guard should skip: \(s)")
        }
    }

    @Test("A digit-leading tag clears the guard but is still not a ref")
    func digitLeadingTagIsNotARef() {
        // `#5` is a hash followed by a digit, so `[#5864M]` pays for the regex
        // scan. It comes back untouched only because the trailing `M` defeats
        // refRe's `\b` — not because of any bracket protection (the protected
        // ranges cover `[[…]]`, `[…](…)` and code spans, not a bare `[…]`).
        let s = "[#5864M] the demo coupon"
        #expect(GitHubRefLinkifier.containsHashDigit(s))
        #expect(GitHubRefLinkifier.linkify(s) == s)
    }

    @Test("The guard admits every shape refRe can match")
    func guardAdmitsRealRefs() {
        for s in [
            "#42",
            "see #42 in example-org/scout",
            "example-org/scout#42",
            "trailing ref example-org/scout#7",
            "[#TAG] alongside a real #99 ref",
        ] {
            #expect(GitHubRefLinkifier.containsHashDigit(s), "guard must not skip: \(s)")
        }
    }

    @Test("Strings with a # still linkify")
    func hashStillLinkifies() {
        // Qualified ref — carries its own repo.
        #expect(GitHubRefLinkifier.linkify("example-org/scout#42")
            .contains("https://github.com/example-org/scout/issues/42"))
        // Bare ref — only linkifies once a single repo can be inferred from the
        // same string, so give it one. (Without a repo it stays plain; that's
        // the existing `leavesBareRefsPlainWhenNoRepo` case, not a fast-path
        // regression.)
        #expect(GitHubRefLinkifier.linkify("see #42 in example-org/scout")
            .contains("https://github.com/example-org/scout/issues/42"))
    }

    @Test("Non-ASCII digits are not issue numbers, guard or no guard")
    func nonASCIIDigitsAreNotRefs() {
        // `refRe` spells its digits as `[0-9]` so it agrees with
        // `containsHashDigit` by construction. Pair the fullwidth ref with an
        // ASCII one so the guard admits the string and the regex really runs.
        let out = GitHubRefLinkifier.linkify("example-org/scout#４２ and #1")
        #expect(!out.contains("issues/４２"), "fullwidth digits linkified: \(out)")
        #expect(out.contains("https://github.com/example-org/scout/issues/1"))
        #expect(!GitHubRefLinkifier.containsHashDigit("example-org/scout#４２"))
    }

    @Test("Every string refRe matches also clears the guard")
    func guardIsNecessaryForRefRe() {
        // The guard and the regex are two encodings of one rule, so pin
        // guard ⊆ regex directly against `refRe` rather than through `linkify`
        // (which returns early on a guard miss and would make the check
        // tautological). Every string up to length 5 over an alphabet that
        // reaches both regex branches, the boundary characters, and non-ASCII
        // digits — a future regex relaxation the guard does not admit fails
        // here instead of silently going dark.
        let alphabet: [Character] = ["#", "1", "a", "/", " ", "４", "٤"]
        var frontier = [""]
        var strings: [String] = []
        for _ in 1...5 {
            frontier = frontier.flatMap { s in alphabet.map { s + String($0) } }
            strings += frontier
        }

        var matched = 0
        for s in strings {
            let range = NSRange(location: 0, length: (s as NSString).length)
            guard GitHubRefLinkifier.refRe.firstMatch(in: s, range: range) != nil else { continue }
            matched += 1
            #expect(GitHubRefLinkifier.containsHashDigit(s), "refRe matches but the guard skips: \(s)")
        }
        // Guard against the alphabet silently failing to reach the regex.
        #expect(matched > 0)
    }
}
