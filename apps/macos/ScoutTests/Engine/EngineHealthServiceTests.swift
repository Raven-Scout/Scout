import Testing
import Foundation
@testable import Scout

@Suite("EngineHealthService")
@MainActor
struct EngineHealthServiceTests {
    /// A temp home holding an app-managed engine. Every caller removes it with
    /// `defer { try? FileManager.default.removeItem(at: layout.home) }`.
    func managedHome() throws -> EngineLayout {
        let fm = FileManager.default
        let layout = EngineLayout(home: fm.temporaryDirectory.appendingPathComponent("health-\(UUID().uuidString)"))
        let scoutctl = layout.scoutctl(version: "0.10.0")
        try fm.createDirectory(at: scoutctl.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "#!/bin/sh\n".write(to: scoutctl, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scoutctl.path)
        try fm.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        try """
        {"schema_version": 1, "version": "0.10.0", "engine_root": "\(layout.engineRoot(version: "0.10.0").path)", "python": "x",
         "scoutctl": "\(scoutctl.path)", "vault": "\(layout.home.path)/Scout", "managed_by": "scout-app", "written_at": "2026-09-08T00:00:00Z"}
        """.write(to: layout.pointerURL, atomically: true, encoding: .utf8)
        return layout
    }

    @Test func refreshLocatesAndRunsDoctorWithVaultEnv() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: #"{"severity": "green", "errors": [], "warnings": []}"#)
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner, environment: ["SCOUT_DATA_DIR": "/v"])
        await svc.refresh()
        #expect(svc.state.isManaged)
        #expect(svc.doctor?.severity == .green)
        #expect(!svc.needsAttention)
        #expect(runner.calls.first?.arguments == ["bootstrap", "doctor", "--json"])
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == "/v")
    }

    /// A red doctor from an engine ≤ v0.11.x, emitted the way the real engine
    /// does: `--json` is rejected, then the text form prints `severity:` on
    /// stdout and every `error:` line on **stderr**.
    @Test func redDoctorNeedsAttention() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor", "--json"], stderr: "Error: No such option: --json", exit: 2)
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: "severity: red\n",
                  stderr: "error: vault directory missing: /x\n", exit: 2)
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.doctor?.severity == .red)
        #expect(svc.doctor?.errors == ["vault directory missing: /x"])
        #expect(svc.lastError == nil)
        #expect(svc.needsAttention)
        // …and the stderr reason reaches Settings ▸ Engine, not just the service.
        let model = EngineSettingsModel(state: svc.state, doctor: svc.doctor, lastError: svc.lastError, bundledVersion: nil)
        #expect(model.healthLabel == "Needs attention")
        #expect(model.messages.first == "vault directory missing: /x")
    }

    @Test func notInstalledSkipsDoctorAndNeedsAttention() async throws {
        let layout = EngineLayout(home: FileManager.default.temporaryDirectory.appendingPathComponent("empty-\(UUID().uuidString)"))
        let runner = RuleBasedRunner()
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.state == .notInstalled)
        #expect(runner.calls.isEmpty)
        #expect(svc.needsAttention)
    }

    /// Older adopted engines (pre E3) don't understand `--json` and exit
    /// non-zero with a Click-style usage error; `refresh()` retries once
    /// without `--json` and falls back to legacy `severity: …` text parsing.
    @Test func fallsBackToLegacyTextWhenJsonFlagIsRejected() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let runner = RuleBasedRunner()
        // Register the more specific (--json) rule before the plain-prefix rule,
        // since `on(tool:prefix:)` matches by prefix and "bootstrap doctor" is a
        // prefix of "bootstrap doctor --json".
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor", "--json"], stderr: "Error: No such option: --json", exit: 2)
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: "severity: yellow\nwarning: snapshot missing: x\n")
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.doctor?.severity == .yellow)
        #expect(svc.lastError == nil)
        #expect(runner.calls(to: "scoutctl").count == 2)
        #expect(runner.calls(to: "scoutctl").first == ["bootstrap", "doctor", "--json"])
        #expect(runner.calls(to: "scoutctl").last == ["bootstrap", "doctor"])
    }

    /// Re-entrant refresh: a slow first call must not clobber a fresher,
    /// faster second call's result (finding #1 — generation guard).
    @Test func latestStartedRefreshWins() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let runner = RuleBasedRunner()
        let callCount = Locked(0)
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.starts(with: ["bootstrap", "doctor"]) }) { _, _, _ in
            let n = callCount.increment()
            if n == 1 {
                Thread.sleep(forTimeInterval: 0.3)
                return ProcessResult(exitCode: 2, stdout: Data(#"{"severity": "red", "errors": ["vault directory missing: /x"], "warnings": []}"#.utf8), stderr: Data())
            }
            return ProcessResult(exitCode: 0, stdout: Data(#"{"severity": "green", "errors": [], "warnings": []}"#.utf8), stderr: Data())
        }
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        async let a: Void = svc.refresh()
        try await Task.sleep(for: .milliseconds(50))
        await svc.refresh()
        await a
        #expect(svc.doctor?.severity == .green)
    }

    /// `scoutctl` is on disk but the doctor can't be run at all (the runner
    /// throws): no report, an error to show, and the sidebar dot on.
    @Test func doctorThatCannotRunNeedsAttention() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let runner = RuleBasedRunner()  // no rules: every call throws ENOENT
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.state.isManaged)
        #expect(!svc.state.gatesTabs)
        #expect(svc.doctor == nil)
        #expect(svc.lastError?.hasPrefix("could not run scoutctl") == true)
        #expect(svc.needsAttention)
        // …and Settings ▸ Engine shows it instead of a silent "Unknown".
        let model = EngineSettingsModel(state: svc.state, doctor: svc.doctor, lastError: svc.lastError, bundledVersion: nil)
        #expect(model.healthLabel == "Could not run doctor")
        #expect(model.messages == [svc.lastError ?? "<nil>"])
    }

    /// The doctor runs but prints a traceback instead of a report, on both
    /// the `--json` and the legacy attempt.
    @Test func doctorPrintingATracebackNeedsAttention() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"],
                  stderr: "Traceback (most recent call last):\n  File \"cli.py\", line 1\nImportError: x\n", exit: 1)
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: runner)
        await svc.refresh()
        #expect(svc.doctor == nil)
        #expect(svc.lastError?.contains("doctor output not understood") == true)
        #expect(svc.lastError?.contains("Traceback") == true)
        #expect(svc.needsAttention)
    }

    /// A doctor failure must not outlive the state it described: once the
    /// engine is gone, `refresh()` takes the no-doctor early return and that
    /// path clears `lastError` too.
    @Test func earlyReturnClearsAStaleDoctorError() async throws {
        let layout = try managedHome()
        defer { try? FileManager.default.removeItem(at: layout.home) }
        let svc = EngineHealthService(locator: EngineLocator(layout: layout), runner: RuleBasedRunner())
        await svc.refresh()
        #expect(svc.lastError != nil)

        try FileManager.default.removeItem(at: layout.home)
        await svc.refresh()
        #expect(svc.state == .notInstalled)
        #expect(svc.doctor == nil)
        #expect(svc.lastError == nil)
    }
}

/// Small thread-safe counter for the re-entrancy test's responder, which is
/// invoked concurrently from overlapping `refresh()` calls.
private final class Locked: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Int
    init(_ value: Int) { self.value = value }
    func increment() -> Int { lock.withLock { value += 1; return value } }
}
