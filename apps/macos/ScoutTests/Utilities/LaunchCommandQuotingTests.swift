import Foundation
import Testing
@testable import Scout

/// The terminal launch commands quote the folder so a shell takes it
/// literally. They used to double-quote it, where `$(…)`, backticks and
/// `$VAR` still run: a folder named `proj$(…)` ran that command on Resume.
/// Each test builds a real folder with a hostile name under a scratch
/// directory, runs the built command through `/bin/sh` (or `bash` for the
/// Ghostty script) with `/bin/pwd` standing in for `claude`, and checks the
/// shell landed in exactly that folder and ran nothing it named.
@Suite("Launch command quoting", .serialized)
struct LaunchCommandQuotingTests {
    /// `$(…)`, backticks, `$HOME`, a single quote, a double quote and a
    /// trailing backslash, all in one folder name.
    static let hostileName = #"proj$(touch pwned-subst)`touch pwned-tick`$HOME'q"d\"#

    private struct Scratch {
        let root: URL
        let folder: URL
    }

    private func makeScratch() throws -> Scratch {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("launch-quoting-\(UUID().uuidString)", isDirectory: true)
        let folder = root.appendingPathComponent(Self.hostileName, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return Scratch(root: root, folder: folder)
    }

    /// Runs `arguments` with `root` as the working directory and as `HOME`,
    /// so anything the name smuggled in would land in `root`.
    private func run(_ executable: String, _ arguments: [String], in root: URL) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = root
        process.environment = ["PATH": "/usr/bin:/bin", "TERM": "xterm-256color", "HOME": root.path]
        let out = Pipe()
        process.standardOutput = out
        process.standardError = Pipe()
        try process.run()
        process.waitUntilExit()
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        return text.split(whereSeparator: \.isNewline).last.map(String.init) ?? ""
    }

    private func expectLanded(_ printed: String, in scratch: Scratch) throws {
        #expect(URL(fileURLWithPath: printed).resolvingSymlinksInPath().path
                == scratch.folder.resolvingSymlinksInPath().path)
        let left = try FileManager.default.contentsOfDirectory(atPath: scratch.root.path)
        #expect(left == [Self.hostileName], "the folder name ran a command: \(left)")
    }

    @Test func theTerminalCommandCdsIntoTheLiteralFolder() throws {
        let scratch = try makeScratch()
        defer { try? FileManager.default.removeItem(at: scratch.root) }
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claudePath: "/bin/pwd", cwd: scratch.folder.path)
        try expectLanded(try run("/bin/sh", ["-c", cmd], in: scratch.root), in: scratch)
    }

    @Test func theGhosttyWindowScriptCdsIntoTheLiteralFolder() throws {
        let scratch = try makeScratch()
        defer { try? FileManager.default.removeItem(at: scratch.root) }
        let script = scratch.root.appendingPathComponent("launch.sh")
        try ClaudeLauncher.makeGhosttyScript(claudePath: "/bin/pwd", cwd: scratch.folder)
            .write(to: script, atomically: true, encoding: .utf8)
        let printed = try run("/bin/bash", [script.path], in: scratch.root)
        try FileManager.default.removeItem(at: script)
        try expectLanded(printed, in: scratch)
    }

    @Test func theGhosttyTabLineCdsIntoTheLiteralFolder() throws {
        let scratch = try makeScratch()
        defer { try? FileManager.default.removeItem(at: scratch.root) }
        let line = GhosttyLauncher.makeShellLine(cwd: scratch.folder, runningCommand: "/bin/pwd")
        try expectLanded(try run("/bin/sh", ["-c", line], in: scratch.root), in: scratch)
        // What the tab types is exactly that line once AppleScript unescapes it.
        let script = GhosttyLauncher.makeNewTabScript(cwd: scratch.folder, runningCommand: "/bin/pwd", delay: 0.3)
        let typed = try #require(script.split(separator: "\n")
            .first { $0.contains("keystroke \"cd ") })
            .trimmingCharacters(in: .whitespaces)
        #expect(Self.appleScriptLiteral(typed.dropFirst("keystroke ".count)) == line)
    }

    @Test func theQuotedFormIsWhatWeExpect() {
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claudePath: "/opt/claude", cwd: "/w/it's")
        #expect(cmd.hasPrefix(#"cd '/w/it'\''s' && clear && "#))
        #expect(cmd.hasSuffix("exec '/opt/claude'"))
        #expect(GhosttyLauncher.makeShellLine(cwd: URL(fileURLWithPath: "/w/$(x)"), runningCommand: "git diff")
                == "cd '/w/$(x)' && git diff")
    }

    /// Decodes one AppleScript string literal (`"…"` with `\\` and `\"`).
    static func appleScriptLiteral(_ quoted: Substring) -> String? {
        guard quoted.first == "\"", quoted.last == "\"", quoted.count >= 2 else { return nil }
        var out = ""
        var escaping = false
        for ch in quoted.dropFirst().dropLast() {
            if escaping { out.append(ch); escaping = false } else if ch == "\\" { escaping = true } else { out.append(ch) }
        }
        return escaping ? nil : out
    }
}
