import Combine
import Foundation

/// Runs the engine's inbox review for Settings ▸ Agent copies ▸ Review (#550 V4b3): what the owner's agents
/// proposed that waits for the owner, and the accepted observations they can promote or make private again.
///
/// It only runs what the CLI already offers the owner at the keyboard, each step bound to what the owner saw:
/// - `inbox review --delegate <id> --json` and `inbox review --accepted --delegate <id> --json` (read-only);
/// - `accept <ULID> --confirm <ULID>`, after a confirmation that repeats the agent's id and full text;
/// - `reject <ULID>`;
/// - `promote <ULID> --preview`, then `promote <ULID> --to <the previewed delegates> --previewed <sha>
///   --confirm <ULID>`, so the note reaches exactly the delegates the button named, and only while it is the
///   text the preview showed;
/// - `unpromote <ULID> --preview`, then `unpromote <ULID> --previewed <sha>`.
///
/// Pulling, applying, reverting and syncing an inbox stay with the schedule tick and Terminal. Every
/// `scoutctl inbox` command in the app goes through `runInbox(_:_:)`, whose `Command` is the allow-list;
/// `InboxReviewTests` fails on the quoted word inbox anywhere else.
@MainActor
final class InboxReviewService: ObservableObject {
    nonisolated enum Work: Equatable, Sendable {
        case loading
        case accepting(String)
        case rejecting(String)
        case previewing(String)
        case promoting(String)
        case unpromoting(String)
    }

    /// The `scoutctl inbox review` steps the app may run, and no others.
    nonisolated enum Command: String, Sendable, CaseIterable {
        case review, accept, reject, promote, unpromote
    }

    /// The delegate whose inbox is open.
    @Published private(set) var delegate: String?
    @Published private(set) var items: [InboxReview.Item] = []
    @Published private(set) var accepted: [InboxReview.Accepted] = []
    @Published private(set) var work: Work?
    /// What the last step said: the engine's notes after a decision, or its refusal in its own words.
    @Published private(set) var message: String?
    /// The last step was refused or failed.
    @Published private(set) var failed = false

    private let runner: any ProcessRunner
    private let environment: [String: String]
    private let scoutctl: @MainActor () -> URL?

    init(runner: any ProcessRunner, environment: [String: String], scoutctl: @escaping @MainActor () -> URL?) {
        self.runner = runner
        self.environment = environment
        self.scoutctl = scoutctl
    }

    /// Open `id`'s inbox: read what waits and what was accepted. Read-only.
    func open(_ id: String) async {
        guard DelegateStatus.isDelegateID(id), work == nil else { return }
        if delegate != id { items = []; accepted = []; message = nil; failed = false }
        delegate = id
        await perform(.loading) { _ in }
    }

    /// Accept a waiting observation or a held comment: the owner pressed the button that names its agent.
    func accept(_ item: InboxReview.Item) async {
        guard item.canAccept, InboxReview.isULID(item.ulid), items.contains(item) else { return }
        await perform(.accepting(item.ulid)) { [self] exe in
            let decided = try decide(try await runInbox(.accept, [item.ulid, "--confirm", item.ulid], exe))
            var notes = decided.notes
            if item.kind == "observation" { notes.append(InboxReview.acceptedObservationNote) }
            say(notes.joined(separator: " "))
        }
    }

    func reject(_ item: InboxReview.Item) async {
        guard InboxReview.isULID(item.ulid), items.contains(item) else { return }
        await perform(.rejecting(item.ulid)) { [self] exe in
            _ = try decide(try await runInbox(.reject, [item.ulid], exe))
            say("Rejected delegate-\(item.delegate)'s proposal. Nothing reached the vault.")
        }
    }

    /// The text promote would publish and who would see it. Writes nothing.
    func previewPromote(_ note: InboxReview.Accepted) async -> InboxReview.Item? {
        await preview(.promote, note)
    }

    /// The text unpromote puts back and who stops seeing it. Writes nothing.
    func previewUnpromote(_ note: InboxReview.Accepted) async -> InboxReview.Item? {
        await preview(.unpromote, note)
    }

