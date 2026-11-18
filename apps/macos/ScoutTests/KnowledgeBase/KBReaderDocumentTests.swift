import Foundation
import Testing
@testable import Scout

/// The pure half of the web reader: what it renders, and every change it can
/// ask for, applied to the note's source line for line.
@Suite("KBReaderDocument — web reader edits")
@MainActor
struct KBReaderDocumentTests {

    private let note = """
    ---
    type: person
    ---
    # Alex

    Works on [[the-demo|the demo]] with [[Priya]].

    - Owns the rollout
      wrapped onto a second line
      - nested child
    - [ ] Send the recap
    - [x] Book the room

    > A quote
    > on two lines

    | Name | Role |
    | --- | --- |
    | [[Priya\\|P]] | Lead |

    ```
    [[not-a-link]]
    ```
    """

    // MARK: - display

    @Test("display markdown rewrites links on their own lines and keeps every line number")
    func display_keepsLineNumbers() {
        let display = KBReaderDocument.displayMarkdown(note, linkify: InlineMarkdownText.linkifiedMarkdown)
        let src = note.components(separatedBy: "\n")
        let out = display.components(separatedBy: "\n")
        #expect(out.count == src.count)
        #expect(out[5].contains("[the demo](scout-wiki://the-demo)"))
        #expect(out[5].contains("[Priya](scout-wiki://Priya)"))
        // The escaped table alias becomes a plain label, so the pipe can't split the row.
        #expect(out[18].contains("[P](scout-wiki://Priya)"))
    }

    @Test("frontmatter and fenced code are rendered exactly as written")
    func display_leavesFrontmatterAndCode() {
        let display = KBReaderDocument.displayMarkdown(note, linkify: { "X" + $0 })
        let out = display.components(separatedBy: "\n")
        #expect(out[0] == "---" && out[1] == "type: person" && out[2] == "---")
        #expect(out[21] == "[[not-a-link]]")
        #expect(out[3] == "X# Alex")
    }

    @Test("a linkifier that adds a newline is ignored for that line")
    func display_refusesSplitLines() {
        let display = KBReaderDocument.displayMarkdown("a\nb", linkify: { $0 + "\nextra" })
        #expect(display == "a\nb")
    }

    @Test("missing targets are the wikilinks that resolve to nothing")
    func missingTargets() {
        let missing = KBReaderDocument.missingTargets(in: note) { $0 == "Priya" ? "people/priya.md" : nil }
        #expect(missing == ["the-demo"])
        // A ticket id opens in Linear: it isn't a missing note.
        #expect(KBReaderDocument.missingTargets(in: "See [[PROJ-1234]] and [[nowhere]].") { _ in nil } == ["nowhere"])
    }

    // MARK: - tasks

    @Test("ticking a task flips only its box")
    func toggleTask() {
        let ticked = KBReaderDocument.toggleTask(in: note, line: 10)
        #expect(ticked?.components(separatedBy: "\n")[10] == "- [x] Send the recap")
        let unticked = KBReaderDocument.toggleTask(in: note, line: 11)
        #expect(unticked?.components(separatedBy: "\n")[11] == "- [ ] Book the room")
        #expect(KBReaderDocument.toggleTask(in: note, line: 7) == nil)   // not a task
        #expect(KBReaderDocument.toggleTask(in: note, line: 999) == nil)
    }

    // MARK: - comments

