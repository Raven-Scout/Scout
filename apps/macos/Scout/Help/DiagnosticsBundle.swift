import Foundation
import OSLog

/// The text bundle Help ▸ Export Diagnostics… saves for a bug report (app
/// audit C11). The owner sees `text` — exactly what will be saved — before
/// anything is written.
///
/// What it holds: the app's and engine's versions, `scoutctl diagnostics`
/// (doctor, run health, sandbox and runtime status, recent run outcomes, all
/// redacted by the engine), the app's view of recent runs, and the app's own
/// log lines from this launch. What it never holds: note text, note or folder
/// names, vault paths outside Scout's own folders, secrets, tokens,
/// transcripts or run logs. Every line passes through `DiagnosticsScrubber`.
nonisolated struct DiagnosticsBundle: Equatable, Sendable {
    struct Section: Equatable, Sendable, Identifiable {
        var id: String { title }
        let title: String
        /// One line, shown in the preview's list of what's included.
        let about: String
        let body: String
    }

    let createdAt: Date
    let sections: [Section]

    static let header = """
    Scout diagnostics
    Attach this file to a bug report. It holds versions, health checks, recent run outcomes and the app's own log.
    It holds none of your notes: no note text, note or folder names, secrets, tokens, transcripts or run logs.
    """

    var text: String {
        var out = Self.header + "\n" + "Created: \(Self.stamp(createdAt))\n"
        for section in sections {
            out += "\n== \(section.title) ==\n\(section.body)\n"
        }
        return out
    }

    var suggestedFileName: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd-HHmm"
        return "Scout-diagnostics-\(f.string(from: createdAt)).txt"
    }

    static func stamp(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: date)
    }
}

/// The app's own facts, read from the bundle and the OS.
nonisolated struct AppDiagnosticsInfo: Equatable, Sendable {
    let version: String
    let build: String
    let bundleID: String
    let os: String
    let architecture: String

    static func current(bundle: Bundle = .main) -> AppDiagnosticsInfo {
        let info = bundle.infoDictionary ?? [:]
        #if arch(arm64)
        let arch = "arm64"
        #else
        let arch = "x86_64"
        #endif
        return AppDiagnosticsInfo(
            version: info["CFBundleShortVersionString"] as? String ?? "?",
            build: info["CFBundleVersion"] as? String ?? "?",
            bundleID: bundle.bundleIdentifier ?? "?",
            os: ProcessInfo.processInfo.operatingSystemVersionString,
            architecture: arch
        )
    }
}

