import Foundation
import Combine

/// The latest progress per step for one installer run. The installer reports
/// from its own actor; the model reads this back on the main actor, both live
/// (via a hop per report) and once, synchronously, when the run returns — so
/// `progress` and `lastError` never depend on whether those hops have run yet.
private nonisolated final class InstallProgressLedger: @unchecked Sendable {
    private let lock = NSLock()
    private var latest: [InstallStep: InstallProgress] = [:]
    func record(_ p: InstallProgress) { lock.withLock { latest[p.step] = p } }
    func latest(_ step: InstallStep) -> InstallProgress? { lock.withLock { latest[step] } }
    var snapshot: [InstallStep: InstallProgress] { lock.withLock { latest } }
}

/// One record of `scoutctl schedule list --json` — only the two fields the
/// first-briefing button needs (`plugin/engine/scout/cli.py`,
/// `cli_schedule_list`).
private nonisolated struct ScheduleSlotSummary: Decodable, Sendable {
    let key: String
    let type: String?
}

/// State machine behind OnboardingView (spec §5): prerequisites → engine
/// (ensureUv, unpack, venv, register with Claude Code) → identity →
/// connectors → vault (`bootstrap auto` + verify; the engine writes the
/// pointer with `managed_by: scout-app`) → ready. Process work runs through
/// the injected runner and the installer actor; every mutation lands here on
/// the main actor.
@MainActor
final class OnboardingViewModel: ObservableObject {
    enum Step: Int, CaseIterable { case welcome, prerequisites, engine, identity, connectors, vault, ready }

    @Published var step: Step
    @Published var vaultPath: String
    @Published var prerequisites: Prerequisites?
    @Published var progress: [InstallStep: InstallProgress] = [:]
    @Published var identity: BootstrapInput
    @Published var detections: [String: ConnectorDetection] = [:]
    @Published var enabledConnectors: Set<String> = []
    /// Optional "Daily budget (USD)" (Ruling 44). Empty leaves the engine's
    /// default; otherwise it must be a positive decimal and is applied with
    /// `scoutctl budget set --daily-usd` once the vault exists.
    @Published var dailyBudget = ""
    @Published var lastError: String?
    @Published var busy = false
    @Published var doctor: DoctorReport?
    /// Confirmation after "Run your first briefing now" started a session.
    @Published var briefingStatus: String?

    /// How often the prerequisites screen re-checks while Claude Code is
    /// missing or signed out. Settable so tests poll in milliseconds.
    var prerequisitePollInterval: Duration = .seconds(3)

