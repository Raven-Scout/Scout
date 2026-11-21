import Foundation
import SwiftUI
import Testing
@testable import Scout

/// `PlainText`: markdown to plain text for snippets, log tails and titles, and
/// backtick code spans for engine text (UI audit T3). All inputs are synthetic.
@Suite("PlainText")
struct PlainTextTests {
    private func strip(_ s: String) -> String { PlainText.strippingMarkdown(s) }

    // MARK: Stripping

    @Test func boldItalicCodeAndStrikeKeepTheirWords() {
        #expect(strip("**Housekeeping:** pruned 3 notes") == "Housekeeping: pruned 3 notes")
        #expect(strip("Ship it _(after review)_ with `make test`") == "Ship it (after review) with make test")
        #expect(strip("*soon* and ~~never~~") == "soon and never")
        #expect(strip("***both***") == "both")
    }

    @Test func unbalancedBoldIsDropped() {
        #expect(strip("**Status** is fine but **cut off here") == "Status is fine but cut off here")
        #expect(strip("Revenue grew **fast") == "Revenue grew fast")
        #expect(strip("**A and **B**") == "A and B")
    }

    @Test func asterisksThatArentMarkupStay() {
        #expect(strip("src/**/*.swift") == "src/**/*.swift")
        #expect(strip("f(**opts)") == "f(**opts)")
        #expect(strip("2**8 is 256") == "2**8 is 256")
        #expect(strip("x ** 2 and a * b * c") == "x ** 2 and a * b * c")
        #expect(strip("snake_case_name and __init__") == "snake_case_name and __init__")
        #expect(strip("*args and **kwargs") == "*args and **kwargs")
        #expect(strip("pass **extra_fields through") == "pass **extra_fields through")
        #expect(strip("call it with **opts") == "call it with **opts")
    }

    /// Cut-off bold that only looked like kwargs: a capitalised or mid-sentence
    /// name, or an italic (`*Note:*`) taken for `*args` (review of #751).
    @Test(arguments: [
        ("**Params changed in config", "Params changed in config"),
        ("**options for the migration", "options for the migration"),
        ("Done: **Extra fields", "Done: Extra fields"),
        ("*Note:* the **deadline moved", "*Note:* the deadline moved"),
        ("*e.g.* the **deadline moved", "*e.g.* the deadline moved"),
    ])
    func cutOffBoldThatIsntKwargsIsDropped(_ text: String, _ cleaned: String) {
        #expect(InlineMarkupCleanup.clean(text) == cleaned)
        #expect(strip(text) == strip(cleaned))
    }

    @Test func titlesLoseOnlyInlineMarkup() {
        let title = PlainText.strippingInlineMarkdown
        #expect(title("> 1000 rows dropped") == "> 1000 rows dropped")
        #expect(title("# of runs, **doubled**") == "# of runs, doubled")
        #expect(title("a | b with `code` and [[notes/x|x]]") == "a | b with code and x")
    }

