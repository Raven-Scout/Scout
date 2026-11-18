import Foundation
import Testing
@testable import Scout

/// Card C3: the Control Center shows real values or nothing, never a
/// hard-coded stand-in, and lists only shortcuts that exist.
@Suite("Control Center placeholders")
struct ControlCenterPlaceholderTests {
    private func vault(head: String?) throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("c3-vault-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root.appendingPathComponent(".git"), withIntermediateDirectories: true)
        if let head { try head.write(to: root.appendingPathComponent(".git/HEAD"), atomically: true, encoding: .utf8) }
        return root
    }

    @Test("the branch row reads the vault's .git/HEAD")
    func branchFromHead() throws {
        let onBranch = try vault(head: "ref: refs/heads/work-notes\n")
        defer { try? FileManager.default.removeItem(at: onBranch) }
        #expect(RepoStateRailCard.currentBranch(vault: onBranch) == "work-notes")

        let detached = try vault(head: "0123456789abcdef0123456789abcdef01234567\n")
        defer { try? FileManager.default.removeItem(at: detached) }
        #expect(RepoStateRailCard.currentBranch(vault: detached) == "detached at 0123456")

        let noHead = try vault(head: nil)
        defer { try? FileManager.default.removeItem(at: noHead) }
        #expect(RepoStateRailCard.currentBranch(vault: noHead) == nil)

        let garbage = try vault(head: "not a head file")
        defer { try? FileManager.default.removeItem(at: garbage) }
        #expect(RepoStateRailCard.currentBranch(vault: garbage) == nil)
    }

    @Test("the Today column shows the vault's budget, or none")
    func budgetLine() {
        let reported = period(.reported, cost: 1.2)
        #expect(NowStripView.costLine(today: reported, dailyBudgetUSD: nil) == "cost $1.20")
        #expect(NowStripView.costLine(today: reported, dailyBudgetUSD: 0) == "cost $1.20")
        let line = NowStripView.costLine(today: reported, dailyBudgetUSD: 12.5)
        #expect(line.hasPrefix("cost $1.20 · budget "))
        #expect(line.contains("12.5"))
        #expect(line.hasSuffix("/day"))
        #expect(!line.contains("8.00"))
    }

    /// UI audit R1: the tile's cost is the usage summary's, so it agrees with the usage card
    /// and the status bar, and a cost no run measured is never a $0.
    @Test("the Today column's cost never shows an unmeasured $0")
    func costFromTheUsageSummary() {
        #expect(NowStripView.costLine(today: nil, dailyBudgetUSD: nil) == "cost —")
        #expect(NowStripView.costLine(today: period(.none, cost: 0), dailyBudgetUSD: nil) == "no cost yet")
        #expect(NowStripView.costLine(today: period(.unpriced, cost: 0), dailyBudgetUSD: nil) == "cost unknown")
        #expect(NowStripView.costLine(today: period(.partial, cost: 42.09), dailyBudgetUSD: nil) == "cost ≥$42.09")
        #expect(NowStripView.costLine(today: period(.priced, cost: 3.5), dailyBudgetUSD: nil) == "cost ≈$3.50")
        for kind in [UsageCostKind.reported, .priced, .partial, .unpriced] {
            let p = period(kind, cost: 7)
            #expect(NowStripView.costLine(today: p, dailyBudgetUSD: nil) == "cost \(UsageFormat.shortCost(p))")
        }
    }

    /// The tile's run count and its cost count the same day: the vault's, from the usage summary.
    @Test("the Today column counts the vault's day once the summary has loaded")
    func todayIsTheVaultsDay() {
        // The vault's day: 2026-11-17 in UTC+1 is 2026-11-16T23:00Z to 2026-11-17T23:00Z.
        let p = period(.reported, cost: 1)
        let iso = ISO8601DateFormatter()
        func run(_ at: String) -> Run {
            Run.make(type: .consolidation, startedAt: iso.date(from: at)!, endedAt: nil, status: .success)
        }
        let early = run("2026-11-16T23:30:00Z"), late = run("2026-11-17T23:30:00Z"), before = run("2026-11-16T22:30:00Z")
        let inTheVaultsDay = iso.date(from: "2026-11-17T20:00:00Z")!
        #expect(NowStripView.todayRuns([late, early, before], today: p, now: inTheVaultsDay).map(\.startedAt)
                == [early.startedAt])
        // Past the summary's day (its refresh after midnight failed): the host's day, never yesterday's count.
        var utcDay = Calendar(identifier: .gregorian); utcDay.timeZone = TimeZone(identifier: "UTC")!
        let nextDay = iso.date(from: "2026-11-18T00:30:00Z")!
        #expect(NowStripView.todayRuns([late, early, before], today: p, now: nextDay, calendar: utcDay).isEmpty)
        // Before the summary loads, the host's day: on a UTC host the 17th is a different window.
        var utc = Calendar(identifier: .gregorian); utc.timeZone = TimeZone(identifier: "UTC")!
        let noon = iso.date(from: "2026-11-17T12:00:00Z")!
        #expect(NowStripView.todayRuns([late, early, before], today: nil, now: noon, calendar: utc).map(\.startedAt)
                == [late.startedAt])
        #expect(p.contains(early.startedAt) == true && p.contains(late.startedAt) == false)
        #expect(p.ifCurrent(at: inTheVaultsDay) == p && p.ifCurrent(at: nextDay) == nil)
    }

    private func period(_ kind: UsageCostKind, cost: Double) -> UsagePeriod {
        UsagePeriod(start: "2026-11-17T00:00:00+01:00", end: "2026-11-18T00:00:00+01:00", runs: 11, runsWithTokens: 11,
                    sessions: 11, tokens: UsageTokens(input: 1, output: 1, cacheRead: 0, cacheCreation: 0),
                    costUSD: cost, costKind: kind, pricedRuns: 7)
    }

    @Test("the Keyboard card lists no unbound shortcut")
    func keyboardCard() {
        let keys = KeyboardRailCard.shortcuts.map(\.keys)
        for fake in ["⌘1", "⌘2", "⌘R", "⌘K", "⌘↵"] {
            #expect(!keys.contains(fake), "\(fake) isn't bound anywhere")
        }
        #expect(keys.contains("⌘."))
        #expect(keys.contains("⇧⌘F"))
    }
}
