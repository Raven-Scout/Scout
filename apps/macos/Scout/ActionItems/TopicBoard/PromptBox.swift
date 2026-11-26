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
    /// A send is launching its terminal; a second Enter must not send twice.
    @State private var sending = false
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            if let note {
                Text(note)
                    .font(DS.font(.body))
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
                    .buttonStyle(.dsPrimary)
                    .disabled(sending || draft.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Cancel") { isOpen = false }
                    .buttonStyle(.dsSecondary)
                if let failure {
                    Text(failure)
                        .font(DS.font(.label))
                        .foregroundStyle(DS.Status.err)
                        .lineLimit(2)
                        .help(failure)
                }
            }
        }
        .font(DS.font(.body))
        .padding(.leading, 20)
        .padding(.trailing, 14)
        .padding(.top, 6)
        .padding(.bottom, 10)
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        let cli = CLIConfig(
            claudePathOverride: claudeCLIPath,
            terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
            customCommand: customLaunchCommand
        )
        let prompt = BoardPromptSender.prompt(note: text, context: context())
        sending = true
        Task {
            defer { sending = false }
            do {
                note = try await BoardPromptSender.send(
                    prompt,
                    session: session,
                    scoutDirectory: scoutDirectory,
                    cli: cli
                )
                failure = nil
                draft = ""
                try? await Task.sleep(for: .seconds(2.5))
                isOpen = false
            } catch {
                failure = "Couldn't send: \(error.localizedDescription)"
            }
        }
    }
}
