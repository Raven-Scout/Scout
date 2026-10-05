import Testing
import Foundation
@testable import Scout

/// Lock-guarded recorder: closures handed to the model may be called from
/// whatever isolation the model chooses, so tests never append to a
/// captured `var` directly.
private final class Recorder<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [T] = []
    func append(_ item: T) { lock.withLock { items.append(item) } }
    var all: [T] { lock.withLock { items } }
}

/// Counts calls and answers from a script; the last answer repeats.
private final class ScriptedResolver: @unchecked Sendable {
    private let lock = NSLock()
    private var answers: [String?]
    private(set) var count = 0
    init(_ answers: [String?]) { self.answers = answers }
    func next() -> String? {
        lock.withLock {
            count += 1
            return answers.count > 1 ? answers.removeFirst() : answers.first ?? nil
        }
    }
    var calls: Int { lock.withLock { count } }
}

private struct NoDownloads: FileDownloader {
    func download(_ url: URL) async throws -> URL { throw URLError(.notConnectedToInternet) }
}

private let releaseFixture = EngineRelease(
    schemaVersion: 2, version: "0.10.0", engine: .init(version: "0.10.0"), uv: .init(version: "0.12.1", sha256: [:]))

@Suite("OnboardingViewModel")
@MainActor
struct OnboardingViewModelTests {
    let layout = EngineLayout(home: URL(fileURLWithPath: "/Users/alex"))
    let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/e/venv/bin/scoutctl"), python: nil,
                                version: "0.10.0", vault: URL(fileURLWithPath: "/Users/alex/Scout"))
    let ready = Prerequisites(claude: .installed(path: URL(fileURLWithPath: "/c"), version: "2.1.259"), auth: .signedIn,
                              git: .present(URL(fileURLWithPath: "/usr/bin/git")), uv: .missing)

    /// Hermetic checker: the claude resolver is injected and the git/uv
    /// system candidates are empty (Ruling 49), so nothing depends on this Mac.
    func checker(_ runner: RuleBasedRunner, layout: EngineLayout? = nil,
                 resolve: @escaping @Sendable (String) -> String? = { _ in nil }) -> PrerequisiteChecker {
        PrerequisiteChecker(runner: runner, layout: layout ?? self.layout, resolveClaude: resolve, gitCandidates: [], uvCandidates: [])
    }

    func model(engineState: EngineState = .notInstalled, layout: EngineLayout? = nil, release: EngineRelease? = nil,
               runner: RuleBasedRunner = RuleBasedRunner(), resolve: @escaping @Sendable (String) -> String? = { _ in nil },
               makeInstaller: @escaping (@escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller? = { _ in nil },
               handoff: @escaping @MainActor (String) throws -> Void = { _ in },
               appVault: URL? = nil) -> OnboardingViewModel {
        let l = layout ?? self.layout
        return OnboardingViewModel(engineState: engineState, layout: l, release: release, runner: runner,
                                   prerequisites: checker(runner, layout: l, resolve: resolve), makeInstaller: makeInstaller,
                                   handoff: handoff, appVault: appVault, onFinished: {})
    }

    // MARK: step machine

    @Test func initialStepFollowsTheEngineState() {
        let noClaude = Prerequisites(claude: .missing, auth: .unknown, git: .missing, uv: .missing)
        #expect(OnboardingViewModel.initialStep(engineState: .notInstalled, prerequisites: ready) == .welcome)
        #expect(OnboardingViewModel.initialStep(engineState: .notInstalled, prerequisites: noClaude) == .welcome)
        #expect(OnboardingViewModel.initialStep(engineState: .managed(install, vaultBootstrapped: false), prerequisites: ready) == .identity)
        #expect(OnboardingViewModel.initialStep(engineState: .managed(install, vaultBootstrapped: false), prerequisites: noClaude) == .prerequisites)
        #expect(OnboardingViewModel.initialStep(engineState: .managed(install, vaultBootstrapped: true), prerequisites: ready) == .ready)
        #expect(OnboardingViewModel.initialStep(engineState: .external(install, .devCheckout), prerequisites: ready) == .ready)
        #expect(OnboardingViewModel.initialStep(engineState: .broken(nil, reason: "x"), prerequisites: ready) == .welcome)
    }

    @Test func prerequisitesGateOnlyOnClaudeInstalled() {
        let m = model()
        m.step = .prerequisites
        m.prerequisites = Prerequisites(claude: .missing, auth: .unknown, git: .present(URL(fileURLWithPath: "/usr/bin/git")), uv: .missing)
        #expect(!m.canContinue)
        m.prerequisites = Prerequisites(claude: .installed(path: URL(fileURLWithPath: "/c"), version: nil), auth: .signedOut, git: .missing, uv: .missing)
        #expect(m.canContinue)   // sign-in and git are surfaced, not blocking
    }

    @Test func identityStepRequiresNameAndEmail() {
        let m = model()
        m.step = .identity
        m.identity = OnboardingViewModel.prefilledIdentity(gitName: nil, gitEmail: nil, vault: URL(fileURLWithPath: "/Users/alex/Scout"))
        #expect(!m.canContinue)
        m.identity.userName = "Alex"; m.identity.userEmail = "alex@example.com"
        #expect(m.canContinue)
    }

    @Test func prefillUsesGitConfigAndSystemTimezone() {
        let i = OnboardingViewModel.prefilledIdentity(gitName: "Alex", gitEmail: "alex@example.com", vault: URL(fileURLWithPath: "/Users/alex/Scout"))
        #expect(i.userName == "Alex" && i.userEmail == "alex@example.com" && i.timezone == TimeZone.current.identifier && i.instanceName == "Scout")
        #expect(i.vault.path == "/Users/alex/Scout")
    }

    @Test func backStepsDownAndStopsAtWelcome() {
        let m = model()
        m.step = .identity
        m.back()
        #expect(m.step == .engine)
        m.step = .welcome
        m.back()
        #expect(m.step == .welcome)
    }

    // MARK: start

    @Test func startPrefillsIdentityFromGitWhenGitIsPresent() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/Library/Developer/CommandLineTools\n")
        runner.on(tool: "git", prefix: ["config", "--global", "user.name"], stdout: "Alex\n")
        runner.on(tool: "git", prefix: ["config", "--global", "user.email"], stdout: "alex@example.com\n")
        let m = model(runner: runner)
        await m.start()
        #expect(m.identity.userName == "Alex")
        #expect(m.identity.userEmail == "alex@example.com")
        #expect(m.step == .welcome)
    }

    /// Spec §5: never touch `/usr/bin/git` when git is missing — on a Mac
    /// without the Command Line Tools that pops Apple's install dialog.
    @Test func startNeverRunsGitWhenGitIsMissing() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stderr: "xcode-select: error", exit: 2)
        runner.on(tool: "git", stdout: "should not be asked\n")
        let m = model(runner: runner)
        await m.start()
        #expect(runner.calls(to: "git").isEmpty)
        #expect(m.prerequisites?.git == .missing)
        #expect(m.identity.userName.isEmpty)
    }

    // MARK: handoffs

    @Test func handoffsRunTheDocumentedCommands() {
        let ran = Recorder<String>()
        let m = model(handoff: { ran.append($0) })
        m.prerequisites = ready
        m.installClaudeCode()
        m.signIn()
        #expect(ran.all == [ClaudeCodeCLI.installCommand, ClaudeCodeCLI.loginCommand(claude: URL(fileURLWithPath: "/c"))])
    }

    @Test func signInWithoutClaudeDoesNothingAndAHandoffFailureIsSurfaced() {
        struct Denied: Error, LocalizedError { var errorDescription: String? { "not authorized" } }
        let ran = Recorder<String>()
        let m = model(handoff: { ran.append($0); throw Denied() })
        m.prerequisites = Prerequisites(claude: .missing, auth: .unknown, git: .missing, uv: .missing)
        m.signIn()
        #expect(ran.all.isEmpty)
        m.installClaudeCode()
        #expect(m.lastError?.contains("not authorized") == true)
    }

    // MARK: prerequisites polling

    @Test func pollingRechecksUntilClaudeIsInstalledAndSignedIn() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "c", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "c", prefix: ["auth", "status"], stdout: #"{"loggedIn": true}"#)
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/x\n")
        let resolver = ScriptedResolver([nil, nil, "/c"])
        let m = model(runner: runner, resolve: { _ in resolver.next() })
        m.prerequisitePollInterval = .milliseconds(5)
        m.step = .prerequisites
        await m.pollPrerequisites()
        #expect(resolver.calls == 3)
        #expect(m.prerequisites?.auth == .signedIn)
        #expect(!m.needsPrerequisitePolling)
    }

    @Test func pollingDoesNothingOffThePrerequisitesStep() async {
        let resolver = ScriptedResolver([nil])
        let m = model(resolve: { _ in resolver.next() })
        m.prerequisitePollInterval = .milliseconds(5)
        m.step = .identity
        await m.pollPrerequisites()
        #expect(resolver.calls == 0)
    }

    /// A second poll (the view re-appearing) supersedes the first, so loops
    /// never stack; changing step ends the survivor.
    @Test func aNewPollSupersedesTheOldOneAndAStepChangeEndsIt() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/x\n")
        let m = model(runner: runner, resolve: { _ in nil })
        m.prerequisitePollInterval = .milliseconds(5)
        m.step = .prerequisites
        let first = Task { await m.pollPrerequisites() }
        try? await Task.sleep(for: .milliseconds(20))
        let second = Task { await m.pollPrerequisites() }
        await first.value          // returns although Claude Code is still missing
        #expect(m.step == .prerequisites && m.needsPrerequisitePolling)
        m.step = .engine
        await second.value
    }

    // MARK: engine

    @Test func installEngineWithoutABundledEngineExplains() async {
        let m = model()
        m.prerequisites = ready
        m.step = .prerequisites
        await m.continueTapped()
        #expect(m.step == .engine)
        #expect(m.lastError?.contains("carries no engine") == true)
        #expect(!m.canContinue)
        #expect(!m.busy)
    }

    // MARK: connectors

    @Test func detectConnectorsEnablesConnectedOnes() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["connectors", "detect"], stdout: #"{"email": {"status": "connected", "needs_user_input": [], "evidence": ""}, "slack": {"status": "needs_auth", "needs_user_input": ["user_slack_id"], "evidence": ""}, "github": {"status": "connected", "needs_user_input": ["github_username"], "evidence": ""}}"#)
        let m = model(engineState: .managed(install, vaultBootstrapped: false), runner: runner)
        m.prerequisites = ready
        await m.detectConnectors()
        #expect(m.enabledConnectors == ["email", "github"])
        #expect(m.detections["slack"]?.status == .needsAuth)
        #expect(runner.calls(to: "scoutctl").first == ["connectors", "detect", "--json", "--claude-bin", "/c"])
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == "/Users/alex/Scout")
        #expect(m.lastError == nil)
    }

    @Test func detectionFailureStillOffersEveryKnownConnector() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["connectors", "detect"], stdout: "Traceback…", exit: 1)
        let m = model(engineState: .managed(install, vaultBootstrapped: false), runner: runner)
        m.prerequisites = ready
        await m.detectConnectors()
        #expect(m.lastError != nil)
        #expect(m.enabledConnectors.isEmpty)
        #expect(Set(m.connectorKeys).isSuperset(of: ConnectorDetection.displayNames.keys))
    }

    @Test func dailyBudgetMustBeEmptyOrAPositiveDecimal() {
        #expect(OnboardingViewModel.parseDailyBudget("") == nil)
        #expect(OnboardingViewModel.parseDailyBudget("  ") == nil)
        #expect(OnboardingViewModel.parseDailyBudget("12.50") == "12.50")
        #expect(OnboardingViewModel.parseDailyBudget(" 20 ") == "20")
        #expect(OnboardingViewModel.parseDailyBudget("0") == nil)
        #expect(OnboardingViewModel.parseDailyBudget("-3") == nil)
        #expect(OnboardingViewModel.parseDailyBudget("1e3") == nil)
        #expect(OnboardingViewModel.parseDailyBudget("abc") == nil)

        let m = model()
        m.step = .connectors
        #expect(m.canContinue)            // empty: optional
        m.dailyBudget = "ten"
        #expect(!m.canContinue)           // non-empty and invalid blocks
        m.dailyBudget = "10"
        #expect(m.canContinue)
    }

    // MARK: vault + daily budget (Ruling 44)

    struct VaultFixture {
        let home: URL
        let layout: EngineLayout
        let vault: URL
        let runner: RuleBasedRunner
    }

    func vaultFixture(bootstrapOK: Bool = true) throws -> VaultFixture {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("onboarding-\(UUID().uuidString)")
        let vault = home.appendingPathComponent("Scout")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        let runner = RuleBasedRunner()
        let action = bootstrapOK ? "install" : "refused"
        let error = bootstrapOK ? "null" : #""install needs --user-name""#
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"\#(action)","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.10.0","error":\#(error),"doctor":{"severity":"green","errors":[],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":"p"}"#)
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: #"{"severity":"green","errors":[],"warnings":[]}"#)
        return VaultFixture(home: home, layout: EngineLayout(home: home), vault: vault, runner: runner)
    }

    func vaultModel(_ f: VaultFixture) -> OnboardingViewModel {
        let layout = f.layout, runner = f.runner
        let m = model(layout: layout, release: releaseFixture, runner: runner, makeInstaller: { sink in
            EngineInstaller(layout: layout, release: releaseFixture, tarballURL: nil, runner: runner,
                            uv: UvInstaller(release: releaseFixture.uv, layout: layout, downloader: NoDownloads(), runner: runner, systemCandidates: []),
                            claude: URL(fileURLWithPath: "/c"), progress: sink)
        })
        m.prerequisites = ready
        m.vaultPath = f.vault.path
        m.identity.userName = "Alex"; m.identity.userEmail = "alex@example.com"
        return m
    }

    func budgetCalls(_ runner: RuleBasedRunner) -> [(arguments: [String], environment: [String: String])] {
        runner.calls.filter { $0.executable.lastPathComponent == "scoutctl" && $0.arguments.first == "budget" }.map { ($0.arguments, $0.environment) }
    }

    @Test func createVaultSetsTheDailyBudgetAfterBootstrap() async throws {
        let f = try vaultFixture()
        defer { try? FileManager.default.removeItem(at: f.home) }
        f.runner.on(tool: "scoutctl", prefix: ["budget", "set"], stdout: #"{"daily_budget_usd": 12.5}"#)
        let m = vaultModel(f)
        m.enabledConnectors = ["email"]
        m.dailyBudget = " 12.5 "
        m.step = .vault
        await m.createVault()
        #expect(m.progress[.bootstrapVault]?.status == .done)
        #expect(m.progress[.verify]?.status == .done)
        let calls = budgetCalls(f.runner)
        #expect(calls.map(\.arguments) == [["budget", "set", "--daily-usd", "12.5", "--json"]])
        #expect(calls.first?.environment["SCOUT_DATA_DIR"] == f.vault.path)
        #expect(m.lastError == nil)
        #expect(m.canContinue)
        let auto = f.runner.calls(to: "scoutctl").first { $0.starts(with: ["bootstrap", "auto"]) }
        #expect(auto?.contains("--connectors") == true && auto?.contains("email") == true)
        #expect(!m.busy)
    }

    @Test func aDailyBudgetFailureIsSurfacedButDoesNotFailSetup() async throws {
        let f = try vaultFixture()
        defer { try? FileManager.default.removeItem(at: f.home) }
        f.runner.on(tool: "scoutctl", prefix: ["budget", "set"], stderr: "error: config is read-only", exit: 1)
        let m = vaultModel(f)
        m.dailyBudget = "20"
        m.step = .vault
        await m.createVault()
        #expect(budgetCalls(f.runner).count == 1)
        #expect(m.lastError?.contains("daily budget") == true)
        #expect(m.lastError?.contains("config is read-only") == true)
        #expect(m.canContinue)            // setup itself succeeded
    }

    @Test func anEmptyDailyBudgetRunsNothing() async throws {
        let f = try vaultFixture()
        defer { try? FileManager.default.removeItem(at: f.home) }
        let m = vaultModel(f)
        m.step = .vault
        await m.createVault()
        #expect(m.progress[.bootstrapVault]?.status == .done)
        #expect(budgetCalls(f.runner).isEmpty)
        #expect(m.lastError == nil)
    }

    @Test func noDailyBudgetWhenBootstrapFails() async throws {
        let f = try vaultFixture(bootstrapOK: false)
        defer { try? FileManager.default.removeItem(at: f.home) }
        let m = vaultModel(f)
        m.dailyBudget = "20"
        m.step = .vault
        await m.createVault()
        #expect(budgetCalls(f.runner).isEmpty)
        #expect(m.lastError?.contains("install needs --user-name") == true)
        #expect(!m.canContinue)
    }

    // MARK: ready

    @Test func continuingToReadyRunsDoctor() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: #"{"severity":"yellow","errors":[],"warnings":["snapshot missing: x"]}"#)
        let m = model(engineState: .managed(install, vaultBootstrapped: false), runner: runner)
        m.step = .vault
        m.progress[.bootstrapVault] = InstallProgress(step: .bootstrapVault, status: .done, log: "")
        await m.continueTapped()
        #expect(m.step == .ready)
        #expect(m.doctor?.severity == .yellow)
        #expect(m.doctor?.warnings == ["snapshot missing: x"])
    }

    /// `type` values come from the engine's `SlotType` enum
    /// (`plugin/engine/scout/schedule.py`): briefing, consolidation,
    /// dreaming, research, manual.
    @Test func firstBriefingFiresTheBriefingSlot() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["schedule", "list"], stdout: #"[{"key": "consolidation-pm", "type": "consolidation", "runner": "claude"}, {"key": "morning", "type": "briefing", "runner": "claude"}]"#)
        runner.on(tool: "scoutctl", prefix: ["schedule", "fire-now"])
        let m = model(engineState: .managed(install, vaultBootstrapped: true), runner: runner)
        await m.runFirstBriefing()
        #expect(runner.calls(to: "scoutctl") == [["schedule", "list", "--json"], ["schedule", "fire-now", "morning"]])
        #expect(m.lastError == nil)
        #expect(m.briefingStatus != nil)
    }

    @Test func firstBriefingFallsBackToTheFirstSlot() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["schedule", "list"], stdout: #"[{"key": "dream", "type": "dreaming"}, {"key": "research", "type": "research"}]"#)
        runner.on(tool: "scoutctl", prefix: ["schedule", "fire-now"])
        let m = model(engineState: .managed(install, vaultBootstrapped: true), runner: runner)
        await m.runFirstBriefing()
        #expect(runner.calls(to: "scoutctl").last == ["schedule", "fire-now", "dream"])
    }

    @Test func firstBriefingReportsAnEmptyScheduleAndAFailedFire() async {
        let empty = RuleBasedRunner()
        empty.on(tool: "scoutctl", prefix: ["schedule", "list"], stdout: "[]")
        let m = model(engineState: .managed(install, vaultBootstrapped: true), runner: empty)
        await m.runFirstBriefing()
        #expect(m.lastError?.contains("No briefing slot") == true)
        #expect(empty.calls(to: "scoutctl").count == 1)

        let failing = RuleBasedRunner()
        failing.on(tool: "scoutctl", prefix: ["schedule", "list"], stdout: #"[{"key": "morning", "type": "briefing"}]"#)
        failing.on(tool: "scoutctl", prefix: ["schedule", "fire-now"], stderr: "unknown slot", exit: 1)
        let m2 = model(engineState: .managed(install, vaultBootstrapped: true), runner: failing)
        await m2.runFirstBriefing()
        #expect(m2.lastError?.contains("unknown slot") == true)
        #expect(m2.briefingStatus == nil)
    }

    // MARK: restart note (Ruling 46)

    @Test func restartNoteOnlyWhenTheChosenVaultDiffersFromTheAppVault() {
        let app = URL(fileURLWithPath: "/Users/alex/Scout")
        #expect(OnboardingViewModel.restartNote(chosenVault: URL(fileURLWithPath: "/Users/alex/Scout"), appVault: app) == nil)
        #expect(OnboardingViewModel.restartNote(chosenVault: URL(fileURLWithPath: "/Users/alex/./Scout/"), appVault: app) == nil)
        #expect(OnboardingViewModel.restartNote(chosenVault: URL(fileURLWithPath: "/Users/alex/Other/../Scout"), appVault: app) == nil)
        #expect(OnboardingViewModel.restartNote(chosenVault: URL(fileURLWithPath: "/Users/alex/Notes"), appVault: app)
                == "Restart Scout to open /Users/alex/Notes")
    }

    @Test func restartNoteOnTheModelUsesTheInjectedAppVault() {
        let none = model()
        #expect(none.restartNote == nil)
        let m = model(appVault: URL(fileURLWithPath: "/Users/alex/Scout"))
        #expect(m.restartNote == nil)
        m.vaultPath = "/Users/alex/Notes"
        #expect(m.restartNote == "Restart Scout to open /Users/alex/Notes")
    }
}
