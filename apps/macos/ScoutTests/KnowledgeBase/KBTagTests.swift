// ScoutTests/KnowledgeBase/KBTagTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("KBTag recognition")
struct KBTagRecognitionTests {
    @Test(arguments: ["PROJB1", "RELONE", "XYZ1", "ABC1234", "AB", "Q9SAMPLE", "A1"])
    func acceptsWellFormedTags(_ body: String) {
        #expect(KBTag.normalized(body) == body)
        #expect(KBTag.normalized("#" + body) == body)
    }

    @Test(arguments: [
        "A",            // 1 char — too short
        "TOOLONGTAG",   // 9 chars — too long
        "555",          // all digits: a GitHub ref, not a tag
        "1234",
        "lower",        // lowercase
        "MiXeD",
        "WITH_UND",     // underscore not allowed
        "WITH-DASH",
        "",
    ])
    func rejectsMalformedTags(_ body: String) {
        #expect(KBTag.normalized(body) == nil)
    }

    @Test func findsBothBracketedAndBareForms() {
        #expect(KBTag.tags(in: "see [#PROJB1] and #RELONE today") == ["PROJB1", "RELONE"])
    }

    @Test func dedupesKeepingFirstAppearanceOrder() {
        #expect(KBTag.tags(in: "#BETA then #ALPHA then #BETA") == ["BETA", "ALPHA"])
    }

    @Test func numericRefIsNotATag() {
        // Stays with GitHubRefLinkifier — the two must not both claim it.
        #expect(KBTag.tags(in: "#647 and #1355 shipped").isEmpty)
        #expect(KBTag.linkify("#647 shipped") == "#647 shipped")
    }

    @Test(arguments: [
        "word#PROJB1",                        // mid-word
        "##PROJB1",                           // doubled hash
        "https://example.com/page#PROJB1",    // URL fragment
        "#PROJB1X",                           // longer tag, not a prefix match
        "#PROJB1x",                           // trailing lowercase
        "#projb1",                            // lowercase
    ])
    func doesNotMatchNonTagContexts(_ text: String) {
        #expect(!KBTag.tags(in: text).contains("PROJB1"))
    }

    @Test(arguments: ["#PROJB1's owner", "#PROJB1, next", "#PROJB1.", "(#PROJB1)", "#PROJB1\nnext"])
    func matchesUpToNonWordPunctuation(_ text: String) {
        #expect(KBTag.tags(in: text) == ["PROJB1"])
    }

    @Test func ignoresTagsInsideProtectedSpans() {
        #expect(KBTag.tags(in: "`#PROJB1`").isEmpty)                    // inline code
        #expect(KBTag.tags(in: "[[#PROJB1]]").isEmpty)                  // wikilink
        #expect(KBTag.tags(in: "[label](https://x.com/#PROJB1)").isEmpty) // markdown link
    }
}

@Suite("KBTag linkify")
struct KBTagLinkifyTests {
    @Test func bareTagBecomesASchemeLink() {
        let out = KBTag.linkify("ship #PROJB1 now")
        #expect(out.contains("](scout-tag://PROJB1)"))
        #expect(out.hasPrefix("ship ["))
        #expect(out.hasSuffix(" now"))
    }

    @Test func bracketedTagLosesItsBracketsAndRendersLikeTheBareForm() {
        let bare = KBTag.linkify("#PROJB1")
        let bracketed = KBTag.linkify("[#PROJB1]")
        #expect(bare == bracketed)
    }

    @Test func multipleTagsAllRewritten() {
        let out = KBTag.linkify("#ALPHA and #BETA and [#GAMMA]")
        #expect(out.contains("scout-tag://ALPHA"))
        #expect(out.contains("scout-tag://BETA"))
        #expect(out.contains("scout-tag://GAMMA"))
    }

    @Test func leavesNonTagTextByteIdentical() {
        for s in ["plain prose", "#647 ref", "`#TAG`", "[[wiki]]", "a/b#12", ""] {
            #expect(KBTag.linkify(s) == s, "mutated \(s.debugDescription)")
        }
    }

    /// The chip label must still read as the tag once the padding is stripped —
    /// the hair spaces are inset, not content.
    @Test func labelIsTheTagPlusInsetOnly() {
        let out = KBTag.linkify("#PROJB1")
        let label = out.drop(while: { $0 != "[" }).dropFirst().prefix(while: { $0 != "]" })
        #expect(label.replacingOccurrences(of: "\u{2009}", with: "") == "#PROJB1")
    }
}

