import Foundation
import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Action Items layout")
struct ActionItemsLayoutTests {
    @Test func aBindingWritesThroughUpdate() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("bind-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        let service = DisplayProfileService(scoutDirectory: vault, fileEvents: InjectableFS())
        service.binding(\.fields.comments).wrappedValue = true
        #expect(service.profile.actionItems.fields.comments)
        #expect(FileManager.default.fileExists(atPath: service.fileURL.path))
    }

    @Test func theBoardIsSortedButNeverMerged() {
        let section = SmokeFixtures.section(kind: .todo, tasks: [
            SmokeFixtures.task(subject: "Book travel"), SmokeFixtures.task(subject: "Ask Priya"),
        ])
        var display = ActionItemsDisplay()
        display.sort = .alphabetical
        display.grouping = .none
        let layout = ActionItemsLayout.make(document: [section], filtered: [section], display: display)
        #expect(layout.list.sections.map(\.kind) == [.neutral])
        let todo = layout.boardColumns.first { $0.kind == .todo }
        #expect(todo?.tasks.map(\.subject) == ["Ask Priya", "Book travel"])
    }

    @Test func theDefaultProfileLeavesTheSectionsAlone() {
        let sections = [SmokeFixtures.section(kind: .urgent), SmokeFixtures.section(kind: .todo)]
        #expect(ActionItemsLayout.isPassThrough(ActionItemsDisplay()))
        var sorted = ActionItemsDisplay()
        sorted.sort = .alphabetical
        #expect(!ActionItemsLayout.isPassThrough(sorted))
        let layout = ActionItemsLayout.make(document: sections, filtered: sections, display: ActionItemsDisplay())
        #expect(layout.list.sections == sections)
    }
}
