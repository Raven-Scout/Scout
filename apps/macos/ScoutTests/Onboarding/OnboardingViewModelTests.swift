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

/// A one-shot latch: `wait()` suspends until `open()` (then returns at once).
private actor Gate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    func wait() async {
        if isOpen { return }
        await withCheckedContinuation { waiters.append($0) }
    }
    func open() {
        isOpen = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}

/// Keeps every progress sink the model handed an installer, in order.
private final class SinkBox: @unchecked Sendable {
    private let lock = NSLock()
    private var sinks: [@Sendable (InstallProgress) -> Void] = []
    func append(_ sink: @escaping @Sendable (InstallProgress) -> Void) { lock.withLock { sinks.append(sink) } }
    var first: (@Sendable (InstallProgress) -> Void)? { lock.withLock { sinks.first } }
    var last: (@Sendable (InstallProgress) -> Void)? { lock.withLock { sinks.last } }
}

/// Lets a runner responder reach the model created after the runner.
private final class ModelHolder: @unchecked Sendable {
    var model: OnboardingViewModel?
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

    /// Ruling 69 I5: a managed engine without a vault skips to Identity only
    /// at the bundled version — the vault step runs the bundled scoutctl.
    @Test func anOffVersionEngineWithoutAVaultStartsAtEngine() {
        let noClaude = Prerequisites(claude: .missing, auth: .unknown, git: .missing, uv: .missing)
        let unset = EngineState.managed(install, vaultBootstrapped: false)   // install.version == "0.10.0"
        #expect(OnboardingViewModel.initialStep(engineState: unset, prerequisites: ready, bundledVersion: "0.10.0") == .identity)
        #expect(OnboardingViewModel.initialStep(engineState: unset, prerequisites: ready, bundledVersion: "0.11.0") == .engine)
        #expect(OnboardingViewModel.initialStep(engineState: unset, prerequisites: noClaude, bundledVersion: "0.11.0") == .prerequisites)
        #expect(OnboardingViewModel.initialStep(engineState: unset, prerequisites: ready, bundledVersion: nil) == .identity)
    }

    /// …and landing on Engine starts the install, as Continue into it does.
    @Test(.timeLimit(.minutes(1))) func startLandingOnEngineInstalls() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/x\n")
        runner.on(tool: "c", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "c", prefix: ["auth", "status"], stdout: #"{"loggedIn": true}"#)
        let asked = Recorder<Int>()
        let newer = EngineRelease(schemaVersion: 2, version: "0.11.0", engine: .init(version: "0.11.0"), uv: .init(version: "0.12.1", sha256: [:]))
        let m = model(engineState: .managed(install, vaultBootstrapped: false), release: newer, runner: runner,
                      resolve: { _ in "/c" }, makeInstaller: { _ in asked.append(1); return nil })
        await m.start()
        #expect(m.step == .engine)
        #expect(asked.all.count == 1)
        #expect(m.lastError?.contains("Claude Code wasn't found") == true)   // the nil factory's message with a release
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

    @Test(.timeLimit(.minutes(1))) func startPrefillsIdentityFromGitWhenGitIsPresent() async {
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
    @Test(.timeLimit(.minutes(1))) func startNeverRunsGitWhenGitIsMissing() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stderr: "xcode-select: error", exit: 2)
        runner.on(tool: "git", stdout: "should not be asked\n")
        let m = model(runner: runner)
        await m.start()
        #expect(runner.calls(to: "git").isEmpty)
        #expect(m.prerequisites?.git == .missing)
        #expect(m.identity.userName.isEmpty)
        #expect(runner.calls(to: "xcode-select") == [["-p"]])   // never `--install` on its own
    }

