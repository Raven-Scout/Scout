import Foundation
import Combine

/// AC-vs-battery state. Plan 5 surfaces this as a yellow banner above the
/// Control Center because launchd misses fires when the lid closes on
/// battery — knowing you're unplugged is load-bearing for trusting the
/// schedule.
enum PowerState: Equatable {
    case onAC
    case onBattery(level: Double)
    case unknown
}

@MainActor
final class PowerStateService: ObservableObject {
    @Published private(set) var state: PowerState = .unknown

    private let runner: any ProcessRunner
    private var pollTimer: Timer?

    init(runner: any ProcessRunner) {
        self.runner = runner
    }

    func start() {
        pollTimer?.invalidate()  // idempotency guard — drop any prior timer so
                                 // a double-call doesn't orphan a still-firing one.
        Task { await self.refresh() }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    func stop() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    func refresh() async {
        do {
            let output = try await runner.run(
                executable: URL(fileURLWithPath: "/usr/bin/pmset"),
                arguments: ["-g", "batt"],
                environment: [:],
                workingDirectory: nil
            )
            let text = String(data: output.stdout, encoding: .utf8) ?? ""
            self.state = Self.parsePmsetOutput(text) ?? .unknown
        } catch {
            self.state = .unknown
        }
    }

    /// Parse the textual output of `pmset -g batt`. Sample lines:
    ///   `Now drawing from 'AC Power'`
    ///   `Now drawing from 'Battery Power'`
    ///   ` -InternalBattery-0 (id=...)    73%; discharging; 4:12 remaining`
    ///
    /// `nonisolated` so tests can call it without spinning the actor.
    nonisolated static func parsePmsetOutput(_ stdout: String) -> PowerState? {
        if stdout.contains("AC Power") {
            return .onAC
        }
        if stdout.contains("Battery Power") {
            // Extract first percentage like "73%". `dropLast()` strips the `%`.
            if let range = stdout.range(of: #"(\d+)%"#, options: .regularExpression),
               let pct = Int(stdout[range].dropLast()) {
                return .onBattery(level: Double(pct) / 100.0)
            }
            return .onBattery(level: 0)
        }
        return nil
    }
}
