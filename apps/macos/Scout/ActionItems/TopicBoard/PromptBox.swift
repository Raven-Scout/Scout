import SwiftUI

/// The inline "What should Claude do?" box under a board row. Enter sends,
/// Escape cancels; after sending it shows where the note went, then closes.
struct PromptBox: View {
    let context: () -> String
    let session: String?
    let scoutDirectory: URL
    @Binding var isOpen: Bool

    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    @State private var draft = ""
    @State private var note: String?
    @State private var failure: String?
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            if let note {
                Text(note)
                    .font(DS.sans(13))
                    .foregroundStyle(DS.Ink.p3)
            } else {
                TextField("What should Claude do? Enter to send, Esc to cancel", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .focused($focused)
                    .onSubmit(send)
                    .onKeyPress(.escape) {
                        isOpen = false
                        return .handled
                    }
                    .task {
                        try? await Task.sleep(for: .milliseconds(50))
                        focused = true
                    }
                Button("Send", action: send)
                    .buttonStyle(.borderedProminent)
                    .tint(BoardState.mine.color)
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Cancel") { isOpen = false }
                    .buttonStyle(.bordered)
                if let failure {
                    Text(failure)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Status.err)
                        .lineLimit(2)
                        .help(failure)
                }
            }
        }
        .font(DS.sans(13))
        .padding(.leading, 20)
        .padding(.trailing, 14)
        .padding(.top, 6)
        .padding(.bottom, 10)
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let cli = CLIConfig(
            claudePathOverride: claudeCLIPath,
            terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
            customCommand: customLaunchCommand
        )
        do {
            note = try BoardPromptSender.send(
                BoardPromptSender.prompt(note: text, context: context()),
                session: session,
                scoutDirectory: scoutDirectory,
                cli: cli
            )
            failure = nil
            draft = ""
            Task {
                try? await Task.sleep(for: .seconds(2.5))
                isOpen = false
            }
        } catch {
            failure = "Couldn't send: \(error.localizedDescription)"
        }
    }
}
