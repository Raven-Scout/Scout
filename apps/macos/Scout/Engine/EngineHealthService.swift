import Foundation
import Combine

/// One observable answer to "is the engine there and healthy?" (spec §4.4).
/// Drives the window gate, Settings ▸ Engine and the sidebar badge.
@MainActor
final class EngineHealthService: ObservableObject {
    @Published private(set) var state: EngineState
    @Published private(set) var doctor: DoctorReport?
    @Published private(set) var lastError: String?
    @Published private(set) var lastChecked: Date?

    private let locator: EngineLocator
    private let runner: any ProcessRunner
    private let environment: [String: String]
    private var timer: Timer?

    /// `initialState` lets the caller pass the synchronously located state so
    /// the window gate is correct from the very first frame, before the async
    /// `refresh()` has had a chance to run.
    init(locator: EngineLocator, runner: any ProcessRunner, environment: [String: String] = [:],
         initialState: EngineState = .notInstalled) {
        self.locator = locator
        self.runner = runner
        self.environment = environment
        self.state = initialState
    }

    var needsAttention: Bool {
        if state.gatesTabs { return true }
        if case .broken = state { return true }
        return doctor?.severity == .red
    }

    /// Locate off the main actor, run the doctor, publish on the main actor.
    ///
    /// Engines ≥ 0.10.0 emit JSON for `bootstrap doctor --json`; older adopted
    /// engines don't recognize `--json` and exit non-zero with a Click-style
    /// usage error. When the `--json` output doesn't parse, this retries once
    /// with the plain (legacy-text) form before giving up.
    func refresh() async {
        let locator = self.locator
        let located = await Task.detached { locator.locate() }.value
        state = located
        lastChecked = Date()
        guard let scoutctl = located.scoutctl, !located.gatesTabs || located.isManaged else {
            doctor = nil
            return
        }
        do {
            let jsonResult = try await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor", "--json"],
                                                  environment: environment, workingDirectory: nil)
            if let report = DoctorReport.parse(stdout: jsonResult.stdout) {
                doctor = report
                lastError = nil
                return
            }
            let textResult = try await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor"],
                                                   environment: environment, workingDirectory: nil)
            if let report = DoctorReport.parse(stdout: textResult.stdout) {
                doctor = report
                lastError = nil
            } else {
                doctor = nil
                let source = textResult.stderr.isEmpty ? textResult.stdout : textResult.stderr
                lastError = "doctor output not understood: \(ScheduleService.previewBytes(source, max: 200))"
            }
        } catch {
            doctor = nil
            lastError = "could not run scoutctl: \(String(describing: error).prefix(160))"
        }
    }

    /// Re-check every 10 minutes (spec §4.4).
    func startPeriodicRefresh(interval: TimeInterval = 600) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }
}