    @Test("a comment on a passage is quoted and lands on the line after it")
    func insertComment_paragraph() throws {
        let out = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 5, quote: "Works on", comment: "Is this still true?"))
        let lines = out.components(separatedBy: "\n")
        #expect(lines[6] == "//==<< on \"Works on\": Is this still true? >>==//")
        #expect(lines.count == note.components(separatedBy: "\n").count + 1)
        // Everything else is byte-identical.
        #expect(Array(lines[0...5]) == Array(note.components(separatedBy: "\n")[0...5]))
    }

    @Test("a comment inside a list item is indented to the item, so the list isn't split")
    func insertComment_listItem() throws {
        let out = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 7, quote: "Owns", comment: "Who now?"))
        #expect(out.components(separatedBy: "\n")[8] == "  //==<< on \"Owns\": Who now? >>==//")
        let nested = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 9, quote: nil, comment: "Done?"))
        #expect(nested.components(separatedBy: "\n")[10] == "    //==<< Done? >>==//")
    }

    @Test("a comment inside a blockquote keeps the quote prefix")
    func insertComment_quote() throws {
        let out = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 14, quote: "two lines", comment: "Source?"))
        #expect(out.components(separatedBy: "\n")[15] == "> //==<< on \"two lines\": Source? >>==//")
    }

    @Test("a comment after a table is set apart, so it doesn't become another row")
    func insertComment_table() throws {
        let out = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 18, quote: "Lead", comment: "Still?"))
        let lines = out.components(separatedBy: "\n")
        #expect(lines[18] == "| [[Priya\\|P]] | Lead |")
        #expect(lines[19] == "")
        #expect(lines[20] == "//==<< on \"Lead\": Still? >>==//")
    }

    @Test("a comment anchored on a blank line moves up to the block above it")
    func insertComment_blankAnchor() throws {
        // markdown-it counts the blank after a list's last item as part of it.
        let out = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 12, quote: "room", comment: "Which?"))
        let lines = out.components(separatedBy: "\n")
        #expect(lines[11] == "- [x] Book the room")
        #expect(lines[12] == "  //==<< on \"room\": Which? >>==//")
        #expect(lines[13] == "")
    }

    @Test("a cell is read back only from a plain table row")
    func cellReadBack() {
        #expect(KBReaderDocument.cell(note, line: 18, col: 0) == "[[Priya|P]]")
        #expect(KBReaderDocument.cell(note, line: 18, col: 1) == "Lead")
        #expect(KBReaderDocument.cell(note, line: 17, col: 0) == nil)   // separator
        #expect(KBReaderDocument.cell(note, line: 5, col: 0) == nil)    // prose
        #expect(KBReaderDocument.cell("> | a | b |", line: 0, col: 0) == nil)  // quoted table
    }

    @Test("an empty comment inserts nothing")
    func insertComment_empty() {
        #expect(KBReaderDocument.insertComment(in: note, afterLine: 5, quote: "x", comment: "   ") == nil)
    }

    @Test("retracting a comment removes its line, or just the marker when it shares a line")
    func removeMarker() throws {
        let marker = "//==<< on \"Works on\": Is this still true? >>==//"
        let withComment = try #require(KBReaderDocument.insertComment(
            in: note, afterLine: 5, quote: "Works on", comment: "Is this still true?"))
        #expect(KBReaderDocument.removeMarker(in: withComment, marker: marker, from: 5, to: 6) == note)

        let inline = "Text here \(marker) and more"
        #expect(KBReaderDocument.removeMarker(in: inline, marker: marker, from: 0, to: 0) == "Text here and more")

        let quoted = "> \(marker)"
        #expect(KBReaderDocument.removeMarker(in: "a\n\(quoted)\nb", marker: marker, from: 1, to: 1) == "a\nb")
        #expect(KBReaderDocument.removeMarker(in: note, marker: marker, from: 0, to: 3) == nil)
    }

    // MARK: - inline edits

    @Test("a block edit replaces only its lines; Discard gives the snapshot back")
    func inlineBlockEdit() {
        var edit = KBInlineEdit(target: .block(lineStart: 5, lineEnd: 5), snapshot: note,
                                original: "Works on [[the-demo|the demo]] with [[Priya]].",
                                buffer: "Works on [[the-demo|the demo]] with [[Priya]].")
        #expect(edit.applied == note)
        edit.buffer = "Works on the demo."
        let lines = edit.applied.components(separatedBy: "\n")
        #expect(lines[5] == "Works on the demo.")
        #expect(lines.count == note.components(separatedBy: "\n").count)
        #expect(edit.owns(edit.applied))
        #expect(!edit.owns(note))
    }

    @Test("lines(_:_:_:) reads a block back, and refuses a range off the end")
    func linesRange() {
        #expect(KBReaderDocument.lines(note, 3, 3) == "# Alex")
        #expect(KBReaderDocument.lines(note, 3, 999) == nil)
        #expect(KBReaderDocument.lines(note, 4, 3) == nil)
    }
}

@Suite("ScoutMarker — comments on a passage")
struct ScoutMarkerQuoteTests {
    @Test("a quoted marker round-trips")
    func roundTrip() throws {
        let m = try #require(ScoutMarker.format("Check this", quote: "the demo ships Friday"))
        #expect(m == "//==<< on \"the demo ships Friday\": Check this >>==//")
        #expect(ScoutMarker.isMarkerLine(m))
        let parts = try #require(ScoutMarker.parts(of: m))
        #expect(parts.quote == "the demo ships Friday")
        #expect(parts.comment == "Check this")
    }

