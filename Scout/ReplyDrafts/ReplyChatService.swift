import Foundation
import SwiftUI

/// Per-draft AI assistant chat. Each draft (keyed by its tag) has its own thread
/// of messages. Sending a message shells out to the user's `claude` CLI — the
/// same binary the scheduled runner uses, so it runs on the user's own Claude
/// license — passing the draft's context (summary, thread, current reply) plus
/// the conversation so far, and appends the model's reply.
///
/// This never sends the email or mutates the draft file — it is a thinking aid.
/// Acting on the draft (fill/edit/mark) goes through `ReplyDraftsWriter`.
@MainActor
final class ReplyChatService: ObservableObject {
    /// Conversation per draft tag.
    @Published private(set) var threads: [String: [ChatMessage]] = [:]
    /// The draft tag currently awaiting a model reply (drives the spinner).
    @Published private(set) var busyTag: String?

    private let runner: any ProcessRunner
    private let claude: URL
    private let claudeArgsPrefix: [String]
    private let workingDirectory: URL

    init(runner: any ProcessRunner, claude: URL, claudeArgsPrefix: [String], workingDirectory: URL) {
        self.runner = runner
        self.claude = claude
        self.claudeArgsPrefix = claudeArgsPrefix
        self.workingDirectory = workingDirectory
    }

    func messages(for tag: String) -> [ChatMessage] { threads[tag] ?? [] }

    func isBusy(_ tag: String) -> Bool { busyTag == tag }

    /// Send a user message about `draft` and append the assistant's reply.
    func send(text: String, about draft: ReplyDraft) async {
        let tag = draft.tag
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, busyTag == nil else { return }

        var thread = threads[tag] ?? []
        thread.append(ChatMessage(role: .user, text: trimmed, id: UUID().uuidString))
        threads[tag] = thread
        busyTag = tag

        let prompt = Self.buildPrompt(draft: draft, history: thread)
        let args = claudeArgsPrefix + ["-p", prompt, "--model", "sonnet"]

        let reply: ChatMessage
        do {
            let result = try await runner.run(
                executable: claude,
                arguments: args,
                environment: [:],
                workingDirectory: workingDirectory
            )
            if result.exitCode == 0 {
                let out = String(data: result.stdout, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                reply = ChatMessage(
                    role: out.isEmpty ? .error : .assistant,
                    text: out.isEmpty ? "No response from claude." : out,
                    id: UUID().uuidString
                )
            } else {
                let err = String(data: result.stderr, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                reply = ChatMessage(
                    role: .error,
                    text: "claude exited \(result.exitCode)" + (err.isEmpty ? "" : ": \(err)"),
                    id: UUID().uuidString
                )
            }
        } catch {
            reply = ChatMessage(role: .error, text: "Couldn't run claude — \(error.localizedDescription)", id: UUID().uuidString)
        }

        var updated = threads[tag] ?? []
        updated.append(reply)
        threads[tag] = updated
        busyTag = nil
    }

    /// Build the single-shot prompt: a context preamble grounded in the draft +
    /// the conversation so far + the latest user turn.
    nonisolated static func buildPrompt(draft: ReplyDraft, history: [ChatMessage]) -> String {
        let threadLines = draft.relatedMessages
            .map { "- [\($0.date)] \($0.sender): \($0.text)" }
            .joined(separator: "\n")
        let context = """
        You are my assistant for ONE specific reply I owe. Help me think it through and draft wording. \
        Be concise and concrete. Do NOT send anything — you are a thinking aid; I send the email myself.

        REPLY CONTEXT
        Channel: \(draft.channel.displayName)
        To: \(draft.to)\(draft.cc.map { "\nCc: \($0)" } ?? "")
        Subject: \(draft.subject ?? "—")

        What this thread is about:
        \(draft.summary ?? "(no summary captured)")

        Thread so far:
        \(threadLines.isEmpty ? "(no thread captured)" : threadLines)

        My current prepared reply:
        \(draft.bodyMarkdown.isEmpty ? "(empty)" : draft.bodyMarkdown)
        """

        let convo = history.map { msg -> String in
            switch msg.role {
            case .user:      return "Me: \(msg.text)"
            case .assistant: return "Assistant: \(msg.text)"
            case .error:     return ""
            }
        }.filter { !$0.isEmpty }.joined(separator: "\n\n")

        return context + "\n\n--- Conversation ---\n\n" + convo + "\n\nAssistant:"
    }
}
