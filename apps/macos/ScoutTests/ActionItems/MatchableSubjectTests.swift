import Testing
import Foundation
@testable import Scout

@Suite("ActionTask.matchableSubject — scoutctl --subject key derivation")
struct MatchableSubjectTests {
    @Test func extractsBoldPortionWhenPresent() {
        // The case from issue #10 / screenshot: bold subject followed by
        // italic parenthetical. Sending the full plainSubject runs into the
        // italic body and scoutctl fails to substring-match it. Bold-only
        // matches reliably.
        let task = make(
            subject: "**🔥 🆕 Update the pricing calculator app with per-client conversion levers + margin maximizer** _(net-new from the pricing review 6-7 AM ET; Alex already iterating during meeting)_",
            plainSubject: "🔥 🆕 Update the pricing calculator app with per-client conversion levers + margin maximizer _(net-new from the pricing review 6-7 AM ET; Alex already iterating during meeting)_"
        )
        #expect(task.matchableSubject == "🔥 🆕 Update the pricing calculator app with per-client conversion levers + margin maximizer")
    }

    @Test func preservesInnerMarkdownInBoldPortion() {
        // scoutctl's cleaned title keeps `[[wikilinks]]` and `[label](url)`
        // verbatim — its parser doesn't reduce them. So Scout's needle must
        // keep the brackets too, otherwise the substring lookup misses.
        let task = make(
            subject: "**Reply to MJ on [[MKT-301]] with consolidated GA-scope answer** _(carries from 5/15…)_",
            plainSubject: "Reply to MJ on MKT-301 with consolidated GA-scope answer _(carries from 5/15…)_"
        )
        #expect(task.matchableSubject == "Reply to MJ on [[MKT-301]] with consolidated GA-scope answer")
    }

    @Test func preservesMarkdownLinkInBoldPortion() {
        // The screenshot case from issue #10: bold subject contains
        // `[PR #N (text)](url)`. scoutctl's title also keeps the link raw,
        // so we have to keep it too — stripping it would yield a substring
        // that doesn't exist in the title.
        let task = make(
            subject: "**🔥 🆕 Close [PR #5526 (PROJ-3079 sandboxId metadata)](https://github.com/example-org/ui/pull/5526) with re-implement-on-OTel note** _(promoted 7:04 AM ET 5/20…)_",
            plainSubject: "🔥 🆕 Close PR #5526 (PROJ-3079 sandboxId metadata) with re-implement-on-OTel note _(promoted 7:04 AM ET 5/20…)_"
        )
        #expect(task.matchableSubject == "🔥 🆕 Close [PR #5526 (PROJ-3079 sandboxId metadata)](https://github.com/example-org/ui/pull/5526) with re-implement-on-OTel note")
    }

    @Test func stripsPriorityEmojiInsideBold() {
        // The 2026-05-28 regression: scout-plugin commit 3071486 moved
        // `--subject` matching from `raw_line` onto the cleaned title, which
        // strips PRIORITY_EMOJI (🔴/🟡/🟢) anywhere. Scout was still sending
        // the raw bold including 🔴, so the substring lookup missed every
        // urgent task with a markdown link in the title. Mirror scoutctl's
        // cleanup here.
        let task = make(
            subject: "[#OIDC-MERGE] **🔴 Merge [mcp-server PR #546](https://github.com/example-org/mcp-server/pull/546) (PROJ-3295)** _(APPROVED…)_",
            plainSubject: "🔴 Merge mcp-server PR #546 (PROJ-3295) _(APPROVED…)_"
        )
        #expect(task.matchableSubject == "Merge [mcp-server PR #546](https://github.com/example-org/mcp-server/pull/546) (PROJ-3295)")
    }

    @Test func stripsLeadingStatusEmoji() {
        // STATUS_EMOJI (✅/🔄/❓/⬜) is anchored to the start of scoutctl's
        // cleaned title. If Scout extracts a bold portion that begins with
        // one, drop it so the substring matches.
        let task = make(
            subject: "**✅ DONE 12:01 PM ET** — Alex merged himself.",
            plainSubject: "✅ DONE 12:01 PM ET — Alex merged himself."
        )
        #expect(task.matchableSubject == "DONE 12:01 PM ET")
    }

    @Test func unwrapsStrikethrough() {
        // STRIKETHROUGH `~~foo~~` is reduced to `foo` in scoutctl's title.
        let task = make(
            subject: "**~~Send report~~** _(superseded by Alex)_",
            plainSubject: "Send report _(superseded by Alex)_"
        )
        #expect(task.matchableSubject == "Send report")
    }

    @Test func trimsAtItalicParenWhenNoBold() {
        // Older/unstyled tasks: no bold marker, but still have an italic
        // body. Trim at the body separator so the match key is just the head.
        let task = make(
            subject: "Send the BAA forms _(carries from 5/16)_",
            plainSubject: "Send the BAA forms _(carries from 5/16)_"
        )
        #expect(task.matchableSubject == "Send the BAA forms")
    }

    @Test func trimsAtEmDashWhenNoBold() {
        let task = make(
            subject: "Merge PR #74 — sl-builder v2",
            plainSubject: "Merge PR #74 — sl-builder v2"
        )
        #expect(task.matchableSubject == "Merge PR #74")
    }

    @Test func passesThroughWhenNoMarkupNoSeparator() {
        let task = make(
            subject: "Drink water",
            plainSubject: "Drink water"
        )
        #expect(task.matchableSubject == "Drink water")
    }

    @Test func boldPortionWinsOverBodySeparator() {
        // Both a bold marker AND an em-dash inside it: bold extraction
        // takes the whole bold portion (including the em-dash), then
        // body separator trimming doesn't apply because we returned early.
        let task = make(
            subject: "**Andrea — Soustruh koncert** _(today 7:30 PM)_",
            plainSubject: "Andrea — Soustruh koncert _(today 7:30 PM)_"
        )
        #expect(task.matchableSubject == "Andrea — Soustruh koncert")
    }

    // MARK: - Helper

    private func make(subject: String, plainSubject: String) -> ActionTask {
        ActionTask(
            id: UUID(),
            lineNumber: 1,
            done: false,
            subject: subject,
            plainSubject: plainSubject,
            body: "",
            comments: [],
            deepLinks: [],
            snoozedUntil: nil,
            carriedInFrom: nil
        )
    }
}
