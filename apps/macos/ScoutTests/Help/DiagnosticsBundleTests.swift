import Testing
import Foundation
@testable import Scout

/// App audit C11: Help ▸ Export Diagnostics… never carries vault content.
///
/// The canary test builds a synthetic vault whose note names, folder names,
/// note bodies and owner each carry a unique string, then feeds the collector
/// inputs that quote them (a leaky engine report, run errors, app log lines
/// with absolute note paths, secrets) and asserts none survives.
@Suite("Diagnostics bundle")
struct DiagnosticsBundleTests {
    static let canaries: [String: String] = [
        "noteBody": "CANARYBODYqv7",
        "noteName": "Zephyrine Canarywood",
        "noteNameNoSpace": "canarynote-kx2",
        "folder": "Project Quillfeather",
        "ownerName": "Ottoline Canarysmith",
        "ownerEmail": "ottoline.canary@example.com",
        "transcript": "CANARYTRANSCRIPTzz1",
        "token": "sk-ant-oat01-CANARYTOKENabcdefghijklmnopqrstuvwxyz0123456789",
        "githubToken": "ghp_CANARYabcdefghijklmnopqrstuvwxyz012345",
        "slackToken": "xoxb-1234-CANARYslack",
    ]
    static let canaryWords = ["Zephyrine", "Canarywood", "Quillfeather", "Ottoline", "Canarysmith"]

    struct Sandbox {
        let home: URL
        let vault: URL
    }

    static func makeVault() throws -> Sandbox {
        let fm = FileManager.default
        let home = fm.temporaryDirectory.appendingPathComponent("diag-\(UUID().uuidString)", isDirectory: true)
        let vault = home.appendingPathComponent("Scout", isDirectory: true)
        let people = vault.appendingPathComponent("knowledge-base/people", isDirectory: true)
        let project = vault.appendingPathComponent("knowledge-base/\(canaries["folder"]!)", isDirectory: true)
        for dir in [people, project, vault.appendingPathComponent(".scout-logs"), vault.appendingPathComponent(".claude/projects")] {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        try "# x\n\(canaries["noteBody"]!)\n".write(to: people.appendingPathComponent("\(canaries["noteName"]!).md"), atomically: true, encoding: .utf8)
        try "[[\(canaries["noteName"]!)]]\n".write(to: people.appendingPathComponent("\(canaries["noteNameNoSpace"]!).md"), atomically: true, encoding: .utf8)
        try "\(canaries["noteBody"]!)\n".write(to: project.appendingPathComponent("kickoff.md"), atomically: true, encoding: .utf8)
        try "\(canaries["transcript"]!)\n".write(to: vault.appendingPathComponent(".claude/projects/s.jsonl"), atomically: true, encoding: .utf8)
        try """
        user:
          name: \(canaries["ownerName"]!)
          email: \(canaries["ownerEmail"]!)
        plugin:
          version_at_last_setup: '0.18.0'
        """.write(to: vault.appendingPathComponent("scout-config.yaml"), atomically: true, encoding: .utf8)
        return Sandbox(home: home, vault: vault)
    }

    /// A runner that answers `scoutctl diagnostics --json` with a report that
    /// (wrongly) quotes vault content, to prove the app's own scrub catches it.
    struct LeakyEngineRunner: ProcessRunner {
        let vault: URL
        func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
            #expect(arguments == ["diagnostics", "--json"])
            let c = DiagnosticsBundleTests.canaries
            let report: [String: Any] = [
                "schema": 1,
                "engine": ["version": "0.18.15"],
                "doctor": [
                    "severity": "yellow",
                    "warnings": [
                        "can't read \(vault.path)/knowledge-base/people/\(c["noteName"]!).md",
                        "see [[\(c["noteName"]!)]] and \(c["noteNameNoSpace"]!).md in \(c["folder"]!)",
                        "owner \(c["ownerName"]!) <\(c["ownerEmail"]!)> token \(c["token"]!) \(c["githubToken"]!)",
                        "kept: \(vault.path)/.scout-logs/run-outcomes.jsonl",
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
        EngineInstall(root: sandbox.home.appendingPathComponent("engine"),
                      scoutctl: sandbox.home.appendingPathComponent("engine/bin/scoutctl"),
                      python: nil, version: "0.18.15", vault: sandbox.vault)
    }

    static func collector(_ sandbox: Sandbox, runner: any ProcessRunner, doctor: DoctorReport? = nil) -> DiagnosticsCollector {
        let c = canaries
        let run = Run.make(
            type: .morningBriefing,
            status: .failure,
            exitCode: 1,
            logPath: sandbox.vault.appendingPathComponent(".scout-logs/scout-2026-11-16_08-00.log")
        )
        // Built the way production builds them, so the category rule applies.
        let at = Date(timeIntervalSince1970: 1_800_000_000)
        let log = [
            DiagnosticsCollector.logLine(date: at, level: "error", category: "BoardWriter",
                message: "Commit of \(sandbox.vault.path)/knowledge-base/people/\(c["noteName"]!).md failed"),
            DiagnosticsCollector.logLine(date: at, level: "debug", category: "FileWatcher",
                message: "FSEventStreamStart failed for \(sandbox.vault.path)/knowledge-base/\(c["folder"]!)"),
            DiagnosticsCollector.logLine(date: at, level: "error", category: "KBPrivacy",
                message: "\(c["noteBody"]!) \(c["transcript"]!)"),
            DiagnosticsCollector.logLine(date: at, level: "error", category: "ConnectorHealth",
                message: "Bearer abc.def \(c["slackToken"]!) for \(c["ownerEmail"]!)"),
        ]
        return DiagnosticsCollector(
            app: AppDiagnosticsInfo(version: "0.18.15", build: "900", bundleID: "com.scout.Scout.dev",
                                    os: "Version 26.0", architecture: "arm64"),
            engineState: .managed(install(sandbox), vaultBootstrapped: true),
            doctor: doctor,
            vault: sandbox.vault,
            runner: runner,
            runs: [run],
            appLog: { log },
            installLog: nil,
            scrubber: DiagnosticsScrubber.forVault(sandbox.vault, home: sandbox.home),
            now: Date(timeIntervalSince1970: 1_800_000_000)
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
    }

    @Test func bundle_neverCarriesVaultContent() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home) }
        let bundle = await Self.collector(sandbox, runner: LeakyEngineRunner(vault: sandbox.vault)).collect()
        Self.assertNoCanary(bundle.text, home: sandbox.home)
        // Still useful: versions, structure, the run, the log lines (scrubbed), Scout's own paths.
        #expect(bundle.text.contains("Version: 0.18.15 (900)"))
        #expect(bundle.text.contains("Bundle ID: com.scout.Scout.dev"))
        #expect(bundle.text.contains("morningBriefing · failure"))
        #expect(bundle.text.contains("<vault>/.scout-logs/run-outcomes.jsonl"))
        #expect(bundle.text.contains("[BoardWriter] Commit of <vault>/<path> failed"))
        #expect(bundle.text.contains("[FileWatcher] FSEventStreamStart failed for <vault>/<path>\n"))
        #expect(bundle.text.contains("[KBPrivacy] <message withheld: this category can quote notes>"))
        #expect(bundle.text.contains("[ConnectorHealth] Bearer <redacted> xox-<redacted> for <owner>"))
        #expect(bundle.sections.map(\.title) == [
            "App", "Engine", "Engine diagnostics", "Recent runs (as the app sees them)", "App log",
        ])
    }

