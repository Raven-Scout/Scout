import AppKit
import SwiftUI
import Testing
@testable import Scout

/// The live-preview markdown editor is an `NSViewRepresentable` wrapping an
/// `NSTextView`, so its inline highlighter only runs once a real text view
/// exists. Hosting it exercises `makeNSView` plus the windowed restyle pass
/// over every token class the KB depends on.
@MainActor
@Suite("KBLiveEditor smoke", .serialized)
struct KBLiveEditorSmokeTests {

    private let editorSize = CGSize(width: 800, height: 600)

    /// A document touching every construct the highlighter special-cases.
    private static let richDocument = """
    ---
    type: person
    aliases: [Alex, alex]
    ---
    # Alex

    ## Context

    A paragraph with **bold**, _italic_, `inline code`, a [[wikilink]], a
    [labelled link](https://github.com/example-org/app/pull/42), and a [#MIRO] tag.

    ### Work

    - a bullet with [[projects/the-demo]]
    - another bullet
    1. an ordered item
    2. a second item

    > a blockquote line
    > continued

    | Column | Value |
    | ------ | ----- |
    | alpha  | 1     |
    | beta   | 2     |

    ```swift
    let x = "a fenced code block"
    // # not a heading — inside a fence
    ```

    ---

    Trailing paragraph after a horizontal rule.
    """

    @Test("the live editor renders a document using every token class")
    func rendersRichDocument() {
        var text = Self.richDocument
        let binding = Binding(get: { text }, set: { text = $0 })
        ViewHost.render(KBLiveEditor(text: binding), size: editorSize)
    }

    @Test("the live editor renders an empty document")
    func rendersEmptyDocument() {
        var text = ""
        let binding = Binding(get: { text }, set: { text = $0 })
        ViewHost.render(KBLiveEditor(text: binding), size: editorSize)
    }

    @Test("the live editor renders a document with only frontmatter")
    func rendersFrontmatterOnly() {
        var text = "---\ntype: project\nstatus: active\n---\n"
        let binding = Binding(get: { text }, set: { text = $0 })
        ViewHost.render(KBLiveEditor(text: binding), size: editorSize)
    }

    @Test("the live editor renders an unterminated code fence")
    func rendersUnterminatedFence() {
        var text = "# Heading\n\n```\nnever closed\nstill inside the fence\n"
        let binding = Binding(get: { text }, set: { text = $0 })
        ViewHost.render(KBLiveEditor(text: binding), size: editorSize)
    }

    @Test("the live editor renders a long document, exercising the windowed restyle")
    func rendersLongDocument() {
        // Long enough that only a window of the document is styled per pass.
        var text = (0..<400).map { i in
            "## Section \(i)\n\nA paragraph with **bold** and [[link-\(i)]].\n"
        }.joined(separator: "\n")
        let binding = Binding(get: { text }, set: { text = $0 })
        ViewHost.render(KBLiveEditor(text: binding), size: editorSize)
    }

    @Test("the live editor renders every heading level")
    func rendersEveryHeadingLevel() {
        var text = (1...6).map { String(repeating: "#", count: $0) + " Heading \($0)" }
            .joined(separator: "\n\n")
        let binding = Binding(get: { text }, set: { text = $0 })
        ViewHost.render(KBLiveEditor(text: binding), size: editorSize)
    }
}
