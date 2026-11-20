import Foundation

/// Why "Resolve with Claude" couldn't build its prompt. Nothing launches when it throws.
nonisolated enum ResolvePromptError: LocalizedError, Equatable, Sendable {
    /// The alert's key isn't one the engine accepts; nothing ran.
    case invalidKey
    /// The engine ran and refused, in its own one-line words.
    case engineFailed(String)
    /// The engine answered in a form this app doesn't read, or for another alert.
    case unreadable
    /// The engine answered for a vault other than the app's.
    case wrongFolder

    var errorDescription: String? {
        let why: String = switch self {
        case .invalidKey: "that alert has no key Scout accepts."
        case .engineFailed(let message): message
        case .unreadable: "the engine's answer isn't one this app reads. Updating the engine usually fixes this."
        case .wrongFolder: "the engine answered for a different vault folder."
        }
        return "Couldn't build the prompt: \(why)"
    }
}

/// Runs `scoutctl health resolve-prompt <kind> <key> --json` for "Resolve with Claude", with the
/// same `scoutctl`, runner and environment as the run ledger. Read-only: the engine builds the
/// prompt from the live state; the app never composes prompt text.
@MainActor
final class ResolvePromptService {
    static let feature = "resolve_prompt_v1"

    /// The vault every prompt's session opens in.
    let scoutDirectory: URL
    private let scoutctl: URL
    private let argumentsPrefix: [String]
    private let runner: any ProcessRunner
    private let environment: [String: String]
    private var supported = false

    init(scoutctl: URL, argumentsPrefix: [String] = [], runner: any ProcessRunner,
         environment: [String: String], scoutDirectory: URL) {
        self.scoutctl = scoutctl
        self.argumentsPrefix = argumentsPrefix
        self.runner = runner
        self.environment = environment
        self.scoutDirectory = scoutDirectory
    }

    nonisolated static func arguments(for subject: ResolveSubject) -> [String] {
        ["health", "resolve-prompt", subject.kind, subject.key, "--json"]
    }

    /// Whether the engine advertises `resolve_prompt_v1`. Only a yes is remembered, like
    /// `BoardWriter.supportsBoardWrites`: an engine upgraded while the app runs gets the button.
    func isSupported() async -> Bool {
        if supported { return true }
        let result = try? await runner.run(executable: scoutctl, arguments: argumentsPrefix + ["manifest", "show"],
                                           environment: environment, workingDirectory: scoutDirectory)
        supported = result.map { $0.exitCode == 0 && EngineFeatures.decode($0.stdout).contains(Self.feature) } ?? false
        return supported
    }

    /// The engine's prompt for `subject`. Throws `ResolvePromptError`.
    func prompt(for subject: ResolveSubject) async throws -> ResolvePrompt {
        guard subject.isValidKey else { throw ResolvePromptError.invalidKey }
        let result: ProcessResult
        do {
            result = try await runner.run(executable: scoutctl, arguments: argumentsPrefix + Self.arguments(for: subject),
                                          environment: environment, workingDirectory: scoutDirectory)
        } catch {
            throw ResolvePromptError.engineFailed("couldn't run scoutctl (\(String(describing: error).prefix(160))).")
        }
        guard result.exitCode == 0 else { throw ResolvePromptError.engineFailed(Self.reason(result)) }
        guard let prompt = ResolvePrompt.parse(result.stdout, for: subject) else { throw ResolvePromptError.unreadable }
        guard Self.sameFolder(prompt.cwd, scoutDirectory) else { throw ResolvePromptError.wrongFolder }
        return prompt
    }

    private struct Refusal: Decodable { let error: String }

    /// The engine's `{"error": …}` when it printed one, else stderr's first line.
    nonisolated static func reason(_ result: ProcessResult) -> String {
        if let refusal = try? JSONDecoder().decode(Refusal.self, from: result.stdout), !refusal.error.isEmpty {
            return String(refusal.error.prefix(200))
        }
        return RunProtectionService.firstLine(result.stderr)
    }

    nonisolated static func sameFolder(_ path: String, _ folder: URL) -> Bool {
        guard path.hasPrefix("/") else { return false }
        let a = URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL.path
        let b = folder.resolvingSymlinksInPath().standardizedFileURL.path
        return a == b
    }
}
