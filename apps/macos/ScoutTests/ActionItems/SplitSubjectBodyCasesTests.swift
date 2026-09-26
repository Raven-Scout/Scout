import Foundation
import Testing
@testable import Scout

/// Named cases for `splitSubjectBody`, complementing `SplitSubjectBodyTests`.
///
/// That suite guards the de-quadratic rewrite (#100) with a generated
/// differential against the original implementation — it proves the new
/// scanner agrees with the old one, but a shared bug in both would pass. These
/// assert the *intended* behaviour directly, on the shapes the vault actually
/// produces: a colon inside a URL or a code span, a `[#TAG]` short prefix, an
/// unclosed token, and precedence between separators.
@Suite("splitSubjectBody — named cases")
struct SplitSubjectBodyCasesTests {

    private func split(_ s: String) -> (subject: String, body: String) {
        let (subject, body) = ActionItemsParser.splitSubjectBody(s)
        return (subject, body)
    }

    // MARK: - separator precedence

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

    // MARK: - near-misses that must not split

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

    // MARK: - colons inside tokens

    @Test("a colon inside a markdown link URL does not split")
    func colonInsideLinkURLIsIgnored() {
        let raw = "Check [thread](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)"
        let r = split(raw)
        #expect(r.subject == raw)
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
        // Pinned because it is the one place the scanner's state machine is
        // observably lossy, and a "fix" would change parsed output vault-wide.
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

    // MARK: - vault shapes

    @Test("a short-prefix tag stays with the subject")
    func shortPrefixStaysInSubject() {
        let r = split("[#AI3026] Land the tracing job — blocked on review")
        #expect(r.subject == "[#AI3026] Land the tracing job")
        #expect(r.body == "blocked on review")
    }

    @Test("consecutive wikilinks are handled")
    func consecutiveWikilinksAreHandled() {
        let r = split("Review [[projects/the-demo]] and [[people/alex]] — due Friday")
        #expect(r.subject == "Review [[projects/the-demo]] and [[people/alex]]")
        #expect(r.body == "due Friday")
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
