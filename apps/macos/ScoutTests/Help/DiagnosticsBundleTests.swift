import Testing
import Foundation
@testable import Scout

/// App audit C11: Help ▸ Export Diagnostics… never carries vault content.
///
/// The canary tests build a synthetic vault where every kind of owner content
/// carries a unique string: note names, folders (a dot-folder included), note
/// bodies, the owner (block and flow YAML), delegates and custom connectors.
/// They then feed the collector inputs that quote them: an engine report
/// that leaks on purpose, an install log, the Engine section's location and
/// broken reason, app log lines, paths with spaces that don't exist, file URLs
/// and secrets. Finally they assert none of the strings survives.
@Suite("Diagnostics bundle")
struct DiagnosticsBundleTests {
    static let canaries: [String: String] = [
        "noteBody": "CANARYBODYqv7",
        "noteName": "Zephyrine Canarywood",
        "noteNameNoSpace": "canarynote-kx2",
        "folder": "Project Quillfeather",
        "dotFolderNote": "Canarytrash Merger",
        "ownerName": "Ottoline Canarysmith",
        "ownerEmail": "ottoline.canary@example.com",
        "delegate": "canarydelegate",
        "customConnector": "canaryconnector",
        "transcript": "CANARYTRANSCRIPTzz1",
        "token": "sk-ant-oat01-CANARYTOKENabcdefghijklmnopqrstuvwxyz0123456789",
        "githubToken": "ghp_CANARYabcdefghijklmnopqrstuvwxyz012345",
        "slackToken": "xoxb-1234-CANARYslack",
        "missingFolder": "Old Canaryproject",
        "missingFile": "Kickoff Canarynotes",
        "homeDocument": "Acme Canarycontract",
        "engineFolder": "Canaryengine Folder",
        "brokenReason": "Canarybroken Thing",
        "installLog": "CANARYINSTALLLOGq2",
        "queryToken": "canaryquerytoken123",
    ]
    static let canaryWords = [
        "Zephyrine", "Canarywood", "Quillfeather", "Canarytrash", "Ottoline", "Canarysmith",
        "Canaryproject", "Canarynotes", "Canarycontract", "Canaryengine", "Canarybroken",
    ]

    struct Sandbox {
        let home: URL
        let vault: URL
    }

    static func write(_ text: String, _ url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    static func makeVault(flowStyleOwner: Bool = false) throws -> Sandbox {
        let c = canaries
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("diag-\(UUID().uuidString)/canaryhome-user", isDirectory: true)
        let vault = home.appendingPathComponent("Scout", isDirectory: true)
        try write("# x\n\(c["noteBody"]!)\n", vault.appendingPathComponent("knowledge-base/people/\(c["noteName"]!).md"))
        try write("[[\(c["noteName"]!)]]\n", vault.appendingPathComponent("knowledge-base/people/\(c["noteNameNoSpace"]!).md"))
        try write("\(c["noteBody"]!)\n", vault.appendingPathComponent("knowledge-base/\(c["folder"]!)/kickoff.md"))
        try write("\(c["noteBody"]!)\n", vault.appendingPathComponent(".trash/\(c["dotFolderNote"]!).md"))
        try write("\(c["transcript"]!)\n", vault.appendingPathComponent(".claude/projects/s.jsonl"))
        try write("", vault.appendingPathComponent(".scout-logs/run-outcomes.jsonl"))
        let owner = flowStyleOwner
            ? "user: {name: \(c["ownerName"]!), email: \(c["ownerEmail"]!)}\n"
            : "user:\n  name: \"\(c["ownerName"]!)\"  # me\n  email: \(c["ownerEmail"]!)\n"
        try write(owner + "plugin:\n  version_at_last_setup: '0.18.0'\n", vault.appendingPathComponent("scout-config.yaml"))
        try write("schema_version: 1\nconnectors:\n  \(c["customConnector"]!):\n    bogus: true\n",
                  vault.appendingPathComponent("connectors.custom.yaml"))
        try write("{\"links\":{\"\(c["delegate"]!)\":{}}}", vault.appendingPathComponent(".scout-state/delegate-mirrors.json"))
        return Sandbox(home: home, vault: vault)
    }

    /// Answers `scoutctl diagnostics --json` with a report that (wrongly) quotes
    /// vault content, to prove the app's own scrub catches what the engine misses.
    struct LeakyEngineRunner: ProcessRunner {
        let sandbox: Sandbox
        func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
            #expect(arguments == ["diagnostics", "--json"])
            let c = DiagnosticsBundleTests.canaries
            let v = sandbox.vault.path, h = sandbox.home.path
            let encodedFolder = c["missingFolder"]!.replacingOccurrences(of: " ", with: "%20")
            let report: [String: Any] = [
                "schema": 1,
                "engine": ["version": "0.18.15"],
                "doctor": [
                    "severity": "yellow",
                    "warnings": [
                        "can't read \(v)/knowledge-base/people/\(c["noteName"]!).md",
                        "see [[\(c["noteName"]!)]] and \(c["noteNameNoSpace"]!).md in \(c["folder"]!)",
                        "owner \(c["ownerName"]!) <\(c["ownerEmail"]!)> token \(c["token"]!) \(c["githubToken"]!)",
                        "delegate \(c["delegate"]!) via \(c["customConnector"]!) failed",
                        "trash \(v)/.trash/\(c["dotFolderNote"]!).md",
                        "missing \(v)/\(c["missingFolder"]!)/\(c["missingFile"]!).txt",
                        "home \(h)/Documents/\(c["homeDocument"]!).pdf",
                        "file file://\(v)/\(encodedFolder)/x.txt",
                        "url https://example.com/x?access_token=\(c["queryToken"]!)",
                        "user \(NSUserName())",
                        "kept: \(v)/.scout-logs/run-outcomes.jsonl",
                    ],
                ],
            ]
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            return ProcessResult(exitCode: 0, stdout: data, stderr: Data())
        }
    }

