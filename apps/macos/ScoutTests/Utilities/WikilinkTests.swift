// ScoutTests/Utilities/WikilinkTests.swift
import Foundation
import Testing
@testable import Scout

/// Every reader of `[[wikilinks]]` follows the one rule in `Wikilink`, so a
/// link the knowledge base counts (as a link, a backlink or a rename target)
/// is a link that renders, and the other way round. MainActor because
/// `InlineMarkdownText.attributedString` is (see `InlineMarkdownTextTests`).
@MainActor
@Suite("Wikilink — one rule for every reader")
struct WikilinkTests {

    /// The text, and the targets every reader should see in it.
    static let cases: [(text: String, targets: [String])] = [
        ("See [[people/alex]] today", ["people/alex"]),
        ("See [[people/alex|Alex]] today", ["people/alex"]),
        (#"| [[people/priya\|Priya]] | lead |"#, ["people/priya"]),
        ("[[notes/one]] and [[notes/two#Status]]", ["notes/one", "notes/two#Status"]),
        // A `[` in the target or the alias: not a link, so not a backlink.
        ("A [[notes/a[b]] here", []),
        ("A [[notes/a|b[c]] here", []),
        // An unclosed `[[` gives up at the next one; the second is still a link.
        ("[[open then [[notes/closed]]", ["notes/closed"]),
        // A line break in the target or the alias: not a link either.
        ("A [[notes/a\nb]] here", []),
        ("A [[notes/a|b\nc]] here", []),
        // A link on the next line is found.
        ("first line\n[[notes/next]]", ["notes/next"]),
    ]

    @Test(arguments: cases.indices)
    func everyReaderSeesTheSameLinks(_ i: Int) {
        let (text, targets) = Self.cases[i]
        #expect(Wikilink.matches(in: text).map(\.target) == targets, "\(text.debugDescription)")
        #expect(KnowledgeBaseService.extractWikilinks(text) == targets, "extract: \(text.debugDescription)")
        #expect(KBLinkRewrite.matches(in: text).map(\.target) == targets, "rename: \(text.debugDescription)")
        #expect(Self.renderedTargets(text) == targets, "rendered: \(text.debugDescription)")
    }

    /// The decoded `scout-wiki://` targets `InlineMarkdownText` renders.
    static func renderedTargets(_ text: String) -> [String] {
        InlineMarkdownText.attributedString(for: text).runs
            .compactMap(\.link)
            .filter { $0.scheme == "scout-wiki" }
            .compactMap { $0.absoluteString.dropFirst("scout-wiki://".count).removingPercentEncoding }
    }

    @Test func groupsAndAliases() {
        #expect(Wikilink.matches(in: "[[a|Alias]]") == [
            Wikilink.Match(range: NSRange(location: 0, length: 11), target: "a", alias: "Alias"),
        ])
        // The table form's `\|` is the separator, not part of the target.
        #expect(Wikilink.matches(in: #"[[a\|b]]"#).first?.alias == "b")
        #expect(Wikilink.matches(in: "[[a]]").first?.alias == nil)
        #expect(Wikilink.whole("[[a|b]]")?.target == "a")
        #expect(Wikilink.whole(" [[a]]") == nil)
        #expect(Wikilink.whole("[[a]] [[b]]") == nil)
    }

    @Test func refsTokensUseTheRule() {
        #expect(ActionItemsParser.parseRefs(#"[[people/alex\|Alex]]"#) == [.entity(path: "people/alex", label: "Alex")])
        #expect(ActionItemsParser.parseRefs("[[people/a[b]]") == [.plainRef(text: "[[people/a[b]]")])
        #expect(ActionItemsParser.parseRefs("[[people/sam|Sam]] · [[x|y[z]]") == [
            .entity(path: "people/sam", label: "Sam"), .plainRef(text: "[[x|y[z]]"),
        ])
    }

    @Test func trailingLinkChipsUseTheRule() {
        #expect(TaskBodyParser.blocks(from: "Ping them. [[people/alex]] [[people/sam]]") == [
            .paragraph(label: nil, text: "Ping them."), .links(["people/alex", "people/sam"]),
        ])
        #expect(TaskBodyParser.blocks(from: #"Ping them. [[people/alex\|Alex]] [[people/sam]]"#).last
            == .links(["people/alex", "people/sam"]))
        // The last one isn't a link, so there's no trailing run of links to pull out.
        let body = "Ping them. [[people/alex]] [[people/a[b]]"
        #expect(TaskBodyParser.blocks(from: body) == [.paragraph(label: nil, text: body)])
    }

    /// An unclosed `[[` after every character used to scan to the end of
    /// the text from each one, in the readers that allowed `[` inside.
    @Test func unclosedLinksAreFast() {
        let text = String(repeating: "[[a", count: 5_000)
        let elapsed = ContinuousClock().measure {
            _ = KnowledgeBaseService.extractWikilinks(text)
            _ = KBLinkRewrite.matches(in: text)
            _ = TaskBodyParser.blocks(from: text)
        }
        #expect(elapsed < .seconds(2), "\(elapsed)")
    }
}
