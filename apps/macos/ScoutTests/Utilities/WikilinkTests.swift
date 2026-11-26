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
    nonisolated static let cases: [(text: String, targets: [String])] = [
        ("See [[people/alex]] today", ["people/alex"]),
        ("See [[people/alex|Alex]] today", ["people/alex"]),
        (#"| [[people/priya\|Priya]] | lead |"#, ["people/priya"]),
        ("[[notes/one]] and [[notes/two#Status]]", ["notes/one", "notes/two#Status"]),
        // A `[` in the target, or an unmatched one in the alias: not a link,
        // so not a backlink.
        ("A [[notes/a[b]] here", []),
        ("A [[notes/a|b[c]] here", []),
        // A balanced `[…]` in the alias is fine.
        ("By [[people/alex|Alex [ext]]] today", ["people/alex"]),
        // An inner link is found where the old rule swallowed it.
        ("[[a[[notes/inner]]", ["notes/inner"]),
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

    @Test func aBracketedAliasRendersAsWritten() {
        let attr = InlineMarkdownText.attributedString(for: "By [[people/alex|Alex [ext]]] today")
        let linked = attr.runs.filter { $0.link?.scheme == "scout-wiki" }.map { String(attr[$0.range].characters) }
        #expect(linked.joined() == "Alex [ext]")
        #expect(String(attr.characters) == "By Alex [ext] today")
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

    /// ~50k characters each. The rules these readers had before took
    /// seconds on them (21 s for the trailing links), so a revert fails;
    /// the limit is generous because CI's macOS runners vary about 2×.
    nonisolated static let pathological: [(name: String, text: String)] = [
        ("unclosed links", String(repeating: "[[a", count: 16_667)),
        ("links then a word", String(repeating: "[[a]] ", count: 8_000) + "x"),
        ("bracketed aliases, never closed", String(repeating: "[[x|[a] ", count: 6_250)),
        ("an unmatched bracket in each alias", "[[x|" + String(repeating: "[a", count: 25_000)),
    ]

    @Test("a pathological 50k-character body reads quickly", arguments: pathological.map(\.name))
    func pathologicalTextIsFast(_ name: String) throws {
        let text = try #require(Self.pathological.first { $0.name == name }).text
        let elapsed = ContinuousClock().measure {
            _ = Wikilink.matches(in: text)
            _ = KnowledgeBaseService.extractWikilinks(text)
            _ = KBLinkRewrite.matches(in: text)
            _ = TaskBodyParser.splitTrailingLinks(text)
        }
        #expect(elapsed < .seconds(2), "\(name): \(elapsed)")
    }

    // MARK: Trailing links (the regex it replaced, kept as the specification)

    nonisolated static let oldTrailingLinksRe = try! NSRegularExpression(
        pattern: #"((?:\s*"# + Wikilink.ungroupedPattern + #")+)\s*$"#
    )

    static func oldSplitTrailingLinks(_ body: String) -> (prose: String, targets: [String]) {
        let ns = body as NSString
        guard let m = oldTrailingLinksRe.firstMatch(in: body, range: NSRange(location: 0, length: ns.length)) else {
            return (body, [])
        }
        let targets = Wikilink.matches(in: ns.substring(with: m.range(at: 1))).map(\.target)
        guard targets.count >= 2 else { return (body, []) }
        return (ns.substring(to: m.range.location).trimmingCharacters(in: .whitespacesAndNewlines), targets)
    }

    @Test func trailingLinksMatchTheOldRegexOnRandomText() {
        var rng = SeededGenerator(seed: 0x11A)
        let alphabet = ["[[a]]", "[[b|B]]", #"[[c\|C]]"#, "[[d|D [x]]]", "[[", "]]", "[", "]", "|", "a", " ", " ",
                        "\n", "\t", "\u{0B}", "\u{85}", "\u{A0}", "\u{2028}", "\u{3000}", "é", "😀"]
        for _ in 0..<5_000 {
            let s = (0..<Int.random(in: 0...12, using: &rng)).map { _ in alphabet.randomElement(using: &rng)! }.joined()
            let new = TaskBodyParser.splitTrailingLinks(s), old = Self.oldSplitTrailingLinks(s)
            #expect(new.prose == old.prose && new.targets == old.targets, "\(s.debugDescription)")
        }
    }
}