    /// A runner whose `xcode-select -p` (inside `start()`'s check) suspends
    /// until `release` opens, after signalling `entered`.
    fileprivate func gatedStartRunner(entered: Gate, release: Gate) -> RuleBasedRunner {
        let runner = RuleBasedRunner()
        runner.on({ url, args in url.lastPathComponent == "xcode-select" && args == ["-p"] }) { _, _, _ in
            await entered.open()
            await release.wait()
            return ProcessResult(exitCode: 0, stdout: Data("/x\n".utf8), stderr: Data())
        }
        runner.on(tool: "c", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "c", prefix: ["auth", "status"], stdout: #"{"loggedIn": true}"#)
        return runner
    }

    /// Ruling 66 guard (a): Welcome's Continue stays disabled until `start()`
    /// has finished, so a click during the check is ignored.
    @Test(.timeLimit(.minutes(1))) func welcomeContinueWaitsForStart() async {
        let entered = Gate(), release = Gate()
        let m = model(runner: gatedStartRunner(entered: entered, release: release), resolve: { _ in "/c" })
        let starting = Task { await m.start() }
        await entered.wait()
        #expect(!m.didStart)
        #expect(!m.canContinue)
        await m.continueTapped()
        #expect(m.step == .welcome)
        await release.open()
        await starting.value
        #expect(m.didStart)
        #expect(m.step == .welcome)
        #expect(m.canContinue)
    }

    /// Ruling 66 guard (b): if the step changed while `start()` was
    /// suspended, `start()` leaves it alone instead of applying `initialStep`
    /// (here `.identity`).
    @Test(.timeLimit(.minutes(1))) func startDoesNotSnapBackAfterTheUserMovedOn() async {
        let entered = Gate(), release = Gate()
        let m = model(engineState: .managed(install, vaultBootstrapped: false),
                      runner: gatedStartRunner(entered: entered, release: release), resolve: { _ in "/c" })
        let starting = Task { await m.start() }
        await entered.wait()
        m.step = .prerequisites
        await release.open()
        await starting.value
        #expect(m.step == .prerequisites)
        #expect(m.didStart)
    }

    /// Control for the guard above: with no navigation, `start()` does jump.
    @Test(.timeLimit(.minutes(1))) func startJumpsToTheInitialStepWhenTheUserStayed() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/x\n")
        runner.on(tool: "c", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        let m = model(engineState: .managed(install, vaultBootstrapped: false), runner: runner, resolve: { _ in "/c" })
        await m.start()
        #expect(m.step == .identity)
    }

    /// Minor 7: the vault must be absolute or `~`-prefixed.
    @Test(.timeLimit(.minutes(1))) func welcomeRejectsARelativeVaultPath() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/x\n")
        let m = model(runner: runner)
        await m.start()
        #expect(m.canContinue)
        for bad in ["Scout", "Documents/Scout", "./Scout", "~alex/Scout", "", "  "] {
            m.vaultPath = bad
            #expect(!m.vaultPathIsValid, "\(bad)")
            #expect(!m.canContinue, "\(bad)")
        }
        for good in ["~", "~/Scout", "/Users/alex/Scout", " /Users/alex/Notes "] {
            m.vaultPath = good
            #expect(m.canContinue, "\(good)")
        }
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

    @Test(.timeLimit(.minutes(1))) func pollingRechecksUntilClaudeIsInstalledAndSignedIn() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "c", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "c", prefix: ["auth", "status"], stdout: #"{"loggedIn": true}"#)
        // Git missing too: polling must still never offer Apple's installer itself.
        runner.on(tool: "xcode-select", prefix: ["-p"], stderr: "xcode-select: error", exit: 2)
        let resolver = ScriptedResolver([nil, nil, "/c"])
        let m = model(runner: runner, resolve: { _ in resolver.next() })
        m.prerequisitePollInterval = .milliseconds(5)
        m.step = .prerequisites
        await m.pollPrerequisites()
        #expect(resolver.calls == 3)
        #expect(m.prerequisites?.auth == .signedIn)
        #expect(!m.needsPrerequisitePolling)
        #expect(m.prerequisites?.git == .missing)
        #expect(!runner.calls(to: "xcode-select").contains(["--install"]))
        #expect(runner.calls(to: "xcode-select").count == 3)
    }

    @Test(.timeLimit(.minutes(1))) func pollingDoesNothingOffThePrerequisitesStep() async {
        let resolver = ScriptedResolver([nil])
        let m = model(resolve: { _ in resolver.next() })
        m.prerequisitePollInterval = .milliseconds(5)
        m.step = .identity
        await m.pollPrerequisites()
        #expect(resolver.calls == 0)
    }

    /// A second poll (the view re-appearing) supersedes the first, so loops
    /// never stack; changing step ends the survivor.
    @Test(.timeLimit(.minutes(1))) func aNewPollSupersedesTheOldOneAndAStepChangeEndsIt() async {
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
        #expect(m.canRetry)
        #expect(m.retryTitle == "Retry")
        m.step = .identity
        #expect(!m.canRetry)              // only Engine and Vault have installs
    }

    /// C8: the app's factory returns nil with a release when `claude` can't
    /// be found — the model says so instead of blaming the build.
    @Test func installEngineWithoutClaudeSaysSo() async {
        let m = model(release: releaseFixture)
        m.prerequisites = ready
        m.step = .prerequisites
        await m.continueTapped()
        #expect(m.step == .engine)
        #expect(m.lastError?.contains("Claude Code wasn't found") == true)
        #expect(m.canRetry)
    }