@MainActor
@Suite("Tag search")
struct KBTagSearchTests {
    /// A KB where tags overlap by prefix and by filename, so substring
    /// semantics and tag semantics give visibly different answers.
    private func makeTaggedKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbtag-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        func write(_ name: String, _ body: String) throws {
            try body.write(to: kb.appendingPathComponent(name), atomically: true, encoding: .utf8)
        }
        try write("alpha.md",   "# Alpha\nTracking #RELONE this week.")
        try write("bravo.md",   "# Bravo\nBracketed [#RELONE] form.")
        try write("charlie.md", "# Charlie\nDifferent tag #RELONEX here.")
        try write("delta.md",   "# Delta\nNo tags, but mentions relone in prose.")
        try write("RELONE.md",  "# Filename only\nBody has no tag at all.")
        return root
    }

    private func paths(_ hits: [KBSearchHit]) -> Set<String> {
        Set(hits.map { ($0.path as NSString).lastPathComponent })
    }

    @Test func tagQueryMatchesBothFormsAndNothingElse() async throws {
        let root = try makeTaggedKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        let hits = paths(svc.searchContent("#RELONE"))
        #expect(hits.contains("alpha.md"))          // bare form
        #expect(hits.contains("bravo.md"))          // bracketed form
        #expect(!hits.contains("charlie.md"))       // #RELONEX is a different tag
        #expect(!hits.contains("delta.md"))         // prose word, not a tag
        #expect(!hits.contains("RELONE.md"))        // filename, not content
    }

    @Test func nonTagQueryKeepsSubstringSemantics() async throws {
        let root = try makeTaggedKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        // No leading '#' → the ordinary case-insensitive substring search,
        // which should still find the prose mention and the filename.
        let hits = paths(svc.searchContent("relone"))
        #expect(hits.contains("delta.md"))
        #expect(hits.contains("RELONE.md"))
        #expect(hits.contains("charlie.md"))
    }
}

@Suite("Tag chip rendering")
@MainActor
struct KBTagChipRenderingTests {
    /// The tag run must come out of `AttributedString(markdown:)` still
    /// carrying the custom scheme — that link is both the click target and the
    /// selector the chip styling keys off.
    @Test func tagRunKeepsItsSchemeLink() {
        let s = InlineMarkdownText.attributedString(for: "ship #PROJB1 now")
        let tagRuns = s.runs.filter { $0.link?.scheme == KBTag.scheme }
        #expect(tagRuns.count == 1)
        #expect(tagRuns.first?.link?.host == "PROJB1")
    }

    @Test func tagRunCarriesTheChipAttributes() {
        let s = InlineMarkdownText.attributedString(for: "#PROJB1")
        let run = s.runs.first { $0.link?.scheme == KBTag.scheme }
        #expect(run != nil)
        #expect(run?.foregroundColor == DS.Accent.ink)
        #expect(run?.backgroundColor == DS.Accent.wash)
        #expect(run?.underlineStyle == nil)
    }

    /// The chip is styled; the prose around it must not be.
    @Test func surroundingProseIsUnstyled() {
        let s = InlineMarkdownText.attributedString(for: "before #PROJB1 after")
        let plain = s.runs.filter { $0.link == nil }
        #expect(!plain.isEmpty)
        #expect(plain.allSatisfy { $0.backgroundColor == nil })
    }

    @Test func visibleTextStillReadsAsTheTag() {
        let s = InlineMarkdownText.attributedString(for: "ship #PROJB1 now")
        let text = String(s.characters).replacingOccurrences(of: "\u{2009}", with: "")
        #expect(text == "ship #PROJB1 now")
    }

    /// A numeric ref must render as a GitHub link, not a tag chip — the two
    /// rewriters sharing one string is the main composition risk.
    @Test func numericRefRendersAsAGitHubLinkNotAChip() {
        let s = InlineMarkdownText.attributedString(for: "example-org/scout#647 shipped")
        #expect(!s.runs.contains { $0.link?.scheme == KBTag.scheme })
        #expect(s.runs.contains { $0.link?.host == "github.com" })
    }

    @Test func wikilinkAndTagCoexist() {
        let s = InlineMarkdownText.attributedString(for: "[[people|Alex]] owns #PROJB1")
        #expect(s.runs.contains { $0.link?.scheme == "scout-wiki" })
        #expect(s.runs.contains { $0.link?.scheme == KBTag.scheme })
    }
}

@Suite("KBTag + GitHubRefLinkifier composition")
struct KBTagCompositionTests {
    /// The real pipeline order in InlineMarkdownText. Tags and numeric refs
    /// coexist in one line all over the vault's research notes.
    private func pipeline(_ s: String) -> String {
        GitHubRefLinkifier.linkify(KBTag.linkify(s))
    }

    @Test func tagAndGitHubRefInOneLineBothSurvive() {
        let out = pipeline("example-org/scout#647 closes #PROJB1")
        #expect(out.contains("scout-tag://PROJB1"))
        #expect(out.contains("github.com/example-org/scout/issues/647"))
    }

    @Test func githubLinkifierDoesNotReenterAnEmittedTagLink() {
        // A tag link's URL must come out intact — not re-linkified or nested.
        let out = pipeline("#PROJB1")
        #expect(out.contains("](scout-tag://PROJB1)"))
        #expect(!out.contains("github.com"))
    }

    @Test func bareRefStillResolvesWhenATagIsAlsoPresent() {
        // The tag rewrite must not inject anything that looks like a second
        // repo slug, which would make the bare #647 ambiguous and unlinked.
        let out = pipeline("in example-org/scout, #647 fixes #PROJB1")
        #expect(out.contains("github.com/example-org/scout/issues/647"))
        #expect(out.contains("scout-tag://PROJB1"))
    }
}
