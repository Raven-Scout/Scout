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
        let a = settingsHelp("Run `scoutctl doctor` now")
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