    /// C8: the window keeps a flow that already changed the machine until
    /// the user taps Open Scout, even once the engine stops gating.
    @Test func holdsWindowOnlyWhileFinishing() {
        let m = model()
        for step in OnboardingViewModel.Step.allCases where step != .ready {
            m.step = step
            #expect(!m.holdsWindow, "\(step)")
        }
        m.step = .vault
        m.progress[.bootstrapVault] = InstallProgress(step: .bootstrapVault, status: .done, log: "")
        #expect(m.holdsWindow)
        m.step = .ready
        #expect(m.holdsWindow)
        m.step = .engine
        m.busy = true
        #expect(m.holdsWindow)
    }

    func engineModel(_ support: EngineInstallerTests, _ f: EngineInstallerTests.Fixture,
                     engineState: EngineState = .notInstalled) -> OnboardingViewModel {
        let m = model(engineState: engineState, layout: f.layout, release: f.release, runner: f.runner,
                      makeInstaller: { sink in support.installer(f, sink: sink) })
        m.prerequisites = Prerequisites(claude: .installed(path: f.claude, version: "2.1.259"), auth: .signedIn,
                                        git: .present(URL(fileURLWithPath: "/usr/bin/git")), uv: .missing)
        return m
    }

    /// A real install (temp home, real tar + fake install-venv.sh, scripted
    /// claude) driven through the model reaches Continue on Engine.
    @Test(.timeLimit(.minutes(1))) func continuingToEngineInstallsAndUnlocksContinue() async throws {
        let support = EngineInstallerTests()
        let f = try support.fixture()
        defer { try? FileManager.default.removeItem(at: f.layout.home) }
        let m = engineModel(support, f)
        m.step = .prerequisites
        await m.continueTapped()
        #expect(m.step == .engine)
        #expect(m.lastError == nil)
        #expect(m.canContinue)
        #expect(!m.canRetry)
        #expect(m.progress[.registerWithClaudeCode]?.status == .done)
        #expect(FileManager.default.isExecutableFile(atPath: f.layout.scoutctl(version: f.release.engine.version).path))
    }

    /// Minor 8: onboarding that started at Identity never ran the install,
    /// so Back to Engine offers "Install" instead of a dead end.
    @Test(.timeLimit(.minutes(1))) func backFromIdentityToAnUntouchedEngineStepOffersInstall() async throws {
        let support = EngineInstallerTests()
        let f = try support.fixture()
        defer { try? FileManager.default.removeItem(at: f.layout.home) }
        let m = engineModel(support, f, engineState: .managed(install, vaultBootstrapped: false))
        m.step = .identity
        m.back()
        #expect(m.step == .engine)
        #expect(!m.canContinue)
        #expect(m.canRetry)
        #expect(m.retryTitle == "Install")
        await m.retry()
        #expect(m.canContinue)
        #expect(!m.canRetry)
        #expect(m.lastError == nil)
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

    static func bootstrapJSON(vault: URL, ok: Bool) -> String {
        let action = ok ? "install" : "refused"
        let error = ok ? "null" : #""install needs --user-name""#
        return #"{"schema_version":1,"action":"\#(action)","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.10.0","error":\#(error),"doctor":{"severity":"green","errors":[],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":"p"}"#
    }

    func vaultFixture(bootstrapOK: Bool = true, scriptAuto: Bool = true) throws -> VaultFixture {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("onboarding-\(UUID().uuidString)")
        let vault = home.appendingPathComponent("Scout")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        let runner = RuleBasedRunner()
        if scriptAuto {
            runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: Self.bootstrapJSON(vault: vault, ok: bootstrapOK))
        }
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor"], stdout: #"{"severity":"green","errors":[],"warnings":[]}"#)
        return VaultFixture(home: home, layout: EngineLayout(home: home), vault: vault, runner: runner)
    }

