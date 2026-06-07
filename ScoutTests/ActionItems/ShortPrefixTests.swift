import Testing
import Foundation
@testable import Scout

@Suite("ActionItemsParser.extractShortPrefix — `[#XXXX]` marker")
struct ExtractShortPrefixTests {
    @Test func extractsValidCrockfordPrefix() {
        let (prefix, rest) = ActionItemsParser.extractShortPrefix("[#A3F7] **subject** body")
        #expect(prefix == "A3F7")
        #expect(rest == "**subject** body")
    }

    @Test func returnsNilWhenNoPrefix() {
        let (prefix, rest) = ActionItemsParser.extractShortPrefix("**subject** body")
        #expect(prefix == nil)
        #expect(rest == "**subject** body")
    }

    @Test func rejectsInvalidTags() {
        // New grammar: 2–8 [A-Z0-9] with >=1 letter. These must all return nil.
        for bad in [
            "[#abcd] subj",      // lowercase
            "[#A] subj",         // too short (<2)
            "[#ABCDEFGHI] subj", // too long (>8)
            "[#555] subj",       // pure digits (reserved for GitHub refs)
            "[#0000] subj",      // pure digits
            "[#A-3] subj",       // punctuation
        ] {
            let (prefix, _) = ActionItemsParser.extractShortPrefix(bad)
            #expect(prefix == nil, "expected nil for \(bad); got \(prefix ?? "nil")")
        }
    }

    @Test func acceptsVariableLengthSemanticTags() {
        // Previously-rejected shapes that the widened grammar now accepts:
        // non-Crockford letters (I/L/O/U), and lengths other than 4.
        for (input, expected) in [
            ("[#ILOU] subj", "ILOU"),   // non-Crockford letters
            ("[#A3F] subj", "A3F"),     // 3 chars
            ("[#AI3026] subj", "AI3026"), // 6 chars, contains I
            ("[#5864M] subj", "5864M"), // digit-led, has a letter
        ] {
            let (prefix, _) = ActionItemsParser.extractShortPrefix(input)
            #expect(prefix == expected, "expected \(expected) for \(input); got \(prefix ?? "nil")")
        }
    }

    @Test func tolerantOfNoSpaceAfterBracket() {
        // `[#ABCD]**bold**` (no separating space) should still parse.
        let (prefix, rest) = ActionItemsParser.extractShortPrefix("[#ABCD]**bold**")
        #expect(prefix == "ABCD")
        #expect(rest == "**bold**")
    }

    @Test func consumesExtraWhitespaceAfterBracket() {
        let (prefix, rest) = ActionItemsParser.extractShortPrefix("[#ABCD]   **bold**")
        #expect(prefix == "ABCD")
        #expect(rest == "**bold**")
    }

    @Test func ignoresPrefixNotAtStart() {
        // The marker only counts at the very start of the body.
        let (prefix, rest) = ActionItemsParser.extractShortPrefix("**Reference [#ABCD]** body")
        #expect(prefix == nil)
        #expect(rest == "**Reference [#ABCD]** body")
    }
}

@Suite("ActionItems end-to-end parse with [#XXXX] prefix")
struct ActionItemsParseWithPrefixTests {
    @Test func prefixSurvivesAttachedSubBulletComment() throws {
        // Regression: an attached `  - author: text` sub-bullet comment
        // would silently rebuild ActionTask without `shortPrefix`, dropping
        // the prefix to nil. After: the writer fell back to --subject and
        // failed on em-dash/Unicode-heavy bold portions. v0.5.6 fixes.
        let md = """
        # Action Items — 2026-05-22

        ## 🔴 Urgent

        - [ ] [#G808] **🔥 Confirm or drop Andrea/Procházka call — WINDOW STAYED CLOSED Thu** _(carries from 5/21)_
          - user: Test
        """
        let doc = try ActionItemsParser.parse(
            text: md,
            sourceURL: URL(fileURLWithPath: "/tmp/action-items-2026-05-22.md"),
            sourceBytes: md.utf8.count
        )
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        let task = try #require(urgent.tasks.first)
        #expect(task.shortPrefix == "G808")
        #expect(task.comments.count == 1)
        #expect(task.comments.first?.text == "Test")
    }

    @Test func prefixSurvivesAttachedBlockquoteComment() throws {
        let md = """
        # Action Items — 2026-05-22

        ## 🔴 Urgent

        - [ ] [#ABCD] **A task** body
          > jordan: a blockquote comment
        """
        let doc = try ActionItemsParser.parse(
            text: md,
            sourceURL: URL(fileURLWithPath: "/tmp/action-items-2026-05-22.md"),
            sourceBytes: md.utf8.count
        )
        let task = try #require(doc.sections.first?.tasks.first)
        #expect(task.shortPrefix == "ABCD")
        #expect(task.comments.count == 1)
    }

    @Test func prefixSurvivesAttachedInlineObsidianComment() throws {
        let md = """
        # Action Items — 2026-05-22

        ## 🔴 Urgent

        - [ ] [#EFGH] **A task** body
          //==<< an Obsidian inline-comment >>==//
        """
        let doc = try ActionItemsParser.parse(
            text: md,
            sourceURL: URL(fileURLWithPath: "/tmp/action-items-2026-05-22.md"),
            sourceBytes: md.utf8.count
        )
        let task = try #require(doc.sections.first?.tasks.first)
        #expect(task.shortPrefix == "EFGH")
        #expect(task.comments.count == 1)
    }

    @Test func parsesPrefixIntoTaskField() throws {
        let md = """
        # Action Items — 2026-05-22

        ## 🔴 Urgent

        - [ ] [#A3F7] **Reply to Procházka thread** _(carries from 5/19)_
        - [ ] **Legacy task without prefix** — should still parse with shortPrefix=nil
        """
        let doc = try ActionItemsParser.parse(
            text: md,
            sourceURL: URL(fileURLWithPath: "/tmp/action-items-2026-05-22.md"),
            sourceBytes: md.utf8.count
        )
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        try #require(urgent.tasks.count == 2)

        let prefixed = urgent.tasks[0]
        #expect(prefixed.shortPrefix == "A3F7")
        // Subject reflects the post-prefix body (no `[#A3F7]` pollution).
        #expect(prefixed.subject.contains("Reply to Procházka thread"))
        #expect(!prefixed.subject.contains("[#A3F7]"))

        let legacy = urgent.tasks[1]
        #expect(legacy.shortPrefix == nil)
        #expect(legacy.subject.contains("Legacy task without prefix"))
    }
}
