import SwiftUI
import AppKit

/// The item's context note, collapsed by default. It checks for the file when
/// the card's detail is built and reads it only when expanded, so the list's
/// first paint never touches the disk for notes.
struct TaskContextNoteSection: View {
    let tag: String?
    let scoutDirectory: URL
    /// Set only when the note exists. Checked here rather than in `.task`:
    /// with no note the body is empty, and an empty `Group` gives a modifier
    /// no child to run on.
    private let noteURL: URL?

    @State private var isExpanded = false
    @State private var text: String?

    init(tag: String?, scoutDirectory: URL) {
        self.tag = tag
        self.scoutDirectory = scoutDirectory
        if let tag, let url = TaskContextNote.url(for: tag, scoutDirectory: scoutDirectory),
           FileManager.default.fileExists(atPath: url.path) {
            noteURL = url
        } else {
            noteURL = nil
        }
    }

    var body: some View {
        if let noteURL {
            DisclosureGroup(isExpanded: $isExpanded) {
                VStack(alignment: .leading, spacing: 8) {
                    if let text {
                        MarkdownBodyView(blocks: MarkdownBodyBlock.blocks(from: text))
                    }
                    Button("Open note") { NSWorkspace.shared.open(noteURL) }
                        .buttonStyle(.link)
                        .font(DS.sans(11.5))
                }
                .padding(.top, 6)
            } label: {
                Text("Context note")
                    .font(DS.sans(12, weight: .medium))
                    .foregroundStyle(DS.Ink.p2)
            }
            .onChange(of: isExpanded) { _, open in
                if open && text == nil {
                    text = TaskContextNote.load(tag: tag, scoutDirectory: scoutDirectory)
                }
            }
        }
    }
}
