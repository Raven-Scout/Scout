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
        #expect(NowStripView.costLine(total: Decimal(string: "1.2")!, dailyBudgetUSD: nil) == "cost $1.2")
        #expect(NowStripView.costLine(total: 0, dailyBudgetUSD: 0) == "cost $0")
        let line = NowStripView.costLine(total: 0, dailyBudgetUSD: 12.5)
        #expect(line.hasPrefix("cost $0 · budget "))
        #expect(line.contains("12.5"))
        #expect(line.hasSuffix("/day"))
        #expect(!line.contains("8.00"))
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