/// Gathers a `DiagnosticsBundle`. Everything it reads is injected, so tests
/// drive it with a synthetic vault and a scripted runner. Nonisolated: the
/// log store, the install log and the scrubbing run off the main thread.
nonisolated struct DiagnosticsCollector: Sendable {
    var app: AppDiagnosticsInfo
    var engineState: EngineState
    var doctor: DoctorReport?
    var vault: URL
    var runner: any ProcessRunner
    var environment: [String: String] = [:]
    var runs: [Run]
    /// The app's own log lines, oldest first. Production: `appLogLines()`.
    var appLog: @Sendable () -> [String]
    /// The app-managed engine's install log, when there is one.
    var installLog: URL?
    var scrubber: DiagnosticsScrubber
    var now: Date = Date()

    static let engineSectionTitle = "Engine diagnostics"
    static let recentRunCount = 20
    static let appLogLimit = 200
    static let installLogLimit = 60

    func collect() async -> DiagnosticsBundle {
        let engineText = await engineDiagnostics()
        let logText = await Task.detached { [self] in logSection() }.value
        // Everything but the engine's JSON is plain text, scrubbed line by line.
        let sections = [
            DiagnosticsBundle.Section(title: "App", about: "Scout.app's version, build and this Mac's macOS version.",
                                      body: appSection()),
            DiagnosticsBundle.Section(title: "Engine", about: "Which engine the app found, and its version.",
                                      body: engineSection()),
            DiagnosticsBundle.Section(title: Self.engineSectionTitle,
                                      about: "scoutctl diagnostics: doctor, run health, sandbox and runtime status, recent run outcomes.",
                                      body: engineText),
            DiagnosticsBundle.Section(title: "Recent runs (as the app sees them)",
                                      about: "The last \(Self.recentRunCount) runs: type, status, times and exit codes.",
                                      body: runsSection()),
            DiagnosticsBundle.Section(title: "App log",
                                      about: "Scout.app's own log lines from this launch.",
                                      body: logText),
        ]
        return DiagnosticsBundle(
            createdAt: now,
            sections: sections.map { section in
                let body = section.title == Self.engineSectionTitle ? section.body : scrubber.scrubLines(section.body)
                return .init(title: section.title, about: section.about, body: body)
            }
        )
    }

    func appSection() -> String {
        """
        Version: \(app.version) (\(app.build))
        Bundle ID: \(app.bundleID)
        macOS: \(app.os) · \(app.architecture)
        """
    }

    func engineSection() -> String {
        let source: String = {
            switch engineState {
            case .notInstalled: return "not installed"
            case .managed(_, let bootstrapped): return "managed by Scout.app" + (bootstrapped ? "" : " (vault not set up)")
            case .external(_, let src): return "external (\(Self.describe(src)))"
            case .broken(_, let reason): return "broken: \(reason)"
            }
        }()
        var lines = ["Engine: \(source)", "Version: \(engineState.install?.version ?? "unknown")"]
        if let root = engineState.install?.root.path { lines.append("Location: \(root)") }
        if let doctor {
            lines.append("Last doctor: \(doctor.severity.rawValue)")
            if let rh = doctor.runHealth { lines.append("Run health: \(rh.severity.rawValue)") }
        }
        return lines.joined(separator: "\n")
    }

    static func describe(_ source: ExternalSource) -> String {
        switch source {
        case .devCheckout: return "developer checkout"
        case .installSh: return "install.sh"
        case .claudeCode: return "Claude Code plugin"
        case .marketplaceCache: return "plugin marketplace cache"
        case .shim: return "scoutctl shim"
        case .unknown(let s): return s
        }
    }

    /// `scoutctl diagnostics --json`, redacted by the engine and then scrubbed
    /// again here value by value (`DiagnosticsScrubber.scrubJSON`). An engine
    /// that predates the command gets the doctor's structural fields only
    /// (severity and finding ids), never its free text.
    func engineDiagnostics() async -> String {
        guard let scoutctl = engineState.scoutctl else { return "No engine to ask." }
        if let result = try? await runner.run(executable: scoutctl, arguments: ["diagnostics", "--json"],
                                              environment: environment, workingDirectory: nil),
           result.exitCode == 0,
           let text = scrubber.scrubJSON(result.stdout) {
            return text
        }
        var lines = ["This engine has no `scoutctl diagnostics` (engines after 0.18.14 have it); showing the doctor's summary."]
        if let doctor {
            lines.append("Doctor: \(doctor.severity.rawValue) · \(doctor.errors.count) error(s), \(doctor.warnings.count) warning(s)")
            if let rh = doctor.runHealth {
                lines.append("Run health: \(rh.severity.rawValue)")
                for f in rh.findings { lines.append("  \(f.severity) \(f.id) (\(f.source))") }
            }
        } else {
            lines.append("Doctor: not run yet")
        }
        return scrubber.scrubLines(lines.joined(separator: "\n"))
    }

    func runsSection() -> String {
        let recent = runs.sorted { $0.startedAt > $1.startedAt }.prefix(Self.recentRunCount)
        guard !recent.isEmpty else { return "No runs found." }
        return recent.map { run in
            var parts = [DiagnosticsBundle.stamp(run.startedAt), run.type.rawValue, run.status.rawValue]
            if let end = run.endedAt { parts.append("\(Int(end.timeIntervalSince(run.startedAt)))s") }
            if let code = run.exitCode { parts.append("exit \(code)") }
            if let model = run.model { parts.append("model \(model)") }
            if !run.errorsDetected.isEmpty { parts.append("\(run.errorsDetected.count) error line(s)") }
            return parts.joined(separator: " · ")
        }.joined(separator: "\n")
    }

    func logSection() -> String {
        var lines = Array(appLog().suffix(Self.appLogLimit))
        if lines.isEmpty { lines = ["No log lines from this launch."] }
        if let installLog, let text = try? String(contentsOf: installLog, encoding: .utf8) {
            lines.append("")
            lines.append("-- engine install log (last \(Self.installLogLimit) steps; messages withheld) --")
            lines.append(contentsOf: text.split(separator: "\n").compactMap { Self.installLogLine(String($0)) }
                .suffix(Self.installLogLimit))
        }
        return lines.joined(separator: "\n")
    }

    /// An install-log line (`<date> <step> <status> <free text>`, `EngineInstaller.report`) cut to
    /// its date, step and status keyword. The free text, and a status's own message
    /// (`failed("…")`), can quote anything a bootstrap printed, so they never leave. nil for
    /// a line that isn't one.
    static func installLogLine(_ line: String) -> String? {
        let parts = line.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: true)
        guard parts.count >= 3, ISO8601DateFormatter().date(from: String(parts[0])) != nil,
              InstallStep(rawValue: String(parts[1])) != nil else { return nil }
        let keyword = parts[2].prefix { $0.isLetter }
        guard ["pending", "running", "done", "skipped", "failed"].contains(String(keyword)) else { return nil }
        return "\(parts[0]) \(parts[1]) \(keyword)"
    }

    /// Log categories known to log only structure (paths, connector ids,
    /// errors). Any other category, including one added later, shows its
    /// timestamp, level and category with the message withheld, because
    /// it might quote a note (KBPrivacy classifies note content).
    static let structuralLogCategories: Set<String> = [
        "FileWatcher", "ConnectorHealth", "DisplayProfile", "BoardWriter",
    ]

    /// One log line, with the message withheld unless its category is structural.
    static func logLine(date: Date, level: String, category: String, message: String) -> String {
        let body = structuralLogCategories.contains(category) ? message : "<message withheld: this category can quote notes>"
        return "\(DiagnosticsBundle.stamp(date)) \(level) [\(category)] \(body)"
    }

    /// This process's own unified-log entries for Scout's subsystem.
    static func appLogLines(subsystem: String = "com.scout.Scout") -> [String] {
        guard let store = try? OSLogStore(scope: .currentProcessIdentifier) else { return [] }
        let start = store.position(timeIntervalSinceLatestBoot: 0)
        let predicate = NSPredicate(format: "subsystem == %@", subsystem)
        guard let entries = try? store.getEntries(at: start, matching: predicate) else { return [] }
        return entries.compactMap { $0 as? OSLogEntryLog }.map { entry in
            let level: String = switch entry.level {
            case .error: "error"
            case .fault: "fault"
            case .notice: "notice"
            case .info: "info"
            case .debug: "debug"
            default: "log"
            }
            return logLine(date: entry.date, level: level, category: entry.category, message: entry.composedMessage)
        }
    }
}