    @Test func olderEngine_fallsBackToDoctorStructureOnly() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home) }
        let c = Self.canaries
        let doctor = DoctorReport(
            severity: .red,
            errors: ["missing \(sandbox.vault.path)/knowledge-base/people/\(c["noteName"]!).md"],
            warnings: ["\(c["ownerName"]!)"],
            runHealth: RunHealth(severity: .red, evaluatedAt: "2026-11-16T12:00:00Z", lastOkAt: nil, findings: [
                .init(id: "runs.failing", severity: "red", since: nil, summary: "\(c["noteBody"]!)", fix: "", source: "ledger",
                      detail: c["transcript"]!, log: nil, until: nil),
            ], slots: []))
        let bundle = await Self.collector(sandbox, runner: MissingCommandRunner(), doctor: doctor).collect()
        Self.assertNoCanary(bundle.text, home: sandbox.home)
        #expect(bundle.text.contains("This engine has no `scoutctl diagnostics`"))
        #expect(bundle.text.contains("Doctor: red · 1 error(s), 1 warning(s)"))
        #expect(bundle.text.contains("red runs.failing (ledger)"))
    }

    @Test func savedText_isExactlyThePreview() async throws {
        let sandbox = try Self.makeVault()
        defer { try? FileManager.default.removeItem(at: sandbox.home) }
        let bundle = await Self.collector(sandbox, runner: MissingCommandRunner()).collect()
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
    let vault = URL(fileURLWithPath: "/Users/someone/Scout")
    let home = URL(fileURLWithPath: "/Users/someone")

    var scrubber: DiagnosticsScrubber { DiagnosticsScrubber(vault: vault, home: home, names: ["run": "<name>", "Acme Deal": "<name>"]) }

    @Test(arguments: [
        ("/Users/someone/Scout/.scout-logs/scout-2026-11-16_08-00.log", "<vault>/.scout-logs/scout-2026-11-16_08-00.log"),
        ("/Users/someone/Scout/SKILL.md is stale", "<vault>/SKILL.md is stale"),
        ("/Users/someone/Scout/knowledge-base/people/Alex.md", "<vault>/<path>"),
        ("see notes/alex.md", "see <note>.md"),
        ("linked from [[Some Note]]", "linked from [[<note>]]"),
        ("/Users/someone/Library/Caches", "~/Library/Caches"),
        ("ask alex@example.com", "ask <email>"),
        ("pushed to https://example.com/private/repo", "pushed to <url>"),
        ("see https://github.com/Raven-Scout/Scout/issues/1", "see https://github.com/Raven-Scout/Scout/issues/1"),
        ("key sk-ant-api03-abcdef", "key <redacted>"),
        ("Bearer abc.def", "Bearer <redacted>"),
        ("id 0123456789abcdef0123456789abcdef", "id <redacted>"),
        ("runs failed: run Acme Deal", "runs failed: <name> <name>"),
        ("rerun", "rerun"),
    ])
    func rules(raw: String, expected: String) {
        #expect(scrubber.scrub(raw) == expected)
    }

    @Test func longLines_areCut() {
        let out = scrubber.scrubLines(String(repeating: "word ", count: 200))
        #expect(out.count == DiagnosticsScrubber.maxLine + 1)
        #expect(out.hasSuffix("…"))
    }

    @Test func scoutFolderNames_areNeverHidden() {
        let s = DiagnosticsScrubber(vault: nil, home: nil, names: ["knowledge-base": "<name>", "action-items": "<name>"])
        #expect(s.scrub("knowledge-base/ontology/parser.py") == "knowledge-base/ontology/parser.py")
    }
}
