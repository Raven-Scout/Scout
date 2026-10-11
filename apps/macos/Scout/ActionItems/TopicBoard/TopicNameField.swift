import SwiftUI

/// A topic's name, with a pencil on hover that turns it into a text field.
/// Enter or leaving the field saves; Escape cancels. Saving the original
/// Scout title, or an empty name, clears the rename.
struct TopicNameField: View {
    let title: String
    let scoutTitle: String
    let canRename: Bool
    /// The whole topic row is hovered, so the pencil shows.
    let rowHovered: Bool
    let onRename: (String) -> Void

    /// Whether the name is being edited; the host row stops handling keys meanwhile.
    @Binding var editing: Bool
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 2) {
            if editing {
                TextField(scoutTitle, text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .focused($focused)
                    .onSubmit(save)
                    .onKeyPress(.escape) {
                        editing = false
                        return .handled
                    }
                    .onChange(of: focused) { _, isFocused in
                        if !isFocused && editing { save() }
                    }
                    .onTapGesture { focused = true }
                    .task {
                        try? await Task.sleep(for: .milliseconds(50))
                        focused = true
                    }
                    .accessibilityLabel("Topic name")
            } else {
                Text(title)
                    .lineLimit(1)
                if canRename {
                    Button {
                        draft = title
                        editing = true
                    } label: {
                        Image(systemName: "pencil")
                            .font(.system(size: 11))
                            .frame(width: 22, height: 22)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(DS.Ink.p3)
                    .opacity(rowHovered ? 1 : 0)
                    .help("Rename")
                    .accessibilityLabel("Rename \(title)")
                }
            }
        }
    }

    private func save() {
        editing = false
        if let name = Self.nameToSave(draft: draft, current: title, scoutTitle: scoutTitle) {
            onRename(name)
        }
    }

    /// The `--name` to send, or `nil` when nothing changed. Empty clears the rename.
    static func nameToSave(draft: String, current: String, scoutTitle: String) -> String? {
        let name = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard name != current else { return nil }
        return name == scoutTitle ? "" : name
    }
}
