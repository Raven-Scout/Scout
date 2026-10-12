import Testing
import Foundation
@testable import Scout

/// Raven-Scout/Scout#295: entity chips open the note in the Knowledge Base
/// tab (obsidian:// only as a fallback), cross-ref chips scroll to the item.
@Suite("Task ref routing")
struct TaskRefRoutingTests {
    private func task(_ tag: String?, done: Bool = false, links: [TaskDeepLink] = []) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: done, subject: "s", plainSubject: "s",
            body: "", comments: [], deepLinks: links, details: [], snoozedUntil: nil, carriedInFrom: nil,
            shortPrefix: tag
        )
    }

    private func section(_ kind: ActionSection.Kind, _ tasks: [ActionTask]) -> ActionSection {
        ActionSection(id: UUID(), emoji: "", title: "\(kind)", kind: kind, tasks: tasks,
                      bullets: [], tables: [], subheads: [], collapsed: [])
    }

    // MARK: - Chip targets

    @Test func entityChipTargetsTheNoteAndKeepsObsidianAsFallback() {
        let chip = TaskChip.chips(for: task(nil, links: [.entity(path: "people/alex", label: nil)])).first
        #expect(chip?.inApp == .note("people/alex"))
        #expect(chip?.links.first?.url.scheme == "obsidian")
    }

    @Test func crossRefChipTargetsTheTaggedItem() {
        let chip = TaskChip.chips(for: task(nil, links: [.crossRef(tag: "XREF")])).first
        #expect(chip?.inApp == .task(tag: "XREF"))
    }

    @Test func otherChipsHaveNoInAppTarget() {
        let chips = TaskChip.chips(for: task(nil, links: [
            .plainRef(text: "??garbage"), .linear(id: "PROJ-1234"),
            .githubPR(repo: "example-org/demo", number: 1, rawURL: URL(string: "https://github.com/example-org/demo/pull/1")!),
        ]))
        #expect(!chips.isEmpty && chips.allSatisfy { $0.inApp == nil })
    }

    // MARK: - KB note request

    @Test func aNoteRequestWaitsForTheIndexThenOpensOrFallsBack() {
        let resolve: (String) -> String? = { $0 == "people/alex" ? "knowledge-base/people/alex.md" : nil }
        #expect(KBNoteRequest.outcome(target: "people/alex", state: .idle, resolve: resolve) == .wait)
        #expect(KBNoteRequest.outcome(target: "people/alex", state: .loading, resolve: resolve) == .wait)
        #expect(KBNoteRequest.outcome(target: "people/alex", state: .loaded, resolve: resolve) == .open("knowledge-base/people/alex.md"))
        #expect(KBNoteRequest.outcome(target: "people/sam", state: .loaded, resolve: resolve) == .fallback)
        #expect(KBNoteRequest.outcome(target: "people/alex", state: .missing(URL(fileURLWithPath: "/tmp/kb")), resolve: resolve) == .fallback)
        #expect(KBNoteRequest.outcome(target: "people/alex", state: .failed("x"), resolve: resolve) == .fallback)
    }

    @Test func theFallbackIsTheObsidianNote() {
        #expect(KBNoteRequest.fallbackURL(for: "people/alex")?.absoluteString == "obsidian://open?vault=Scout&file=people/alex")
    }

    @MainActor @Test func requestingANoteSwitchesToTheKnowledgeBase() {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("TaskRefRouting-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let state = AppState(configuration: .testing(scoutDirectory: tmp))
        state.requestOpenKBNote("people/alex")
        #expect(state.pendingKBNote == "people/alex")
        #expect(state.requestedSidebar == .knowledgeBase)
    }

    // MARK: - Cross-ref lookup

    @Test func findsTheTaggedRowAndItsIndexInTheShownList() {
        let target = task("XREF")
        let todo = section(.todo, (0..<45).map { _ in task(nil) } + [target])
        let found = CrossRefLocator.locate(tag: "XREF", in: [section(.urgent, [task("NTX")]), todo])
        #expect(found == .init(sectionID: todo.id, index: 45, taskID: target.id))

        // Past the first page: revealing the index builds the row.
        var window = TaskWindow()
        #expect(!window.visible(todo.tasks).contains { $0.id == target.id })
        window.reveal(found!.index)
        #expect(window.visible(todo.tasks).contains { $0.id == target.id })
    }

    @Test func aStableActionComparesEqualAndRuns() {
        var seen: [String] = []
        let a = StableAction<String> { seen.append($0) }
        #expect(a == StableAction<String> { _ in })
        a("XREF")
        #expect(seen == ["XREF"])
    }

    @Test func matchesTheTagCaseInsensitively() {
        let target = task("XREF")
        let todo = section(.todo, [target])
        #expect(CrossRefLocator.locate(tag: "xref", in: [todo])?.taskID == target.id)
    }

    @Test func aDoneOrMissingItemIsNotFound() {
        let done = section(.done, [task("XREF", done: true)])
        #expect(CrossRefLocator.locate(tag: "XREF", in: [done]) == nil)
        #expect(CrossRefLocator.locate(tag: "XREF", in: [section(.todo, [task("NTX")])]) == nil)
    }
}
