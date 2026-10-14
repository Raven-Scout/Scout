import Foundation
import Combine

/// Reads the models scheduled runs use (`scoutctl model show --json`) and
/// writes the default (`scoutctl model set-default`).
///
/// Like `BudgetSettingsService`, the app holds no YAML knowledge of
/// `scout-config.yaml`: the engine edits the `models:` block in place and keeps
/// every other line. A slot's own model is part of `schedule.yaml` and is
/// saved by `ScheduleEditService` with the rest of the slot.
///
/// `settings` stays nil on an engine without `scoutctl model` (before
/// per-slot models); the model pickers hide in that case, since a `model:`
/// such an engine ignores would only mislead.
@MainActor
final class ModelSettingsService: ObservableObject {
    @Published private(set) var settings: ModelSettings?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]

    init(scoutctl: URL, runner: any ProcessRunner, argumentsPrefix: [String] = []) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
    }

    func load() async throws {
        let stdout = try await invoke(["model", "show", "--json"])
        do {
            settings = try JSONDecoder().decode(ModelSettings.self, from: stdout)
        } catch {
            let snippet = String((String(data: stdout, encoding: .utf8) ?? "").prefix(200))
            throw NSError(
                domain: "ModelSettingsService",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey:
                    "could not decode scoutctl model output: \(error.localizedDescription)\n\(snippet)"]
            )
        }
    }

    /// Write `models.default`, then re-read so every slot's effective model
    /// reflects it.
    func setDefault(_ model: String) async throws {
        _ = try await invoke(Self.setDefaultArguments(model))
        try await load()
    }

    nonisolated static func setDefaultArguments(_ model: String) -> [String] {
        ["model", "set-default", model, "--json"]
    }

    private func invoke(_ arguments: [String]) async throws -> Data {
        let result = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + arguments,
            environment: [:],
            workingDirectory: nil
        )
        guard result.exitCode == 0 else {
            let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
            let detail = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(
                domain: "ModelSettingsService",
                code: Int(result.exitCode),
                userInfo: [NSLocalizedDescriptionKey: detail.isEmpty
                    ? "scoutctl \(arguments.joined(separator: " ")) exited \(result.exitCode)"
                    : detail]
            )
        }
        return result.stdout
    }
}
