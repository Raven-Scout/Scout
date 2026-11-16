import Combine
import Foundation

/// Today's and this week's usage of Scout's own scheduled runs, from
/// `scoutctl budget usage --json`.
///
/// The app used to sum a per-session token log itself. On a real vault that
/// showed ~270,000M tokens a day: it counted the same message once per
/// streamed content block and took in sessions that were not Scout's runs.
/// The engine already scopes usage to the runs in its usage tracker and
/// dedupes by message id (`scout/scripts/usage_summary.py`), so the app asks
/// it instead of re-parsing transcripts. Shape mirrors `BudgetSettingsService`.
@MainActor
final class ScoutUsageService: ObservableObject {
    @Published private(set) var summary: UsageSummary?
    @Published private(set) var lastError: String?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]

    static let arguments = ["budget", "usage", "--json"]

    init(scoutctl: URL, runner: any ProcessRunner, argumentsPrefix: [String] = []) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
    }

    /// Re-read the summary. A failure keeps the last good summary (the card
    /// keeps showing it) and records why, so a transient engine error doesn't
    /// blank the card.
    func refresh() async {
        do {
            let result = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + Self.arguments,
                environment: [:],
                workingDirectory: nil
            )
            guard result.exitCode == 0 else {
                let stderr = String(data: result.stderr, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                lastError = stderr.isEmpty ? "Scout's engine couldn't report usage (exit \(result.exitCode))." : stderr
                return
            }
            summary = try Self.decode(result.stdout)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    nonisolated static func decode(_ data: Data) throws -> UsageSummary {
        let decoded = try JSONDecoder().decode(UsageSummary.self, from: data)
        guard decoded.schemaVersion == UsageSummary.supportedSchemaVersion else {
            throw NSError(
                domain: "ScoutUsageService",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey:
                    "Scout's engine reports usage in a newer format (\(decoded.schemaVersion)); update the app."]
            )
        }
        return decoded
    }
}