    struct MissingCommandRunner: ProcessRunner {
        func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
            ProcessResult(exitCode: 2, stdout: Data(), stderr: Data("No such command 'diagnostics'.".utf8))
        }
    }

    static func install(_ sandbox: Sandbox) -> EngineInstall {
        let root = sandbox.home.appendingPathComponent(canaries["engineFolder"]!)
        return EngineInstall(root: root, scoutctl: root.appendingPathComponent("bin/scoutctl"),
                             python: nil, version: "0.18.15", vault: sandbox.vault)
    }

    static func collector(_ sandbox: Sandbox, runner: any ProcessRunner, state: EngineState? = nil,
                          doctor: DoctorReport? = nil) throws -> DiagnosticsCollector {
        let c = canaries
        let run = Run.make(type: .morningBriefing, status: .failure, exitCode: 1,
                           logPath: sandbox.vault.appendingPathComponent(".scout-logs/scout-2026-11-16_08-00.log"))
        let at = Date(timeIntervalSince1970: 1_800_000_000)
        let v = sandbox.vault.path
        // Built the way production builds them, so the category rule applies.
        let log = [
            DiagnosticsCollector.logLine(date: at, level: "error", category: "BoardWriter",
                message: "Commit of \(v)/knowledge-base/people/\(c["noteName"]!).md failed"),
            DiagnosticsCollector.logLine(date: at, level: "debug", category: "FileWatcher",
                message: "FSEventStreamStart failed for \(v)/knowledge-base/\(c["folder"]!)"),
            DiagnosticsCollector.logLine(date: at, level: "error", category: "KBPrivacy",
                message: "\(c["noteBody"]!) \(c["transcript"]!)"),
            DiagnosticsCollector.logLine(date: at, level: "error", category: "ConnectorHealth",
                message: "Bearer abc.def \(c["slackToken"]!) for \(c["ownerEmail"]!) and \(c["customConnector"]!)"),
            DiagnosticsCollector.logLine(date: at, level: "error", category: "DisplayProfile",
                message: "couldn't write \(v)/\(c["missingFolder"]!)/\(c["missingFile"]!).yaml"),
        ]
        let installLog = sandbox.home.appendingPathComponent("install.log")
        try write("""
        2026-11-16T08:00:00Z buildVenv done \(c["installLog"]!) \(v)/\(c["missingFolder"]!)/x
        2026-11-16T08:00:01Z bootstrapVault failed("doctor: \(c["ownerName"]!) \(c["installLog"]!)") more
        not a step line \(c["installLog"]!)

        """, installLog)
        return DiagnosticsCollector(
            app: AppDiagnosticsInfo(version: "0.18.15", build: "900", bundleID: "com.scout.Scout.dev",
                                    os: "Version 26.0", architecture: "arm64"),
            engineState: state ?? .managed(install(sandbox), vaultBootstrapped: true),
            doctor: doctor,
            vault: sandbox.vault,
            runner: runner,
            runs: [run],
            appLog: { log },
            installLog: installLog,
            scrubber: DiagnosticsScrubber.forVault(sandbox.vault, home: sandbox.home),
            now: at
        )
    }

    static func assertNoCanary(_ text: String, home: URL) {
        for (kind, canary) in canaries {
            #expect(!text.contains(canary), "\(kind) canary leaked")
        }
        for word in canaryWords {
            #expect(!text.contains(word), "\(word) leaked")
        }
        #expect(!text.contains(home.path), "home path leaked")
        #expect(!text.contains(NSUserName()), "login name leaked")
    }

    @Test func bundle_neverCarriesVaultContent() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home.deletingLastPathComponent()) }
        let bundle = try await Self.collector(sandbox, runner: LeakyEngineRunner(sandbox: sandbox)).collect()
        Self.assertNoCanary(bundle.text, home: sandbox.home)
        // Still useful: versions, structure, the run, the log lines (scrubbed), Scout's own paths.
        #expect(bundle.text.contains("Version: 0.18.15 (900)"))
        #expect(bundle.text.contains("Bundle ID: com.scout.Scout.dev"))
        #expect(bundle.text.contains("Location: ~/<path>"))
        #expect(bundle.text.contains("morningBriefing · failure"))
        #expect(bundle.text.contains("<vault>/.scout-logs/run-outcomes.jsonl"))
        #expect(bundle.text.contains("[BoardWriter] Commit of <vault>/<path>"))
        #expect(bundle.text.contains("[KBPrivacy] <message withheld: this category can quote notes>"))
        #expect(bundle.text.contains("2026-11-16T08:00:00Z buildVenv done\n2026-11-16T08:00:01Z bootstrapVault failed\n"))
        #expect(bundle.sections.map(\.title) == [
            "App", "Engine", "Engine diagnostics", "Recent runs (as the app sees them)", "App log",
        ])
    }

    @Test func flowStyleOwner_isHiddenToo() async throws {
        let sandbox = try Self.makeVault(flowStyleOwner: true)
        defer { try? FileManager.default.removeItem(at: sandbox.home.deletingLastPathComponent()) }
        let bundle = try await Self.collector(sandbox, runner: LeakyEngineRunner(sandbox: sandbox)).collect()
        Self.assertNoCanary(bundle.text, home: sandbox.home)
    }

    @Test func brokenEngine_reasonAndLocationAreScrubbed() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home.deletingLastPathComponent()) }
        let c = Self.canaries
        let reason = "pointer names \(sandbox.vault.path)/\(c["missingFolder"]!)/scoutctl for \(c["ownerName"]!) '\(c["brokenReason"]!)'"
        let bundle = try await Self.collector(sandbox, runner: MissingCommandRunner(),
                                              state: .broken(Self.install(sandbox), reason: reason)).collect()
        Self.assertNoCanary(bundle.text, home: sandbox.home)
        #expect(bundle.text.contains("Engine: broken: pointer names <vault>/<path>"))
    }

    @Test func olderEngine_fallsBackToDoctorStructureOnly() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home.deletingLastPathComponent()) }
        let c = Self.canaries
        let doctor = DoctorReport(
            severity: .red,
            errors: ["missing \(sandbox.vault.path)/knowledge-base/people/\(c["noteName"]!).md"],
            warnings: ["\(c["ownerName"]!)"],
            runHealth: RunHealth(severity: .red, evaluatedAt: "2026-11-16T12:00:00Z", lastOkAt: nil, findings: [
                .init(id: "runs.failing", severity: "red", since: nil, summary: "\(c["noteBody"]!)", fix: "", source: "ledger",
                      detail: c["transcript"]!, log: nil, until: nil),
            ], slots: []))
        let bundle = try await Self.collector(sandbox, runner: MissingCommandRunner(), doctor: doctor).collect()
        Self.assertNoCanary(bundle.text, home: sandbox.home)
        #expect(bundle.text.contains("This engine has no `scoutctl diagnostics`"))
        #expect(bundle.text.contains("Doctor: red · 1 error(s), 1 warning(s)"))
        #expect(bundle.text.contains("red runs.failing (ledger)"))
    }

    @Test func savedText_isExactlyThePreview() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home.deletingLastPathComponent()) }
        let bundle = try await Self.collector(sandbox, runner: MissingCommandRunner()).collect()
        #expect(bundle.text.hasPrefix(DiagnosticsBundle.header))
        for section in bundle.sections {
            #expect(bundle.text.contains("== \(section.title) ==\n\(section.body)\n"))
        }
        #expect(bundle.suggestedFileName.hasPrefix("Scout-diagnostics-"))
        #expect(bundle.suggestedFileName.hasSuffix(".txt"))
    }
}