    let engineState: EngineState
    let layout: EngineLayout
    let release: EngineRelease?
    /// The vault the running app's services were wired to at launch (C8
    /// passes it). When onboarding picks a different one, Ready says to restart.
    let appVault: URL?
    private let runner: any ProcessRunner
    private let checker: PrerequisiteChecker
    private let makeInstaller: (@escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller?
    private let handoff: @MainActor (String) throws -> Void
    private let onFinished: () -> Void

    /// True once `start()` has finished its first check. Welcome's Continue
    /// waits for it, so a click during the check can't race `start()`
    /// (Ruling 66).
    @Published private(set) var didStart = false

    private var started = false
    private var pollGeneration = 0
    private var installGeneration = 0
    /// The installer run whose live progress hops may still land; nil once
    /// that run has settled, so a hop that arrives late is dropped.
    private var liveGeneration: Int?

    static let briefingSlotType = "briefing"   // `SlotType.BRIEFING` in plugin/engine/scout/schedule.py

    init(engineState: EngineState, layout: EngineLayout, release: EngineRelease?, runner: any ProcessRunner,
         prerequisites: PrerequisiteChecker,
         makeInstaller: @escaping (@escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller?,
         handoff: @escaping @MainActor (String) throws -> Void = { try TerminalHandoff.run($0) },
         appVault: URL? = nil,
         onFinished: @escaping () -> Void) {
        self.engineState = engineState; self.layout = layout; self.release = release; self.runner = runner
        self.checker = prerequisites; self.makeInstaller = makeInstaller; self.handoff = handoff
        self.appVault = appVault; self.onFinished = onFinished
        let vault = engineState.install?.vault ?? layout.home.appending(path: "Scout")
        self.vaultPath = vault.path
        self.identity = Self.prefilledIdentity(gitName: nil, gitEmail: nil, vault: vault)
        self.step = .welcome
    }

    // MARK: derived

    /// `vaultPath` with a leading `~` expanded against the layout's home (not
    /// the process's), so tests stay inside their temp home.
    var vaultURL: URL {
        let trimmed = vaultPath.trimmingCharacters(in: .whitespaces)
        if trimmed == "~" { return layout.home }
        if trimmed.hasPrefix("~/") { return layout.home.appending(path: String(trimmed.dropFirst(2))) }
        return URL(fileURLWithPath: trimmed)
    }

    var claudePath: URL? { if case .installed(let p, _)? = prerequisites?.claude { return p }; return nil }

    /// The engine this onboarding drives: an external engine's own scoutctl,
    /// else the managed venv for the bundled version, else whatever the
    /// located install names.
    var engineScoutctl: URL? {
        if case .external(let install, _) = engineState { return install.scoutctl }
        return release.map { layout.scoutctl(version: $0.engine.version) } ?? engineState.scoutctl
    }

    var canContinue: Bool {
        switch step {
        case .welcome: return didStart && vaultPathIsValid
        case .prerequisites: return prerequisites?.canInstallEngine == true
        case .engine: return [.unpackEngine, .buildVenv, .registerWithClaudeCode].allSatisfy { isDone($0) }
        case .identity: return !identity.userName.trimmingCharacters(in: .whitespaces).isEmpty && identity.userEmail.contains("@")
        case .connectors: return dailyBudgetIsValid
        case .vault: return isDone(.bootstrapVault)
        case .ready: return true
        }
    }

    /// Every connector the user can toggle: what detection reported plus the
    /// shipped registry, so a failed detection still leaves them pickable.
    var connectorKeys: [String] {
        Set(detections.keys).union(ConnectorDetection.displayNames.keys)
            .sorted { (ConnectorDetection.displayNames[$0] ?? $0) < (ConnectorDetection.displayNames[$1] ?? $1) }
    }

    /// The vault must be absolute or `~`-prefixed: a relative path would
    /// resolve against whatever the app's working directory happens to be.
    var vaultPathIsValid: Bool { Self.isAcceptableVaultPath(vaultPath) }

    static func isAcceptableVaultPath(_ path: String) -> Bool {
        let t = path.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix("/") || t == "~" || t.hasPrefix("~/")
    }

    /// The install steps of the current step, when it has any.
    private var installSteps: [InstallStep]? {
        switch step {
        case .engine: return [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode]
        case .vault: return [.bootstrapVault, .verify]
        default: return nil
        }
    }

    /// Offer Install/Retry on Engine and Vault while that step is unfinished
    /// and nothing is running. This covers a failed run and also an Engine
    /// step reached by Back from Identity (onboarding that started at
    /// Identity never ran the install here). A daily-budget warning after a
    /// successful bootstrap leaves Vault finished, so it never offers to
    /// re-run setup.
    var canRetry: Bool { installSteps != nil && !busy && !canContinue }

    /// "Install" when this step's install has never run, else "Retry".
    var retryTitle: String {
        let attempted = lastError != nil || (installSteps ?? []).contains { progress[$0] != nil }
        return attempted ? "Retry" : "Install"
    }

    func retry() async {
        guard canRetry else { return }
        lastError = nil
        switch step {
        case .engine: await installEngine()
        case .vault: await createVault()
        default: break
        }
    }

    var dailyBudgetIsValid: Bool {
        dailyBudget.trimmingCharacters(in: .whitespaces).isEmpty || Self.parseDailyBudget(dailyBudget) != nil
    }

    var needsPrerequisitePolling: Bool {
        guard let p = prerequisites else { return true }
        return !p.canInstallEngine || p.auth != .signedIn
    }

    var restartNote: String? { appVault.flatMap { Self.restartNote(chosenVault: vaultURL, appVault: $0) } }

    private func isDone(_ s: InstallStep) -> Bool {
        switch progress[s]?.status { case .done?, .skipped?: return true; default: return false }
    }

    static func initialStep(engineState: EngineState, prerequisites: Prerequisites) -> Step {
        switch engineState {
        case .managed(_, vaultBootstrapped: true), .external: return .ready
        case .managed(_, vaultBootstrapped: false): return prerequisites.canInstallEngine ? .identity : .prerequisites
        case .notInstalled, .broken: return .welcome
        }
    }

    static func prefilledIdentity(gitName: String?, gitEmail: String?, vault: URL) -> BootstrapInput {
        BootstrapInput(vault: vault, userName: gitName ?? "", userEmail: gitEmail ?? "", timezone: TimeZone.current.identifier)
    }

    /// A positive plain decimal ("20", "12.50", ".5"), trimmed; nil for
    /// empty, zero, negative, exponent or anything else.
    static func parseDailyBudget(_ text: String) -> String? {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty,
              t.allSatisfy({ $0.isASCII && ($0.isNumber || $0 == ".") }),
              t.filter({ $0 == "." }).count <= 1,
              let value = Double(t), value.isFinite, value > 0 else { return nil }
        return t
    }

    /// Ruling 46: services were wired to `appVault` at launch, so a different
    /// vault chosen here only takes effect after a restart.
    static func restartNote(chosenVault: URL, appVault: URL) -> String? {
        let chosen = chosenVault.standardizedFileURL.path
        return chosen == appVault.standardizedFileURL.path ? nil : "Restart Scout to open \(chosen)"
    }

    // MARK: flow

    /// Runs once per model (the view's `.task` may fire again on re-appear).
    /// The jump to `initialStep` only happens if the user is still on the
    /// step `start()` began from. Welcome's Continue is also disabled until
    /// this finishes. Two independent guards (Ruling 66), so the 1–3 s check
    /// can never snap a user who has moved on back to an earlier step.
    func start() async {
        guard !started else { return }
        started = true
        let entryStep = step
        let checked = await checker.check()
        prerequisites = checked
        let git = await gitIdentity()
        if identity.userName.isEmpty, let name = git.name { identity.userName = name }
        if identity.userEmail.isEmpty, let email = git.email { identity.userEmail = email }
        identity.vault = vaultURL
        if step == entryStep {
            step = Self.initialStep(engineState: engineState, prerequisites: checked)
        }
        didStart = true
        if step == .ready { await refreshDoctor() }
    }

    func continueTapped() async {
        guard canContinue, !busy, let next = Step(rawValue: step.rawValue + 1) else { return }
        lastError = nil
        identity.vault = vaultURL
        step = next
        switch next {
        case .prerequisites: await recheckPrerequisites()
        case .engine: await installEngine()
        case .connectors: await detectConnectors()
        case .vault: await createVault()
        case .ready: await refreshDoctor()
        case .welcome, .identity: break
        }
    }

    func back() {
        guard let prev = Step(rawValue: step.rawValue - 1) else { return }
        lastError = nil
        step = prev
    }

    func finish() { onFinished() }

    // MARK: prerequisites

    func recheckPrerequisites() async { prerequisites = await checker.check() }

    /// Re-checks every `prerequisitePollInterval` while Claude Code is missing
    /// or signed out and this is still the prerequisites step. A newer call
    /// supersedes an older one (the view re-appearing), so loops never stack;
    /// cancellation (the view leaving) ends it at the next sleep.
    func pollPrerequisites() async {
        pollGeneration += 1
        let generation = pollGeneration
        while generation == pollGeneration, step == .prerequisites, needsPrerequisitePolling, !Task.isCancelled {
            do { try await Task.sleep(for: prerequisitePollInterval) } catch { return }
            guard generation == pollGeneration, step == .prerequisites else { return }
            await recheckPrerequisites()
        }
    }

    func installClaudeCode() { runHandoff(ClaudeCodeCLI.installCommand) }

    func signIn() {
        guard let claudePath else { return }
        runHandoff(ClaudeCodeCLI.loginCommand(claude: claudePath))
    }

    /// Apple's own installer dialog; the user re-checks once it finishes.
    func installCommandLineTools() async {
        _ = try? await runner.run(executable: URL(fileURLWithPath: "/usr/bin/xcode-select"), arguments: ["--install"], environment: [:], workingDirectory: nil)
    }

    private func runHandoff(_ command: String) {
        do { try handoff(command) } catch { lastError = "Could not open Terminal: \(error.localizedDescription)" }
    }

    // MARK: engine

    func installEngine() async {
        busy = true
        defer { busy = false }
        await runInstaller(steps: [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode], mode: .upgrade(vault: vaultURL))
    }

    func createVault() async {
        busy = true
        defer { busy = false }
        identity.vault = vaultURL
        identity.connectors = enabledConnectors
        let budget = Self.parseDailyBudget(dailyBudget)
        await runInstaller(steps: [.bootstrapVault, .verify], mode: .install(identity))
        // Ruling 44: the daily budget lives in the vault's config, so it is
        // only written once bootstrap created the vault. Its failure is
        // reported but never fails setup.
        if let budget, isDone(.bootstrapVault) { await setDailyBudget(budget) }
    }

    @discardableResult
    private func runInstaller(steps: [InstallStep], mode: InstallMode) async -> Bool {
        lastError = nil
        installGeneration += 1
        let generation = installGeneration
        liveGeneration = generation
        let ledger = InstallProgressLedger()
        let installer = makeInstaller { [weak self] p in
            ledger.record(p)
            guard let model = self else { return }
            Task { @MainActor in model.applyLiveProgress(ledger, step: p.step, generation: generation) }
        }
        guard let installer else {
            lastError = "This build of Scout carries no engine (Debug build without a bundled tarball)."
            return false
        }
        for s in steps { progress[s] = nil }
        let ok = await installer.run(steps: steps, mode: mode)
        // Settle from the ledger: the live hops above may not have run yet.
        // From here on this run's hops are dropped, so a late one can never
        // overwrite the settled state.
        if liveGeneration == generation { liveGeneration = nil }
        let final = ledger.snapshot
        for (s, p) in final { progress[s] = p }
        if !ok {
            let failed = steps.compactMap { final[$0] }.first { if case .failed = $0.status { return true }; return false }
            lastError = failed?.log ?? "Setup stopped before it finished."
        }
        return ok
    }

    private func applyLiveProgress(_ ledger: InstallProgressLedger, step: InstallStep, generation: Int) {
        guard generation == liveGeneration, let latest = ledger.latest(step) else { return }
        progress[step] = latest
    }

    private func setDailyBudget(_ value: String) async {
        guard let scoutctl = engineScoutctl else { return }
        let failure: String?
        do {
            let r = try await runner.run(executable: scoutctl, arguments: ["budget", "set", "--daily-usd", value, "--json"],
                                         environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: nil)
            failure = r.exitCode == 0 ? nil : Self.oneLine(r.stderr.isEmpty ? r.stdout : r.stderr)
        } catch {
            failure = error.localizedDescription
        }
        guard let failure else { return }
        appendError("Could not set the daily budget to $\(value): \(failure). Setup is otherwise complete — set it later in Settings ▸ Budget.")
    }

    // MARK: connectors

    func detectConnectors() async {
        guard let scoutctl = engineScoutctl, let claudePath else {
            lastError = "Connectors can't be detected without the engine and Claude Code — you can still pick them by hand."
            return
        }
        busy = true
        defer { busy = false }
        guard let result = try? await runner.run(executable: scoutctl, arguments: ["connectors", "detect", "--json", "--claude-bin", claudePath.path],
                                                 environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: nil),
              let parsed = ConnectorDetection.parse(result.stdout) else {
            lastError = "Could not detect connectors — you can still pick them by hand."
            return
        }
        lastError = nil
        detections = parsed
        enabledConnectors = Set(parsed.filter { $0.value.status == .connected }.map(\.key))
    }

    // MARK: ready

    func refreshDoctor() async {
        guard let scoutctl = engineScoutctl else { return }
        if let r = try? await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor", "--json"],
                                         environment: ["SCOUT_DATA_DIR": vaultURL.path], workingDirectory: nil) {
            doctor = DoctorReport.parse(stdout: r.stdout, stderr: r.stderr)
        }
    }

    /// Fires the schedule's briefing slot (`type == "briefing"`, the engine's
    /// `SlotType.BRIEFING`), falling back to the first slot if none matches.
    func runFirstBriefing() async {
        guard let scoutctl = engineScoutctl else { lastError = "No engine to run a briefing with."; return }
        busy = true
        defer { busy = false }
        lastError = nil
        briefingStatus = nil
        let environment = ["SCOUT_DATA_DIR": vaultURL.path]
        guard let list = try? await runner.run(executable: scoutctl, arguments: ["schedule", "list", "--json"], environment: environment, workingDirectory: nil),
              let slots = try? JSONDecoder().decode([ScheduleSlotSummary].self, from: list.stdout),
              let slot = slots.first(where: { $0.type == Self.briefingSlotType }) ?? slots.first else {
            lastError = "No briefing slot found in the schedule."
            return
        }
        do {
            let r = try await runner.run(executable: scoutctl, arguments: ["schedule", "fire-now", slot.key], environment: environment, workingDirectory: vaultURL)
            if r.exitCode == 0 {
                briefingStatus = "Started \u{201C}\(slot.key)\u{201D}. It runs in the background; results land in your vault."
            } else {
                lastError = "Could not start the briefing: \(Self.oneLine(r.stderr.isEmpty ? r.stdout : r.stderr))"
            }
        } catch {
            lastError = "Could not start the briefing: \(error.localizedDescription)"
        }
    }

    // MARK: helpers

    /// Reads `user.name`/`user.email` with the git prerequisites found. Never
    /// runs `/usr/bin/git` when git is `.missing` — without the Command Line
    /// Tools that pops Apple's install dialog (spec §5).
    private func gitIdentity() async -> (name: String?, email: String?) {
        guard case .present(let git)? = prerequisites?.git else { return (nil, nil) }
        func read(_ key: String) async -> String? {
            guard let r = try? await runner.run(executable: git, arguments: ["config", "--global", key], environment: [:], workingDirectory: nil),
                  r.exitCode == 0 else { return nil }
            let s = String(decoding: r.stdout, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            return s.isEmpty ? nil : s
        }
        return (await read("user.name"), await read("user.email"))
    }

    private func appendError(_ message: String) {
        lastError = [lastError, message].compactMap { $0 }.joined(separator: "\n")
    }

    private static func oneLine(_ data: Data, max: Int = 300) -> String {
        let text = String(decoding: data.prefix(max), as: UTF8.self)
            .replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
        if text.isEmpty { return "no output" }
        return data.count > max ? text + "…" : text
    }
}