    fileprivate func vaultModel(_ f: VaultFixture, sinks: SinkBox? = nil) -> OnboardingViewModel {
        let layout = f.layout, runner = f.runner
        let m = model(layout: layout, release: releaseFixture, runner: runner, makeInstaller: { sink in
            sinks?.append(sink)
            return EngineInstaller(layout: layout, release: releaseFixture, tarballURL: nil, runner: runner,
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
        #expect(!m.canRetry)              // so it never offers to re-run bootstrap
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
        #expect(m.canRetry)
        #expect(m.retryTitle == "Retry")
        await m.retry()
        #expect(f.runner.calls(to: "scoutctl").filter { $0.starts(with: ["bootstrap", "auto"]) }.count == 2)
    }

    /// Final review minor: a vault the engine refuses ("non-empty but not a
    /// Scout vault") is fixed on the first step, so the Vault step's error
    /// says how to get there, and Change… goes back to Welcome with the
    /// failed attempt forgotten. Nothing to change once the vault exists.
    @Test func aRefusedVaultPointsBackToTheFolderPicker() async throws {
        let f = try vaultFixture(scriptAuto: false)
        defer { try? FileManager.default.removeItem(at: f.home) }
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"refused","reason":"","dry_run":false,"vault":"\#(f.vault.path)","plugin_version":"0.10.0","error":"non-empty but not a Scout vault — pick an empty folder","doctor":null,"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#)
        let m = vaultModel(f)
        m.step = .vault
        #expect(m.canChangeVault)
        await m.createVault()
        #expect(m.lastError?.contains("non-empty but not a Scout vault") == true)
        #expect(m.lastError?.hasSuffix(OnboardingViewModel.changeVaultHint) == true)
        #expect(m.canChangeVault && m.canRetry)

        m.changeVault()
        #expect(m.step == .welcome)
        #expect(m.lastError == nil && m.progress[.bootstrapVault] == nil && m.progress[.verify] == nil)
        #expect(!m.canChangeVault)                  // only on the Vault step
        m.changeVault()                             // a no-op elsewhere
        #expect(m.step == .welcome)
    }

    @Test func aCreatedVaultCannotBeChanged() async throws {
        let f = try vaultFixture()
        defer { try? FileManager.default.removeItem(at: f.home) }
        let m = vaultModel(f)
        m.step = .vault
        await m.createVault()
        #expect(m.progress[.bootstrapVault]?.status == .done && m.lastError == nil)
        #expect(!m.canChangeVault)
        m.changeVault()
        #expect(m.step == .vault)
    }

    /// Minor 3: once a run has settled, a progress hop arriving late (here
    /// a contradicting report through that run's sink) changes nothing.
    @Test(.timeLimit(.minutes(1))) func aLateProgressHopCannotChangeTheSettledState() async throws {
        let f = try vaultFixture()
        defer { try? FileManager.default.removeItem(at: f.home) }
        let sinks = SinkBox()
        let m = vaultModel(f, sinks: sinks)
        m.step = .vault
        await m.createVault()
        #expect(m.progress[.bootstrapVault]?.status == .done)
        sinks.last?(InstallProgress(step: .bootstrapVault, status: .failed("late"), log: "late"))
        try? await Task.sleep(for: .milliseconds(50))   // let the hop reach the main actor
        #expect(m.progress[.bootstrapVault]?.status == .done)
        #expect(m.lastError == nil)
        #expect(m.canContinue)
    }

    /// Minor 3: while a second run is in flight, a hop from the first run is
    /// dropped. The first run's sink reports a failure in the middle of the
    /// second run's bootstrap, and the model never shows it.
    @Test(.timeLimit(.minutes(1))) func anOlderRunsProgressIsDroppedDuringANewRun() async throws {
        let f = try vaultFixture(scriptAuto: false)
        defer { try? FileManager.default.removeItem(at: f.home) }
        let sinks = SinkBox()
        let holder = ModelHolder()
        let autoCalls = Recorder<Int>()
        let seen = Recorder<String>()
        let okJSON = Self.bootstrapJSON(vault: f.vault, ok: true)
        f.runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.starts(with: ["bootstrap", "auto"]) }) { _, _, _ in
            autoCalls.append(1)
            if autoCalls.all.count == 2 {
                sinks.first?(InstallProgress(step: .bootstrapVault, status: .failed("stale"), log: "stale"))
                try? await Task.sleep(for: .milliseconds(50))
                let status = await MainActor.run { holder.model?.progress[.bootstrapVault]?.status }
                seen.append(String(describing: status))
            }
            return ProcessResult(exitCode: 0, stdout: Data(okJSON.utf8), stderr: Data())
        }
        let m = vaultModel(f, sinks: sinks)
        holder.model = m
        m.step = .vault
        await m.createVault()
        await m.createVault()
        #expect(seen.all.count == 1)
        #expect(seen.all.first?.contains("stale") == false)
        #expect(m.progress[.bootstrapVault]?.status == .done)
        #expect(m.lastError == nil)
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
        m.vaultPath = "/Users/alex/Notes"      // the vault chosen in onboarding, not the install's
        await m.runFirstBriefing()
        #expect(runner.calls(to: "scoutctl") == [["schedule", "list", "--json"], ["schedule", "fire-now", "morning"]])
        #expect(runner.calls.map { $0.environment["SCOUT_DATA_DIR"] } == ["/Users/alex/Notes", "/Users/alex/Notes"])
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
