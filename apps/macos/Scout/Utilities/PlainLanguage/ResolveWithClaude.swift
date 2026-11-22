import SwiftUI

extension EnvironmentValues {
    /// The engine's "Resolve with Claude" prompts. `ScoutApp` sets it from `AppState`; previews
    /// and any view without it show no button.
    @Entry var resolvePromptService: ResolvePromptService? = nil
}

/// "Resolve with Claude" on a problem: the action-item launcher, whose prompt the engine builds
/// at click time (`scoutctl health resolve-prompt`). The main button opens a new Claude Code
/// session in Claude Desktop, in the vault; the chevron offers the terminal, chat and Cowork.
/// Shows nothing until the engine says it has the command.
struct ResolveWithClaudeButton: View {
    static let title = "Resolve with Claude"
    static let help = "Open a new Claude session that investigates this problem, read-only first. You review the prompt before sending it."

    let subject: ResolveSubject
    let service: ResolvePromptService
    @Binding var launchError: String?

    @State private var supported = false

    var body: some View {
        Group {
            if supported {
                LaunchClaudeMenu(
                    title: Self.title,
                    help: Self.help,
                    asyncPrompt: { [service, subject] in try await service.prompt(for: subject).prompt },
                    scoutDirectory: service.scoutDirectory,
                    launchError: $launchError
                )
            } else {
                Color.clear.frame(width: 0, height: 0)
            }
        }
        .task(id: subject) { supported = await service.isSupported() }
    }
}