@Suite("Diagnostics scrubber")
struct DiagnosticsScrubberTests {
    static let vault = "/Users/someone/Scout"
    static let home = "/Users/someone"

    var scrubber: DiagnosticsScrubber {
        var b = DiagnosticsScrubber.Builder()
        b.addPath(Self.vault, "<vault>")
        b.addPath(Self.home, "~")
        b.add("Acme Deal", "<name>")
        b.add("notes", "<name>")
        b.add("Riverbend", "<name>")
        return b.build()
    }

    // Keep in step with plugin/engine/tests/unit/test_diagnostics.py::test_scrub_rules.
    @Test(arguments: [
        ("\(vault)/.scout-logs/scout-2026-11-16_08-00.log", "<vault>/.scout-logs/scout-2026-11-16_08-00.log"),
        ("kept \(vault)/.scout-logs/a.log and more", "kept <vault>/.scout-logs/a.log and more"),
        ("\(vault)/SKILL.md is stale", "<vault>/SKILL.md is stale"),
        ("\(vault)/knowledge-base/ontology/parser.py", "<vault>/knowledge-base/ontology/parser.py"),
        ("\(vault)/knowledge-base/people/Alex.md", "<vault>/<path>"),
        ("open \(vault)/Old Project/Kickoff Notes.txt", "open <vault>/<path>"),
        ("'\(vault)/Old Project/x.txt' failed", "'<vault>/<path>' failed"),
        ("\(vault)/.trash/Secret Merger.md", "<vault>/<path>"),
        ("\(vault)/.claude/projects/-Users-jane-Vault/a.jsonl", "<vault>/<path>"),
        ("/users/someone/scout/Old Project/x", "<vault>/<path>"),
        ("see notes/alex.md", "see <note>.md"),
        ("\(vault)/SKILL.md", "<vault>/SKILL.md"),
        ("linked from [[Some Note]]", "linked from [[<note>]]"),
        ("note 'Board Meeting Prep' has no title", "note '<quoted>' has no title"),
        ("it's Alex's", "it's Alex's"),
        ("\(home)/Library/Caches/com.scout.Scout/x.json", "~/Library/Caches/com.scout.Scout/<path>"),
        ("\(home)/.local/share/scout/engine/current", "~/.local/share/scout/engine/<path>"),
        ("\(home)/Library/Application Support/Scout/vaults/Acme Plan/state", "~/Library/Application Support/Scout/vaults/<path>"),
        ("\(vault)/.git/index.lock: File exists", "<vault>/.git/index.lock: File exists"),
        ("\(vault)/.git/refs/heads/acme-merger-notes is locked by Jane Roe", "<vault>/<path>"),
        ("/Volumes/Work Drive/Secret Plans/Q3 Board.pdf could not be read", "/Volumes/<path>"),
        ("/private/tmp/Acme Board Deck.key", "/private/<path>"),
        ("/tmp/Acme Board Deck.key", "/tmp/<path>"),
        ("/usr/bin/git failed", "/usr/bin/git failed"),
        ("invalid weekday 'funday'; must be one of ['fri', 'mon']", "invalid weekday '<quoted>'; must be one of ['fri', 'mon']"),
        ("\(home)/Library/Caches/Other App/x.json", "~/<path>"),
        ("\(home)/Documents/Acme Contract.pdf", "~/<path>"),
        ("~/My Vault/Old Project/plan.txt", "~/<path>"),
        ("/Users/jane/My Vault/x.txt", "/Users/<user>/<path>"),
        ("/Users/jane/.local/bin/claude", "/Users/<user>/.local/bin/claude"),
        ("-Users-jane-Vault", "-Users-<user>-Vault"),
        ("file:///Users/jane/My%20Vault/x.txt", "/Users/<user>/<path>"),
        ("\(vault)\\/Old Project\\/x.txt", "<vault>/<path>"),
        ("ask alex@example.com", "ask <email>"),
        ("pushed to https://example.com/private/repo", "pushed to <url>"),
        ("see https://github.com/Raven-Scout/Scout/issues/1", "see https://github.com/Raven-Scout/Scout/issues/1"),
        ("https://github.com/Raven-Scout/Scout/issues?access_token=abc", "https://github.com/Raven-Scout/Scout/issues"),
        ("push to git@github.com:jane/acme-notes.git failed", "push to <email>:<repo> failed"),
        ("key sk-ant-api03-abcdef", "key <redacted>"),
        ("api_key=AbC123xyz789", "api_key=<redacted>"),
        ("Bearer abc.def", "Bearer <redacted>"),
        ("id 0123456789abcdef0123456789abcdef", "id <redacted>"),
        ("runs failed for Acme Deal and Riverbend", "runs failed for <name> and <name>"),
        ("release notes", "release notes"),
        ("rerun", "rerun"),
    ])
    func rules(raw: String, expected: String) {
        #expect(scrubber.scrub(raw) == expected)
    }

