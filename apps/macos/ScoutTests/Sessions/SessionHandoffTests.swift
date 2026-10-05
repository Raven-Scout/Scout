import Foundation
import Testing
@testable import Scout

@Suite("SessionHandoff")
struct SessionHandoffTests {

    @Test func aFullSessionHandsOffEverything() throws {
        let index = try SessionsFixture.index()
        let a = try #require(SessionsFixture.session("local_A", in: index))
        #expect(SessionHandoff.markdown(for: a, projectName: "Example Repo") == """
        ## Fix the parser

        - **Project:** Example Repo
        - **State:** needs you — changes requested on PR #98; CI failing; ended on a question
        - **PR:** example-org/example-repo#98 (changes requested) — https://github.com/example-org/example-repo/pull/98
        - **Branch:** `claude/w-compass` in worktree `w-compass`
        - **Folder:** `/Users/alex/code/example-repo/.claude/worktrees/w-compass`
        - **Resume:** `claude --resume aaaaaaaa-0000-0000-0000-000000000001`

        ### First prompt

        > Please fix the parser so blank lines between items are kept.

        ### Files touched

        - `~/code/example-repo/parser.py`
        - `~/code/example-repo/tests/test_parser.py`
        """)
    }

    @Test func aSparseSessionLeavesOutWhatItDoesNotHave() throws {
        let index = try SessionsFixture.index()
        let s = try #require(SessionsFixture.session("local_S", in: index))
        let text = SessionHandoff.markdown(for: s, projectName: "Example Repo")
        #expect(!text.contains("**PR:**"))
        #expect(!text.contains("### First prompt"))
        #expect(text.contains("- **State:** stale — dirty worktree, idle 6d"))
    }

    @Test func aMultiLinePromptStaysQuoted() throws {
        let index = try SessionsFixture.index()
        let cli = try #require(SessionsFixture.session("cli:aaaaaaaa-0000-0000-0000-000000000007", in: index))
        let text = SessionHandoff.markdown(for: cli, projectName: "other-repo")
        #expect(text.contains("> Sam asked for a release checklist.\n> Keep it short."))
        #expect(text.hasPrefix("## Sam asked for a release checklist."))
    }
}
