import Foundation
import Testing
@testable import Scout

/// A task line is `subject — body`. The split has to happen on a separator
/// that is *outside* markdown tokens, otherwise a hyphen inside `**bold**`,
/// `` `code` ``, a `[[wikilink]]` or a `[label](url)` would truncate the
/// subject. Mirrors `action-items/render.py::_split_subject`.
@Suite("ActionItemsParser.splitSubjectBody")
struct SplitSubjectBodyTests {

    private func split(_ s: String) -> (subject: String, body: String) {
        let (subject, body) = ActionItemsParser.splitSubjectBody(s)
        return (subject, body)
    }

    // MARK: - separators

    @Test("an em dash splits subject from body")
    func emDashSplits() {
        let r = split("Reply to Priya — she needs the RFC by Friday")
        #expect(r.subject == "Reply to Priya")
        #expect(r.body == "she needs the RFC by Friday")
    }

    @Test("an en dash splits subject from body")
    func enDashSplits() {
        let r = split("Reply to Priya – she needs the RFC by Friday")
        #expect(r.subject == "Reply to Priya")
        #expect(r.body == "she needs the RFC by Friday")
    }

    @Test("a spaced hyphen splits subject from body")
    func spacedHyphenSplits() {
        let r = split("Reply to Priya - she needs the RFC by Friday")
        #expect(r.subject == "Reply to Priya")
        #expect(r.body == "she needs the RFC by Friday")
    }

    @Test("a colon-space is the fallback separator")
    func colonSpaceIsFallback() {
        let r = split("Blocked: waiting on Sam's review")
        #expect(r.subject == "Blocked")
        #expect(r.body == "waiting on Sam's review")
    }

    @Test("a dash separator wins over a later colon")
    func dashBeatsLaterColon() {
        let r = split("Ship the demo — status: still red")
        #expect(r.subject == "Ship the demo")
        #expect(r.body == "status: still red")
    }

    @Test("only the first separator splits; later ones stay in the body")
    func onlyTheFirstSeparatorSplits() {
        let r = split("A — B — C")
        #expect(r.subject == "A")
        #expect(r.body == "B — C")
    }

    @Test("surrounding whitespace is trimmed from both halves")
    func trimsBothHalves() {
        let r = split("  Subject   —   the body  ")
        #expect(r.subject == "Subject")
        #expect(r.body == "the body")
    }

    // MARK: - no separator

    @Test("a line with no separator is all subject")
    func noSeparatorMeansNoBody() {
        let r = split("Just a bare subject")
        #expect(r.subject == "Just a bare subject")
        #expect(r.body == "")
    }

    @Test("an unspaced hyphen is not a separator")
    func unspacedHyphenIsNotASeparator() {
        let r = split("Fix the well-known race in the queue-drain path")
        #expect(r.subject == "Fix the well-known race in the queue-drain path")
        #expect(r.body == "")
    }

    @Test("a colon with no following space is not a separator")
    func colonWithoutSpaceIsNotASeparator() {
        let r = split("Update https://example.com/docs")
        #expect(r.subject == "Update https://example.com/docs")
        #expect(r.body == "")
    }

    @Test("an empty line stays empty")
    func emptyInput() {
        let r = split("")
        #expect(r.subject == "")
        #expect(r.body == "")
    }

    // MARK: - separators inside markdown tokens are ignored

    @Test("a dash inside **bold** does not split")
    func dashInsideBoldIsIgnored() {
        let r = split("**Ship - the - demo** — actually the body")
        #expect(r.subject == "**Ship - the - demo**")
        #expect(r.body == "actually the body")
    }

    @Test("a dash inside ~~strikethrough~~ does not split")
    func dashInsideStrikeIsIgnored() {
        let r = split("~~Drop - this~~ — superseded by PROJ-1234")
        #expect(r.subject == "~~Drop - this~~")
        #expect(r.body == "superseded by PROJ-1234")
    }

    @Test("a dash inside `code` does not split")
    func dashInsideCodeIsIgnored() {
        let r = split("Run `git diff --cached - HEAD` — before landing")
        #expect(r.subject == "Run `git diff --cached - HEAD`")
        #expect(r.body == "before landing")
    }

    @Test("a dash inside a [[wikilink]] does not split")
    func dashInsideWikilinkIsIgnored() {
        let r = split("Read [[the - demo - notes]] — then reply")
        #expect(r.subject == "Read [[the - demo - notes]]")
        #expect(r.body == "then reply")
    }

    @Test("a dash inside a [label](url) does not split")
    func dashInsideMarkdownLinkIsIgnored() {
        let r = split("See [the PR](https://github.com/example-org/app/pull/42) — needs review")
        #expect(r.subject == "See [the PR](https://github.com/example-org/app/pull/42)")
        #expect(r.body == "needs review")
    }

    @Test("a colon inside a markdown link URL does not split")
    func colonInsideLinkURLIsIgnored() {
        let r = split("Check [thread](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)")
        #expect(r.subject
                == "Check [thread](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)")
        #expect(r.body == "")
    }

    @Test("a colon inside `code` does not split")
    func colonInsideCodeIsIgnored() {
        let r = split("Set `key: value` in the config")
        #expect(r.subject == "Set `key: value` in the config")
        #expect(r.body == "")
    }

    @Test("a colon inside **bold** does not split")
    func colonInsideBoldIsIgnored() {
        let r = split("**Status: red** — the demo is still failing")
        #expect(r.subject == "**Status: red**")
        #expect(r.body == "the demo is still failing")
    }

    // MARK: - token bookkeeping

    @Test("an unclosed bold token swallows the rest of the line")
    func unclosedBoldSwallowsSeparators() {
        // Deliberate: the scanner stays "inside" the token, so nothing splits.
        let r = split("**never closed — still the subject")
        #expect(r.subject == "**never closed — still the subject")
        #expect(r.body == "")
    }

    @Test("separators after a closed token still split")
    func closedTokenReleasesTheScanner() {
        let r = split("`code` **bold** [[wiki]] — the body")
        #expect(r.subject == "`code` **bold** [[wiki]]")
        #expect(r.body == "the body")
    }

    @Test("a separator before any token splits normally")
    func separatorBeforeTokensSplits() {
        let r = split("Subject — body with **bold** and `code`")
        #expect(r.subject == "Subject")
        #expect(r.body == "body with **bold** and `code`")
    }

    @Test("nested wikilinks inside a markdown link are handled")
    func nestedBracketsAreHandled() {
        let r = split("Review [[projects/the-demo]] and [[people/alex]] — due Friday")
        #expect(r.subject == "Review [[projects/the-demo]] and [[people/alex]]")
        #expect(r.body == "due Friday")
    }

    @Test("a short-prefix tag stays with the subject")
    func shortPrefixStaysInSubject() {
        let r = split("[#AI3026] Land the tracing job — blocked on review")
        #expect(r.subject == "[#AI3026] Land the tracing job")
        #expect(r.body == "blocked on review")
    }

    // MARK: - degenerate separators

    @Test("a line that is only a separator yields empty halves")
    func separatorOnlyLine() {
        let r = split(" — ")
        #expect(r.subject == "")
        #expect(r.body == "")
    }

    @Test("a trailing separator leaves an empty body")
    func trailingSeparator() {
        let r = split("Subject — ")
        #expect(r.subject == "Subject")
        #expect(r.body == "")
    }
}
