import Foundation
import Testing
@testable import Scout

@Suite("SessionIndex decoding")
struct SessionIndexDecodingTests {

    @Test func decodesEveryFixtureSession() throws {
        let index = try SessionsFixture.index()
        #expect(index.schemaVersion == 1)
        #expect(index.sessions.count == 11)
        #expect(index.unreadableSessions == 0)
        #expect(index.generatedAt == SessionsFixture.now)
        #expect(index.display == SessionIndexDisplay(doneVisibleHours: 24, staleAfterDays: 3))
        #expect(index.projects.map(\.name) == ["Example Repo", "other-repo", "Scout"])
        #expect(index.sourceErrors.map(\.source) == ["desktop"])
        #expect(index.sourceCounts["open"] == 3)
    }

    @Test func mapsEverySnakeCaseField() throws {
        let a = try #require(SessionsFixture.session("local_A", in: SessionsFixture.index()))
        #expect(a.cliSessionID == "aaaaaaaa-0000-0000-0000-000000000001")
        #expect(a.titleSource == "auto")
        #expect(a.projectKey == "/Users/alex/code/example-repo")
        #expect(a.groupName == "Example Repo")
        #expect(a.originCwd == "/Users/alex/code/example-repo")
        #expect(a.worktree?.sourceBranch == "main")
        #expect(a.worktree?.branch == "claude/w-compass")
        #expect(a.isOpen && !a.isArchived && !a.isScoutRun)
        #expect(a.turns == 19)
        #expect(a.state == .needsYou)
        #expect(a.stateReasons == ["changes requested on PR #98", "CI failing", "ended on a question"])
        #expect(a.prs.map(\.number) == [98, 90])
        let pr = try #require(a.pr)
        #expect(pr.reviewDecision == "CHANGES_REQUESTED")
        #expect(pr.checks == "failing")
        #expect(pr.mergeState == "CLEAN")
        #expect(pr.isDraft == false && pr.reviewRequested == false && pr.stale == false)
        #expect(pr.updatedAt == SessionIndex.parseTimestamp("2026-09-15T10:00:00Z"))
        let transcript = try #require(a.transcript)
        #expect(transcript.toolCalls == 212)
        #expect(transcript.filesTouched.count == 2)
        #expect(transcript.lastTurn.kind == "question")
        #expect(transcript.mtimeNs == 1_789_470_000_000_000_000)
    }

