import Foundation
import Combine

/// Which layer supplied the budget values the engine is using.
///
/// Coarse by design — the engine reports where the values came from, canonical
/// block winning over the legacy `plan:`/`thresholds:` spellings. Used only to
/// pick an advisory line in the UI, never to decide what the values are.
enum BudgetSource: String, Codable, Sendable {
    case vault
    case legacy
    case defaults

    /// An unrecognized value decodes to `.defaults` rather than throwing: a
    /// newer engine adding a source must not stop the pane from loading.
    init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = BudgetSource(rawValue: raw) ?? .defaults
    }
}

/// The `scoutctl budget show --json` payload. Key names are the engine's
/// canonical YAML spellings; see the engine's `budget_config.show_payload`,
/// whose test asserts this exact key set.
struct BudgetSettings: Codable, Equatable, Sendable {
    let configPath: String
    let source: BudgetSource
    let dailyUSD: Double
    let windowHours: Int
    let skipAtPct: Double
    let failureBackoffMinutes: Int
    let windowBudgetUSD: Double
    let skipThresholdUSD: Double

    enum CodingKeys: String, CodingKey {
        case configPath = "config_path"
        case source
        case dailyUSD = "daily_usd"
        case windowHours = "window_hours"
        case skipAtPct = "skip_at_pct"
        case failureBackoffMinutes = "failure_backoff_minutes"
        case windowBudgetUSD = "window_budget_usd"
        case skipThresholdUSD = "skip_threshold_usd"
    }
}

/// Reads and writes the vault's budget configuration by shelling out to
/// `scoutctl budget show/set`.
///
/// The app deliberately holds no YAML knowledge: `scout-config.yaml` doubles as
/// bootstrap state written by several producers, so composing it here would put
/// the app in a position to drop a subtree bootstrap wrote. The engine does a
/// line-surgical in-place update instead. Shape mirrors `ScheduleEditService`.
@MainActor
final class BudgetSettingsService: ObservableObject {
    @Published private(set) var settings: BudgetSettings?

    /// Set when our locally derived gate disagrees with the figures the engine
    /// reported — meaning `BudgetGate` and the engine's `BudgetConfig` have
    /// drifted apart. The engine's values stay authoritative; this only tells
    /// the user the live readout may mislead while they type.
    @Published private(set) var parityWarning: String?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]

    init(scoutctl: URL, runner: any ProcessRunner, argumentsPrefix: [String] = []) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
    }

    func load() async throws {
        let payload = try await invoke(["budget", "show", "--json"])
        publish(payload)
    }

    func save(
        dailyUSD: Double,
        windowHours: Int,
        skipAtPct: Double,
        failureBackoffMinutes: Int
    ) async throws {
        let payload = try await invoke(Self.setArguments(
            dailyUSD: dailyUSD,
            windowHours: windowHours,
            skipAtPct: skipAtPct,
            failureBackoffMinutes: failureBackoffMinutes
        ))
        publish(payload)
    }

    /// `budget set` echoes the resulting config with `--json`, so a save needs
    /// no follow-up read.
    ///
    /// `nonisolated` because it is pure — it reads no instance state and only
    /// formats its arguments, so it has no reason to inherit the class's
    /// main-actor isolation and can be exercised directly from a test.
    nonisolated static func setArguments(
        dailyUSD: Double,
        windowHours: Int,
        skipAtPct: Double,
        failureBackoffMinutes: Int
    ) -> [String] {
        [
            "budget", "set",
            "--daily-usd", numeric(dailyUSD),
            "--window-hours", "\(windowHours)",
            "--skip-at-pct", numeric(skipAtPct),
            "--failure-backoff-minutes", "\(failureBackoffMinutes)",
            "--json",
        ]
    }

    // MARK: - Internals

    private func invoke(_ arguments: [String]) async throws -> BudgetSettings {
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
                domain: "BudgetSettingsService",
                code: Int(result.exitCode),
                userInfo: [NSLocalizedDescriptionKey: detail.isEmpty
                    ? "scoutctl \(arguments.joined(separator: " ")) exited \(result.exitCode)"
                    : detail]
            )
        }
        do {
            return try JSONDecoder().decode(BudgetSettings.self, from: result.stdout)
        } catch {
            let stdout = String(data: result.stdout, encoding: .utf8) ?? ""
            let snippet = String(stdout.prefix(200))
            throw NSError(
                domain: "BudgetSettingsService",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey:
                    "could not decode scoutctl budget output: \(error.localizedDescription)\n\(snippet)"]
            )
        }
    }

    private func publish(_ payload: BudgetSettings) {
        settings = payload
        let derived = BudgetGate.derive(
            dailyUSD: payload.dailyUSD,
            windowHours: payload.windowHours,
            skipAtPct: payload.skipAtPct
        )
        if derived.matches(
            windowBudgetUSD: payload.windowBudgetUSD,
            skipThresholdUSD: payload.skipThresholdUSD
        ) {
            parityWarning = nil
        } else {
            parityWarning = String(
                format: "Scout computes this gate as $%.2f (skip at $%.2f); the engine reports "
                    + "$%.2f (skip at $%.2f). The engine's values are the ones in effect.",
                derived.windowBudgetUSD, derived.skipThresholdUSD,
                payload.windowBudgetUSD, payload.skipThresholdUSD
            )
        }
    }

    /// Emit a number the way a person writes it, so the YAML the engine writes
    /// reads as `90` rather than `90.0`.
    nonisolated private static func numeric(_ value: Double) -> String {
        value == value.rounded() ? "\(Int(value))" : "\(value)"
    }
}