    @Test func scrub_isIdempotent() {
        let once = scrubber.scrub("open \(Self.vault)/Old Project/x.txt for Acme Deal at \(Self.home)/Documents/x")
        #expect(scrubber.scrub(once) == once)
    }

    @Test func names_matchInAnyUnicodeForm() {
        var b = DiagnosticsScrubber.Builder()
        b.add("Zoë Quill", "<name>")
        #expect(b.build().scrub("linked " + "Zoë Quill".decomposedStringWithCanonicalMapping) == "linked <name>")
    }

    @Test func longLines_areCut() {
        let out = scrubber.scrubLines(String(repeating: "word ", count: 200))
        #expect(out.count == DiagnosticsScrubber.maxLine + 1)
        #expect(out.hasSuffix("…"))
    }

    @Test func scoutFolderNames_areNeverHidden() {
        var b = DiagnosticsScrubber.Builder()
        b.add("knowledge-base", "<name>", anywhere: true)
        b.add("action-items", "<name>", anywhere: true)
        #expect(b.build().scrub("knowledge-base/ontology/parser.py") == "knowledge-base/ontology/parser.py")
    }

    @Test func json_isScrubbedValueByValue() throws {
        let doc = try JSONSerialization.data(withJSONObject: [
            "reason": "\(Self.vault)/Old Project/x", "schema": 3, "warnings": ["Acme Deal"],
        ])
        let out = try #require(scrubber.scrubJSON(doc))
        #expect(out.contains("\"reason\" : \"<vault>/<path>\""))
        #expect(out.contains("\"<name>\""))
        #expect(out.contains("\"schema\" : 3"))
    }