    @Test func aCLIOnlySessionHasNoTitleAndUsesItsFirstPromptLine() throws {
        let cli = try #require(SessionsFixture.session(
            "cli:aaaaaaaa-0000-0000-0000-000000000007", in: SessionsFixture.index()))
        #expect(cli.title == nil && cli.worktree == nil && cli.createdAt == nil && cli.turns == nil)
        #expect(cli.displayTitle == "Sam asked for a release checklist.")
    }

    @Test func projectCountsKeepTheEngineSpelling() throws {
        // `.convertFromSnakeCase` would rewrite these dictionary keys to "needsYou".
        let project = try #require(SessionsFixture.index().projects.first)
        #expect(project.counts[AgentSessionState.needsYou.rawValue] == 1)
        #expect(project.counts["needs_you"] == 1)
    }

    @Test func unknownFieldsAreIgnored() throws {
        var object = try SessionsFixture.object()
        object["added_later"] = ["anything": true]
        var sessions = try #require(object["sessions"] as? [[String: Any]])
        sessions[0]["added_later"] = 42
        object["sessions"] = sessions
        let index = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(index.sessions.count == 11 && index.unreadableSessions == 0)
    }

    @Test func anUnsupportedSchemaVersionIsRejected() throws {
        var object = try SessionsFixture.object()
        object["schema_version"] = 2
        #expect(throws: SessionIndexError.unsupportedSchema(2)) {
            try SessionIndex.decode(SessionsFixture.encode(object))
        }
    }

    @Test func aSessionThatDoesNotDecodeIsSkippedAndCounted() throws {
        var object = try SessionsFixture.object()
        var sessions = try #require(object["sessions"] as? [[String: Any]])
        sessions[1]["state"] = "exploded"
        sessions[2]["last_activity_at"] = "yesterday"
        object["sessions"] = sessions
        let index = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(index.sessions.count == 9)
        #expect(index.unreadableSessions == 2)
        #expect(index.sessions.first?.id == "local_A")
    }

    @Test func malformedJSONIsReportedAsMalformed() {
        #expect {
            try SessionIndex.decode(Data("{".utf8))
        } throws: { error in
            if case SessionIndexError.malformed = error { return true }
            return false
        }
    }

    @Test func timestampsAcceptWholeAndFractionalSeconds() {
        #expect(SessionIndex.parseTimestamp("2026-09-15T12:00:00Z") == SessionsFixture.now)
        #expect(SessionIndex.parseTimestamp("2026-09-15T12:00:00.250Z") == SessionsFixture.now.addingTimeInterval(0.25))
        #expect(SessionIndex.parseTimestamp("yesterday") == nil)
    }

    @Test func sameContentIgnoresOnlyGeneratedAt() throws {
        let index = try SessionsFixture.index()
        var object = try SessionsFixture.object()
        object["generated_at"] = "2026-09-15T12:00:02Z"
        let later = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(later.generatedAt != index.generatedAt)
        #expect(later.hasSameContent(as: index))

        var sessions = try #require(object["sessions"] as? [[String: Any]])
        sessions[0]["state_reasons"] = ["changes requested on PR #98"]
        object["sessions"] = sessions
        let changed = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(!changed.hasSameContent(as: index))
    }

    /// Mirrors scout-plugin `engine/tests/unit/test_sessions_index.py::
    /// test_index_json_contract_key_sets`. When that test changes, change this
    /// one, the fixture and the models together.
    @Test func theFixtureMatchesTheEngineContract() throws {
        let object = try SessionsFixture.object()
        #expect(Set(object.keys) == ["schema_version", "generated_at", "source_counts", "source_errors",
                                     "display", "projects", "sessions"])
        let sessions = try #require(object["sessions"] as? [[String: Any]])
        let sessionKeys: Set<String> = [
            "id", "cli_session_id", "title", "title_source", "project_key", "group_name", "cwd",
            "origin_cwd", "worktree", "created_at", "last_activity_at", "model", "effort", "turns",
            "is_archived", "is_open", "is_scout_run", "parent_session_id", "spawned_task_id",
            "scheduled_task_id", "prs", "pr", "transcript", "state", "state_reasons",
        ]
        let prKeys: Set<String> = [
            "number", "repo", "url", "state", "is_draft", "review_decision", "review_requested",
            "checks", "merge_state", "fetched_at", "stale", "updated_at",
        ]
        for session in sessions {
            #expect(Set(session.keys) == sessionKeys)
            if let worktree = session["worktree"] as? [String: Any] {
                #expect(Set(worktree.keys) == ["path", "name", "branch", "source_branch", "dirty"])
            }
            let prs = (session["prs"] as? [[String: Any]] ?? []) + [session["pr"] as? [String: Any]].compactMap { $0 }
            for pr in prs { #expect(Set(pr.keys) == prKeys) }
            if let transcript = session["transcript"] as? [String: Any] {
                #expect(Set(transcript.keys) == ["path", "first_prompt", "files_touched", "tool_calls",
                                                 "last_turn", "mtime_ns"])
                #expect(Set((transcript["last_turn"] as? [String: Any] ?? [:]).keys) == ["at", "kind"])
            }
        }
        let projects = try #require(object["projects"] as? [[String: Any]])
        for project in projects {
            #expect(Set(project.keys) == ["key", "name", "group_id", "counts"])
            #expect(Set((project["counts"] as? [String: Any] ?? [:]).keys)
                    == Set(AgentSessionState.allCases.map(\.rawValue)))
        }
        #expect(Set((object["display"] as? [String: Any] ?? [:]).keys) == ["done_visible_hours", "stale_after_days"])
        #expect(Set((object["source_counts"] as? [String: Any] ?? [:]).keys)
                == ["desktop", "cli_only", "open", "running", "prs_refreshed"])
    }
}
