import Foundation
import Testing
@testable import Scout

/// `--resume <id>` on every CLI terminal path (spec §6.4 "Resume in terminal").
@Suite("ClaudeLauncher resume")
struct ClaudeLauncherResumeTests {
    private let id = "aaaaaaaa-0000-0000-0000-000000000001"
    private var resume: ClaudeLauncher.CLIPurpose { .resume(cliSessionID: id) }

    @Test func resumeArgumentsAreTheFlagAndTheID() {
        #expect(resume.arguments == ["--resume", id])
        #expect(ClaudeLauncher.CLIPurpose.actionItem.arguments.isEmpty)
    }

    @Test func terminalAndITermExecClaudeWithTheResumeFlag() {
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claudePath: "/cl", cwd: "/w", purpose: resume)
        #expect(cmd.hasPrefix("cd \"/w\" && clear && "))
        #expect(cmd.hasSuffix("exec \"/cl\" '--resume' '\(id)'"))
        #expect(cmd.contains("resuming a Claude Code session"))
        #expect(!cmd.contains("clipboard"))
        #expect(ClaudeLauncher.makeTerminalAppScript(claudePath: "/cl", cwd: "/w", purpose: resume)
            .contains("'--resume' '\(id)'"))
        #expect(ClaudeLauncher.makeITermScript(claudePath: "/cl", cwd: "/w", purpose: resume)
            .contains("'--resume' '\(id)'"))
    }

    @Test func theActionItemCommandIsUnchanged() {
        #expect(ClaudeLauncher.makeTerminalShellCommand(claudePath: "/cl", cwd: "/w")
            == "cd \"/w\" && clear && "
            + "echo 'Scout: action-item context copied to your clipboard. Paste with Cmd+V.' && "
            + "exec \"/cl\"")
    }

    @Test func tmuxPassesTheFlagAsSeparateArgv() {
        #expect(ClaudeLauncher.makeTmuxNewWindowArguments(session: "main", claudePath: "/cl", cwd: "/w", purpose: resume)
            == ["new-window", "-t", "main:", "-c", "/w", "-n", "claude", "/cl", "--resume", id])
        #expect(ClaudeLauncher.makeTmuxNewWindowArguments(session: "main", claudePath: "/cl", cwd: "/w")
            == ["new-window", "-t", "main:", "-c", "/w", "-n", "claude", "/cl"])
    }

    @Test func ghosttyScriptsResumeAndKeepTheActionItemText() {
        let resumed = ClaudeLauncher.makeGhosttyScript(claudePath: "/cl", cwd: URL(fileURLWithPath: "/w"), purpose: resume)
        #expect(resumed.hasSuffix("exec \"/cl\" '--resume' '\(id)'"))
        #expect(!resumed.contains("clipboard"))
        #expect(ClaudeLauncher.makeGhosttyScript(claudePath: "/cl", cwd: URL(fileURLWithPath: "/w")) == """
        #!/bin/bash
        cd "/w" || exit 1
        clear
        echo "Scout: action-item context copied to your clipboard."
        echo "When Claude prompts you, paste (Cmd+V) and press Enter to send."
        echo
        exec "/cl"
        """)
    }

    @Test func aCustomCommandGetsTheArgumentsInsideClaude() {
        #expect(ClaudeLauncher.expandCustomCommand(
            template: "kitty -d {cwd} -e {claude}", claudePath: "/opt/claude", cwd: "/w", arguments: resume.arguments)
            == "kitty -d '/w' -e '/opt/claude' '--resume' '\(id)'")
    }

    @Test func aHostileIDStaysOneArgument() {
        let hostile = ClaudeLauncher.CLIPurpose.resume(cliSessionID: "x'; rm -rf ~; '")
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claudePath: "/cl", cwd: "/w", purpose: hostile)
        #expect(cmd.hasSuffix("'--resume' 'x'\\''; rm -rf ~; '\\'''"))
    }
}