    @Test func peopleAndNonASCIINames_areHiddenAnywhere() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("Scout")
        defer { try? FileManager.default.removeItem(at: vault.deletingLastPathComponent()) }
        for name in ["Priya", "王小明", "Zoë"] {
            try DiagnosticsBundleTests.write("x", vault.appendingPathComponent("knowledge-base/people/\(name).md"))
        }
        try FileManager.default.createDirectory(at: vault.appendingPathComponent("knowledge-base/Acme"), withIntermediateDirectories: true)
        let s = DiagnosticsScrubber.forVault(vault, home: vault.deletingLastPathComponent())
        #expect(s.scrub("note Priya changed") == "note <name> changed")
        #expect(s.scrub("note 王小明 changed") == "note <name> changed")
        #expect(s.scrub("王小明的笔记") == "<name>的笔记")
        #expect(s.scrub("ask Zoë") == "ask <name>")
        #expect(s.scrub("folder Acme changed") == "folder Acme changed")
    }

    @Test func json_scrubsUnknownKeys_keepsAllowlistedValues() throws {
        var b = DiagnosticsScrubber.Builder()
        b.add("Zephyr Acme Renewal", "<name>")
        let s = b.build()
        let doc = try JSONSerialization.data(withJSONObject: [
            "doctor": ["Zephyr Acme Renewal": "x", "warnings": ["Zephyr Acme Renewal"]],
            "recent_runs": [["failure_class": "oauth_expired", "slot": "custom-1", "mode": "Zephyr Acme Renewal"]],
        ])
        let out = try #require(s.scrubJSON(doc))
        #expect(!out.contains("Zephyr"))
        #expect(out.contains("\"<key-1>\" : \"x\""))
        #expect(out.contains("\"failure_class\" : \"oauth_expired\""))
        #expect(out.contains("\"slot\" : \"custom-1\""))
        #expect(out.contains("\"mode\" : \"<name>\""))
    }

    @Test func identity_readsFlowStyleAndComments() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        try DiagnosticsBundleTests.write("user: {name: Ann Lee, email: ann@example.org}\n", dir.appendingPathComponent("scout-config.yaml"))
        #expect(DiagnosticsScrubber.ownerIdentity(dir).name == "Ann Lee")
        #expect(DiagnosticsScrubber.ownerIdentity(dir).email == "ann@example.org")
        try DiagnosticsBundleTests.write("user:\n  name: \"Ann Lee\"  # me\n  email: ann@example.org # work\n",
                                         dir.appendingPathComponent("scout-config.yaml"))
        #expect(DiagnosticsScrubber.ownerIdentity(dir).name == "Ann Lee")
        #expect(DiagnosticsScrubber.ownerIdentity(dir).email == "ann@example.org")
    }
}