    /// Promote exactly what `preview` showed, to exactly the delegates it named.
    func promote(_ preview: InboxReview.Item) async {
        guard let sha = preview.sha256, InboxReview.isSHA256(sha), InboxReview.isULID(preview.ulid),
              let audience = preview.audience, !audience.isEmpty, audience.allSatisfy(DelegateStatus.isDelegateID)
        else { return refuse("Nothing was promoted: the preview didn't name a note and the agents to show it to.") }
        await perform(.promoting(preview.ulid)) { [self] exe in
            let args = [preview.ulid, "--to", audience.joined(separator: ","), "--previewed", sha,
                        "--confirm", preview.ulid]
            say(try decide(try await runInbox(.promote, args, exe)).notes.joined(separator: " "))
        }
    }

    /// Make the note private again, while it is exactly what `preview` showed.
    func unpromote(_ preview: InboxReview.Item) async {
        guard let sha = preview.sha256, InboxReview.isSHA256(sha), InboxReview.isULID(preview.ulid) else {
            return refuse("Nothing changed: the preview didn't name a note.")
        }
        await perform(.unpromoting(preview.ulid)) { [self] exe in
            _ = try decide(try await runInbox(.unpromote, [preview.ulid, "--previewed", sha], exe))
            say("Private again: no agent sees delegate-\(preview.delegate)'s note now, from the next mirror sync.")
        }
    }

    // MARK: -

    private struct Failure: Error { let message: String; init(_ m: String) { message = m } }

    private func say(_ text: String) { message = text; failed = false }
    private func refuse(_ text: String) { message = text; failed = true }

    private func preview(_ command: Command, _ note: InboxReview.Accepted) async -> InboxReview.Item? {
        guard InboxReview.isULID(note.ulid), accepted.contains(note) else { return nil }
        var shown: InboxReview.Item?
        await perform(.previewing(note.ulid), reload: false) { [self] exe in
            let result = try await runInbox(command, [note.ulid, "--preview"], exe)
            if result.exitCode == 0, let item = InboxReview.preview(result.stdout), item.ulid == note.ulid {
                shown = item
                return
            }
            throw refusalOrFailure(result, command)
        }
        return shown
    }

    /// The one place the app runs `scoutctl inbox …`.
    private func runInbox(_ command: Command, _ args: [String], _ exe: URL) async throws -> ProcessResult {
        let step = command == .review ? [] : [command.rawValue]
        return try await runner.run(executable: exe, arguments: ["inbox", "review"] + step + args + ["--json"],
                                    environment: environment, workingDirectory: nil)
    }

    private func decide(_ result: ProcessResult) throws -> InboxReview.Decision {
        if result.exitCode == 0, let decision = InboxReview.decision(result.stdout) { return decision }
        throw refusalOrFailure(result, nil)
    }

    /// The engine's refusal in its own words, or a plain failure.
    private func refusalOrFailure(_ result: ProcessResult, _ command: Command?) -> Failure {
        if let refused = InboxReview.refusal(result.stdout) { return Failure(refused.message) }
        let what = command.map { "scoutctl inbox review \($0.rawValue)" } ?? "scoutctl inbox review"
        return Failure("That didn't finish. Run \(what) in Terminal to see why.")
    }

    /// Run `action`, then re-read the lists (unless `reload` is false).
    private func perform(_ kind: Work, reload: Bool = true,
                         _ action: @escaping @MainActor (URL) async throws -> Void) async {
        guard work == nil else { return }
        guard let exe = scoutctl() else {
            message = "Scout's engine isn't set up yet."
            failed = true
            return
        }
        let started = delegate   // what this step's result belongs to; `open` can't switch while it runs
        work = kind
        defer { work = nil }
        do { try await action(exe) } catch let f as Failure {
            message = f.message
            failed = true
        } catch {
            message = "Couldn't run scoutctl: \(String(describing: error).prefix(160))"
            failed = true
        }
        guard reload, let id = started, id == delegate else { return }
        do {
            let listed = try await runInbox(.review, ["--delegate", id], exe)
            let kept = try await runInbox(.review, ["--accepted", "--delegate", id], exe)
            guard let newItems = InboxReview.items(listed.stdout), let newAccepted = InboxReview.accepted(kept.stdout)
            else {
                let refusal = InboxReview.refusal(listed.stdout) ?? InboxReview.refusal(kept.stdout)
                if !failed {
                    message = refusal?.message ?? "Couldn't read the inbox. Run scoutctl inbox review in Terminal to see why."
                    failed = true
                }
                return
            }
            items = newItems.filter { $0.delegate == id }
            accepted = newAccepted.filter { $0.delegate == id }
        } catch {
            if !failed { message = "Couldn't run scoutctl: \(String(describing: error).prefix(160))"; failed = true }
        }
    }
}