    @Test("the quote is flattened, its double quotes softened and long ones clipped")
    func quoteSanitising() throws {
        let m = try #require(ScoutMarker.format("x", quote: "line one\n  \"two\" >>==// three"))
        #expect(m == "//==<< on \"line one 'two'  three\": x >>==//")
        let long = String(repeating: "a", count: 300)
        let longMarker = try #require(ScoutMarker.format("x", quote: long))
        let clipped = try #require(ScoutMarker.parts(of: longMarker))
        #expect(clipped.quote?.count == ScoutMarker.maxQuoteLength + 1)
        #expect(clipped.quote?.hasSuffix("…") == true)
    }

    @Test("no quote gives a plain marker; a plain marker has no quote")
    func plain() throws {
        #expect(ScoutMarker.format("Hello", quote: nil) == "//==<< Hello >>==//")
        #expect(ScoutMarker.format("Hello", quote: "  ") == "//==<< Hello >>==//")
        let parts = try #require(ScoutMarker.parts(of: "//==<< Hello >>==//"))
        #expect(parts.quote == nil && parts.comment == "Hello")
        #expect(ScoutMarker.format("  ", quote: "x") == nil)
    }
}

@Suite("KBMapPayload — what the web map draws")
struct KBMapPayloadTests {
    private let graph = KBGraph(
        nodes: [
            KBGraphNode(id: "knowledge-base/people/alex.md", label: "alex", group: .people, degree: 2, isCenter: false),
            KBGraphNode(id: "knowledge-base/projects/demo/demo.md", label: "demo", group: .projects, degree: 1, isCenter: false),
            KBGraphNode(id: "knowledge-base/topics/evals.md", label: "evals", group: .topics, degree: 1, isCenter: false),
        ],
        edges: [KBGraphEdge(from: "knowledge-base/people/alex.md", to: "knowledge-base/projects/demo/demo.md"),
                KBGraphEdge(from: "knowledge-base/topics/evals.md", to: "knowledge-base/people/alex.md"),
                KBGraphEdge(from: "knowledge-base/people/alex.md", to: "missing.md")])

    @Test("links become index pairs, and links to notes off the map are dropped")
    func links() {
        let p = KBMapPayload.build(from: graph)
        #expect(p.links == [[0, 1], [0, 2]])
        #expect(p.nodes.map(\.folder) == ["people", "projects/demo", "topics"])
    }

    @Test("groups come in legend order with counts, empty ones left out")
    func groups() {
        let p = KBMapPayload.build(from: graph)
        #expect(p.groups.map(\.key) == ["people", "projects", "topics"])
        #expect(p.groups.map(\.count) == [1, 1, 1])
    }

    @Test("modification times and the centre note come through")
    func modifiedAndCenter() {
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        let p = KBMapPayload.build(from: graph, center: "knowledge-base/topics/evals.md",
                                   modified: ["knowledge-base/people/alex.md": date])
        #expect(p.nodes[0].modified == 1_700_000_000)
        #expect(p.nodes[1].modified == 0)
        #expect(p.center == "knowledge-base/topics/evals.md")
        #expect(p.nodes[2].a11y == "evals, this note, 1 link")
        #expect(p.nodes[0].a11y == "alex, 2 links")
    }
}

@Suite("KnowledgeBaseService.summary — the map card's excerpt")
struct KBSummaryTests {
    @Test("skips frontmatter, headings and the history header; strips markdown")
    func summary() {
        let text = """
        ---
        type: person
        ---
        # Alex

        **Last updated:** 2026-10-01

        Alex leads **the demo** with [[people/priya|Priya]] and [[sam]], see [notes](https://example.com).
        //==<< ask about this >>==//
        """
        #expect(KnowledgeBaseService.summary(of: text)
                == "Alex leads the demo with Priya and sam, see notes.")
    }

    @Test("long summaries are clipped with an ellipsis")
    func clipped() {
        let text = String(repeating: "word ", count: 200)
        let s = KnowledgeBaseService.summary(of: text, maxLength: 50)
        #expect(s.count == 50)
        #expect(s.hasSuffix("…"))
    }
}