    @Test func linksBecomeTheirText() {
        #expect(strip("See [the plan](https://example.com/plan) and ![chart](img/c.png)") == "See the plan and chart")
        #expect(strip("Mail <https://example.com/x>") == "Mail https://example.com/x")
        #expect(strip("Linked from [[projects/demo/demo]] and [[notes/a|the alias]]") ==
                "Linked from demo and the alias")
        #expect(strip("[[people/Alex.md#Role]]") == "Alex")
        // A snippet cut at its length limit can end inside a wikilink.
        #expect(strip("Owner: [[people/Pri") == "Owner: people/Pri")
    }

    @Test func blockPrefixesGo() {
        #expect(strip("## Where it stands") == "Where it stands")
        #expect(strip("> > quoted twice") == "quoted twice")
        #expect(strip("- [ ] follow up with Sam") == "- follow up with Sam")
        #expect(strip("* a bullet") == "- a bullet")
        #expect(strip("- a dash bullet") == "- a dash bullet")
    }

    @Test func tableRowsReadAsCells() {
        #expect(strip("| [[projects/demo/demo]] | **owner** | Alex |") == "demo · owner · Alex")
        #expect(strip("| [[notes/a\\|alias]] | `a|b` | x |") == "alias · a|b · x")
        #expect(strip("|---|:---:|---|") == "")
        #expect(strip("| --- |") == "")
        let table = "| Date | What |\n|---|---|\n| Mon | **ship** |"
        #expect(strip(table) == "Date · What\nMon · ship")
    }

    @Test func escapesAndBreaks() {
        #expect(strip(#"a \*literal\* star and a\_b"#) == "a *literal* star and a_b")
        #expect(strip("one<br>two") == "one two")
        #expect(strip(#"C:\new\temp"#) == #"C:\new\temp"#)
    }

    @Test func plainTextIsUnchanged() {
        let s = "Morning briefing finished in 4 minutes, 2 notes updated."
        #expect(strip(s) == s)
    }

    @Test func aLogTailKeepsItsLines() {
        let log = "=== SCOUT run ===\n**Housekeeping:** done\n- [x] pruned `.scout-logs`\nexit 0"
        #expect(strip(log) == "=== SCOUT run ===\nHousekeeping: done\n- pruned .scout-logs\nexit 0")
        #expect(SummaryTab.fallbackPreview(of: log) == strip(log))
    }

    @Test func aLongLineIsCut() {
        let line = String(repeating: "word ", count: 1_000)
        let shown = strip(line + "**")
        #expect(shown.count == PlainText.maxLineLength + 1)
        #expect(shown.hasSuffix("…"))
    }

    /// A line of few characters but many UTF-16 units (combining marks) is
    /// cut too, where a character starts (review of #751).
    @Test func aLineOfCombiningMarksIsCutAtACharacter() {
        let heavy = "a" + String(repeating: "\u{0301}", count: 600)   // one character, 601 units
        let shown = strip(String(repeating: heavy, count: 10) + "**")
        #expect(shown.utf16.count <= PlainText.maxLineLength + 1)
        #expect(shown == String(repeating: heavy, count: 3) + "…")
    }

    @Test func aLogIsReadOffTheMainActor() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let log = dir.appendingPathComponent("run.log")
        try "start\n**Housekeeping:** done\nexit 0".write(to: log, atomically: true, encoding: .utf8)
        let loaded = try #require(SummaryTab.read(log))
        #expect(loaded.summary == nil)
        #expect(loaded.fallbackPreview == "start\nHousekeeping: done\nexit 0")
        #expect(SummaryTab.read(dir.appendingPathComponent("missing.log")) == nil)
    }

    // MARK: Untrusted text can't hang the main thread (review of #751)

    /// Long lines (10 KB, or 1.2M UTF-16 units) that made the old code-span and wikilink regexes take
    /// seconds each: a log tail of them took about a minute.
    static let pathological: [(name: String, line: String)] = [
        ("backticks", String(repeating: "`", count: 10_000)),
        ("backticks then text", String(repeating: "`", count: 5_000) + String(repeating: "a", count: 5_000)),
        ("short tick runs", String(repeating: "``a", count: 3_400)),
        ("unclosed wikilinks", String(repeating: "[[", count: 5_000)),
        ("unclosed links", String(repeating: "[a", count: 5_000)),
        ("unclosed bold", String(repeating: "**x ", count: 2_500)),
        ("stars", String(repeating: "*", count: 10_000)),
        ("underscores", String(repeating: "_a", count: 5_000)),
    ]

    @Test("a pathological line strips quickly", arguments: pathological.map(\.name))
    func pathologicalLineIsFast(_ name: String) throws {
        let line = try #require(Self.pathological.first { $0.name == name }).line
        let clock = ContinuousClock()
        let elapsed = clock.measure {
            _ = PlainText.strippingMarkdown(line)
            _ = PlainText.codeSpans(line)
            _ = InlineMarkupCleanup.clean(line)
        }
        #expect(elapsed < .seconds(2), "\(name): \(elapsed)")
    }

    /// 1.2M UTF-16 units in 600 characters: the cap counts units, so titles
    /// and snippets like this strip quickly on the main thread.
    @Test func aLineOfManyCombiningMarksStripsQuickly() {
        let line = String(repeating: "\\(" + "a" + String(repeating: "\u{0301}", count: 2_000), count: 600)
        let clock = ContinuousClock()
        let elapsed = clock.measure {
            _ = PlainText.strippingMarkdown(line)
            _ = PlainText.strippingInlineMarkdown(line)
        }
        #expect(elapsed < .seconds(2), "\(elapsed)")
    }

    @Test func aTailOfPathologicalLinesIsFast() {
        let log = Self.pathological.map(\.line).joined(separator: "\n") + "\n"
            + Array(repeating: String(repeating: "`", count: 10_000), count: 12).joined(separator: "\n")
        let clock = ContinuousClock()
        let elapsed = clock.measure { _ = SummaryTab.fallbackPreview(of: log) }
        #expect(elapsed < .seconds(5), "\(elapsed)")
    }

    // MARK: Code-span scanner

    /// The regex the scanner replaced, kept here as its specification.
    static let oldCodeSpanRe = try! NSRegularExpression(pattern: #"(`+)[^`\n].*?\1"#)

    static func oldRanges(_ s: String) -> [NSRange] {
        oldCodeSpanRe.matches(in: s, range: NSRange(location: 0, length: (s as NSString).length)).map(\.range)
    }

    @Test(arguments: [
        "", "no ticks", "`a`", "``a``", "``a`", "`a``", "``a `b` c``", "` `", "``", "`\n`", "`a\nb`",
        "x `a` y `b` z", "```a``b```", "`é`and`ü`", "`a\r\nb`", "`\ra`", "`` ` ``", "a `b",
    ])
    func scannerFindsTheOldRegexSpans(_ s: String) {
        #expect(CodeSpanScanner.ranges(in: s) == Self.oldRanges(s), "\(s.debugDescription)")
    }

    @Test func scannerMatchesTheOldRegexOnRandomText() {
        var rng = SystemRandomNumberGenerator()
        let alphabet: [Character] = ["`", "`", "`", "a", " ", "\n", "\r", "é"]
        for _ in 0..<3_000 {
            let s = String((0..<Int.random(in: 0...24, using: &rng)).map { _ in alphabet.randomElement(using: &rng)! })
            #expect(CodeSpanScanner.ranges(in: s) == Self.oldRanges(s), "\(s.debugDescription)")
        }
    }

    // MARK: Code spans

    @Test func codeSpansDropTheirBackticksAndAreMarkedAsCode() throws {
        let a = PlainText.codeSpans("Read the `integrity check:` lines, then run `scoutctl kb files`.")
        #expect(String(a.characters) == "Read the integrity check: lines, then run scoutctl kb files.")
        let code = a.runs.filter { $0.inlinePresentationIntent?.contains(.code) == true }
            .map { String(a[$0.range].characters) }
        #expect(code == ["integrity check:", "scoutctl kb files"])
        #expect(PlainText.codeSpansAsText("``a `tick` here``") == "a `tick` here")
        #expect(PlainText.codeSpansAsText("an odd ` backtick") == "an odd ` backtick")
    }

    /// Engine text can quote run logs and note names: only backticks are read,
    /// nothing becomes a link, and everything else shows as written.
    @Test(arguments: [
        ("See [the docs](https://example.com) or https://example.com/x",
         "See [the docs](https://example.com) or https://example.com/x"),
        ("Open <file:///etc/passwd> and [x](javascript:alert(1))",
         "Open <file:///etc/passwd> and [x](javascript:alert(1))"),
        ("`[[note]]` and [[note]] and **bold**", "[[note]] and [[note]] and **bold**"),
    ])
    func codeSpansNeverMakeLinksOrParseMarkdown(_ text: String, _ shown: String) {
        let a = PlainText.codeSpans(text)
        #expect(a.runs.allSatisfy { $0.link == nil })
        #expect(String(a.characters) == shown)
    }

    @Test func settingsHelpStylesCodeRuns() {
        let a = SettingsHelp.attributed("Run `scoutctl doctor` now")
        #expect(String(a.characters) == "Run scoutctl doctor now")
        #expect(a.runs.contains { $0.backgroundColor != nil })
    }

    // MARK: Counted nouns

    @Test func countedNouns() {
        #expect(PlainText.count(1, "warning") == "1 warning")
        #expect(PlainText.count(0, "warning") == "0 warnings")
        #expect(PlainText.count(3, "error line") == "3 error lines")
        #expect(PlainText.count(2, "copy", "copies") == "2 copies")
    }
}
