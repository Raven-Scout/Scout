// ScoutTests/PerFile/PerFileItemParserTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("PerFileItemParser")
struct PerFileItemParserTests {
    private func url(_ name: String) -> URL { URL(fileURLWithPath: "/tmp/\(name)") }

    @Test func parsesFullFrontmatterWishlist() throws {
        let text = """
        ---
        title: "Upgrade the graph system"
        status: in-progress
        priority: high
        date: 2026-06-12
        source: "Alex Slack DM"
        ---

        # Upgrade the graph system

        Evaluate the graph query engine.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-12-graph.md")))
        #expect(item.title == "Upgrade the graph system")
        #expect(item.status == .inProgress)
        #expect(item.priority == .high)
        #expect(item.date == "2026-06-12")
        #expect(item.source == "Alex Slack DM")
        #expect(item.area == nil)
        #expect(item.bodyMarkdown == "Evaluate the graph query engine.")   // H1 stripped
    }

    @Test func parsesResearchAreaAndUrgent() throws {
        let text = """
        ---
        title: Graph upgrade
        status: open
        priority: urgent
        date: 2026-06-10
        area: knowledge-graph
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.priority == .urgent)
        #expect(item.area == "knowledge-graph")
        #expect(item.source == nil)
    }

    @Test func defaultsAndFilenameDateFallback() throws {
        let text = "---\ntitle: No date here\n---\n\nbody"
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-16-no-date-here.md")))
        #expect(item.status == .open)        // missing -> open
        #expect(item.priority == .medium)    // missing -> medium
        #expect(item.date == "2026-06-16")   // from filename prefix
    }

    /// A retired research lane annotated its keyword fields with YAML inline
    /// comments. Read verbatim, `status` became an unknown value — which counts
    /// as active — so a done 200 KB item rendered on the Research tab and its
    /// comment-length status pill pushed the card off-screen.
    @Test func keywordFieldsIgnoreInlineYAMLComments() throws {
        let text = """
        ---
        title: "standing: the demo lane"
        status: done   # ⏹️ RETIRED 2026-10-09 `dreaming` — `expires:` reached; see §Expiry ("regardless of state").
        priority: medium   # 🔻 DEMOTED from `urgent` 2026-10-03 on Alex's direction
        date: 2026-08-25 # first logged
        area: demo
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.status == .done)
        #expect(item.isActive == false)
        #expect(item.priority == .medium)
        #expect(item.date == "2026-08-25")
    }

    @Test func keywordFieldIsBlankWhenWhollyAComment() throws {
        let text = "---\ntitle: T\nstatus: # TODO decide\npriority: #high\n---\n\nbody"
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.status == .open)        // blank -> open, as when missing
        #expect(item.priority == .medium)
    }

    private func status(_ line: String) throws -> ItemStatus {
        try #require(PerFileItemParser.parseFile(contents: "---\ntitle: T\nstatus: \(line)\n---\n\nbody",
                                                 fileURL: url("x.md"))).status
    }

    /// In YAML a `#` inside quotes is content, not a comment. A quoted
    /// `"done # pending review"` is not `done`, so it is an unknown status and
    /// stays active (#85) — reading it as done would hide unfinished work.
    @Test func quotedKeywordKeepsItsHash() throws {
        #expect(try status(#""done # pending review""#) == .unknown("done # pending review"))
        #expect(try status("'done # pending review'") == .unknown("done # pending review"))
        #expect(try status(#""done # pending review""#).isActive)
    }

    /// After a closing quote, the rest of the line is a comment.
    @Test func quotedKeywordWithTrailingComment() throws {
        #expect(try status(#""done" # retired"#) == .done)
        #expect(try status("'done' # retired") == .done)
        #expect(try status("'in-progress'") == .inProgress)
        #expect(try status(#""done"#) == .unknown(#""done"#))   // unterminated: verbatim, so active
    }

    /// The engine (`plugin/engine/scout/scripts/heartbeat.py` `_item_status`)
    /// reads a status as the first non-space token after `status:`. Where the
    /// value is a bare keyword, with or without a spaced comment, both sides
    /// must read the same status. Known differences are pinned below.
    @Test func agreesWithEngineFirstToken() throws {
        func engineToken(_ value: String) -> String {
            String(value.drop(while: \.isWhitespace).prefix(while: { !$0.isWhitespace })).lowercased()
        }
        for value in ["done   # retired, expires reached", "open # c", "in-progress # c",
                      "dropped", "Done", "p#1", "dropped#x"] {
            #expect(try status(value) == ItemStatus.parse(engineToken(value)), "status: \(value)")
        }
    }

    /// Where the app departs from the engine's first-token rule, on purpose:
    /// it follows YAML, which the engine only approximates.
    @Test func knownDifferencesFromEngine() throws {
        // A value that is wholly a comment is blank (engine reads `#`): open.
        #expect(try status("# TODO decide") == .open)
        // A spaced keyword is whole (engine reads `in`).
        #expect(try status("in progress # c") == .inProgress)
        // Quotes are YAML quoting (engine keeps them, so `"open"` isn't open there).
        #expect(try status(#""open""#) == .open)
    }

    /// Free-text fields keep their `#`: `Pattern #197` and `C#` are content,
    /// and the app has always shown these values verbatim.
    @Test func freeTextFieldsKeepHashes() throws {
        let text = """
        ---
        title: C# notes for issue #42
        status: open
        source: Pattern #197, logged by the evening run
        area: build #2
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.title == "C# notes for issue #42")
        #expect(item.source == "Pattern #197, logged by the evening run")
        #expect(item.area == "build #2")
    }

    @Test func returnsNilWhenNoFrontmatter() {
        #expect(PerFileItemParser.parseFile(contents: "# Just a heading\n\ntext", fileURL: url("a.md")) == nil)
    }
}
